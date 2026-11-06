"""The `full_checks` node with `ci:` declared (GH-376): the integrated head is published to its
own ref, the node waits for the declared jobs on exactly that head and records their verdict,
and any way the CI cannot be used runs the local tier with the cause written down. Fake ports:
`MemoryTracker.branch_reports` scripts what the CI says, `MemoryWorkspace` records the refs."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.application.checkouts import task_base
from agentic.core.process.application.full_checks import _DISPATCH_DETAIL
from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.domain import ci_push, rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, DispatchedRun
from agentic.core.process.domain.ci_push import PUSH_ATTEMPTS, PUSH_UNKNOWN_ATTEMPTS
from agentic.core.process.domain.presubmit import CiCover, CiDispatch
from agentic.core.process.domain.roles import DEVELOPER_INTEGRATOR
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN, CiCause, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCheckResults,
    MemoryCommands,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    full_checks_in_task_checkout,
    memory_ports,
    unstamped,
)

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT, check_timeout_s=100.0, ci_start_grace_s=30.0)
CHECKS = {"tests": "pytest", "lint": "ruff check ."}
REF = "agentic-ci/GH-1"
HEAD = "base"
LINK = "https://github.com/o/r/actions/runs/7"


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
        "title": "Tiers",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {INTEGRATION: subtask},
        "task_head": "base",
        **more,
    }


def job(name: str, conclusion: str | None = "SUCCESS", status: str = "COMPLETED") -> CiCheck:
    return CiCheck(name, status, conclusion, f"{LINK}/{name}")


def report(*checks: CiCheck, sha: str = HEAD) -> CiReport:
    return CiReport(sha, None, None, None, tuple(checks))


GREEN = report(job("unit"), job("web"))


class Clock:
    """A monotonic clock that only `sleep` moves: a wait costs no real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Tracker(MemoryTracker):
    """`branch_ci` answers the scripted reads in order, the last one for good, and counts."""

    def __init__(self, *reads: CiReport | CiUnreadable) -> None:
        super().__init__()
        self.reads = list(reads)

    def branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        self.branch_reads.append(branch)
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]


def ports_of(*reads, commands=None, ci=("unit", "web"), **more):
    stack = MemoryStack(checks=CHECKS, delivery_checks=("tests",), ci=ci)
    return memory_ports(
        stack=stack, commands=commands or MemoryCommands(), tracker=Tracker(*reads), **more
    )


async def run(state, ports, clock: Clock | None = None):
    clock = clock or Clock()
    return await full_checks_in_task_checkout(
        state, CFG, ports, sleep=clock.sleep, clock=clock, push_sleep=lambda _s: None
    )


async def test_green_ci_passes_with_no_local_check_and_publishes_the_head_once():
    ports = ports_of(GREEN)
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci"
    assert "ci_fallback" not in record and "gate" not in record and "over_budget" not in record
    assert record["head"] == HEAD
    assert record["declared"] == {"name": "ci"}
    assert [(r["name"], r["passed"]) for r in record["results"]] == [("unit", True), ("web", True)]
    assert record["witness"] is None
    assert ports.commands.ran == []
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert ports.workspace.ci_refs == {REF: HEAD}
    assert ports.tracker.branch_reads == [REF]
    text = patch["sections"]["Full checks"]
    assert text.startswith(f"Passed on CI for `{HEAD}`: unit, web.")
    assert f"{LINK}/unit" in text and "reused" not in text


async def test_the_wait_reads_again_after_the_poll_interval_until_the_jobs_decide():
    pending = report(job("unit", None, "IN_PROGRESS"), job("web"))
    ports = ports_of(pending, pending, GREEN)
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert patch["full_checks"]["passed"] is True
    assert clock.slept == [30, 30]
    assert len(ports.tracker.branch_reads) == 3
    assert patch["full_checks"]["duration_s"] == 60.0


async def test_the_names_are_the_merge_gate_names_with_the_plan_ones_joined():
    only_plan = report(job("unit"), job("web"))  # no "extra" job: waits, then times out
    ports = ports_of(only_plan)
    patch = await run(state_of(ci_required=["extra"]), ports)
    record = patch["full_checks"]
    assert record["passed"] is False and record["failed_step"] == "timeout"
    assert "extra" in patch["sections"]["Full checks"]


async def test_a_red_job_sends_the_task_back_with_its_name_and_link_without_sleeping():
    ports = ports_of(report(job("unit"), job("web", "FAILURE")))
    clock = Clock()
    state = state_of()
    patch = await run(state, ports, clock)
    record = patch["full_checks"]
    assert clock.slept == []
    assert record["passed"] is False and record["ran_on"] == "ci"
    assert record["failed_step"] == "web"
    assert {r["name"]: r.get("link") for r in record["results"]} == {
        "unit": None,
        "web": f"{LINK}/web",
    }
    assert patch["subtasks"][INTEGRATION]["returned_by"] == "full_checks"
    assert patch["subtasks"][INTEGRATION]["reason"].endswith(": web")
    assert [f["location"] for f in patch["findings"]] == ["full_checks:web"]
    text = patch["sections"]["Full checks"]
    assert "web" in text and "FAILURE" in text and f"{LINK}/web" in text
    assert "reproduce it with the product's declared checks" in text
    assert ports.commands.ran == []
    assert apply(state, patch)["subtasks"][INTEGRATION]["status"] == "returned"


def assert_escalated_ci(patch, ports, state=None):
    """GH-462: a CI with no verdict is an escalation of class `ci`: no local command, no
    record, no return."""
    assert patch["escalation_class"] == "ci"
    assert patch["status"] == WAITING_ON_HUMAN
    assert patch["current_role"] == DEVELOPER_INTEGRATOR.name
    assert "full_checks" not in patch and "subtasks" not in patch and "findings" not in patch
    assert ports.commands.ran == []
    assert ports.check_scheduler.requests == []
    assert unstamped(patch["pause_log"]) == [{"kind": "escalation", "value": "ci"}]
    if state is not None:
        assert rules.auto_resumable(apply(state, patch)) is None


async def test_a_report_of_another_head_is_never_accepted_and_escalates_as_no_run():
    ports = ports_of(report(job("unit"), job("web"), sha="f" * 40))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    # Two grace periods (30 s each), not the 100 s check timeout.
    assert sum(clock.slept) == 60
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("delete_ci", REF), ("publish", REF)]
    assert "no CI run" in patch["escalated"] and HEAD[:7] in patch["escalated"]
    assert "pushed twice" in patch["escalated"] and "1 min" in patch["escalated"]
    assert ports.commands.ran == [] and "full_checks" not in patch


async def test_a_job_that_never_started_escalates_as_not_started():
    ports = ports_of(report(job("unit", "STARTUP_FAILURE"), job("web", "CANCELLED")))
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert "did not start" in patch["escalated"]


@pytest.mark.parametrize("cause", ["gh_unavailable", "gh_unreadable"])
async def test_a_ci_that_cannot_be_read_up_to_the_bound_escalates_with_its_cause(cause):
    ports = ports_of(CiUnreadable(cause))
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert cause in patch["escalated"]


async def test_a_timed_out_ci_fails_and_names_the_pending_jobs_never_passes():
    ports = ports_of(report(job("unit"), job("web", None, "IN_PROGRESS")))
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["passed"] is False and record["ran_on"] == "ci"
    assert record["failed_step"] == "timeout"
    assert "web" in patch["sections"]["Full checks"]
    assert ports.commands.ran == []


async def test_a_github_path_in_the_diff_runs_local_as_untrusted_and_publishes_nothing():
    ports = ports_of(GREEN)
    ports.workspace.diff_paths = ("src/a.py", ".github/workflows/ci.yml")
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ci_fallback"] == "untrusted"
    assert ports.workspace.ci_ref_calls == []
    assert ports.tracker.branch_reads == []
    assert ports.workspace.diff_reads == [(task_base(state_of(), ports), HEAD)]


async def test_paths_that_cannot_be_read_run_local_as_untrusted():
    class Unreadable(MemoryWorkspace):
        def changed_paths(self, base, head):
            raise GitError("no merge base")

    ports = ports_of(GREEN, workspace=Unreadable())
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ci_fallback"] == "untrusted"
    assert ports.workspace.ci_ref_calls == []


async def test_a_failed_publish_escalates_as_publish_failed_and_reads_no_ci():
    ports = ports_of(GREEN)
    ports.workspace.ci_ref_error = "push refused"
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert ports.tracker.branch_reads == []
    assert f"push of {REF} failed" in patch["escalated"]


async def test_the_wait_takes_no_check_slot():
    ports = ports_of(GREEN)
    await run(state_of(), ports)
    assert ports.check_scheduler.requests == []


async def test_an_untrusted_local_run_that_fails_still_returns_the_task_with_the_cause_kept():
    commands = MemoryCommands(**{"ruff check .": [CommandResult(1, "E501")] * 2})
    ports = ports_of(GREEN, commands=commands)
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["passed"] is False
    assert record["ran_on"] == "local" and record["ci_fallback"] == "untrusted"
    assert "E501" in patch["sections"]["Full checks"]
    assert patch["sections"]["Full checks"].startswith("CI not used (untrusted)")


async def test_without_ci_no_ci_or_ref_port_is_called_and_no_new_key_is_written():
    ports = ports_of(GREEN, ci=())
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert "ran_on" not in record and "ci_fallback" not in record
    assert ports.workspace.ci_ref_calls == []
    assert ports.tracker.branch_reads == []
    assert ports.workspace.diff_reads == []
    assert [c for _, c in ports.commands.ran] == ["pytest", "ruff check ."]
    assert not patch["sections"]["Full checks"].startswith("CI not used")


# GH-453: a timed-out check that a gating CI job covers is deferred, never red.
COVER = CiCover("e2e", r"✘")
WEB_OUT = "  ✓ 216 [mobile] works\n  ✓ 217 [mobile] last\n"
CHECKS_WEB = {"unit": "pytest", "web-e2e": "playwright test"}


def deferring_ports(
    output: str, cause_read=None, *, covers=None, ci=("unit", "web"), commands_override=None, **more
):
    stack = MemoryStack(checks=CHECKS_WEB, delivery_checks=("unit",), ci=ci)
    stack.declared_ci_covers = {"web-e2e": COVER} if covers is None else covers
    timed_out = commands_override or CommandResult(-9, output, timed_out=True)
    commands = MemoryCommands(**{"playwright test": [timed_out]})
    read = cause_read if cause_read is not None else report(job("unit"), sha="f" * 40)
    return memory_ports(stack=stack, commands=commands, tracker=Tracker(read), **more)


async def test_a_covered_check_is_never_run_when_the_ci_gave_no_verdict():
    # GH-462: the deferral of GH-453 needed the local run a no-verdict CI used to lead to;
    # now the task escalates before any check, covered or not, runs.
    ports = deferring_ports(WEB_OUT, check_results=MemoryCheckResults())
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert ports.check_results.puts == []


async def test_an_untrusted_fallback_keeps_a_timeout_red():
    ports = deferring_ports(WEB_OUT)
    ports.workspace.diff_paths = ("src/a.py", ".github/workflows/ci.yml")
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ci_fallback"] == "untrusted"
    assert patch["full_checks"]["passed"] is False and "deferred" not in patch["full_checks"]


async def test_a_product_without_ci_keeps_a_timeout_red():
    patch = await run(state_of(), deferring_ports(WEB_OUT, ci=()))
    assert patch["full_checks"]["passed"] is False
    assert "deferred" not in patch["full_checks"]


# --- the grace period and the one republish (GH-462) ---------------------------------------


async def test_no_run_ever_publishes_once_republishes_once_and_escalates_within_two_graces():
    ports = ports_of(report(sha="f" * 40))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("delete_ci", REF), ("publish", REF)]
    assert clock.now == 2 * CFG.ci_start_grace_s < CFG.check_timeout_s
    assert ports.commands.ran == []


async def test_a_run_that_appears_after_the_republish_is_awaited_and_recorded_green():
    nothing = report(sha="f" * 40)
    ports = ports_of(nothing, nothing, GREEN)
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci"
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("delete_ci", REF), ("publish", REF)]
    assert ports.commands.ran == []


async def test_a_run_that_started_before_the_grace_is_never_republished():
    started = report(job("unit", None, "IN_PROGRESS"), job("web", None, "QUEUED"))
    ports = ports_of(*[started] * 3, GREEN)
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert patch["full_checks"]["passed"] is True
    assert clock.now > CFG.ci_start_grace_s
    assert ports.workspace.ci_ref_calls == [("publish", REF)]


async def test_a_run_that_started_after_the_republish_is_bounded_by_the_check_timeout():
    nothing = report(sha="f" * 40)
    pending = report(job("unit"), job("web", None, "IN_PROGRESS"))
    ports = ports_of(nothing, nothing, pending)
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert patch["full_checks"]["failed_step"] == "timeout"
    assert clock.now > CFG.ci_start_grace_s * 2


async def test_a_refused_republish_escalates_as_publish_failed_and_is_not_retried():
    class Refused(MemoryWorkspace):
        def delete_ci_ref(self, path, ref):
            super().delete_ci_ref(path, ref)
            raise GitError("push refused", stderr="remote: Permission to o/r denied to workflow")

    ports = ports_of(report(sha="f" * 40), workspace=Refused())
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("delete_ci", REF)]
    assert f"push of {REF} failed" in patch["escalated"]
    assert ports.commands.ran == []


async def test_an_unreadable_ci_past_the_grace_keeps_waiting_and_never_republishes():
    ports = ports_of(CiUnreadable("gh_unavailable"))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    assert clock.now >= CFG.check_timeout_s
    assert ports.workspace.ci_ref_calls == [("publish", REF)]


# --- an untrusted head judged by a dispatched run of the base's workflow (GH-462) -----------

FULL = "c" * 40
DISPATCH = CiDispatch(workflow="ci.yml", sha_input="sha")


def run_of(*jobs: CiCheck, run_id: int = 99, title: str = f"CI {FULL}", **more) -> DispatchedRun:
    fields = {
        "event": "workflow_dispatch",
        "status": "completed",
        "created_at": "2026-11-03T10:00:00Z",
        **more,
    }
    return DispatchedRun(
        run_id=run_id, display_title=title, link=f"{LINK}/{run_id}", jobs=tuple(jobs), **fields
    )


class FullHead(MemoryWorkspace):
    """A workspace whose head is a full 40-character sha, as a real one answers."""

    def full_head(self, path):
        return FULL


def dispatching(*listings, dispatch=DISPATCH, **more):
    ports = ports_of(GREEN, workspace=FullHead(), **more)
    ports.workspace.diff_paths = ("src/a.py", ".github/workflows/ci.yml")
    ports.stack.declared_ci_dispatch = dispatch
    ports.tracker.dispatched = list(listings)
    return ports


async def test_a_github_head_with_the_key_is_judged_by_a_dispatched_green_run_and_runs_nothing():
    ports = dispatching((run_of(job("unit"), job("web")),))
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["passed"] is True
    assert record["ran_on"] == "ci_dispatch" and record["run_id"] == 99
    assert "ci_fallback" not in record and record["head"] == FULL
    assert ports.commands.ran == [] and ports.check_scheduler.requests == []
    assert ports.workspace.ci_ref_calls == [("publish_dispatch", "GH-1")]
    assert ports.tracker.branch_reads == []
    [(workflow, ref, inputs)] = ports.tracker.dispatch_calls
    assert (workflow, ref, inputs) == ("ci.yml", "", {"sha": FULL})
    assert f"{LINK}/unit" in patch["sections"]["Full checks"]


async def test_the_listing_is_asked_with_an_iso_lower_bound_for_the_workflow():
    ports = dispatching((run_of(job("unit"), job("web")),))
    await run(state_of(), ports)
    [(workflow, since)] = ports.tracker.dispatched_reads
    assert workflow == "ci.yml" and since.endswith("Z") and len(since) == 20


async def test_a_dispatched_run_still_going_is_awaited_by_polling():
    going = run_of(job("unit"), job("web", None, "IN_PROGRESS"), status="in_progress")
    ports = dispatching((), (going,), (run_of(job("unit"), job("web")),))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert patch["full_checks"]["ran_on"] == "ci_dispatch"
    assert clock.slept == [30, 30]
    assert len(ports.tracker.dispatch_calls) == 1


async def test_a_dispatched_red_run_returns_the_integration_and_names_the_run():
    ports = dispatching((run_of(job("unit"), job("web", "FAILURE")),))
    state = state_of()
    patch = await run(state, ports)
    record = patch["full_checks"]
    assert record["passed"] is False and record["failed_step"] == "web"
    assert record["ran_on"] == "ci_dispatch" and record["run_id"] == 99
    assert patch["subtasks"][INTEGRATION]["returned_by"] == "full_checks"
    assert apply(state, patch)["subtasks"][INTEGRATION]["status"] == "returned"
    assert ports.commands.ran == []


async def test_a_refused_dispatch_escalates_at_once_with_one_call_and_no_sleep():
    ports = dispatching()
    ports.tracker.dispatch_result = CiUnreadable("dispatch_refused")
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    assert len(ports.tracker.dispatch_calls) == 1 and clock.slept == []
    assert ports.tracker.dispatched_reads == []
    assert "dispatch_refused" in patch["escalated"] and "ci.yml" in patch["escalated"]


async def test_a_dispatch_with_no_gh_escalates_as_unavailable_without_waiting():
    ports = dispatching()
    ports.tracker.dispatch_result = CiUnreadable("gh_unavailable")
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert "gh_unavailable" in patch["escalated"]
    assert len(ports.tracker.dispatch_calls) == 1


async def test_no_dispatched_run_dispatches_twice_within_two_graces_and_escalates():
    ports = dispatching()
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    assert len(ports.tracker.dispatch_calls) == 2
    assert clock.now == 2 * CFG.ci_start_grace_s < CFG.check_timeout_s
    assert "no dispatched run" in patch["escalated"] and "ci.yml" in patch["escalated"]
    assert "no_run" in patch["escalated"]


async def test_a_run_that_appears_after_the_second_dispatch_is_judged():
    ports = dispatching((), (), (run_of(job("unit"), job("web")),))
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ran_on"] == "ci_dispatch"
    assert len(ports.tracker.dispatch_calls) == 2


async def test_a_push_run_naming_the_head_is_no_dispatched_run():
    push = run_of(job("unit"), job("web"), event="push")
    ports = dispatching((push,))
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert "full_checks" not in patch


@pytest.mark.parametrize(
    "jobs",
    [(job("unit"),), (job("unit"), job("web", "SKIPPED"))],
    ids=["lacks", "skips"],
)
async def test_a_completed_run_lacking_or_skipping_a_declared_job_escalates_as_jobs_missing(jobs):
    ports = dispatching((run_of(*jobs),))
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert "jobs_missing" in patch["escalated"] or "without a declared job" in patch["escalated"]


async def test_a_dispatched_run_whose_jobs_never_started_escalates_as_not_started():
    ports = dispatching((run_of(job("unit", "STARTUP_FAILURE"), job("web", "CANCELLED")),))
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert "did not start" in patch["escalated"]


async def test_an_unreadable_listing_keeps_the_check_timeout_then_escalates():
    ports = dispatching(CiUnreadable("gh_unreadable"))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    assert clock.now >= CFG.check_timeout_s and len(ports.tracker.dispatch_calls) == 1


async def test_a_trusted_head_never_dispatches_even_with_the_key():
    ports = ports_of(GREEN)
    ports.stack.declared_ci_dispatch = DISPATCH
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ran_on"] == "ci"
    assert ports.tracker.dispatch_calls == []


async def test_without_the_key_the_untrusted_head_still_runs_local():
    ports = ports_of(GREEN)
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ran_on"] == "local"
    assert ports.tracker.dispatch_calls == [] and ports.tracker.dispatched_reads == []


class FlakyPush(MemoryWorkspace):
    """`publish_ci_ref` raises `GitError` with `stderr` for its first `failures` calls."""

    def __init__(self, stderr: str, failures: int | None = None) -> None:
        super().__init__()
        self.stderr = stderr
        self.failures = failures

    def publish_ci_ref(self, path, ref):
        self.ci_ref_calls.append(("publish", ref))
        if self.failures is None or self.failures > 0:
            if self.failures is not None:
                self.failures -= 1
            raise GitError("git push failed", stderr=self.stderr)
        self.ci_refs[ref] = self.head(path)


async def test_a_transient_push_failure_is_retried_and_the_record_notes_it():
    stderr = "fatal: unable to access 'https://github.com/o/r/': Could not resolve host: github.com"
    ports = ports_of(GREEN, workspace=FlakyPush(stderr, failures=1))
    waits: list[float] = []
    clock = Clock()
    patch = await full_checks_in_task_checkout(
        state_of(), CFG, ports, sleep=clock.sleep, clock=clock, push_sleep=waits.append
    )
    assert patch.get("status") != WAITING_ON_HUMAN and "escalated" not in patch
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci"
    assert record["push_attempts"] == 2 and record["push_kind"] == "transient"
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("publish", REF)]
    assert waits == [ci_push.PUSH_BACKOFF_S[0]]


async def test_a_push_that_passed_first_leaves_no_push_keys():
    patch = await run(state_of(), ports_of(GREEN))
    assert "push_attempts" not in patch["full_checks"] and "push_kind" not in patch["full_checks"]


async def test_a_red_run_after_a_retried_push_records_the_retry_too():
    stderr = "fatal: unable to access 'x': Connection reset by peer"
    red = report(job("unit", "FAILURE"), job("web"))
    ports = ports_of(red, workspace=FlakyPush(stderr, failures=2))
    patch = await run(state_of(), ports)
    record = patch["full_checks"]
    assert record["passed"] is False
    assert record["push_attempts"] == PUSH_ATTEMPTS and record["push_kind"] == "transient"


async def test_a_permanent_push_refusal_escalates_after_one_attempt_with_what_git_said():
    stderr = (
        "remote: permission denied to workflow\nremote: refusing to update `workflow` "
        "scope {x}\nfatal: unable to access 'https://u:ghp_secret1234@github.com/o/r/'"
    )
    workspace = FlakyPush(stderr)
    ports = ports_of(GREEN, workspace=workspace)
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert workspace.ci_ref_calls == [("publish", REF)]
    assert "permission denied to workflow" in patch["escalated"]
    assert ci_push.scrub(stderr) in patch["escalated"]
    assert "after 1 attempt(s) (permanent); git said: " in patch["escalated"]
    assert "ghp_secret1234" not in patch["escalated"]
    section = patch["sections"]["Full checks"]
    assert ci_push.scrub(stderr) in section and "ghp_secret1234" not in section
    assert ports.tracker.branch_reads == []


async def test_an_unknown_push_failure_is_tried_twice_and_escalates_with_its_kind():
    stderr = "fatal: something nobody has seen"
    workspace = FlakyPush(stderr)
    patch = await run(state_of(), ports_of(GREEN, workspace=workspace))
    assert len(workspace.ci_ref_calls) == PUSH_UNKNOWN_ATTEMPTS
    assert (
        f"after {PUSH_UNKNOWN_ATTEMPTS} attempt(s) (unknown); git said: {ci_push.scrub(stderr)}"
        in patch["escalated"]
    )


async def test_a_push_failure_without_git_text_keeps_the_escalation_it_always_had():
    ports = ports_of(GREEN)
    ports.workspace.ci_ref_error = "push refused"
    patch = await run(state_of(), ports)
    said = ci_push.scrub("push refused")
    assert (
        f"push of {REF} failed after {PUSH_UNKNOWN_ATTEMPTS} attempt(s) (unknown); git said: {said}"
        in (patch["escalated"])
    )


async def test_two_escalations_on_the_same_head_keep_the_first_section_as_history():
    stderr = "remote: permission denied to workflow"
    state = state_of()
    first = await run(state, ports_of(GREEN, workspace=FlakyPush(stderr)))
    state = apply(state, first)
    second = await run(state, ports_of(GREEN, workspace=FlakyPush(stderr)))
    state = apply(state, second)
    assert "Full checks (v1)" in state["sections"]
    third = await run(state, ports_of(GREEN, workspace=FlakyPush(stderr)))
    state = apply(state, third)
    assert {"Full checks", "Full checks (v1)", "Full checks (v2)"} <= set(state["sections"])
    assert rules.next_section_version(state, "Full checks") == 3


async def test_a_green_run_after_an_escalation_keeps_the_escalation_section_as_history():
    state = state_of()
    stderr = "remote: permission denied `x`"
    first = await run(state, ports_of(GREEN, workspace=FlakyPush(stderr)))
    state = apply(state, first)
    done = await run(state, ports_of(GREEN))
    state = apply(state, done)
    assert state["sections"]["Full checks"].startswith("Passed on CI")
    assert ci_push.scrub(stderr) in state["sections"]["Full checks (v1)"]


async def test_a_failed_delete_of_the_republish_carries_git_said():
    stderr = "remote: Permission to o/r denied to workflow"

    class Refused(MemoryWorkspace):
        def delete_ci_ref(self, path, ref):
            raise GitError("x", stderr=stderr)

    ports = ports_of(report(sha="f" * 40), workspace=Refused())
    patch = await run(state_of(), ports)
    assert f"git said: {ci_push.scrub(stderr)}" in patch["escalated"]


# -- GH-485: a required name no check run carries escalates as a planning defect ---------------


def complete_report(*checks: CiCheck) -> CiReport:
    return CiReport(HEAD, None, None, None, tuple(checks), True)


MATRIX = tuple(job(f"e2e ({n})") for n in range(1, 5))


def counters(patch: dict) -> list[str]:
    return [key for key in ("full_checks", "subtasks", "findings") if key in patch]


async def test_gh543_a_matrix_job_id_required_escalates_unknown_names_without_a_return():
    ports = ports_of(complete_report(job("unit"), *MATRIX), ci=("unit",))
    clock = Clock()
    state = state_of(ci_required=["e2e"])
    patch = await run(state, ports, clock)
    assert_escalated_ci(patch, ports, state)
    assert counters(patch) == []
    # Two reads agreed (one poll apart); the check timeout (100 s) was not waited out.
    assert clock.slept == [30]
    text = patch["escalated"]
    assert "Planning defect" in text and "no check run named `e2e`" in text
    assert HEAD in text and "`e2e (1)`" in text and "`e2e (4)`" in text
    assert "`ci_required`" in text and "task document" in text and "agentic resume" in text
    after = apply(state, patch)
    assert after["subtasks"][INTEGRATION]["status"] == "approved"
    assert "full_checks" not in after


async def test_an_unknown_stack_ci_name_points_at_the_stack_on_master_not_the_document():
    ports = ports_of(complete_report(job("unit")), ci=("unit", "e2e"))
    patch = await run(state_of(), ports)
    assert patch["escalation_class"] == "ci" and counters(patch) == []
    assert "`.agentic/stack.yaml` on master" in patch["escalated"]
    assert "task document" not in patch["escalated"]


async def test_a_run_status_that_is_not_known_keeps_waiting_to_the_timeout():
    ports = ports_of(CiReport(HEAD, None, None, None, (job("unit"), *MATRIX), None), ci=("unit",))
    patch = await run(state_of(ci_required=["e2e"]), ports)
    assert patch["full_checks"]["failed_step"] == "timeout"


async def test_a_name_that_appears_on_the_second_read_is_waited_for_then_green():
    first = complete_report(job("unit"))
    second = complete_report(job("unit"), job("e2e"))
    ports = ports_of(first, second, ci=("unit",))
    patch = await run(state_of(ci_required=["e2e"]), ports)
    assert patch["full_checks"]["passed"] is True and "escalated" not in patch


async def test_a_red_job_with_an_unknown_name_is_still_a_return():
    ports = ports_of(complete_report(job("unit", "FAILURE")), ci=("unit",))
    patch = await run(state_of(ci_required=["e2e"]), ports)
    assert patch["full_checks"]["failed_step"] == "unit"
    assert "escalated" not in patch


async def test_correcting_the_name_in_the_document_and_resuming_goes_green_with_counters_kept():
    reads = (complete_report(job("unit"), *MATRIX),)
    state = state_of(ci_required=["e2e"])
    stopped = apply(state, await run(state, ports_of(*reads, ci=("unit",))))
    before = stopped["subtasks"][INTEGRATION]
    assert stopped["escalation_class"] == "ci"
    resumed = {**stopped, "ci_required": ["e2e (1)"]}
    resumed.pop("escalated")
    patch = await run(resumed, ports_of(*reads, ci=("unit",)))
    assert patch["full_checks"]["passed"] is True
    assert patch["full_checks"]["version"] == 1
    after = apply(resumed, patch)
    assert after["subtasks"][INTEGRATION]["status"] == before["status"]
    assert after["subtasks"][INTEGRATION].get("returns", 0) == before.get("returns", 0)


# --- the head is published to the dispatch ref before it is dispatched (GH-504) --------------

DISPATCH_REF = rules.ci_dispatch_ref("GH-1")


def ordered(ports) -> list[str]:
    """Record the order of `publish_dispatch_ref` and `dispatch_ci` on one list."""
    calls: list[str] = []
    publish, dispatch = ports.workspace.publish_dispatch_ref, ports.tracker.dispatch_ci

    def publishing(path, ref):
        calls.append("publish")
        return publish(path, ref)

    def dispatching_ci(*args):
        calls.append("dispatch")
        return dispatch(*args)

    ports.workspace.publish_dispatch_ref = publishing
    ports.tracker.dispatch_ci = dispatching_ci
    return calls


async def test_the_head_is_published_to_the_dispatch_ref_before_each_dispatch_and_kept():
    ports = dispatching((), (), (run_of(job("unit"), job("web")),))
    calls = ordered(ports)
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["ran_on"] == "ci_dispatch"
    assert calls == ["publish", "dispatch", "publish", "dispatch"]
    assert list(ports.workspace.dispatch_refs) == ["GH-1"]
    assert [v for v, _ in ports.workspace.ci_ref_calls] == ["publish_dispatch"] * 2
    assert ("delete_dispatch", "GH-1") not in ports.workspace.ci_ref_calls


async def test_the_dispatch_ref_stays_when_the_verdict_is_red():
    ports = dispatching((run_of(job("unit"), job("web", "FAILURE")),))
    await run(state_of(), ports)
    assert list(ports.workspace.dispatch_refs) == ["GH-1"]
    assert not any(verb.startswith("delete") for verb, _ in ports.workspace.ci_ref_calls)


async def test_a_failed_publish_escalates_as_ci_and_dispatches_nothing():
    ports = dispatching((run_of(job("unit"), job("web")),))
    ports.workspace.ci_ref_error = "git push failed: scripted"
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert ports.tracker.dispatch_calls == [] and ports.tracker.dispatched_reads == []
    assert "publish_failed" in patch["escalated"]
    assert DISPATCH_REF in patch["escalated"] and FULL[:7] in patch["escalated"]
    assert "push of ci.yml" not in patch["escalated"]


def failing_dispatch_publish(ports, stderr: str, failures: int | None = None) -> list[str]:
    """Make `publish_dispatch_ref` raise `GitError` with `stderr` for its first `failures` calls
    (every call when None); returns the list of calls made."""
    calls: list[str] = []
    publish = ports.workspace.publish_dispatch_ref
    left = [failures]

    def flaky(path, ref):
        calls.append(ref)
        if left[0] is None or left[0] > 0:
            if left[0] is not None:
                left[0] -= 1
            raise GitError("git push failed", stderr=stderr)
        return publish(path, ref)

    ports.workspace.publish_dispatch_ref = flaky
    return calls


async def test_a_transient_failure_of_the_dispatch_publish_is_retried_before_dispatching():
    ports = dispatching((run_of(job("unit"), job("web")),))
    calls = failing_dispatch_publish(ports, "fatal: Could not resolve host: github.com", 1)
    waits: list[float] = []
    clock = Clock()
    patch = await full_checks_in_task_checkout(
        state_of(), CFG, ports, sleep=clock.sleep, clock=clock, push_sleep=waits.append
    )
    assert patch["full_checks"]["ran_on"] == "ci_dispatch" and "escalated" not in patch
    assert calls == [DISPATCH_REF.split("/")[-1]] * 2
    assert waits == [ci_push.PUSH_BACKOFF_S[0]]
    assert len(ports.tracker.dispatch_calls) == 1


async def test_a_permanent_refusal_of_the_dispatch_publish_names_the_ref_and_git_and_no_dispatch():
    stderr = "remote: permission denied to workflow {x}"
    ports = dispatching((run_of(job("unit"), job("web")),))
    calls = failing_dispatch_publish(ports, stderr)
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert len(calls) == 1 and ports.tracker.dispatch_calls == []
    assert "after 1 attempt(s) (permanent); git said: " in patch["escalated"]
    assert ci_push.scrub(stderr) in patch["escalated"]
    section = patch["sections"]["Full checks"]
    assert f"the push of `{DISPATCH_REF}` failed" in section and "push of `ci.yml`" not in section


@pytest.mark.parametrize("stage", ["checkout", "setup"])
async def test_a_run_whose_only_failing_job_failed_before_the_suite_is_ci_not_usable(stage):
    failed = CiCheck("web", "COMPLETED", "FAILURE", f"{LINK}/web", failed_stage=stage)
    ports = dispatching((run_of(job("unit"), failed),))
    state = state_of()
    patch = await run(state, ports)
    assert_escalated_ci(patch, ports, state)
    text = patch["escalated"]
    assert "checkout_failed" in text and stage in text
    assert DISPATCH_REF in text and FULL[:7] in text
    assert "returns" not in patch and "pairs" not in patch
    assert len(ports.tracker.dispatch_calls) == 1


async def test_a_suite_failure_stays_red_and_returns_the_integration():
    failed = CiCheck("web", "COMPLETED", "FAILURE", f"{LINK}/web", failed_stage="suite")
    ports = dispatching((run_of(job("unit"), failed),))
    patch = await run(state_of(), ports)
    assert patch["full_checks"]["passed"] is False


def test_every_ci_cause_has_its_own_dispatch_message_and_none_speaks_of_a_push():
    for cause in get_args(CiCause):
        assert cause in _DISPATCH_DETAIL, cause
        rendered = _DISPATCH_DETAIL[cause].format(
            ref="ci.yml", min=1.0, stage="checkout", dispatch_ref=DISPATCH_REF, sha="abc1234"
        )
        assert "push of ci.yml" not in rendered
    assert set(_DISPATCH_DETAIL) == set(get_args(CiCause))
