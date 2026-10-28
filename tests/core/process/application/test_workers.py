"""GH-361: a gate's share becomes a worker count. Seams: the pure `workers_command` /
`workers_env`, and `CheckoutPresubmit.__call__` over a `FakeCheckScheduler` and the fake
`Commands`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain import presubmit
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.presubmit import AffectedPlan
from tests.fakes import FakeCheckScheduler, MemoryCommands, MemoryWorkspace, memory_ports

PATH = Path("/memory/worktrees/GH-1.api")


def test_workers_replaces_every_literal_placeholder():
    assert presubmit.workers_command("pytest -n {workers} -p {workers}", 6) == "pytest -n 6 -p 6"


def test_a_command_without_the_placeholder_is_unchanged_even_with_other_braces():
    command = "echo '{x}' && awk '{print $1}' f"
    assert presubmit.workers_command(command, 6) == command


def test_the_env_carries_the_worker_count_and_never_an_agentic_key():
    env = presubmit.workers_env(6)
    assert env == {"CHECK_WORKERS": "6", "PYTEST_XDIST_AUTO_NUM_WORKERS": "6"}
    assert not any(key.startswith("AGENTIC_") for key in env)


def gate_over(
    commands: MemoryCommands,
    checks: dict[str, str],
    *,
    scheduler: FakeCheckScheduler | None = None,
    witness: str | None = None,
    affected: AffectedPlan | None = None,
) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.diff_paths = ("src/a.py",)
    ports = memory_ports(
        workspace=workspace, commands=commands, check_scheduler=scheduler or FakeCheckScheduler(6)
    )
    return CheckoutPresubmit(
        ports,
        PATH,
        "base",
        checks,
        30.0,
        priority=CheckPriority.SUBTASK,
        witness=witness,
        affected=affected,
    )


def test_a_gate_with_a_share_of_six_runs_the_declared_workers_as_six_with_the_env():
    commands = MemoryCommands()
    gate = gate_over(commands, {"tests": "pytest -n {workers}"})
    assert gate() is None
    assert [command for _, command in commands.ran] == ["pytest -n 6"]
    assert commands.envs == [{"CHECK_WORKERS": "6", "PYTEST_XDIST_AUTO_NUM_WORKERS": "6"}]
    assert gate.results is not None and gate.results[0]["command"] == "pytest -n 6"


def test_a_command_without_the_placeholder_keeps_its_string_and_gets_only_the_env():
    commands = MemoryCommands()
    gate_over(commands, {"lint": "ruff check ."}, scheduler=FakeCheckScheduler(3))()
    assert [command for _, command in commands.ran] == ["ruff check ."]
    assert commands.envs == [{"CHECK_WORKERS": "3", "PYTEST_XDIST_AUTO_NUM_WORKERS": "3"}]


def test_the_witness_gets_the_env_but_no_substitution():
    commands = MemoryCommands()
    gate_over(commands, {"tests": "pytest"}, witness="probe {workers}")()
    assert [command for _, command in commands.ran] == ["probe {workers}", "pytest"]
    assert commands.envs[0] == {"CHECK_WORKERS": "6", "PYTEST_XDIST_AUTO_NUM_WORKERS": "6"}


PLAN = AffectedPlan(
    base="abc", commands={"tests": "pytest -n {workers} --since {base}"}, fallback=()
)


def test_base_is_substituted_first_then_workers_in_the_affected_form():
    commands = MemoryCommands()
    gate = gate_over(
        commands,
        {"tests": "pytest -n {workers}"},
        scheduler=FakeCheckScheduler(4),
        affected=PLAN,
    )
    assert gate() is None
    assert [command for _, command in commands.ran] == ["pytest -n 4 --since abc"]


def test_a_failed_affected_form_reruns_the_full_command_sized_to_the_share():
    commands = MemoryCommands(**{"pytest -n 2 --since abc": [CommandResult(1, "red")]})
    gate_over(
        commands,
        {"tests": "pytest -n {workers}"},
        scheduler=FakeCheckScheduler(2),
        affected=PLAN,
    )()
    assert [command for _, command in commands.ran] == [
        "pytest -n 2 --since abc",
        "pytest -n 2",
    ]


@pytest.mark.parametrize("share", [1, 16])
def test_the_share_is_the_only_source_of_the_count(share):
    commands = MemoryCommands()
    gate_over(commands, {"tests": "t {workers}"}, scheduler=FakeCheckScheduler(share))()
    assert commands.ran[0][1] == f"t {share}"
