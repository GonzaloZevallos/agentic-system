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
from agentic.core.process.domain.reaping import CHECK_TASK, MARK_NAME, ReapedProcess
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

    assert reaped == OTHER + OTHER
    assert reaper.calls == [("run", "GH-12", 4321, None), ("run", CHECK_TASK, 4321, None)]
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
