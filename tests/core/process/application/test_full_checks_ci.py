"""The `full_checks` node with `ci:` declared (GH-376): the integrated head is published to its
own ref, the node waits for the declared jobs on exactly that head and records their verdict,
and any way the CI cannot be used runs the local tier with the cause written down. Fake ports:
`MemoryTracker.branch_reports` scripts what the CI says, `MemoryWorkspace` records the refs."""

from __future__ import annotations

import pytest

from agentic.core.process.application.checkouts import task_base
from agentic.core.process.application.full_checks import full_checks
from agentic.core.process.application.ports import CommandResult, GitError
from agentic.core.process.domain import rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.core.process.domain.presubmit import CiCover
from agentic.core.process.domain.roles import DEVELOPER_INTEGRATOR
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryCheckResults,
    MemoryCommands,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    memory_ports,
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
    return await full_checks(state, CFG, ports, sleep=clock.sleep, clock=clock)


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
    assert patch["pause_log"] == [{"kind": "escalation", "value": "ci"}]
    if state is not None:
        assert rules.auto_resumable(apply(state, patch)) is None


async def test_a_report_of_another_head_is_never_accepted_and_escalates_as_no_run():
    ports = ports_of(report(job("unit"), job("web"), sha="f" * 40))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    # Two grace periods (30 s each), not the 100 s check timeout.
    assert sum(clock.slept) == 60
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("republish", REF)]
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
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("republish", REF)]
    assert clock.now == 2 * CFG.ci_start_grace_s < CFG.check_timeout_s
    assert ports.commands.ran == []


async def test_a_run_that_appears_after_the_republish_is_awaited_and_recorded_green():
    nothing = report(sha="f" * 40)
    ports = ports_of(nothing, nothing, GREEN)
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    record = patch["full_checks"]
    assert record["passed"] is True and record["ran_on"] == "ci"
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("republish", REF)]
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


async def test_a_failed_republish_escalates_as_publish_failed_and_never_retries():
    class Flaky(MemoryWorkspace):
        def republish_ci_ref(self, path, ref):
            super().republish_ci_ref(path, ref)
            raise GitError("push refused")

    ports = ports_of(report(sha="f" * 40), workspace=Flaky())
    patch = await run(state_of(), ports)
    assert_escalated_ci(patch, ports, state_of())
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("republish", REF)]
    assert f"push of {REF} failed" in patch["escalated"]
    assert ports.commands.ran == []


async def test_an_unreadable_ci_past_the_grace_keeps_waiting_and_never_republishes():
    ports = ports_of(CiUnreadable("gh_unavailable"))
    clock = Clock()
    patch = await run(state_of(), ports, clock)
    assert_escalated_ci(patch, ports, state_of())
    assert clock.now >= CFG.check_timeout_s
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
