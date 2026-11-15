"""Releasing a task's processes (GH-282, ADR-0052) through `memory_ports` and `FakeReaper`."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic.core.process.adapters.shell.reaper import ShellReaper
from agentic.core.process.application.ports import GitError
from agentic.core.process.application.release import (
    release_checkout,
    release_processes,
    release_task,
)
from agentic.core.process.domain.reaping import CHECK_TASK, MARK_NAME, PERF_TASK, ReapedProcess
from tests.fakes import FakeReaper, MemoryWorkspace, memory_ports

LEFT: tuple[ReapedProcess, ...] = ({"pid": 7, "command": "sleep 99", "outcome": "kill"},)
OTHER: tuple[ReapedProcess, ...] = ({"pid": 8, "command": "uv run app", "outcome": "term"},)


class _Layout(MemoryWorkspace):
    """Worktrees of a task and scratch checkouts laid out under `root`, the way the real
    workspace does it, and every call journalled."""

    def __init__(self, root: Path, journal: list[str]) -> None:
        super().__init__()
        self.root = root
        self.journal = journal

    def worktrees(self, task_id: str) -> list[Path]:
        return [self.root / task_id, self.root / f"{task_id}.api"]

    def checkout_path(self, name: str) -> Path:
        return self.root / name


def _journalled(root: Path) -> tuple[FakeReaper, _Layout, list[str]]:
    journal: list[str] = []
    reaper = FakeReaper(LEFT, reaped_runs=OTHER)
    reaper.journal = journal
    return reaper, _Layout(root, journal), journal


def test_the_task_is_reaped_by_mark_then_by_each_checkout_then_removed(tmp_path):
    reaper, workspace, journal = _journalled(tmp_path)
    reaper.reaped_within = OTHER
    ports = memory_ports(reaper=reaper, workspace=workspace)

    reaped = release_task(ports, "GH-12")

    assert reaper.calls == [("task", "GH-12", None, None)]  # never one run's scope
    assert reaper.within_calls == [
        (tmp_path / "GH-12",),
        (tmp_path / "GH-12.api",),
        (tmp_path / "merge-GH-12",),
        (tmp_path / "slice-GH-12",),
    ]
    assert journal == [
        "reap:GH-12",
        "reap:GH-12.api",
        "reap:merge-GH-12",
        "reap:slice-GH-12",
        "remove:merge-GH-12",
        "remove:slice-GH-12",
        "cleanup:GH-12",
    ]
    assert reaped == LEFT + OTHER * 4  # the mark's entries, then each checkout's


def test_a_discard_after_a_killed_merge_reaps_the_merge_checkout(tmp_path):
    reaper, workspace, journal = _journalled(tmp_path)
    ports = memory_ports(reaper=reaper, workspace=workspace)

    release_task(ports, "GH-12")

    assert journal.index("reap:merge-GH-12") < journal.index("remove:merge-GH-12")


def test_a_failed_cleanup_propagates_unchanged_after_every_reap(tmp_path):
    reaper, workspace, journal = _journalled(tmp_path)
    workspace.cleanup_error = "worktree is dirty"
    ports = memory_ports(reaper=reaper, workspace=workspace)

    with pytest.raises(GitError, match="worktree is dirty"):
        release_task(ports, "GH-12")

    assert reaper.calls == [("task", "GH-12", None, None)]
    assert journal[-1] == "cleanup:GH-12" and len(reaper.within_calls) == 4


def test_a_checkout_is_reaped_alone_and_what_it_stopped_is_said_on_stderr(tmp_path, capsys):
    reaper = FakeReaper()
    reaper.reaped_within = LEFT
    ports = memory_ports(reaper=reaper)

    reaped = release_checkout(ports, tmp_path / "slice-GH-12")

    assert reaped == LEFT
    assert reaper.within_calls == [(tmp_path / "slice-GH-12",)]
    assert capsys.readouterr().err == (
        "release: slice-GH-12: reaped 1 process: `sleep 99` (kill)\n"
    )
    release_checkout(memory_ports(reaper=FakeReaper()), tmp_path)
    assert capsys.readouterr().err == ""  # nothing stopped, nothing said


def test_a_dead_run_releases_its_role_scope_and_its_checks_and_never_cleans_up():
    reaper = FakeReaper(LEFT, reaped_runs=OTHER)
    workspace = MemoryWorkspace()
    ports = memory_ports(reaper=reaper, workspace=workspace)

    reaped = release_processes(ports, "GH-12", run=4321)

    assert reaped == OTHER + OTHER + OTHER
    assert reaper.calls == [
        ("run", "GH-12", 4321, None),
        ("run", CHECK_TASK, 4321, None),
        ("run", PERF_TASK, 4321, None),
    ]
    assert workspace.cleaned == []


@pytest.fixture
def worktrees(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "worktrees"
    root.mkdir()
    yield root
    # Teardown: the scope this test made, by the production reaper over its own root.
    ShellReaper("product", root).reap_within(sorted(root.glob("*")))


def test_an_unmarked_process_in_a_task_worktree_is_dead_after_release_task(worktrees):
    inside = worktrees / "GH-12"
    inside.mkdir()
    env = {k: v for k, v in os.environ.items() if k != MARK_NAME}
    child = subprocess.Popen(
        ["sleep", "300"], cwd=inside, env=env, start_new_session=True
    )  # no mark, no terminal: only its cwd says where it belongs
    try:
        time.sleep(0.2)  # let it exec, so its cwd is the checkout's
        assert child.poll() is None
        ports = memory_ports(
            reaper=ShellReaper("product", worktrees), workspace=_Layout(worktrees, [])
        )

        reaped = release_task(ports, "GH-12")

        assert child.wait(timeout=10) is not None
        assert [entry["pid"] for entry in reaped] == [child.pid]
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_process_outside_the_worktrees_root_survives_release_task(worktrees, tmp_path):
    elsewhere = tmp_path / "prod"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if k != MARK_NAME}
    child = subprocess.Popen(["sleep", "300"], cwd=elsewhere, env=env, start_new_session=True)
    try:
        time.sleep(0.2)
        # The rejected input: a checkout that is not strictly inside the root is dropped.
        outside = release_checkout(
            memory_ports(reaper=ShellReaper("product", worktrees)), elsewhere
        )

        assert outside == () and child.poll() is None
    finally:
        child.kill()
        child.wait()


def _calls_to_cleanup(path: Path) -> list[str]:
    return [
        f"{path.name}:{n}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if "workspace.cleanup(" in line
    ]


def test_only_release_calls_cleanup_among_the_application_modules(tmp_path):
    application = Path(__file__).parents[4] / "agentic/core/process/application"
    offenders = [
        hit
        for path in sorted(application.rglob("*.py"))
        if path.name != "release.py"
        for hit in _calls_to_cleanup(path)
    ]
    assert offenders == []

    # The rejected input: a copy of a module with a direct call is found.
    copy = tmp_path / "deliver_copy.py"
    copy.write_text("def f(ports, state):\n    ports.workspace.cleanup(state['id'])\n")
    assert _calls_to_cleanup(copy) == ["deliver_copy.py:2"]


def test_the_summary_names_the_count_and_each_command_and_is_empty_for_nothing():
    from agentic.core.process.application.release import summary

    assert summary(()) == ""
    assert summary(LEFT + OTHER) == "reaped 2 processes: `sleep 99` (kill), `uv run app` (term)"
    assert summary(LEFT) == "reaped 1 process: `sleep 99` (kill)"


# GH-820: `release_after_merge`, the removal that never fails a merge.


class _Clock:
    def __call__(self) -> str:
        return "2026-11-15T10:00:00+00:00"


def _after_merge(workspace=None, reaper=None, *, sleeps=None, lines=None):
    from agentic.core.process.application.release import release_after_merge

    ports = memory_ports(reaper=reaper or FakeReaper(), workspace=workspace or MemoryWorkspace())
    ports.documents.save({"id": "GH-12", "title": "t", "description": "d", "status": "done"})
    seen: list[float] = [] if sleeps is None else sleeps
    said: list[str] = [] if lines is None else lines
    release_after_merge(ports, "GH-12", said.append, _Clock(), seen.append)
    return ports


class _Failing(MemoryWorkspace):
    def __init__(self, error: BaseException, *, times: int = 99) -> None:
        super().__init__()
        self.error = error
        self.times = times
        self.attempts = 0

    def cleanup(self, task_id: str) -> None:
        self.attempts += 1
        if self.attempts <= self.times:
            raise self.error
        super().cleanup(task_id)


def test_a_release_after_merge_that_works_logs_once_and_records_no_lapse():
    lines: list[str] = []
    sleeps: list[float] = []
    workspace = MemoryWorkspace()

    ports = _after_merge(workspace, FakeReaper(LEFT), sleeps=sleeps, lines=lines)

    assert workspace.cleaned == ["GH-12"] and sleeps == []
    assert len(lines) == 1
    assert lines[0].startswith("agentic: merge: GH-12: worktrees released in ")
    assert lines[0].endswith("s; reaped 1 process: `sleep 99` (kill)")
    assert "worktree_release_lapse" not in (ports.documents.load("GH-12") or {})
    quiet: list[str] = []
    _after_merge(lines=quiet)
    assert len(quiet) == 1 and quiet[0].endswith("s") and ";" not in quiet[0]


def test_a_git_error_is_retried_once_after_the_pause_and_then_recorded_as_git_failed():
    from agentic.core.process.application.release import RELEASE_RETRY_PAUSE_S

    sleeps: list[float] = []
    lines: list[str] = []
    workspace = _Failing(GitError("index.lock exists"))

    ports = _after_merge(workspace, sleeps=sleeps, lines=lines)

    assert workspace.attempts == 2 and sleeps == [RELEASE_RETRY_PAUSE_S]
    assert (ports.documents.load("GH-12") or {})["worktree_release_lapse"] == {
        "at": "2026-11-15T10:00:00+00:00",
        "cause": "git_failed",
    }
    assert lines == ["agentic: merge: GH-12: worktree release lapsed (git_failed)"]


def test_a_git_error_that_clears_on_the_retry_records_no_lapse():
    sleeps: list[float] = []
    lines: list[str] = []
    workspace = _Failing(GitError("locked"), times=1)

    ports = _after_merge(workspace, sleeps=sleeps, lines=lines)

    assert workspace.attempts == 2 and len(sleeps) == 1
    assert "worktree_release_lapse" not in (ports.documents.load("GH-12") or {})
    assert "worktrees released in" in lines[0]


def test_an_os_error_is_not_retried_and_is_recorded_as_os_error():
    sleeps: list[float] = []
    workspace = _Failing(PermissionError("denied"))

    ports = _after_merge(workspace, sleeps=sleeps)

    assert workspace.attempts == 1 and sleeps == []
    lapse = (ports.documents.load("GH-12") or {})["worktree_release_lapse"]
    assert lapse == {"at": "2026-11-15T10:00:00+00:00", "cause": "os_error"}


def test_a_subprocess_error_is_recorded_as_os_error_and_never_raised():
    workspace = _Failing(subprocess.TimeoutExpired("git", 5))

    ports = _after_merge(workspace)

    lapse = (ports.documents.load("GH-12") or {})["worktree_release_lapse"]
    assert lapse["cause"] == "os_error" and workspace.attempts == 1


def test_a_bug_is_not_swallowed_and_leaves_no_lapse():
    # The rejected input: a TypeError is not one of the closed causes.
    from agentic.core.process.application.release import release_after_merge

    ports = memory_ports(workspace=_Failing(TypeError("bug")))
    ports.documents.save({"id": "GH-12", "title": "t", "description": "d", "status": "done"})

    with pytest.raises(TypeError, match="bug"):
        release_after_merge(ports, "GH-12", lambda line: None, _Clock(), lambda s: None)

    assert "worktree_release_lapse" not in (ports.documents.load("GH-12") or {})


def test_a_lapse_that_cannot_be_written_is_only_logged():
    # No document saved: the memory fake raises FileNotFoundError (an OSError) on the write.
    from agentic.core.process.application.release import release_after_merge

    lines: list[str] = []
    ports = memory_ports(workspace=_Failing(GitError("locked")))

    release_after_merge(ports, "GH-12", lines.append, _Clock(), lambda s: None)

    assert len(lines) == 1 and "lapsed (git_failed), not recorded" in lines[0]


def test_the_reaps_of_a_release_after_merge_carry_the_tasks_own_ids_and_paths(tmp_path):
    reaper, workspace, journal = _journalled(tmp_path)
    ports = memory_ports(reaper=reaper, workspace=workspace)
    from agentic.core.process.application.release import release_after_merge

    release_after_merge(ports, "GH-12", lambda line: None, _Clock(), lambda s: None)

    assert reaper.calls == [("task", "GH-12", None, None)]
    assert reaper.within_calls == [
        (tmp_path / "GH-12",),
        (tmp_path / "GH-12.api",),
        (tmp_path / "merge-GH-12",),
        (tmp_path / "slice-GH-12",),
    ]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_a_real_release_after_merge_removes_every_checkout_and_keeps_every_branch(tmp_path):
    from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
    from agentic.core.process.adapters.git.workspace import GitWorkspace
    from agentic.core.process.application.release import release_after_merge
    from agentic.shared.configuration import Configuration

    origin, product = tmp_path / "origin.git", tmp_path / "product"
    _git("init", "-q", "--bare", "-b", "master", str(origin), cwd=tmp_path)
    _git("clone", "-q", str(origin), str(product), cwd=tmp_path)
    (product / "a.txt").write_text("a\n")
    _git("add", "-A", cwd=product)
    _git("commit", "-q", "-m", "v0", cwd=product)
    _git("push", "-q", "-u", "origin", "HEAD:master", cwd=product)
    _git("branch", "-q", "--set-upstream-to=origin/master", cwd=product)
    cfg = Configuration(product_dir=product, run_registry_dir=tmp_path / "registry")
    workspace = GitWorkspace(product, cfg.git_identity)
    documents = FilesystemTaskDocuments(product)
    documents.save({"id": "GH-8", "title": "t", "description": "d", "status": "done"})
    root = product / ".agentic/worktrees"

    task = workspace.task_checkout("GH-8")
    sub = workspace.subtask_checkout("GH-8", "a")
    neighbour = workspace.task_checkout("GH-82")
    (sub.path / "work.txt").write_text("work\n")
    _git("add", "-A", cwd=sub.path)
    _git("commit", "-q", "-m", "unpushed", cwd=sub.path)
    unpushed = _git("rev-parse", "HEAD", cwd=sub.path)
    (sub.path / "dirty.txt").write_text("uncommitted\n")
    scratch = workspace.detached_checkout("merge-GH-8", _git("rev-parse", "HEAD", cwd=product))
    assert {p.name for p in root.iterdir()} == {"GH-8", "GH-8.a", "merge-GH-8", "GH-82"}
    branches = _git("branch", "--list", "--format=%(refname:short)", cwd=product).splitlines()
    ports = memory_ports(
        reaper=ShellReaper("product", root), workspace=workspace, documents=documents
    )
    lines: list[str] = []

    release_after_merge(ports, "GH-8", lines.append, _Clock(), lambda s: None)

    assert {p.name for p in root.iterdir()} == {"GH-82"}
    assert not task.path.exists() and not sub.path.exists() and not scratch.exists()
    assert neighbour.path.is_dir()
    assert (
        _git("branch", "--list", "--format=%(refname:short)", cwd=product).splitlines() == branches
    )
    assert _git("rev-parse", sub.branch, cwd=product) == unpushed
    assert "worktree_release_lapse" not in (documents.load("GH-8") or {})

    release_after_merge(ports, "GH-8", lines.append, _Clock(), lambda s: None)  # nothing left

    assert "worktree_release_lapse" not in (documents.load("GH-8") or {})
    assert all("lapsed" not in line for line in lines)
