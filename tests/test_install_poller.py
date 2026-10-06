"""`scripts/install-poller.sh --print` against a stub stage0 and stub tools: what the
rendered unit must hold for a poller to survive a reboot (GH-176). The script and the
template are outside mypy (ADR-0021); this render is what covers them."""

import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

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
