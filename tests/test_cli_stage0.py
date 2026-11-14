"""`agentic stage0 rotate` through `cli.main` (GH-632, part 2d): the factory is replaced by
port doubles, so these tests pin the wiring and the exit codes, not the rotation."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agentic import cli, composition
from agentic.core.process.domain.stage0_rotation import Unreadable
from agentic.shared import stage0


class HeldStore:
    @contextmanager
    def lock(self) -> Iterator[bool]:
        yield False


class Idle:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} must not run while the lock is held")


def idle() -> Any:
    return Idle()


def bundle(products: Any = None) -> composition.Stage0Rotation:
    return composition.Stage0Rotation(
        store=HeldStore(),
        users=idle(),
        window=idle(),
        candidate=idle(),
        failure_policy=idle(),
        escalation=idle(),
        prune=lambda store, users, own, out: None,
        products=products if products is not None else composition.NoInstalledProducts(),
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
        "smoke: --help only (installed products are read by #726)",
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
    assert stage0.DEFAULT_ROOT == Path.home() / ".agentic-stage0s"


def test_an_installed_products_source_prints_no_help_only_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class Some:
        def read(self) -> tuple[Path, ...]:
            return (Path("/srv/a"),)

    invoke(monkeypatch, tmp_path, lambda root, environ: bundle(Some()))

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

    rotation = composition.stage0_rotation(tmp_path / "stages", {})

    assert isinstance(rotation, composition.Stage0Rotation)
    assert isinstance(rotation.products, composition.NoInstalledProducts)
    assert rotation.window.remote_url == url
    assert rotation.window.root == tmp_path / "stages"


@pytest.mark.parametrize(
    "url", ["https://example.com/acme/system.git", "git@alias:acme/s.git", None]
)
def test_the_factory_refuses_an_origin_that_is_not_github_before_building_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str | None
) -> None:
    monkeypatch.setattr(stage0, "CHECKOUT", _checkout_with_origin(tmp_path, url))

    assert composition.stage0_rotation(tmp_path / "stages", {}) == Unreadable()
    assert not (tmp_path / "stages").exists()
