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
    parallel: bool = False,
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
        parallel=parallel,
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


def _workers_by_command(commands: MemoryCommands) -> dict[str, int]:
    """CHECK_WORKERS each command ran with; both variables must carry the same count."""
    counts: dict[str, int] = {}
    for (_, command), env in zip(commands.ran, commands.envs, strict=True):
        assert env is not None
        assert env["CHECK_WORKERS"] == env["PYTEST_XDIST_AUTO_NUM_WORKERS"]
        counts[command] = int(env["CHECK_WORKERS"])
    return counts


def test_a_parallel_gate_splits_its_share_across_its_checks():
    commands = MemoryCommands()
    checks = {"tests": "pytest -n {workers}", "lint": "ruff", "types": "mypy", "docs": "docs"}
    gate = gate_over(commands, checks, scheduler=FakeCheckScheduler(6), parallel=True)
    assert gate() is None
    by_command = _workers_by_command(commands)
    assert sum(by_command.values()) == 6
    assert by_command == {"pytest -n 2": 2, "ruff": 2, "mypy": 1, "docs": 1}


def test_a_check_with_the_placeholder_carries_its_own_slice_in_the_command_and_the_env():
    commands = MemoryCommands()
    gate_over(
        commands,
        {"a": "run-a {workers}", "b": "run-b {workers}"},
        scheduler=FakeCheckScheduler(5),
        parallel=True,
    )()
    assert _workers_by_command(commands) == {"run-a 3": 3, "run-b 2": 2}


def test_a_parallel_gate_with_more_checks_than_cores_gives_each_check_one():
    commands = MemoryCommands()
    checks = {"a": "a {workers}", "b": "b {workers}", "c": "c {workers}"}
    gate_over(commands, checks, scheduler=FakeCheckScheduler(2), parallel=True)()
    assert sorted(_workers_by_command(commands).items()) == [("a 1", 1), ("b 1", 1), ("c 1", 1)]


def test_a_serial_gate_gives_every_check_the_whole_share():
    commands = MemoryCommands()
    checks = {"tests": "pytest -n {workers}", "lint": "ruff", "types": "mypy"}
    gate_over(commands, checks, scheduler=FakeCheckScheduler(6))()
    assert _workers_by_command(commands) == {"pytest -n 6": 6, "ruff": 6, "mypy": 6}


@pytest.mark.parametrize("parallel", [False, True])
def test_a_gate_without_a_grant_passes_no_env_and_leaves_the_command_alone(parallel):
    # The gate always holds a grant while its checks run (the fake scheduler grants at once),
    # so the no-grant path is driven through the gate's own slices, before any `_share`.
    commands = MemoryCommands()
    gate = gate_over(commands, {"a": "a", "b": "b"}, parallel=parallel)
    assert gate.grant is None
    slices = gate._slices()
    assert slices == {"a": None, "b": None}
    for name, command in gate.checks.items():
        gate._run_once(name, command, slices[name])
    assert sorted(command for _, command in commands.ran) == ["a", "b"]
    assert commands.envs == [None, None]


def test_a_parallel_check_whose_affected_form_fails_keeps_its_count_in_every_row():
    commands = MemoryCommands(**{"pytest -n 3 --since abc": [CommandResult(1, "red")]})
    plan = AffectedPlan(
        base="abc",
        commands={"tests": "pytest -n {workers} --since {base}", "lint": "ruff {workers}"},
        fallback=(),
    )
    gate = gate_over(
        commands,
        {"tests": "pytest -n {workers}", "lint": "ruff {workers}"},
        scheduler=FakeCheckScheduler(5),
        affected=plan,
        parallel=True,
    )
    gate()
    rows = [row for row in gate.results or [] if row["name"] == "tests"]
    assert [row["command"] for row in rows] == ["pytest -n 3 --since abc", "pytest -n 3"]
    for (_, command), env in zip(commands.ran, commands.envs, strict=True):
        assert env is not None
        expected = "2" if command.startswith("ruff") else "3"
        assert env["CHECK_WORKERS"] == expected
        assert command.endswith(expected) or f"-n {expected}" in command


def test_a_rerun_gets_the_count_of_its_check_in_a_parallel_gate():
    commands = MemoryCommands(**{"t 3": [CommandResult(1, "red")]})
    gate = gate_over(
        commands,
        {"t": "t {workers}", "u": "u {workers}"},
        scheduler=FakeCheckScheduler(5),
        parallel=True,
    )
    gate()
    assert [(row["command"], row.get("rerun", False)) for row in gate.results or []] == [
        ("t 3", False),
        ("t 3", True),
        ("u 2", False),
    ]


def test_a_command_with_the_token_and_no_grant_raises_instead_of_reaching_the_shell():
    gate = gate_over(MemoryCommands(), {"tests": "pytest -n {workers}"})
    with pytest.raises(presubmit.UngrantedWorkers, match="check scheduler"):
        gate._sized("pytest -n {workers}", None)
    assert gate._sized("pytest -x", None) == "pytest -x"


def test_a_command_with_the_token_and_a_count_is_sized_to_that_count():
    gate = gate_over(MemoryCommands(), {"tests": "pytest"}, scheduler=FakeCheckScheduler(5))
    assert gate._sized("pytest -n {workers} --dist load", 5) == "pytest -n 5 --dist load"
    assert gate._sized("pytest -n {workers} --dist load", 2) == "pytest -n 2 --dist load"
