"""A `not_ready` exit under `run_check` and the gate (ADR-0065 addendum, GH-451): the exit a
product declared is `not_ready` only when the machine was saturated, is retried once, and a
second one is `unverified`; neither is ever a flake, a `passed: no` or a stored pass."""

from pathlib import Path

import pytest

from agentic.core.process.application.ports import CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit, Readiness, Reuse
from agentic.core.process.application.run_check import RunCheck, resolved_walls
from agentic.core.process.domain import run_check as rules
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.run_check import Outcome, Verification
from tests.fakes import (
    FakeLoadReader,
    MemoryCheckResults,
    MemoryCommands,
    MemoryWorkspace,
    memory_ports,
)

PATH = Path("/memory/worktrees/GH-1.api")
CORES = 4
LOW, HIGH = 1.0, 9.0
NOT_READY = CommandResult(75, "app not ready")
RED = CommandResult(1, "assertion failed")
COMPARE = Verification("scripts/design/compare.sh", 900.0)


def rig(
    load: FakeLoadReader,
    commands: MemoryCommands,
    *,
    exits: dict[str, int] | None = None,
) -> tuple[RunCheck, CheckoutPresubmit, MemoryCheckResults]:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    workspace.record_setup_tree(PATH)
    store = MemoryCheckResults()
    ports = memory_ports(workspace=workspace, commands=commands, check_results=store)
    declared = {"compare": COMPARE}
    gate = CheckoutPresubmit(
        ports,
        PATH,
        "base",
        {"tests": "pytest"},
        30.0,
        priority=CheckPriority.SUBTASK,
        reuse=Reuse("shop", ("uv sync",), "a" * 40),
        timeouts=resolved_walls(declared, {}, 30.0, 3600.0),
        readiness=Readiness({"compare": 75, "tests": 75} if exits is None else exits, load, CORES),
    )
    return RunCheck(gate, verifications=declared), gate, store


@pytest.mark.parametrize(
    ("before", "after", "retried"),
    [(LOW, LOW, False), (HIGH, LOW, True), (LOW, HIGH, True), (HIGH, HIGH, True)],
)
def test_an_exit_75_is_not_ready_only_when_the_load_before_or_after_saturated(
    before: float, after: float, retried: bool
) -> None:
    commands = MemoryCommands(**{COMPARE.command: [NOT_READY, NOT_READY]})
    handler, _, _ = rig(FakeLoadReader(before, after, HIGH, HIGH), commands)
    run = handler.run("compare")
    assert len(commands.ran) == (2 if retried else 1)
    assert run.outcome is (Outcome.UNVERIFIED if retried else Outcome.FAILED)


def test_not_ready_twice_is_unverified_and_never_a_flake_a_failure_or_a_stored_pass() -> None:
    commands = MemoryCommands(**{"pytest": [NOT_READY, NOT_READY]})
    handler, gate, store = rig(FakeLoadReader(HIGH), commands)
    run = handler.run("tests")
    assert run.outcome is Outcome.UNVERIFIED and not run.passed
    assert gate.flakes() == [] and store.puts == []
    reply = rules.render(run)
    assert "outcome: unverified" in reply and "passed:" not in reply
    assert f"cores: {CORES}" in reply and "load before: 9.0" in reply


def test_not_ready_then_a_pass_is_passed_without_a_flake_record() -> None:
    commands = MemoryCommands(**{"pytest": [NOT_READY, CommandResult(0, "ok")]})
    handler, gate, store = rig(FakeLoadReader(HIGH, HIGH, LOW, LOW), commands)
    run = handler.run("tests")
    assert run.outcome is Outcome.PASSED and run.passed
    assert gate.flakes() == []
    assert len(store.puts) == 1  # the second run is a real pass of a declared check


def test_not_ready_then_a_red_is_failed() -> None:
    commands = MemoryCommands(**{COMPARE.command: [NOT_READY, RED]})
    handler, _, _ = rig(FakeLoadReader(HIGH, HIGH, LOW, LOW), commands)
    assert handler.run("compare").outcome is Outcome.FAILED


def test_an_exit_that_is_not_the_declared_one_is_failed_and_never_retried() -> None:
    commands = MemoryCommands(**{COMPARE.command: [RED]})
    handler, _, _ = rig(FakeLoadReader(HIGH), commands)
    run = handler.run("compare")
    assert len(commands.ran) == 1 and run.outcome is Outcome.FAILED


def test_a_command_with_no_declared_exit_never_reads_the_load() -> None:
    load = FakeLoadReader(HIGH)
    commands = MemoryCommands(**{"pytest": [NOT_READY]})
    handler, _, _ = rig(load, commands, exits={})
    run = handler.run("tests")
    assert load.reads == 0 and len(commands.ran) == 1 and run.outcome is None


def test_the_delivery_gate_never_reruns_a_not_ready_check_so_it_is_never_a_flake() -> None:
    commands = MemoryCommands(**{"pytest": [NOT_READY, CommandResult(0, "ok")]})
    _, gate, _ = rig(FakeLoadReader(HIGH), commands)
    gate()
    assert len(commands.ran) == 1  # no rerun: the row is a failure with its outcome
    assert gate.flakes() == []
    assert gate.results is not None and gate.results[0].get("not_ready") is True


def test_the_same_red_without_saturation_is_rerun_and_judged_as_before() -> None:
    commands = MemoryCommands(**{"pytest": [NOT_READY, CommandResult(0, "ok")]})
    _, gate, _ = rig(FakeLoadReader(LOW), commands)
    gate()
    assert len(commands.ran) == 2 and len(gate.flakes()) == 1


def test_a_verification_not_ready_twice_leaves_an_unverified_evidence_line_with_the_gate_load() -> (
    None
):
    # The two branches meet here: the call log of the evidence line takes the outcome and the
    # load the gate's own classification measured, not a second reading of its own.
    commands = MemoryCommands(**{COMPARE.command: [NOT_READY, NOT_READY]})
    handler, _, _ = rig(FakeLoadReader(HIGH), commands)
    handler.run("compare")
    (call,) = handler.calls
    assert call.outcome is Outcome.UNVERIFIED
    assert (call.load_before, call.load_after, call.cores) == (HIGH, HIGH, CORES)
    section = handler.evidence() or ""
    assert "unverified" in section and f"{CORES} cores" in section


def test_a_verification_that_failed_with_75_on_a_quiet_machine_is_a_failed_line() -> None:
    commands = MemoryCommands(**{COMPARE.command: [NOT_READY]})
    handler, _, _ = rig(FakeLoadReader(LOW), commands)
    handler.run("compare")
    (call,) = handler.calls
    assert call.outcome is Outcome.FAILED and len(commands.ran) == 1


def test_a_retried_verification_keeps_the_first_run_on_the_line_and_in_the_reply() -> None:
    # First run saturated before (9.0) and slow; the retry is quiet but still exits 75 on a
    # quiet machine, so it is `failed`. The first run's figures must not vanish.
    first = CommandResult(75, "app not ready")
    commands = MemoryCommands(**{COMPARE.command: [first, first]})
    handler, _, _ = rig(FakeLoadReader(HIGH, HIGH, LOW, LOW), commands)
    reply = rules.render(handler.run("compare"))
    (call,) = handler.calls
    assert call.retry is not None and (call.retry.load_before, call.retry.load_after) == (
        HIGH,
        HIGH,
    )
    assert "retried once after a not_ready" in reply and "load 9.00 before, 9.00 after" in reply
    assert "retried once after a not_ready" in (handler.evidence() or "")


def test_a_verification_that_was_not_retried_says_nothing_of_a_retry() -> None:
    commands = MemoryCommands(**{COMPARE.command: [CommandResult(0, "ok")]})
    handler, _, _ = rig(FakeLoadReader(LOW), commands)
    reply = rules.render(handler.run("compare"))
    assert "retried" not in reply and "retried" not in (handler.evidence() or "")
    assert handler.calls[0].retry is None
