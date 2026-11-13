"""`full_checks` over a pushed head whose live runs are all ghosts (GH-601 part 2): the upstream's
workflow is dispatched once, `ci_ghost_dispatch` is saved right after the accepted dispatch and
carried by every patch that follows, and the fresh run judges. Fake ports: the scripted tracker
answers `watched_branch_ci` as a function of the node's injected clock, `sleep` moves that clock,
so no wait costs real time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, DispatchedRun
from agentic.core.process.domain.presubmit import CiDispatch
from agentic.core.process.domain.task import INTEGRATION, apply
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

CFG = Configuration(product_dir=MemoryWorkspace.ROOT, check_timeout_s=600.0, ci_start_grace_s=300.0)
FULL = "c" * 40
OTHER = "b" * 40
UPSTREAM = "d" * 40
REF = "agentic-ci/GH-1"
LINK = "https://github.com/o/r/actions/runs/7"
DISPATCH = CiDispatch(workflow="ci.yml", sha_input="sha")
PUSHED = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
OLD_DISPATCH_AT = "2026-11-13T10:00:00+00:00"


class FullHead(MemoryWorkspace):
    def full_head(self, path):
        return FULL


class Clock:
    """A clock only `sleep` (and a tracker's read time) moves."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class Tracker(MemoryTracker):
    """`watched_branch_ci` answers `watched(clock.now)`; the dispatched listing is `listing`
    (a tuple, or a function of this tracker); a dispatched read costs `step` clock seconds."""

    def __init__(self, clock: Clock, watched, listing=(), step: float = 0.0) -> None:
        super().__init__()
        self.clock = clock
        self.watched = watched
        self.listing = listing
        self.step = step
        self.dispatch_times: list[float] = []
        self.saved_when_listed: list[int] = []
        self.documents = None

    def watched_branch_ci(self, branch):
        self.watched_reads.append(branch)
        return self.watched(self.clock.now)

    def dispatch_ci(self, workflow, ref, inputs):
        self.dispatch_times.append(self.clock.now)
        return super().dispatch_ci(workflow, ref, inputs)

    def dispatched_ci(self, workflow, since):
        self.dispatched_reads.append((workflow, since))
        self.saved_when_listed.append(len(self.documents.saved))
        self.clock.now += self.step
        return self.listing(self) if callable(self.listing) else self.listing


def job(name, conclusion="SUCCESS", status="COMPLETED"):
    return CiCheck(name, status, conclusion, f"{LINK}/{name}")


def ghost(*run_ids):
    ids = frozenset(run_ids or ("9",))
    return CiReport(FULL, None, None, None, (), running_runs=ids, queued_empty_runs=ids)


def live():
    """A run of the head that is going and has reported no declared job."""
    return CiReport(FULL, None, None, None, (), running_runs=frozenset({"9"}))


NOT_YET = CiReport(OTHER, None, None, None, ())
GREEN = CiReport(FULL, None, None, None, (job("unit"), job("web")), runs_completed=True)


def dispatched_run(*jobs, status="completed", run_id=99):
    return DispatchedRun(
        run_id=run_id,
        event="workflow_dispatch",
        display_title=f"CI {FULL}",
        status=status,
        created_at="2026-11-13T10:00:00Z",
        link=f"{LINK}/{run_id}",
        jobs=tuple(jobs),
    )


GREEN_RUN = (dispatched_run(job("unit"), job("web")),)
QUEUED_RUN = (dispatched_run(status="queued"),)


def fact(head=FULL, *, escalated=False, ids=("9",), at=OLD_DISPATCH_AT):
    return {"head": head, "ghost_run_ids": list(ids), "dispatched_at": at, "escalated": escalated}


def state_of(**more) -> dict:
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
    return {
        "id": "GH-1",
        "title": "Ghosts",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {INTEGRATION: subtask},
        "task_head": FULL,
        **more,
    }


def ports_of(clock, watched, *, listing=GREEN_RUN, step=0.0, declared=DISPATCH):
    stack = MemoryStack(checks={"tests": "pytest"}, delivery_checks=("tests",), ci=("unit", "web"))
    tracker = Tracker(clock, watched, listing, step)
    ports = memory_ports(
        stack=stack, commands=MemoryCommands(), tracker=tracker, workspace=FullHead()
    )
    tracker.documents = ports.documents
    ports.workspace.upstream_answer = Upstream("origin/master", UPSTREAM, None)
    if declared is not None:
        ports.stack.declared_ci_dispatch_at[UPSTREAM] = declared
    return ports


async def run(state, ports, clock, **more):
    return await full_checks_in_task_checkout(
        state, CFG, ports, sleep=clock.sleep, clock=clock, push_sleep=lambda _s: None, **more
    )


def assert_run_unfinished(patch, ports):
    assert patch["escalation_class"] == "ci" and patch["escalation_cause"] == "ci_run_unfinished"
    assert "full_checks" not in patch and "subtasks" not in patch
    assert ports.commands.ran == []


def ghost_from(start):
    """A head with no run until `start` seconds on the node clock, a ghost run 9 from then."""
    return lambda t: NOT_YET if t < start else ghost()


def fact_saves(ports):
    """The documents saved with a ghost-dispatch fact: the node saves others for its own reasons."""
    return [s for s in ports.documents.saved if "ci_ghost_dispatch" in s]


def listed_after_a_dispatch(tracker):
    return GREEN_RUN if tracker.dispatch_calls else ()


async def test_a_ghost_seen_from_180_s_is_dispatched_once_at_780_s_and_the_fresh_run_judges():
    clock = Clock()
    ports = ports_of(clock, ghost_from(180))
    patch = await run(state_of(), ports, clock)
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci_dispatch" and record["run_id"] == 99
    assert record["dispatch_cause"] == "ghost" and record["stack_at"] == UPSTREAM
    assert "speculative" not in record
    assert ports.tracker.dispatch_times == [780.0]
    assert ports.tracker.dispatch_calls == [("ci.yml", "", {"sha": FULL})]
    saved = patch["ci_ghost_dispatch"]
    assert saved["head"] == FULL and saved["ghost_run_ids"] == ["9"] and saved["escalated"] is False
    first = fact_saves(ports)[0]  # the fact alone: saved before the fresh run is waited on
    assert first["ci_ghost_dispatch"] == saved and "full_checks" not in first
    assert ports.tracker.saved_when_listed[0] == 1


async def test_a_running_run_with_no_reported_job_is_run_unfinished_at_600_s_with_no_dispatch():
    clock = Clock()
    ports = ports_of(clock, lambda t: live())
    patch = await run(state_of(), ports, clock)
    assert_run_unfinished(patch, ports)
    assert clock.now == 600.0
    assert ports.tracker.dispatch_calls == [] and fact_saves(ports) == []
    assert "ci_ghost_dispatch" not in patch


async def test_a_ghost_beside_a_live_run_that_reported_a_job_is_run_unfinished_not_dispatched():
    clock = Clock()
    busy = CiReport(
        FULL,
        None,
        None,
        None,
        (job("lint", None, "IN_PROGRESS"),),
        running_runs=frozenset({"9", "10"}),
        queued_empty_runs=frozenset({"9"}),
    )
    ports = ports_of(clock, lambda t: busy)
    patch = await run(state_of(), ports, clock)
    assert_run_unfinished(patch, ports)
    assert ports.tracker.dispatch_calls == [] and fact_saves(ports) == []


async def test_a_ghost_that_gains_a_job_at_720_s_takes_todays_path():
    clock = Clock()
    ports = ports_of(clock, lambda t: NOT_YET if t < 180 else (ghost() if t < 720 else GREEN))
    patch = await run(state_of(), ports, clock)
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci"
    assert "dispatch_cause" not in record
    assert ports.tracker.dispatch_calls == [] and fact_saves(ports) == []
    assert "ci_ghost_dispatch" not in patch


async def test_the_node_ceiling_at_1200_s_is_run_unfinished_when_a_ghost_is_still_maturing():
    clock = Clock()

    def watched(t):
        if t < 100:
            return NOT_YET
        if t < 650:
            return ghost("9")
        return ghost("9", "10")  # a second ghost, seen at 660 s, is mature only at 1260 s

    ports = ports_of(clock, watched)
    patch = await run(state_of(), ports, clock)
    assert_run_unfinished(patch, ports)
    assert clock.now == 1200.0
    assert ports.tracker.dispatch_calls == [] and fact_saves(ports) == []


async def test_a_ghost_seen_in_the_speculative_wait_survives_the_republish_and_is_dispatched():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost())
    state = state_of(ci_speculation={"head": FULL, "pushed_at": PUSHED.isoformat()})
    lost = PUSHED + timedelta(seconds=500)  # past the grace: the speculative event is lost
    patch = await run(state, ports, clock, now=lambda: lost)
    assert ("delete_ci", REF) in ports.workspace.ci_ref_calls  # the republish happened
    assert ports.tracker.dispatch_times == [600.0]  # the ghost's age ran on the node clock
    record = patch["full_checks"]
    assert record["dispatch_cause"] == "ghost" and "speculative" not in record
    assert patch["ci_ghost_dispatch"]["ghost_run_ids"] == ["9"]


async def test_a_speculative_push_whose_run_is_a_ghost_is_dispatched_once_without_speculative():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost())
    state = state_of(ci_speculation={"head": FULL, "pushed_at": PUSHED.isoformat()})
    soon = PUSHED + timedelta(seconds=5)
    patch = await run(state, ports, clock, now=lambda: soon)
    assert len(ports.tracker.dispatch_calls) == 1
    assert ports.workspace.ci_ref_calls == [("publish_dispatch", "GH-1")]  # no republish
    record = patch["full_checks"]
    assert record["dispatch_cause"] == "ghost" and "speculative" not in record
    assert patch["ci_ghost_dispatch"]["head"] == FULL


async def test_reentry_with_a_fact_of_the_same_head_and_a_listed_run_dispatches_nothing():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost())
    patch = await run(state_of(ci_ghost_dispatch=fact()), ports, clock)
    assert ports.tracker.dispatch_calls == []
    assert ports.workspace.ci_ref_calls == [] and ports.tracker.watched_reads == []
    assert all(s["ci_ghost_dispatch"] == fact() for s in fact_saves(ports))  # no new fact
    assert ports.tracker.dispatched_reads == [("ci.yml", "2026-11-13T09:58:00Z")]
    record = patch["full_checks"]
    assert record["passed"] is True and record["dispatch_cause"] == "ghost"
    assert patch["ci_ghost_dispatch"] == fact()


async def test_reentry_with_the_fact_and_no_listed_run_dispatches_once_and_keeps_the_ghosts():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost(), listing=listed_after_a_dispatch)
    patch = await run(state_of(ci_ghost_dispatch=fact(ids=("4", "5"))), ports, clock)
    assert len(ports.tracker.dispatch_calls) == 1
    saved = patch["ci_ghost_dispatch"]
    assert saved["ghost_run_ids"] == ["4", "5"] and saved["dispatched_at"] != OLD_DISPATCH_AT
    assert fact_saves(ports)[0]["ci_ghost_dispatch"] == saved
    assert "full_checks" not in fact_saves(ports)[0]
    assert patch["full_checks"]["passed"] is True


@pytest.mark.parametrize(
    "stale", [fact(head=OTHER), fact(escalated=True)], ids=["another-head", "escalated"]
)
async def test_a_fact_that_is_not_in_force_is_ignored_and_overwritten_by_the_next_dispatch(stale):
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost())
    patch = await run(state_of(ci_ghost_dispatch=stale), ports, clock)
    assert ("publish", REF) in ports.workspace.ci_ref_calls  # today's push path ran
    assert len(ports.tracker.dispatch_calls) == 1
    assert patch["ci_ghost_dispatch"]["head"] == FULL
    assert patch["ci_ghost_dispatch"]["dispatched_at"] != OLD_DISPATCH_AT
    assert fact_saves(ports)[0]["ci_ghost_dispatch"]["head"] == FULL


async def test_an_upstream_without_ci_dispatch_is_run_unfinished_with_no_dispatch():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost(), declared=None)
    patch = await run(state_of(), ports, clock)
    assert_run_unfinished(patch, ports)
    assert ports.tracker.dispatch_calls == [] and fact_saves(ports) == []
    assert "ci_ghost_dispatch" not in patch


async def test_a_refused_dispatch_saves_no_fact():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost())
    ports.tracker.dispatch_result = CiUnreadable("gh_unreadable")
    patch = await run(state_of(), ports, clock)
    assert patch["escalation_class"] == "ci" and "full_checks" not in patch
    assert len(ports.tracker.dispatch_calls) == 1
    assert fact_saves(ports) == [] and "ci_ghost_dispatch" not in patch


async def test_a_dispatched_run_that_is_also_a_ghost_is_run_unfinished_never_red_or_redispatched():
    clock = Clock()
    # Each dispatched read costs 1.5 s of the clock, so the 20th read ends at exactly 600 s into
    # the wait: the run is a ghost then only if the first read is counted from the wait's start
    # (600 s old), and not if it is counted from the end of that read (598.5 s old).
    ports = ports_of(clock, lambda t: ghost(), listing=QUEUED_RUN, step=1.5)
    patch = await run(state_of(), ports, clock)
    assert_run_unfinished(patch, ports)
    assert len(ports.tracker.dispatch_calls) == 1
    assert patch["ci_ghost_dispatch"]["ghost_run_ids"] == ["9"]
    unescalated = [s for s in fact_saves(ports) if "escalation_cause" not in s]
    assert len(unescalated) == 1  # the one save after the dispatch; the rest is the escalation


async def test_a_resume_from_the_state_of_a_run_unfinished_after_a_dispatch_dispatches_nothing():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost(), listing=QUEUED_RUN, step=1.5)
    first = await run(state_of(), ports, clock)
    assert_run_unfinished(first, ports)
    resumed = apply(state_of(), first)
    clock = Clock()
    again = ports_of(clock, lambda t: ghost(), listing=QUEUED_RUN, step=1.5)
    patch = await run(resumed, again, clock)
    assert again.tracker.dispatch_calls == [] and again.workspace.ci_ref_calls == []
    assert_run_unfinished(patch, again)
    assert patch["ci_ghost_dispatch"] == first["ci_ghost_dispatch"]
