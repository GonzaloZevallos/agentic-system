"""`agentic stage0 rotate` through `cli.main` (GH-632, part 2d): the factory is replaced by
port doubles, so these tests pin the wiring and the exit codes, not the rotation."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agentic import cli, composition
from agentic.core.process.adapters.shell import stage0_candidate
from agentic.core.process.domain.stage0_rotation import Unreadable
from agentic.shared import stage0
from tests.conftest import REAL_STAGE0_ROOT

GITHUB = "https://github.com/acme/system.git"


class HeldStore:
    @contextmanager
    def lock(self) -> Iterator[bool]:
        yield False


class Idle:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} must not run while the lock is held")


def idle() -> Any:
    return Idle()


class Products:
    """An `InstalledProducts` source that counts its reads."""

    def __init__(self, answer: tuple[Path, ...] | Unreadable = ()) -> None:
        self.answer = answer
        self.reads = 0

    def read(self) -> tuple[Path, ...] | Unreadable:
        self.reads += 1
        return self.answer


def bundle(products: tuple[Path, ...] = ()) -> composition.Stage0Rotation:
    return composition.Stage0Rotation(
        store=HeldStore(),
        users=idle(),
        window=idle(),
        candidate=idle(),
        failure_policy=idle(),
        escalation=idle(),
        prune=lambda store, users, own, out: None,
        products=products,
    )


def invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    factory: Callable[..., Any],
    *args: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(composition, "stage0_rotation", factory)
    monkeypatch.setattr("sys.argv", ["agentic", "stage0", "rotate", *args])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 0


def test_a_held_lock_prints_keep_busy_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roots: list[Path] = []

    def factory(root: Path, environ: Any) -> composition.Stage0Rotation:
        roots.append(root)
        return bundle()

    invoke(monkeypatch, tmp_path, factory, "--root", str(tmp_path / "stages"))

    out = capsys.readouterr().out.splitlines()
    assert out == [
        "smoke: --help only (no installed pollers)",
        "keep: busy",
    ]
    assert roots == [tmp_path / "stages"]


def test_the_root_defaults_to_the_stage0s_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    roots: list[Path] = []

    def factory(root: Path, environ: Any) -> composition.Stage0Rotation:
        roots.append(root)
        return bundle()

    invoke(monkeypatch, tmp_path, factory)

    assert roots == [stage0.DEFAULT_ROOT]
    assert REAL_STAGE0_ROOT == Path.home() / ".agentic-stage0s"


def test_an_installed_products_source_prints_no_help_only_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invoke(monkeypatch, tmp_path, lambda root, environ: bundle((Path("/srv/a"),)))

    assert capsys.readouterr().out.splitlines() == ["keep: busy"]


def test_an_origin_that_is_not_github_keeps_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invoke(monkeypatch, tmp_path, lambda root, environ: Unreadable())

    assert capsys.readouterr().out.splitlines() == ["keep: unreadable"]


@pytest.mark.parametrize(("value", "pin"), [("", None), ("abc1234", "abc1234")])
def test_the_pin_comes_from_the_environment_and_empty_means_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str, pin: str | None
) -> None:
    pins: list[str | None] = []
    monkeypatch.setattr(cli, "rotate", lambda *a, pin, **kw: pins.append(pin))
    monkeypatch.setenv("AGENTIC_STAGE0_PIN", value)

    invoke(monkeypatch, tmp_path, lambda root, environ: bundle())

    assert pins == [pin]


def test_an_absent_pin_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pins: list[str | None] = []
    monkeypatch.setattr(cli, "rotate", lambda *a, pin, **kw: pins.append(pin))
    monkeypatch.delenv("AGENTIC_STAGE0_PIN", raising=False)

    invoke(monkeypatch, tmp_path, lambda root, environ: bundle())

    assert pins == [None]


def test_the_origin_check_refuses_a_non_github_url_and_accepts_the_two_github_forms() -> None:
    pattern = composition._GITHUB_URL
    for good in (
        "https://github.com/acme/system",
        "https://github.com/acme/system.git",
        "git@github.com:acme/system.git",
    ):
        assert pattern.fullmatch(good)
    for bad in (
        "https://evil.example/acme/system.git",
        "https://github.com.evil.example/acme/system",
        "ssh://github-alias/acme/system.git",
        "https://github.com/acme/system/extra",
        "",
    ):
        assert pattern.fullmatch(bad) is None


def _checkout_with_origin(tmp_path: Path, url: str | None) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    if url is not None:
        subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", url], check=True)
    return checkout


def test_the_factory_wires_the_rotation_from_the_origin_of_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://github.com/acme/system.git"
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, url))

    source = Products((Path("/srv/a"), Path("/srv/b")))

    rotation = composition.stage0_rotation(tmp_path / "stages", {}, source)

    assert isinstance(rotation, composition.Stage0Rotation)
    assert rotation.products == (Path("/srv/a"), Path("/srv/b"))
    assert source.reads == 1
    assert rotation.window.remote_url == url
    assert rotation.window.root == tmp_path / "stages"


@pytest.mark.parametrize(
    "url", ["https://example.com/acme/system.git", "git@alias:acme/s.git", None]
)
def test_the_factory_refuses_an_origin_that_is_not_github_before_building_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str | None
) -> None:
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, url))

    assert composition.stage0_rotation(tmp_path / "stages", {}, Products()) == Unreadable()
    assert not (tmp_path / "stages").exists()


def test_the_factory_keeps_when_the_installed_products_are_unreadable_before_building_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://github.com/acme/system.git"
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, url))
    source = Products(Unreadable())

    assert composition.stage0_rotation(tmp_path / "stages", {}, source) == Unreadable()
    assert source.reads == 1
    assert not (tmp_path / "stages").exists()


def test_the_factory_reads_the_units_of_the_xdg_config_home_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://github.com/acme/system.git"
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, url))
    units = write_units(tmp_path / "cfg", ("a", "/srv/a"), ("b", "/srv/b"))

    rotation = composition.stage0_rotation(
        tmp_path / "stages", {"XDG_CONFIG_HOME": str(units.parents[1])}
    )

    assert isinstance(rotation, composition.Stage0Rotation)
    assert rotation.products == (Path("/srv/a"), Path("/srv/b"))


def test_the_factory_keeps_when_there_is_no_home_to_find_the_units_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://github.com/acme/system.git"
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, url))

    assert composition.stage0_rotation(tmp_path / "stages", {}) == Unreadable()
    assert not (tmp_path / "stages").exists()


def write_units(config_home: Path, *units: tuple[str, str]) -> Path:
    """`<config_home>/systemd/user` with one `agentic-poll-<name>.service` per `(name, dir)`."""
    directory = config_home / "systemd" / "user"
    directory.mkdir(parents=True)
    for name, product in units:
        (directory / f"agentic-poll-{name}.service").write_text(
            f'[Service]\nExecStart="/s/.venv/bin/agentic" poll --product-dir "{product}"\n'
        )
    return directory


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def run(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float) -> int:
        self.calls.append((tuple(argv), cwd))
        return 0


@pytest.fixture
def smoking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[RecordingRunner, Path, list[str]]]:
    """The real factory over a checkout with a GitHub origin and `XDG_CONFIG_HOME` in
    `tmp_path`, `rotate` replaced by one smoke of `<root>/stage`, and every child recorded."""
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, GITHUB))
    config_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    runner = RecordingRunner()
    monkeypatch.setattr(stage0_candidate, "SubprocessRunner", lambda: runner)
    smoked: list[str] = []

    def rotate(store: Any, users: Any, window: Any, candidate: Any, *a: Any, **kw: Any) -> None:
        smoked.append(type(candidate.smoke("stage")).__name__)

    monkeypatch.setattr(cli, "rotate", rotate)
    yield runner, config_home, smoked


def test_rotate_smokes_help_and_one_dry_poll_per_installed_product(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    smoking: tuple[RecordingRunner, Path, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, config_home, smoked = smoking
    write_units(config_home, ("b", "/srv/b"), ("a", "/srv/a"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "stage0", "rotate", "--root", str(tmp_path / "stages")]
    )

    with pytest.raises(SystemExit) as exit_:
        cli.main()

    stage = tmp_path / "stages" / "stage"
    agentic = str(stage / ".venv" / "bin" / "agentic")
    assert exit_.value.code == 0
    assert smoked == ["Green"]
    assert runner.calls == [
        ((agentic, "--help"), stage),
        ((agentic, "poll", "--product-dir", "/srv/a", "--dry-run", "--once"), stage),
        ((agentic, "poll", "--product-dir", "/srv/b", "--dry-run", "--once"), stage),
    ]
    assert capsys.readouterr().out == ""


def test_rotate_with_no_units_smokes_only_help_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    smoking: tuple[RecordingRunner, Path, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, _, _ = smoking  # no unit dir at all
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "stage0", "rotate", "--root", str(tmp_path / "stages")]
    )

    with pytest.raises(SystemExit) as exit_:
        cli.main()

    stage = tmp_path / "stages" / "stage"
    assert exit_.value.code == 0
    assert [argv for argv, _ in runner.calls] == [
        (str(stage / ".venv" / "bin" / "agentic"), "--help")
    ]
    assert capsys.readouterr().out.splitlines() == ["smoke: --help only (no installed pollers)"]


def test_rotate_with_an_unreadable_unit_prints_keep_unreadable_and_builds_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    smoking: tuple[RecordingRunner, Path, list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner, config_home, smoked = smoking
    units = write_units(config_home, ("a", "/srv/a"))
    (units / "agentic-poll-b.service").write_text("[Service]\nExecStart=/bin/true\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv", ["agentic", "stage0", "rotate", "--root", str(tmp_path / "stages")]
    )

    with pytest.raises(SystemExit) as exit_:
        cli.main()

    assert exit_.value.code == 0
    assert capsys.readouterr().out.splitlines() == ["keep: unreadable"]
    assert runner.calls == []
    assert smoked == []
    assert not (tmp_path / "stages").exists()
