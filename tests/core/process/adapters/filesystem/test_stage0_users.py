"""`ProcStage0Users` on a fake proc directory under `tmp_path` (GH-632, part 2b): one numeric
entry per process with `cwd`/`exe` symlinks and a NUL-separated `cmdline`. Files are owned by
the test's uid; a foreign uid is made by faking `os.getuid`. The real /proc is never read."""

import inspect
import os
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.stage0_users import ProcStage0Users
from agentic.core.process.domain.stage0_rotation import Unreadable

A = "a" * 40
B = "b" * 40


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "root" / A).mkdir(parents=True)
    (tmp_path / "root" / B).mkdir()
    return tmp_path / "root"


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    (tmp_path / "proc").mkdir()
    return tmp_path / "proc"


def process(
    proc: Path, pid: str, *, cwd: Path | None = None, exe: Path | None = None, argv=()
) -> Path:
    entry = proc / pid
    entry.mkdir()
    if cwd is not None:
        os.symlink(cwd, entry / "cwd")
    if exe is not None:
        os.symlink(exe, entry / "exe")
    (entry / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))
    return entry


def test_the_default_proc_root_is_proc():
    assert inspect.signature(ProcStage0Users).parameters["proc"].default == Path("/proc")


def test_an_empty_proc_uses_no_stage(root, proc):
    assert ProcStage0Users(root, proc).users() == frozenset()


def test_cwd_marks_its_stage_even_from_a_subdirectory(root, proc, tmp_path):
    (root / A / "sub").mkdir()
    process(proc, "100", cwd=root / A / "sub")
    process(proc, "101", cwd=tmp_path)
    assert ProcStage0Users(root, proc).users() == frozenset({A})


def test_exe_and_an_absolute_cmdline_element_mark_their_stages(root, proc, tmp_path):
    (root / A / "bin").mkdir()
    (root / A / "bin" / "python").write_text("")
    process(proc, "100", cwd=tmp_path, exe=root / A / "bin" / "python")
    process(proc, "101", cwd=tmp_path, argv=["python", "-u", str(root / B / "x.py"), "plain"])
    assert ProcStage0Users(root, proc).users() == frozenset({A, B})


def test_a_relative_cmdline_element_resolves_against_the_process_cwd(root, proc, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    process(proc, "100", cwd=work, argv=["python", "../root/" + A + "/x.py"])
    assert ProcStage0Users(root, proc).users() == frozenset({A})


def test_a_cmdline_through_the_current_link_marks_the_stage_it_points_at(root, proc, tmp_path):
    os.symlink(A, root / "current")
    process(proc, "100", cwd=tmp_path, argv=[str(root / "current" / "bin" / "python")])
    assert ProcStage0Users(root, proc).users() == frozenset({A})


def test_the_prefix_match_is_by_component(root, proc, tmp_path):
    (root / (A + "x")).mkdir()
    process(proc, "100", cwd=root / (A + "x"))
    process(proc, "101", cwd=tmp_path, argv=[str(root) + "-other/" + A])
    assert ProcStage0Users(root, proc).users() == frozenset({A + "x"})


def test_a_foreign_uid_is_skipped_without_reading_it(root, proc, tmp_path, monkeypatch):
    entry = process(proc, "100", cwd=root / A)
    (entry / "cmdline").unlink()
    (entry / "cmdline").mkdir()  # reading it would fail: it must not be read
    monkeypatch.setattr(os, "getuid", lambda: os.stat(proc).st_uid + 1)
    assert ProcStage0Users(root, proc).users() == frozenset()


def test_an_own_uid_entry_that_cannot_be_read_is_unreadable(root, proc, tmp_path):
    process(proc, "100", cwd=root / A)
    entry = process(proc, "101", cwd=tmp_path)
    (entry / "cmdline").unlink()
    (entry / "cmdline").mkdir()
    assert ProcStage0Users(root, proc).users() == Unreadable()


def test_a_missing_proc_is_unreadable(root, tmp_path):
    assert ProcStage0Users(root, tmp_path / "nope").users() == Unreadable()


@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
def test_a_pid_that_vanishes_during_the_scan_is_skipped(root, proc, tmp_path, monkeypatch, error):
    process(proc, "100", cwd=tmp_path)
    process(proc, "101", cwd=root / A)
    real = os.stat

    def stat(path, *args, **kwargs):
        if Path(path).name == "100":
            raise error()
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    assert ProcStage0Users(root, proc).users() == frozenset({A})


def test_a_process_without_cmdline_or_cwd_is_skipped(root, proc, tmp_path):
    (proc / "100").mkdir()
    process(proc, "101", cwd=root / B)
    assert ProcStage0Users(root, proc).users() == frozenset({B})


def test_non_numeric_entries_are_ignored(root, proc):
    process(proc, "self", cwd=root / A)
    (proc / "cpuinfo").write_text("x")
    assert ProcStage0Users(root, proc).users() == frozenset()


def test_the_command_line_is_never_printed(root, proc, tmp_path, capsys):
    process(proc, "100", cwd=tmp_path, argv=["tool", "--token=s3cret/value", str(root / A)])
    assert ProcStage0Users(root, proc).users() == frozenset({A})
    captured = capsys.readouterr()
    assert "s3cret" not in captured.out + captured.err
