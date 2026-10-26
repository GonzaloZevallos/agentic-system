"""GH-361: `CheckoutPresubmit` holds a share of the machine's cores while it runs checks.
Seam: `__call__` over a `FakeCheckScheduler`, and the priority each construction site gives
its gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.full_checks import full_checks
from agentic.core.process.application.ports import CheckRequest, CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_landing_slice import _land, _world
from tests.core.process.application.test_merge_full_checks import _merged_with
from tests.fakes import (
    FakeCheckScheduler,
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

PATH = Path("/memory/worktrees/GH-1.api")
CHECKS = {"tests": "pytest", "lint": "ruff check ."}


def gate_over(
    scheduler: FakeCheckScheduler,
    commands: MemoryCommands | None = None,
    *,
    moved: bool = True,
    clean: bool = True,
    **options,
) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    if moved:
        workspace.moved.add(PATH)
    if not clean:
        workspace.dirty.add(PATH)
    ports = memory_ports(
        workspace=workspace, commands=commands or MemoryCommands(), check_scheduler=scheduler
    )
    options.setdefault("priority", CheckPriority.SUBTASK)
    return CheckoutPresubmit(ports, PATH, "base", CHECKS, 30.0, **options)


# -- one place in the queue across refusals ------------------------------------------


def test_a_gate_refused_twice_then_passing_acquires_three_times_under_one_place():
    scheduler = FakeCheckScheduler(wait_s=400.0)
    commands = MemoryCommands(pytest=[CommandResult(1, "red"), CommandResult(1, "red")])
    gate = gate_over(scheduler, commands, wait_max_s=1000.0)
    assert gate() is not None and gate() is not None and gate() is None
    assert len(scheduler.requests) == 3
    assert len({(r.enqueued_at, r.token) for r in scheduler.requests[1:]}) == 1
    assert scheduler.requests[0].enqueued_at is None and scheduler.requests[0].token is None
    assert {g.token for g in scheduler.released} == {scheduler.released[0].token}
    assert len(scheduler.released) == 3


def test_the_total_wait_asked_never_passes_the_ceiling():
    scheduler = FakeCheckScheduler(wait_s=400.0)
    commands = MemoryCommands(pytest=[CommandResult(1, "red")] * 4)
    gate = gate_over(scheduler, commands, wait_max_s=1000.0)
    for _ in range(4):
        gate()
    asked = [r.wait_max_s for r in scheduler.requests]
    assert asked == [1000.0, 600.0, 200.0, 0.0]  # floored at zero, never negative


def test_the_wait_is_not_part_of_the_timeout_of_the_gate():
    quiet = gate_over(FakeCheckScheduler(), wait_max_s=0.0)
    patient = gate_over(FakeCheckScheduler(), wait_max_s=1800.0)
    assert quiet.timeout_s == patient.timeout_s == 60.0
    assert patient.wait_max_s == 1800.0


def test_the_share_is_held_for_the_checks_and_the_hook_margin():
    scheduler = FakeCheckScheduler()
    gate = gate_over(scheduler)
    gate()
    (request,) = scheduler.requests
    assert request.expires_after_s == gate.timeout_s + 30.0
    assert request.priority is CheckPriority.SUBTASK
    assert gate.grant is scheduler.released[0]


# -- when it never queues, and when it always lets go ---------------------------------


@pytest.mark.parametrize("moved,clean", [(False, True), (True, False), (False, False)])
def test_a_call_refused_for_a_missing_commit_or_an_unclean_tree_never_acquires(moved, clean):
    scheduler = FakeCheckScheduler()
    commands = MemoryCommands()
    gate = gate_over(scheduler, commands, moved=moved, clean=clean)
    assert gate() is not None
    assert scheduler.requests == [] and commands.ran == [] and gate.grant is None


def test_a_check_that_raises_still_gives_the_share_back():
    scheduler = FakeCheckScheduler()
    gate = gate_over(scheduler, MemoryCommands(raises={"pytest"}))
    with pytest.raises(RuntimeError):
        gate()
    assert len(scheduler.requests) == 1 and len(scheduler.released) == 1


def test_a_witness_that_raises_still_gives_the_share_back():
    scheduler = FakeCheckScheduler()
    gate = gate_over(scheduler, MemoryCommands(raises={"witness"}), witness="witness")
    with pytest.raises(RuntimeError):
        gate()
    assert len(scheduler.released) == 1


def test_the_share_is_acquired_before_any_command_runs():
    order: list[str] = []

    class Journal(FakeCheckScheduler):
        def acquire(self, request: CheckRequest):
            order.append("acquire")
            return super().acquire(request)

    class Spy(MemoryCommands):
        def run(self, cwd, command, timeout_s, *, env=None):
            order.append(command)
            return super().run(cwd, command, timeout_s, env=env)

    gate = gate_over(Journal(), Spy(), witness="witness")
    gate()
    assert order[:2] == ["acquire", "witness"]


# -- equivalence with the gate before the scheduler -----------------------------------


@pytest.mark.parametrize("parallel", [False, True])
def test_a_granted_gate_gives_the_same_results_and_timeout_as_one_granted_at_once(parallel):
    def run(wait_s: float, share: int):
        commands = MemoryCommands(pytest=[CommandResult(1, "1 failed")])
        gate = gate_over(
            FakeCheckScheduler(share, wait_s=wait_s), commands, parallel=parallel, witness="w"
        )
        reason = gate()
        rows = [{k: v for k, v in r.items() if k != "duration_s"} for r in gate.results or []]
        return reason, rows, gate.timeout_s, sorted(c for _, c in commands.ran)

    assert run(0.0, 4) == run(900.0, 2)


# -- the four construction sites -------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_develop_gates_as_a_subtask(cfg):
    from tests.core.process.application.test_delivery_tier import TASK, building

    ports = memory_ports(
        ScriptedRunner(developer=[delivered()]), stack=MemoryStack(checks={"tests": "pytest"})
    )
    state, _ = building(ports)
    await develop({**TASK, **state}, cfg, ports)
    assert [r.priority for r in ports.check_scheduler.requests] == [CheckPriority.SUBTASK]
    assert ports.check_scheduler.requests[0].wait_max_s == cfg.check_wait_max_s


async def test_full_checks_gates_as_integration(cfg):
    from tests.core.process.application.test_full_checks import state_of

    ports = memory_ports(stack=MemoryStack(checks={"tests": "pytest"}))
    await full_checks(state_of(), cfg, ports)
    assert [r.priority for r in ports.check_scheduler.requests] == [CheckPriority.INTEGRATION]


def test_the_merge_gate_asks_as_land():
    ports = _merged_with(MemoryStack(checks={"tests": "pytest"}))
    assert [r.priority for r in ports.check_scheduler.requests] == [CheckPriority.LAND]


def test_the_slice_gate_asks_as_land():
    ports = _world(frozenset())
    _land(ports)
    assert ports.check_scheduler.requests, "a landed slice must run its checks under a share"
    assert {r.priority for r in ports.check_scheduler.requests} == {CheckPriority.LAND}
