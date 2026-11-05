"""`full_checks` over a run a review already pushed (GH-465): the same commit is waited for on
the ref, not pushed again, and the record says `speculative`; another commit, a cancelled or
absent run and a push that GitHub never saw all end on today's republish and wait, and only
that wait may escalate. Fake ports: the scripted tracker, injected `sleep`, `clock` and `now`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.application.ports import GitError
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCommands,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    full_checks_in_task_checkout,
    memory_ports,
)

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT, check_timeout_s=100.0, ci_start_grace_s=30.0)
REF = "agentic-ci/GH-1"
HEAD = "a" * 40
OTHER = "b" * 40
PUSHED = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
LINK = "https://github.com/o/r/actions/runs/7"


class Workspace(MemoryWorkspace):
    def full_head(self, path):
        return HEAD


class Tracker(MemoryTracker):
    def __init__(self, *reads: CiReport | CiUnreadable) -> None:
        super().__init__()
        self.reads = list(reads)

    def branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        self.branch_reads.append(branch)
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def job(name: str, conclusion: str | None = "SUCCESS", status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"{LINK}/{name}")


def report(*checks: CiCheck, sha: str = HEAD) -> CiReport:
    return CiReport(sha, None, None, None, tuple(checks))


GREEN = report(job("check"))
RED = report(job("check", "FAILURE"))
CANCELLED = report(job("check", "CANCELLED"))
NOT_YET = CiReport(OTHER, None, None, None, ())


def state_of(speculated: str | None = HEAD, **more) -> dict:
    subtask = {
        "id": INTEGRATION,
        "title": "integration",
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": "agentic/GH-1",
        "base": "b",
    }
    state = {
        "id": "GH-1",
        "title": "Speculation",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {INTEGRATION: subtask},
        "task_head": HEAD,
        **more,
    }
    if speculated is not None:
        state["ci_speculation"] = {"head": speculated, "pushed_at": PUSHED.isoformat()}
    return state


def ports_of(*reads, ci=("check",), workspace=None):
    stack = MemoryStack(checks={"tests": "pytest"}, delivery_checks=("tests",), ci=ci)
    return memory_ports(
        stack=stack,
        commands=MemoryCommands(),
        tracker=Tracker(*reads),
        workspace=workspace or Workspace(),
    )


async def run(state, ports, *, after_s: float = 5.0):
    clock = Clock()
    now = PUSHED + timedelta(seconds=after_s)
    return await full_checks_in_task_checkout(
        state,
        CFG,
        ports,
        sleep=clock.sleep,
        clock=clock,
        now=lambda: now,
        push_sleep=lambda _s: None,
    )


async def test_the_same_head_is_waited_for_without_a_new_push_and_says_so():
    ports = ports_of(GREEN)
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci" and record["speculative"] is True
    assert record["head"] == HEAD
    assert ports.workspace.ci_ref_calls == []
    assert ports.tracker.branch_reads == [REF]


async def test_a_red_speculated_run_is_a_return_with_its_rows_and_says_so():
    patch = await run(state_of(), ports_of(RED))
    record = patch["full_checks"]
    assert record["passed"] is False and record["speculative"] is True
    assert [(r["name"], r["passed"]) for r in record["results"]] == [("check", False)]
    assert patch["subtasks"][INTEGRATION]["status"] == "returned"


async def test_the_run_still_starting_is_waited_for_with_one_read_per_poll():
    pending = report(job("check", None, "IN_PROGRESS"))
    ports = ports_of(pending, pending, GREEN)
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["speculative"] is True
    assert len(ports.tracker.branch_reads) == 3
    assert ports.workspace.ci_ref_calls == []


async def test_a_changed_head_runs_as_today_and_is_not_speculative():
    ports = ports_of(GREEN)
    patch = await run(state_of(speculated=OTHER), ports)
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert "speculative" not in patch["full_checks"]


async def test_no_speculation_runs_as_today():
    ports = ports_of(GREEN)
    patch = await run(state_of(speculated=None), ports)
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert "speculative" not in patch["full_checks"]


async def test_a_discarded_record_runs_as_today():
    ports = ports_of(GREEN)
    patch = await run({**state_of(), "ci_speculation": None}, ports)
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert "speculative" not in patch["full_checks"]


async def test_a_short_recorded_sha_is_never_consumed():
    ports = ports_of(GREEN)
    patch = await run(state_of(speculated=HEAD[:7]), ports)
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert "speculative" not in patch["full_checks"]


async def test_a_run_of_an_older_head_republishes_and_never_escalates_by_itself():
    # Head A was pushed, then B overwrote the ref, A was approved: the ref shows B's run.
    ports = ports_of(report(job("check"), sha=OTHER), GREEN)
    patch = await run(state_of(), ports, after_s=40.0)
    assert ports.workspace.ci_ref_calls == [("delete_ci", REF), ("publish", REF)]
    assert patch["full_checks"]["passed"] is True
    assert "speculative" not in patch["full_checks"]
    assert patch.get("status") != WAITING_ON_HUMAN


async def test_a_stale_push_with_no_run_republishes_without_sleeping():
    clock = Clock()
    ports = ports_of(NOT_YET, GREEN)
    now = PUSHED + timedelta(seconds=31)
    patch = await full_checks_in_task_checkout(
        state_of(), CFG, ports, sleep=clock.sleep, clock=clock, now=lambda: now
    )
    assert ports.workspace.ci_ref_calls == [("delete_ci", REF), ("publish", REF)]
    assert clock.slept == []
    assert patch["full_checks"]["passed"] is True
    assert "speculative" not in patch["full_checks"]


async def test_a_fresh_push_with_no_run_yet_is_waited_for():
    ports = ports_of(NOT_YET, GREEN)
    patch = await run(state_of(), ports, after_s=2.0)
    assert ports.workspace.ci_ref_calls == []
    assert patch["full_checks"]["speculative"] is True


async def test_a_cancelled_speculated_run_falls_back_to_the_republish_and_its_wait():
    # The run of the ref was cancelled by a newer push; the republish finds a fresh green one.
    ports = ports_of(CANCELLED, GREEN)
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == [("delete_ci", REF), ("publish", REF)]
    assert patch["full_checks"]["passed"] is True
    assert "speculative" not in patch["full_checks"]
    assert patch.get("status") != WAITING_ON_HUMAN


async def test_only_the_wait_after_the_fallback_may_escalate():
    ports = ports_of(CANCELLED)
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == [("delete_ci", REF), ("publish", REF)]
    assert patch["status"] == WAITING_ON_HUMAN
    assert "full_checks" not in patch


async def test_an_unreadable_ci_on_the_speculated_run_escalates_without_a_republish():
    ports = ports_of(CiUnreadable("gh_unavailable"))
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == []
    assert patch["status"] == WAITING_ON_HUMAN


async def test_a_failed_republish_after_a_lost_push_escalates_as_a_failed_publish():
    ports = ports_of(NOT_YET)
    ports.workspace.ci_ref_error = "refused"
    patch = await run(state_of(), ports, after_s=60.0)
    assert patch["status"] == WAITING_ON_HUMAN
    assert f"push of {REF} failed" in patch["escalated"]


async def test_a_product_without_ci_ignores_a_record_and_runs_locally():
    ports = ports_of(GREEN, ci=())
    patch = await run(state_of(), ports)
    assert ports.workspace.ci_ref_calls == [] and ports.tracker.branch_reads == []
    assert "speculative" not in patch["full_checks"]


async def test_a_speculated_run_writes_no_push_keys_and_a_retried_republish_notes_its_create():
    ports = ports_of(GREEN)
    patch = await run(state_of(), ports)
    assert "push_attempts" not in patch["full_checks"]

    class Flaky(Workspace):
        failed = False

        def publish_ci_ref(self, path, ref):
            if not self.failed:
                self.failed = True
                raise GitError("x", stderr="fatal: the remote end hung up unexpectedly")
            super().publish_ci_ref(path, ref)

    again = ports_of(CANCELLED, GREEN, workspace=Flaky())
    patch = await run(state_of(), again)
    record = patch["full_checks"]
    assert record["push_attempts"] == 2 and record["push_kind"] == "transient"
    assert "speculative" not in record
