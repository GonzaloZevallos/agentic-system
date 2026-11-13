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
from agentic.core.process.domain.task import INTEGRATION
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
YOUNG_AT = "2026-11-06T11:58:00+00:00"  # 2 min before the fake `ports.now`
OLD_AT = "2026-11-06T09:00:00+00:00"


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


def fact(head=FULL, *, escalated=False, ids=("9",), at=YOUNG_AT):
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


def new_facts(ports, old):
    """The facts saved that differ from `old`, the one the state already held."""
    return [s["ci_ghost_dispatch"] for s in fact_saves(ports) if s["ci_ghost_dispatch"] != old]


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
    assert ports.tracker.dispatched_reads == [("ci.yml", "2026-11-06T11:56:00Z")]
    record = patch["full_checks"]
    assert record["passed"] is True and record["dispatch_cause"] == "ghost"
    assert patch["ci_ghost_dispatch"] == fact()


async def test_reentry_with_the_fact_and_no_listed_run_dispatches_once_and_keeps_the_ghosts():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost(), listing=listed_after_a_dispatch)
    patch = await run(state_of(ci_ghost_dispatch=fact(ids=("4", "5"))), ports, clock)
    assert len(ports.tracker.dispatch_calls) == 1
    saved = patch["ci_ghost_dispatch"]
    assert saved["ghost_run_ids"] == ["4", "5"] and saved["dispatched_at"] != YOUNG_AT
    assert fact_saves(ports)[0]["ci_ghost_dispatch"] == saved
    assert "full_checks" not in fact_saves(ports)[0]
    assert patch["full_checks"]["passed"] is True


async def test_a_fact_of_another_head_is_ignored_and_overwritten_by_the_next_dispatch():
    clock = Clock()
    ports = ports_of(clock, lambda t: ghost())
    patch = await run(state_of(ci_ghost_dispatch=fact(head=OTHER)), ports, clock)
    assert ("publish", REF) in ports.workspace.ci_ref_calls  # today's push path ran
    assert len(ports.tracker.dispatch_calls) == 1
    assert patch["ci_ghost_dispatch"]["head"] == FULL
    assert patch["ci_ghost_dispatch"]["dispatched_at"] != YOUNG_AT
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


def ghost_dispatches(tracker):
    """Each dispatch lists a new run, queued with no job: ids 11, 12, ..."""
    return (dispatched_run(status="queued", run_id=10 + len(tracker.dispatch_calls)),)


class Distrusted(FullHead):
    """A head whose diff touches the CI definition, so only a dispatched run can judge it."""

    def changed_paths(self, base, head):
        return (".github/workflows/ci.yml",)


def untrusted_ports(clock, listing):
    workspace = Distrusted()
    workspace.merge_bases[(FULL, UPSTREAM)] = "e" * 40
    ports = ports_of(clock, lambda t: ghost(), listing=listing)
    ports.workspace.merge_bases = workspace.merge_bases
    ports.workspace.changed_paths = workspace.changed_paths
    return ports


def assert_ghost_run(patch, ports, ids):
    """The `ghost_run` escalation: a human's pause, never a record, its fact `escalated` and its
    text naming the first id, the last and the count."""
    assert patch["escalation_class"] == "ci" and "(ghost_run)" in patch["escalated"]
    assert "full_checks" not in patch and "subtasks" not in patch and ports.commands.ran == []
    escalated = patch["ci_ghost_dispatch"]
    assert escalated["escalated"] is True and escalated["ghost_run_ids"] == ids
    named = f"runs {ids[0]} … {ids[-1]} ({len(ids)} recorded)" if len(ids) > 1 else f"run {ids[0]}"
    assert named in patch["escalated"]


async def test_a_pushed_ghost_then_two_dispatched_ghosts_escalate_ghost_run_with_the_three_ids():
    clock = Clock()
    ports = ports_of(clock, ghost_from(0), listing=ghost_dispatches)
    patch = await run(state_of(), ports, clock)
    assert_ghost_run(patch, ports, ["9", "11", "12"])
    assert ports.tracker.dispatch_times == [600.0, 1200.0]
    assert len(ports.tracker.watched_reads) == 21 and len(ports.tracker.dispatched_reads) == 42
    unescalated = [s for s in fact_saves(ports) if "escalated" not in s]
    assert [s["ci_ghost_dispatch"]["ghost_run_ids"] for s in unescalated] == [["9"], ["9", "11"]]
    assert all(s["ci_ghost_dispatch"]["escalated"] is False for s in unescalated)


async def test_an_untrusted_head_whose_dispatched_runs_are_ghosts_dispatches_twice_and_escalates():
    clock = Clock()
    ports = untrusted_ports(clock, ghost_dispatches)
    patch = await run(state_of(), ports, clock)
    assert_ghost_run(patch, ports, ["11", "12"])
    assert "pushed" not in patch["escalated"]  # nothing was pushed on this path
    assert len(ports.tracker.dispatch_calls) == 2 and ports.tracker.watched_reads == []
    assert len(ports.tracker.dispatched_reads) == 42  # two waits of 600 s, one read per 30 s
    assert [s["ci_ghost_dispatch"]["ghost_run_ids"] for s in fact_saves(ports)[:1]] == [["11"]]


@pytest.mark.parametrize("order", [("ghost", "none"), ("none", "ghost")])
async def test_a_ghost_and_a_run_that_never_came_end_ghost_run_in_either_order(order):
    clock = Clock()

    def listing(tracker):
        return () if order[len(tracker.dispatch_calls) - 1] == "none" else ghost_dispatches(tracker)

    ports = untrusted_ports(clock, listing)
    patch = await run(state_of(), ports, clock)
    assert_ghost_run(patch, ports, ["11"] if order[0] == "ghost" else ["12"])
    assert len(ports.tracker.dispatch_calls) == 2
    assert len(ports.tracker.dispatched_reads) == 21 + 11  # a ghost wait of 600 s, a grace of 300 s


async def test_reentry_with_an_escalated_fact_dispatches_at_once_and_escalates_on_two_new_ghosts():
    clock = Clock()
    ports = ports_of(clock, ghost_from(0), listing=ghost_dispatches)
    patch = await run(
        state_of(ci_ghost_dispatch=fact(escalated=True, ids=("5", "6", "7"))), ports, clock
    )
    assert_ghost_run(patch, ports, ["5", "6", "7", "11", "12"])
    assert ports.tracker.dispatch_times == [0.0, 600.0] and ports.tracker.watched_reads == []
    assert ports.workspace.ci_ref_calls == [("publish_dispatch", "GH-1")] * 2  # no push, no wait
    first = fact_saves(ports)[0]["ci_ghost_dispatch"]  # replaced before the first wait
    assert first["escalated"] is False and first["ghost_run_ids"] == ["5", "6", "7"]
    assert first["dispatched_at"] != YOUNG_AT and ports.tracker.saved_when_listed[0] == 1
    assert len(ports.tracker.dispatched_reads) == 42


async def test_reentry_with_an_old_fact_and_a_refused_dispatch_leaves_the_fact_untouched():
    clock = Clock()
    ports = ports_of(clock, ghost_from(0))
    ports.tracker.dispatch_result = CiUnreadable("gh_unreadable")
    patch = await run(state_of(ci_ghost_dispatch=fact(at=OLD_AT)), ports, clock)
    assert patch["escalation_class"] == "ci" and "(gh_unreadable)" in patch["escalated"]
    assert new_facts(ports, fact(at=OLD_AT)) == [] and "ci_ghost_dispatch" not in patch
    assert len(ports.tracker.dispatch_calls) == 1 and ports.tracker.dispatched_reads == []


@pytest.mark.parametrize(("at", "dispatches"), [(YOUNG_AT, 0), (OLD_AT, 1)], ids=["young", "old"])
async def test_a_young_fact_is_waited_on_and_an_old_one_is_dispatched_once_with_the_fact_rewritten(
    at, dispatches
):
    clock = Clock()
    ports = ports_of(clock, ghost_from(0))
    patch = await run(state_of(ci_ghost_dispatch=fact(at=at, ids=("4", "5"))), ports, clock)
    assert (
        len(ports.tracker.dispatch_calls) == dispatches and len(ports.tracker.dispatched_reads) == 1
    )
    assert patch["full_checks"]["passed"] is True and ports.tracker.watched_reads == []
    kept = patch["ci_ghost_dispatch"]
    assert kept["ghost_run_ids"] == ["4", "5"] and kept["escalated"] is False
    assert (kept["dispatched_at"] != at) is bool(dispatches)
    assert bool(new_facts(ports, fact(at=at, ids=("4", "5")))) is bool(dispatches)
    if dispatches:
        assert ports.tracker.saved_when_listed == [1]  # saved after the dispatch, before the wait


@pytest.mark.parametrize(
    "stored",
    [fact(escalated=True, ids=("4", "5", "6")), fact(ids=("4", "5", "6"))],
    ids=["escalated", "young"],
)
async def test_reentry_with_no_fallback_escalates_ghost_run_on_the_stored_fact_untouched(stored):
    clock = Clock()
    ports = ports_of(clock, ghost_from(0), declared=None)
    patch = await run(state_of(ci_ghost_dispatch=stored), ports, clock)
    assert patch["escalation_class"] == "ci" and "(ghost_run)" in patch["escalated"]
    assert patch["ci_ghost_dispatch"] == stored and "full_checks" not in patch
    assert "runs 4 … 6 (3 recorded)" in patch["escalated"]
    assert " min" not in patch["escalated"]  # no wait happened on this entry
    assert ports.tracker.dispatch_calls == [] and new_facts(ports, stored) == []
    assert ports.tracker.dispatched_reads == [] and ports.tracker.watched_reads == []
