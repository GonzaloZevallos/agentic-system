"""The sleep/shutdown inhibitor: a child `systemd-inhibit ... cat` held open through its
stdin for the lifetime of the run. A stub `systemd-inhibit` on a tmp PATH shows the
lifetime without needing logind; the real binary is exercised only where it exists."""

import shutil
import stat
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.shell import inhibitor


def stub_binary(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.02)


def test_without_the_binary_the_context_is_a_no_op_that_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # empty: no systemd-inhibit

    with inhibitor.hold("GH-1") as unavailable:
        assert unavailable is not None
        assert "systemd-inhibit" in unavailable


def test_the_child_sees_eof_on_stdin_when_the_context_exits(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started, eof = tmp_path / "started", tmp_path / "eof"
    # Like the real thing: options, then the command to run (`cat`) for the lock's life.
    stub_binary(
        bin_dir,
        "systemd-inhibit",
        f'echo "$@" > {started}; cat > /dev/null; echo done > {eof}\n',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")

    with inhibitor.hold("GH-1: run") as unavailable:
        assert unavailable is None
        wait_for(started)
        assert not eof.exists()  # held open for as long as the context
    wait_for(eof)

    args = started.read_text()
    assert "--what=sleep:shutdown" in args
    assert "--mode=block" in args
    assert "--why=GH-1: run" in args


def test_a_child_that_dies_at_once_is_unavailable_and_never_raises(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_binary(bin_dir, "systemd-inhibit", "echo no bus >&2; exit 1\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")

    with inhibitor.hold("GH-1") as unavailable:
        assert unavailable is not None


def test_a_binary_that_cannot_be_executed_is_unavailable(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Found by `which` (executable bit) but `Popen` cannot run it: no interpreter.
    broken = bin_dir / "systemd-inhibit"
    broken.write_text("#!/nonexistent/interpreter\n")
    broken.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    with inhibitor.hold("GH-1") as unavailable:
        assert unavailable is not None


def test_a_child_that_ignores_eof_is_killed_after_the_timeout(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_binary(bin_dir, "systemd-inhibit", "trap '' TERM; while true; do sleep 1; done\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(inhibitor, "RELEASE_TIMEOUT_S", 0.3)

    start = time.monotonic()
    with inhibitor.hold("GH-1"):
        pass
    assert time.monotonic() - start < 4


@pytest.mark.skipif(shutil.which("systemd-inhibit") is None, reason="no systemd-inhibit")
def test_the_real_binary_holds_and_releases():
    with inhibitor.hold("agentic test") as unavailable:
        # Without a logind session bus the real binary may refuse: both are fine, and
        # neither raises into the run.
        assert unavailable is None or isinstance(unavailable, str)
