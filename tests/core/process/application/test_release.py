"""Releasing a task's processes (GH-282, ADR-0052) through `memory_ports` and `FakeReaper`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application.ports import GitError
from agentic.core.process.application.release import release_processes, release_task
from agentic.core.process.domain.reaping import ReapedProcess
from tests.fakes import FakeReaper, MemoryWorkspace, memory_ports

LEFT: tuple[ReapedProcess, ...] = ({"pid": 7, "command": "sleep 99", "outcome": "kill"},)
OTHER: tuple[ReapedProcess, ...] = ({"pid": 8, "command": "uv run app", "outcome": "term"},)


class OrderedWorkspace(MemoryWorkspace):
    """Remembers how many reaps had already been asked when `cleanup` ran."""

    def __init__(self, reaper: FakeReaper) -> None:
        super().__init__()
        self.reaper = reaper
        self.reaps_before_cleanup: list[int] = []

    def cleanup(self, task_id: str) -> None:
        self.reaps_before_cleanup.append(len(self.reaper.calls))
        super().cleanup(task_id)


def test_the_whole_task_is_reaped_before_the_worktree_goes():
    reaper = FakeReaper(LEFT, reaped_runs=OTHER)
    workspace = OrderedWorkspace(reaper)
    ports = memory_ports(reaper=reaper, workspace=workspace)

    reaped = release_task(ports, "GH-12")

    assert reaped == LEFT
    assert reaper.calls == [("task", "GH-12", None, None)]  # never one run's scope
    assert workspace.reaps_before_cleanup == [1]
    assert workspace.cleaned == ["GH-12"]


def test_a_failed_cleanup_propagates_unchanged_after_the_reap():
    reaper = FakeReaper(LEFT)
    workspace = OrderedWorkspace(reaper)
    workspace.cleanup_error = "worktree is dirty"
    ports = memory_ports(reaper=reaper, workspace=workspace)

    with pytest.raises(GitError, match="worktree is dirty"):
        release_task(ports, "GH-12")

    assert reaper.calls == [("task", "GH-12", None, None)]


def test_a_dead_runs_release_asks_for_that_run_only_and_never_cleans_up():
    reaper = FakeReaper(LEFT, reaped_runs=OTHER)
    workspace = MemoryWorkspace()
    ports = memory_ports(reaper=reaper, workspace=workspace)

    reaped = release_processes(ports, "GH-12", run=4321)

    assert reaped == OTHER
    assert reaper.calls == [("run", "GH-12", 4321, None)]
    assert workspace.cleaned == []


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
