"""GH-361: `CheckoutPresubmit` holds a share of the machine's cores while it runs checks.
Seam: `__call__` over a `FakeCheckScheduler`, and the priority each construction site gives
its gate."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.check_ledger import FilesystemCheckLedger
from agentic.core.process.application.develop import develop
from agentic.core.process.application.ports import CheckAbandoned, CheckRequest, CommandResult
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain.check_schedule import CheckPriority, Limits
from agentic.core.process.domain.presubmit import AffectedPlan
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_landing_slice import _land, _world
from tests.core.process.application.test_merge_full_checks import _merged_with
from tests.fakes import (
    FakeCheckScheduler,
    FakeReaper,
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    full_checks_in_task_checkout,
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
    # A red check is rerun once (ADR-0066): two reds per refused look.
    commands = MemoryCommands(pytest=[CommandResult(1, "red")] * 4)
    gate = gate_over(scheduler, commands, wait_max_s=1000.0)
    assert gate() is not None and gate() is not None and gate() is None
    assert len(scheduler.requests) == 3
    assert len({(r.enqueued_at, r.token) for r in scheduler.requests[1:]}) == 1
    assert scheduler.requests[0].enqueued_at is None and scheduler.requests[0].token is None
    assert {g.token for g in scheduler.released} == {scheduler.released[0].token}
    assert len(scheduler.released) == 3


def test_the_total_wait_asked_never_passes_the_ceiling():
    scheduler = FakeCheckScheduler(wait_s=400.0)
    commands = MemoryCommands(pytest=[CommandResult(1, "red")] * 8)
    gate = gate_over(scheduler, commands, wait_max_s=1000.0)
    for _ in range(4):
        gate()
    asked = [r.wait_max_s for r in scheduler.requests]
    assert asked == [1000.0, 600.0, 200.0, 0.0]  # floored at zero, never negative


def test_the_wait_is_not_part_of_the_timeout_of_the_gate():
    quiet = gate_over(FakeCheckScheduler(), wait_max_s=0.0)
    patient = gate_over(FakeCheckScheduler(), wait_max_s=1800.0)
    # Two checks, each with its one rerun (ADR-0066), and nothing for the wait.
    assert quiet.timeout_s == patient.timeout_s == 30.0 * (2 + 2)
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
        rows = [
            {k: v for k, v in r.items() if k not in ("duration_s", "workers")}
            for r in gate.results or []
        ]
        return reason, rows, gate.timeout_s, sorted(c for _, c in commands.ran)

    assert run(0.0, 4) == run(900.0, 2)


# -- the four construction sites -------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


class CallingTheTool(ScriptedRunner):
    """A runner whose Developer calls the `run_check` tool of the attempt before delivering."""

    async def run_once(self, role, prompt, **kwargs):
        if (tool := kwargs.get("check_tool")) is not None:
            tool("tests")
        return await super().run_once(role, prompt, **kwargs)


def _pass(**over):
    """The open `landing` entry of a live pass on a pull-request task."""
    return {
        "pull_request": "https://example.test/pull/1",
        "landing": [{"step": "sync_master", "at": "2026-11-13T10:00:00Z", **over}],
    }


@pytest.mark.parametrize(
    "unit,extra,expected",
    [
        ("api", {}, CheckPriority.SUBTASK),
        (INTEGRATION, {}, CheckPriority.INTEGRATION),
        ("api", _pass(), CheckPriority.LAND),
        (INTEGRATION, _pass(), CheckPriority.LAND),
        ("api", _pass(before_pr=True), CheckPriority.SUBTASK),
        (INTEGRATION, _pass(before_pr=True), CheckPriority.INTEGRATION),
        ("api", _pass(resolved_at="2026-11-13T11:00:00Z"), CheckPriority.SUBTASK),
    ],
)
async def test_develop_gates_with_the_priority_of_its_step(cfg, unit, extra, expected):
    from agentic.core.process.domain.task import TaskState
    from tests.core.process.application.test_delivery_tier import TASK, building
    from tests.fakes import MemoryCheckResults

    ports = memory_ports(
        CallingTheTool(developer=[delivered()], developer_integrator=[delivered()]),
        stack=MemoryStack(checks={"tests": "pytest"}),
        check_results=MemoryCheckResults(),
    )
    state, checkout = building(ports, unit)
    state["subtasks"][INTEGRATION] = {**state["subtasks"]["api"], "branch": "", "base": ""}
    ports.workspace.moved.add(ports.workspace.task_checkout("GH-12").path)
    full: TaskState = {**TASK, **state, **extra}
    await develop(full, cfg, ports)
    requests = ports.check_scheduler.requests
    assert requests[0].wait_max_s == cfg.check_wait_max_s
    # The delivery gate and the tool's presubmit of the same attempt: both asked, both the same.
    assert len(requests) >= 2
    assert {r.priority for r in requests} == {expected}


async def test_full_checks_gates_as_integration(cfg):
    from tests.core.process.application.test_full_checks import state_of

    ports = memory_ports(stack=MemoryStack(checks={"tests": "pytest"}))
    await full_checks_in_task_checkout(state_of(), cfg, ports)
    assert [r.priority for r in ports.check_scheduler.requests] == [CheckPriority.INTEGRATION]


def test_the_merge_gate_asks_as_land():
    ports = _merged_with(MemoryStack(checks={"tests": "pytest"}))
    assert [r.priority for r in ports.check_scheduler.requests] == [CheckPriority.LAND]


def test_the_slice_gate_asks_as_land():
    ports = _world(frozenset())
    _land(ports)
    assert ports.check_scheduler.requests, "a landed slice must run its checks under a share"
    assert {r.priority for r in ports.check_scheduler.requests} == {CheckPriority.LAND}


# -- an abandoned attempt (GH-1169) ------------------------------------------------------
# Seam: `CheckoutPresubmit.__call__(abandoned=...)`. The flag is the attempt's own: a call that
# sees it set books no share, launches nothing and writes nothing on the shared presubmit.


class Flag:
    """The abandonment flag of one attempt: `is_set` is what a call receives."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


class FlippingCommands(MemoryCommands):
    """Sets `flag` once `command` has run, as the reap of a stalled attempt would."""

    def __init__(self, flag: Flag, command: str, **queues: list[CommandResult]) -> None:
        super().__init__(**queues)
        self._flag, self._command = flag, command

    def run(self, cwd, command, timeout_s, *, env=None):
        result = super().run(cwd, command, timeout_s, env=env)
        if command == self._command:
            self._flag.set()
        return result


def _untouched(gate: CheckoutPresubmit) -> None:
    """What an abandoned call leaves on the shared presubmit: nothing."""
    assert gate.results is None and gate.head is None and gate.regressions == []
    assert gate.forms == [] and gate.gates == [] and gate.invocations == []
    assert gate.flakes() == [] and gate.timeouts() == []
    assert gate.refusals == 0 and gate._refused_ids == [] and gate.waited_s == 0.0
    assert gate.witness_result is None and gate.stuck_full == set()


def test_a_flag_set_before_the_call_books_nothing_and_runs_nothing():
    scheduler, commands, flag = FakeCheckScheduler(), MemoryCommands(), Flag()
    gate = gate_over(scheduler, commands, witness="witness")
    flag.set()
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    assert scheduler.requests == [] and commands.ran == []
    _untouched(gate)


def _plan() -> AffectedPlan:
    return AffectedPlan(
        base="abc",
        commands={"tests": "pytest --picked={base}", "lint": "ruff check --since {base}"},
        fallback=(),
    )


def test_a_gate_abandoned_during_its_first_check_does_not_launch_the_second():
    flag, scheduler = Flag(), FakeCheckScheduler()
    commands = FlippingCommands(flag, "pytest", pytest=[CommandResult(1, "red")])
    gate = gate_over(scheduler, commands)
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    # The launched check ran to its end; nothing after it: no rerun of the red, no lint.
    assert [c for _, c in commands.ran] == ["pytest"]
    assert len(scheduler.released) == 1  # the share is back
    _untouched(gate)


def test_a_planned_gate_abandoned_during_its_affected_check_does_not_launch_the_next():
    flag, scheduler = Flag(), FakeCheckScheduler()
    commands = FlippingCommands(flag, "pytest --picked=abc")
    gate = gate_over(scheduler, commands, affected=_plan())
    assert isinstance(gate.ports.workspace, MemoryWorkspace)
    gate.ports.workspace.diff_paths = ("src/a.py",)
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    assert [c for _, c in commands.ran] == ["pytest --picked=abc"]
    assert len(scheduler.released) == 1
    _untouched(gate)


def test_a_gate_abandoned_during_its_last_check_stores_nothing_of_it():
    flag, scheduler = Flag(), FakeCheckScheduler()
    commands = FlippingCommands(flag, "ruff check .")
    gate = gate_over(scheduler, commands)
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    assert [c for _, c in commands.ran] == ["pytest", "ruff check ."]
    assert len(scheduler.released) == 1
    _untouched(gate)


def test_a_parallel_gate_abandoned_during_a_check_does_not_rerun_it():
    flag, scheduler = Flag(), FakeCheckScheduler()
    commands = FlippingCommands(flag, "pytest", pytest=[CommandResult(1, "red")] * 2)
    gate = gate_over(scheduler, commands, parallel=True)
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    assert [c for _, c in commands.ran].count("pytest") == 1  # the red is not rerun
    assert len(scheduler.released) == 1
    _untouched(gate)


def test_a_witness_that_finishes_after_the_flag_leaves_no_result_behind():
    flag, scheduler = Flag(), FakeCheckScheduler()
    commands = FlippingCommands(flag, "witness")
    gate = gate_over(scheduler, commands, witness="witness")
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    assert [c for _, c in commands.ran] == ["witness"]
    _untouched(gate)


def test_the_next_attempt_runs_its_gate_in_full_on_the_same_presubmit():
    first, second = Flag(), Flag()
    scheduler = FakeCheckScheduler()
    commands = FlippingCommands(first, "pytest")
    gate = gate_over(scheduler, commands)
    with pytest.raises(CheckAbandoned):
        gate(abandoned=first.is_set)
    commands.ran.clear()
    assert gate(abandoned=second.is_set) is None
    assert [c for _, c in commands.ran] == ["pytest", "ruff check ."]
    assert len(gate.invocations) == 1 and len(gate.gates) == 1
    assert gate.results is not None and len(gate.results) == 2


def test_without_a_flag_the_request_is_the_one_of_before():
    scheduler = FakeCheckScheduler(wait_s=1.0)
    gate = gate_over(scheduler, MemoryCommands(pytest=[CommandResult(1, "red")] * 2))
    gate()
    gate()
    assert [r.cancelled for r in scheduler.requests] == [None, None]
    assert len({(r.enqueued_at, r.token) for r in scheduler.requests[1:]}) == 1


def test_a_grant_that_comes_back_after_the_flag_is_released_and_not_kept():
    flag = Flag()

    class LateFlag(FakeCheckScheduler):
        def acquire(self, request: CheckRequest):
            grant = super().acquire(request)
            flag.set()  # the stall reap lands while the wait is being answered
            return grant

    scheduler = LateFlag(wait_s=7.0)
    commands = MemoryCommands()
    gate = gate_over(scheduler, commands)
    with pytest.raises(CheckAbandoned):
        gate(abandoned=flag.is_set)
    assert len(scheduler.released) == 1 and commands.ran == []
    assert gate.grant is None and gate.waited_s == 0.0
    _untouched(gate)


# Against a real ledger: the token is the call's, so the retry of the next attempt and the
# abandoned thread of the previous one never share a booking.


class Parked(MemoryCommands):
    """The first command of each named thread waits inside `run` until the test lets it go."""

    def __init__(self) -> None:
        super().__init__()
        self.reached: dict[str, threading.Event] = {}
        self.go: dict[str, threading.Event] = {}

    def park(self, thread: str) -> None:
        self.reached[thread], self.go[thread] = threading.Event(), threading.Event()

    def run(self, cwd, command, timeout_s, *, env=None):
        name = threading.current_thread().name
        if name in self.go and not self.reached[name].is_set():
            self.reached[name].set()
            self.go[name].wait(10.0)
        return super().run(cwd, command, timeout_s, env=env)


class Outcome:
    """What a call run on a thread of its own ended with."""

    def __init__(self, gate: CheckoutPresubmit, flag: Flag, name: str) -> None:
        self.result: str | None = None
        self.error: BaseException | None = None

        def target() -> None:
            try:
                self.result = gate(abandoned=flag.is_set)
            except BaseException as error:  # noqa: BLE001 - the thread reports whatever ended it
                self.error = error

        self.thread = threading.Thread(target=target, name=name)
        self.thread.start()

    def join(self) -> None:
        self.thread.join(10.0)
        assert not self.thread.is_alive()


def _real_ledger(tmp_path, sleeping: dict[str, threading.Event] | None = None):
    def sleep(seconds: float) -> None:
        if sleeping is not None and (polled := sleeping.get(threading.current_thread().name)):
            polled.set()
        time.sleep(0.01)

    return FilesystemCheckLedger(
        tmp_path / "ledger",
        "mine",
        FakeReaper(),
        Limits(cores=16),
        boot_id=lambda: "boot",
        is_alive=lambda pid, boot: True,
        wall_clock=lambda: 1_000_000.0,
        sleep=sleep,
        pid=lambda: 7,
    )


def _rows(tmp_path) -> list[dict]:
    path = tmp_path / "ledger" / "ledger.json"
    return json.loads(path.read_text())["entries"] if path.exists() else []


def _gate_on(ledger: FilesystemCheckLedger, commands: MemoryCommands) -> CheckoutPresubmit:
    workspace = MemoryWorkspace()
    workspace.moved.add(PATH)
    ports = memory_ports(workspace=workspace, commands=commands, check_scheduler=ledger)
    return CheckoutPresubmit(
        ports, PATH, "base", CHECKS, 30.0, priority=CheckPriority.SUBTASK, wait_max_s=60.0
    )


def test_a_second_attempt_books_under_its_own_token_and_the_first_one_leaves_it_alone(tmp_path):
    commands, ledger = Parked(), _real_ledger(tmp_path)
    gate = _gate_on(ledger, commands)
    first, second = Flag(), Flag()
    commands.park("one")
    one = Outcome(gate, first, "one")
    assert commands.reached["one"].wait(10.0)
    (booked_by_one,) = _rows(tmp_path)
    commands.park("two")
    two = Outcome(gate, second, "two")
    assert commands.reached["two"].wait(10.0)
    rows = _rows(tmp_path)
    assert len({r["token"] for r in rows}) == 2 and booked_by_one["token"] in {
        r["token"] for r in rows
    }
    assert {r["enqueued_at"] for r in rows} == {booked_by_one["enqueued_at"]}
    first.set()  # attempt 1 is reaped; its thread is still inside the first check
    commands.go["one"].set()
    one.join()
    assert isinstance(one.error, CheckAbandoned)
    remaining = _rows(tmp_path)
    assert [r["token"] for r in remaining] != [booked_by_one["token"]]
    assert len(remaining) == 1 and remaining[0]["share"] > 0  # the second attempt's share
    assert gate.grant is not None and gate.grant.token == remaining[0]["token"]
    commands.go["two"].set()
    two.join()
    assert two.error is None and two.result is None
    assert _rows(tmp_path) == []


def test_an_abandoned_wait_leaves_the_line_of_the_next_attempt_standing(tmp_path):
    polled = {"one": threading.Event(), "two": threading.Event()}
    ledger = _real_ledger(tmp_path, polled)
    held = [ledger.acquire(CheckRequest(CheckPriority.SUBTASK, 0.0, 7200.0)) for _ in range(2)]
    # `pid=7` for all three: tokens tell them apart, and the ledger is full (6 + 6 of 16).
    commands = MemoryCommands()
    gate = _gate_on(ledger, commands)
    first, second = Flag(), Flag()
    one = Outcome(gate, first, "one")
    assert polled["one"].wait(10.0)
    two = Outcome(gate, second, "two")
    assert polled["two"].wait(10.0)
    waiting = {r["token"] for r in _rows(tmp_path) if r["share"] == 0}
    assert len(waiting) == 2
    first.set()
    one.join()
    assert isinstance(one.error, CheckAbandoned)
    left = {r["token"] for r in _rows(tmp_path) if r["share"] == 0}
    assert len(left) == 1 and left < waiting  # attempt 2 still has its place
    for grant in held:
        ledger.release(grant)
    two.join()
    assert two.error is None and commands.ran  # attempt 2 got its share and ran its checks
    assert _rows(tmp_path) == []
