"""`scripts/install-poller.sh --print` against a stub stage0 and stub tools: what the
rendered unit must hold for a poller to survive a reboot (GH-176). The script and the
template are outside mypy (ADR-0021); this render is what covers them."""

import fnmatch
import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from agentic.cli import build_parser

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-poller.sh"
TOOLS = ("git", "gh", "uv", "claude")


def executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def stage0(tmp_path):
    root = tmp_path / "stage0"
    executable(root / ".venv/bin/agentic")
    return root


@pytest.fixture
def product(tmp_path):
    root = tmp_path / "work" / "my product"
    root.mkdir(parents=True)
    return root


def stub_path(tmp_path, tools=TOOLS) -> str:
    """Two directories, so the de-duplication and the order are exercised. The system's
    own bins follow, for the shell, sed, realpath and systemd-escape the script uses."""
    first, second = tmp_path / "tools-a", tmp_path / "tools-b"
    for tool in tools:
        executable((first if tool in ("git", "gh") else second) / tool)
    return f"{first}:{second}:/usr/bin:/bin"


def render(product, stage0, path, *extra):
    return subprocess.run(
        ["bash", str(SCRIPT), str(product), str(stage0), "--print", *extra],
        env={"PATH": path, "HOME": str(product)},
        capture_output=True,
        text=True,
    )


def test_the_rendered_unit_is_absolute_complete_and_free_of_specifiers(tmp_path, product, stage0):
    result = render(product, stage0, stub_path(tmp_path))

    assert result.returncode == 0, result.stderr
    unit = result.stdout
    assert not re.search(r"@[A-Z0-9_]+@", unit)
    assert "%" not in unit
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    # What systemd parses: whitespace separates arguments unless the item is quoted, and
    # the product dir of the fixture has a space in it.
    argv = shlex.split(exec_start.removeprefix("ExecStart="))
    binary = Path(argv[0])
    assert binary.is_absolute() and binary.exists() and binary.stat().st_mode & stat.S_IXUSR
    assert f"WorkingDirectory={stage0.resolve()}" in unit
    assert " " in str(product.resolve())
    assert argv[1:] == ["poll", "--product-dir", str(product.resolve())]
    assert "--detach" not in exec_start
    for line in (
        "Restart=on-failure",
        "RestartSec=30",
        "StartLimitIntervalSec=0",
        "KillMode=process",
        "WantedBy=default.target",
    ):
        assert line in unit.splitlines()
    env_path = next(line for line in unit.splitlines() if line.startswith('Environment="PATH='))
    dirs = env_path.removeprefix('Environment="PATH=').removesuffix('"').split(":")
    assert dirs == [str(tmp_path / "tools-a"), str(tmp_path / "tools-b"), "/usr/bin", "/bin"]
    assert all(d.startswith("/") for d in dirs)


@pytest.mark.parametrize("missing", TOOLS)
def test_a_missing_tool_makes_the_install_fail_and_names_it(tmp_path, product, stage0, missing):
    path = stub_path(tmp_path, tools=tuple(t for t in TOOLS if t != missing))
    # `git` and `gh` often live in /usr/bin too: hide them by using a PATH without it.
    only = ":".join(p for p in path.split(":") if p.startswith(str(tmp_path)))
    system_bins = tmp_path / "system"
    system_bins.mkdir()
    for name in ("bash", "sed", "realpath", "dirname", "cat", "mkdir"):
        found = shutil.which(name)
        assert found
        (system_bins / name).symlink_to(found)

    result = render(product, stage0, f"{only}:{system_bins}")

    assert result.returncode != 0
    assert f"{missing} not found" in result.stderr
    assert result.stdout == ""


def test_a_stage0_without_the_agentic_binary_is_refused(tmp_path, product):
    empty = tmp_path / "empty-stage0"
    empty.mkdir()

    result = render(product, empty, stub_path(tmp_path))

    assert result.returncode != 0
    assert ".venv/bin/agentic" in result.stderr


@pytest.mark.parametrize("name", ["a|b", 'a"b', "a$b"])
def test_a_path_that_would_break_the_rendering_is_refused(tmp_path, stage0, name):
    bad = tmp_path / name
    bad.mkdir()

    result = render(bad, stage0, stub_path(tmp_path))

    assert result.returncode != 0
    assert "refusing a path" in result.stderr


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="no systemd-analyze")
def test_systemd_accepts_the_rendered_unit(tmp_path, product, stage0):
    result = render(product, stage0, stub_path(tmp_path))
    unit = tmp_path / "agentic-poll-test.service"
    unit.write_text(result.stdout)

    verify = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True
    )

    # Only what the unit itself got wrong: its own syntax, not other units on the machine
    # nor the stub tools.
    ours = [line for line in verify.stderr.splitlines() if str(unit) in line]
    assert not ours, "\n".join(ours)


# ---- GH-634 part 4a: a symlinked stage0 and the stage0 rotate units -------------------

REPO = SCRIPT.parent.parent

# The full render of the script and template as they were before this change (git show
# 7b0a1a1c:scripts/install-poller.sh and the template, run in a temp dir with a product
# "my product", a stage0 that is not a link and two tool dirs, --print), pasted literally;
# the fixture paths are placeholders the test fills.
GOLDEN = """# One poller per product, as a `systemd --user` service (GH-176). Not installed by hand:
# `scripts/install-poller.sh` renders the placeholders into absolute paths and writes
# the result under ~/.config/systemd/user.
#
# KillMode=process: the runs the poller launches with `--detach` stay in this unit's
# cgroup (setsid leaves the session, not the cgroup). With the default `control-group`
# every restart of the poller would kill the runs it launched. `process` stops only the
# poller itself and leaves its runs alone; they are found again through their locks.
#
# There is no EnvironmentFile=: the poller reads `.env` itself with `load_dotenv()` from
# its working directory, which is why WorkingDirectory= is the stage0 checkout.
[Unit]
Description=agentic poller for @PRODUCT@
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=@STAGE0@
Environment="PATH=@TOOLS_A@:@TOOLS_B@:/usr/bin:/bin"
ExecStart="@STAGE0@/.venv/bin/agentic" poll --product-dir "@PRODUCT@"
Restart=on-failure
RestartSec=30
KillMode=process

[Install]
WantedBy=default.target
"""


def run_script(script, args, env, cwd=None):
    return subprocess.run(
        ["bash", str(script), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def rotation_setup(tmp_path):
    """HOME with ~/.agentic-stage0s/current -> <sha>, a stub agentic in the stage."""
    home = tmp_path / "home"
    root = home / ".agentic-stage0s"
    executable(root / "abc123/.venv/bin/agentic")
    link = root / "current"
    link.symlink_to("abc123")
    work = tmp_path / "work" / "my product"
    work.mkdir(parents=True)
    return home, root, link, work


def rotate_render(home, work, link, path, *extra):
    return run_script(
        SCRIPT,
        [work, link, "--print", "--rotate", *extra],
        {"PATH": path, "HOME": str(home)},
    )


def split_units(stdout):
    units = {}
    name = None
    for line in stdout.splitlines():
        match = re.fullmatch(r"# ---- (.+) ----", line)
        if match:
            name = match.group(1)
            units[name] = []
        else:
            assert name is not None, line
            units[name].append(line)
    return {key: "\n".join(value) + "\n" for key, value in units.items()}


def test_the_full_render_with_an_explicit_stage0_that_is_not_a_link_is_the_golden_text(
    tmp_path, product, stage0
):
    result = render(product, stage0, stub_path(tmp_path))

    assert result.returncode == 0, result.stderr
    expected = (
        GOLDEN.replace("@PRODUCT@", str(product.resolve()))
        .replace("@STAGE0@", str(stage0.resolve()))
        .replace("@TOOLS_A@", str(tmp_path / "tools-a"))
        .replace("@TOOLS_B@", str(tmp_path / "tools-b"))
    )
    assert result.stdout == expected


def test_a_stage0_that_is_an_absolute_symlink_keeps_the_link_path(tmp_path, product, stage0):
    link = tmp_path / "current"
    link.symlink_to(stage0)

    result = render(product, link, stub_path(tmp_path))

    assert result.returncode == 0, result.stderr
    assert f"WorkingDirectory={link}\n" in result.stdout
    assert f'ExecStart="{link}/.venv/bin/agentic" poll' in result.stdout
    assert str(stage0) not in result.stdout


@pytest.mark.parametrize("argument", ["current", "current/", "current//", "./current"])
def test_a_relative_argument_that_is_a_link_is_rendered_absolute_and_unresolved(
    tmp_path, product, stage0, argument
):
    link = tmp_path / "current"
    link.symlink_to(stage0)

    result = run_script(
        SCRIPT,
        [product, argument, "--print"],
        {"PATH": stub_path(tmp_path), "HOME": str(product)},
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert f"WorkingDirectory={tmp_path / 'current'}\n" in result.stdout
    assert f'ExecStart="{tmp_path / "current"}/.venv/bin/agentic"' in result.stdout
    assert str(stage0) not in result.stdout


def test_the_default_stage0_through_a_linked_checkout_is_still_resolved(tmp_path, product):
    real = tmp_path / "real"
    shutil.copytree(REPO / "scripts", real / "scripts")
    executable(real / ".venv/bin/agentic")
    (tmp_path / "via").symlink_to(real)

    result = run_script(
        tmp_path / "via/scripts/install-poller.sh",
        [product, "--print"],
        {"PATH": stub_path(tmp_path), "HOME": str(product)},
    )

    assert result.returncode == 0, result.stderr
    assert f"WorkingDirectory={real}\n" in result.stdout
    assert f'ExecStart="{real}/.venv/bin/agentic"' in result.stdout
    assert "via" not in result.stdout


def test_rotate_prints_the_poller_the_service_and_the_timer(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)

    result = rotate_render(home, work, link, stub_path(tmp_path))

    assert result.returncode == 0, result.stderr
    units = split_units(result.stdout)
    poller = next(name for name in units if name.startswith("agentic-poll-"))
    assert list(units) == [poller, "agentic-stage0-rotate.service", "agentic-stage0-rotate.timer"]
    assert [name for name in units if fnmatch.fnmatch(name, "agentic-poll*.service")] == [poller]
    for name, unit in units.items():
        assert not re.search(r"@[A-Z0-9_]+@", unit), name
        assert "%" not in unit, name
        assert not any(line.startswith("## ") for line in unit.splitlines()), name
        for line in unit.splitlines():
            if line.startswith(("WorkingDirectory=", "ExecStart=")):
                assert Path(shlex.split(line.split("=", 1)[1])[0]).is_absolute(), line
    assert f"WorkingDirectory={link}\n" in units[poller]
    service = units["agentic-stage0-rotate.service"]
    assert "Type=oneshot" in service.splitlines()
    assert "TimeoutStartSec=60min" in service.splitlines()
    assert f"WorkingDirectory={link}\n" in service
    timer = units["agentic-stage0-rotate.timer"]
    assert "OnUnitActiveSec=5min" in timer.splitlines()
    assert "OnBootSec=2min" in timer.splitlines()
    assert not any(line.startswith("Persistent") for line in timer.splitlines())


def test_the_rotate_exec_start_parses_with_the_real_command_line(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)

    result = rotate_render(home, work, link, stub_path(tmp_path))

    service = split_units(result.stdout)["agentic-stage0-rotate.service"]
    exec_start = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    argv = shlex.split(exec_start.removeprefix("ExecStart="))
    assert argv[0] == f"{link}/.venv/bin/agentic"
    parsed = build_parser().root.parse_args(argv[1:])
    assert parsed.command == "stage0"
    assert parsed.stage0_command == "rotate"
    assert parsed.root == Path(str(root))


def test_lines_starting_with_two_hashes_never_reach_a_rendered_unit(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)
    scripts = tmp_path / "copy" / "scripts"
    shutil.copytree(REPO / "scripts", scripts)
    for template in (scripts / "systemd").glob("*.in"):
        with template.open("a") as handle:
            handle.write("## injected\n")
        text = template.read_text().replace("[Unit]\n", "## injected\n[Unit]\n", 1)
        template.write_text(text)

    result = run_script(
        scripts / "install-poller.sh",
        [work, link, "--print", "--rotate"],
        {"PATH": stub_path(tmp_path), "HOME": str(home)},
    )

    assert result.returncode == 0, result.stderr
    assert "injected" not in result.stdout
    assert not any(line.startswith("## ") for line in result.stdout.splitlines())


def test_a_symlinked_stage0_without_rotate_has_no_template_notes(tmp_path, product, stage0):
    link = tmp_path / "current"
    link.symlink_to(stage0)

    result = render(product, link, stub_path(tmp_path))

    assert not any(line.startswith("## ") for line in result.stdout.splitlines())
    assert "# ----" not in result.stdout
    assert "rotate" not in result.stdout


def test_rotate_with_a_stage0_that_is_not_a_link_is_refused_with_the_bootstrap(
    tmp_path, product, stage0
):
    result = render(product, stage0, stub_path(tmp_path), "--rotate")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "scripts/stage0.sh <ref> <root>/<sha>" in result.stderr
    assert "uv sync" in result.stderr
    assert "ln -s <sha> <root>/current" in result.stderr


def test_rotate_with_a_link_that_is_not_called_current_is_refused(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)
    other = root / "latest"
    other.symlink_to("abc123")

    result = rotate_render(home, work, other, stub_path(tmp_path))

    assert result.returncode == 1
    assert "ln -s <sha> <root>/current" in result.stderr


def test_rotate_with_a_current_whose_stage_has_no_agentic_is_refused(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)
    (root / "empty").mkdir()
    link.unlink()
    link.symlink_to("empty")

    result = rotate_render(home, work, link, stub_path(tmp_path))

    assert result.returncode == 1
    assert "ln -s <sha> <root>/current" in result.stderr


def test_rotate_with_a_current_outside_the_rotation_root_is_refused_naming_the_root(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    executable(elsewhere / "abc/.venv/bin/agentic")
    (elsewhere / "current").symlink_to("abc")

    result = rotate_render(home, work, elsewhere / "current", stub_path(tmp_path))

    assert result.returncode == 1
    assert str(home / ".agentic-stage0s") in result.stderr
    assert str(elsewhere) in result.stderr
    assert result.stdout == ""


def test_rotate_installs_three_files_and_enables_the_poller_and_the_timer(tmp_path):
    home, root, link, work = rotation_setup(tmp_path)
    bin_dir = tmp_path / "stubs"
    log = tmp_path / "systemctl.log"
    executable(bin_dir / "systemctl", f'#!/bin/sh\necho "$@" >> {log}\n')
    executable(bin_dir / "systemd-escape", "#!/bin/sh\necho escaped-product\n")
    config = tmp_path / "config"
    env = {
        "PATH": f"{bin_dir}:{stub_path(tmp_path)}",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "USER": "tester",
    }

    first = run_script(SCRIPT, [work, link, "--rotate"], env)

    assert first.returncode == 0, first.stderr
    target = config / "systemd" / "user"
    names = sorted(path.name for path in target.iterdir())
    assert names == [
        "agentic-poll-escaped-product.service",
        "agentic-stage0-rotate.service",
        "agentic-stage0-rotate.timer",
    ]
    calls = log.read_text().splitlines()
    assert calls == [
        "--user daemon-reload",
        "--user enable --now agentic-poll-escaped-product.service",
        "--user enable --now agentic-stage0-rotate.timer",
    ]
    assert "systemctl --user restart agentic-poll-escaped-product.service" in first.stdout
    before = {path.name: path.read_bytes() for path in target.iterdir()}

    second = run_script(SCRIPT, [work, link, "--rotate"], env)

    assert second.returncode == 0, second.stderr
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_rotate_refuses_a_root_that_would_break_the_rendering(tmp_path):
    home = tmp_path / "ho$me"
    executable(home / ".agentic-stage0s/abc/.venv/bin/agentic")
    link = home / ".agentic-stage0s" / "current"
    link.symlink_to("abc")
    work = tmp_path / "work"
    work.mkdir()

    result = rotate_render(home, work, link, stub_path(tmp_path))

    assert result.returncode == 1
    assert "refusing a path" in result.stderr
    assert result.stdout == ""
