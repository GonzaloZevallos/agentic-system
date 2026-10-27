"""GH-371, subtask merge-wiring: the plan's required checks reach every place that builds
`MergeFacts` -- the merge verb, `poll.with_ci` and the slice landing -- through
`rules.ci_gate_names`, on in-memory ports and a fake tracker."""

from __future__ import annotations

import dataclasses

from agentic.core.process.application import landing_plan, merge
from agentic.core.process.application.poll import (
    MergeAssessment,
    assess_merge,
    ci_wait_line,
    with_ci,
)
from agentic.core.process.domain import rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, ci_state
from tests.core.process.application.test_landing_fix import _land
from tests.core.process.application.test_landing_slice import SLICE_HEAD, SLICE_URL
from tests.core.process.application.test_landing_slice import _doc as _slice_doc
from tests.core.process.application.test_landing_slice import _land as _slice_land
from tests.core.process.application.test_landing_slice import _world as _slice_ports
from tests.core.process.application.test_merge_ci import (
    _ago,
    _check,
    _doc,
    _report,
    _run,
    _world,
)
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL
from tests.fakes import FakeLauncher


def _blank() -> rules.MergeFacts:
    return rules.MergeFacts(
        task_id="GH-5",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(),
        attempts=0,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
    )


def _red_web_world(base: CiReport | CiUnreadable):
    """The #432 shape: the stack declares no CI, the plan requires `web` and `e2e`, local checks
    pass, the head's `web` is red and `base` is what the base branch's latest report says."""
    red = _report(_check("web", "FAILURE", run=7), _check("e2e", "SUCCESS", run=8))
    ports = _world(
        red,
        declared=(),
        ci_required=["web", "e2e"],
        merge_checks={"head": HEAD, "master": MASTER, "passed": True},
    )
    ports.tracker.branch_reports["master"] = base
    return ports


def _step(ports) -> str:
    raw = ports.documents.load("GH-5") or {}
    assessed = assess_merge(ports, "GH-5", raw, 5, frozenset(), MASTER)
    assert isinstance(assessed, MergeAssessment)
    assert assessed.decision == rules.MergeDecision("refuse", "ci_failed")
    return landing_plan.step_for(raw, "ci_failed", assessed.facts).kind


def test_a_plan_required_check_red_on_the_head_and_the_base_is_refused_and_lands_as_escalate(
    tmp_path,
):
    base = _report(_check("web", "FAILURE", run=9))
    ports = _red_web_world(base)

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_failed")
    assert ports.tracker.merge_calls == [] and ports.workspace.detached == []
    assert ports.tracker.branch_reads == ["master"]
    assert _step(ports) == "escalate"

    landed, _ = _land(_red_web_world(base), tmp_path, FakeLauncher())
    assert landed.kind == "dispatched" and landed.step == "escalate"


def test_a_plan_required_check_red_on_the_head_and_green_on_the_base_lands_as_rerun(tmp_path):
    ports = _red_web_world(_report(_check("web", "SUCCESS", run=9)))

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_failed")
    assert ports.tracker.merge_calls == []
    assert _step(ports) == "rerun"


def test_an_unreadable_base_report_proves_nothing_and_the_step_stays_rerun(tmp_path):
    ports = _red_web_world(CiUnreadable("gh_unreadable"))

    result, _ = _run(ports, tmp_path)

    assert result.reason == "ci_failed"
    assert _step(ports) == "rerun"


def test_the_base_report_is_read_only_when_the_head_is_red(tmp_path):
    green = _report(_check("web"), _check("e2e", run=2))
    ports = _world(green, declared=(), ci_required=["web", "e2e"])

    _run(ports, tmp_path)

    assert ports.tracker.branch_reads == []


def test_a_green_fresh_ci_over_plan_only_names_never_merges_on_the_ci_alone(tmp_path):
    ports = _world(_report(_check("web")), declared=(), ci_required=["web"])

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    assert ports.tracker.ci_reads != []
    assert len(ports.workspace.detached) == 1
    assert [command for _, command in ports.commands.ran] == ["run tests"]
    document = _doc(ports)
    assert document["auto_merge_via"] == "local" and "auto_merge_ci" not in document


def test_a_task_with_no_required_names_and_no_stack_ci_reads_no_tracker_ci(tmp_path):
    ports = _world(declared=())

    result, _ = _run(ports, tmp_path)

    assert result.kind == "merged"
    assert "auto_merge_via" not in _doc(ports)
    raw = ports.documents.load("GH-5") or {}
    assessed = assess_merge(ports, "GH-5", raw, 5, frozenset(), MASTER)
    assert isinstance(assessed, MergeAssessment)
    assert assessed.facts.ci_required == () and assessed.facts.ci_plan_only == ()
    assert ports.tracker.ci_reads == [] and ports.tracker.branch_reads == []


def test_with_ci_joins_the_stack_names_and_the_plan_names_through_the_gate_rule():
    raw = {"ci_required": ["e2e", "web"], "pushed_head": HEAD, "pushed_at": _ago(10)}
    report = _report(_check("web"), _check("e2e", "FAILURE", run=2))

    joined = with_ci(_blank(), raw, ("web",), report)
    assert joined.ci_required == ("web", "e2e") and joined.ci_plan_only == ("e2e",)
    assert joined.ci_verdict == ci_state(report, ("web", "e2e"))

    only_plan = with_ci(_blank(), raw, (), report)
    assert only_plan.ci_required == ("e2e", "web") and only_plan.ci_plan_only == ("e2e", "web")


def test_with_ci_without_stack_names_or_plan_names_is_the_facts_unchanged():
    facts = _blank()

    assert with_ci(facts, {}, (), None) is facts
    assert with_ci(facts, {"ci_required": []}, (), _report(_check())) is facts


def test_ci_missing_after_the_wait_lists_what_never_reported_and_what_the_report_carried(
    tmp_path,
):
    ports = _world(
        _report(_check("other"), _check("lint", run=3)),
        declared=(),
        ci_required=["web", "e2e"],
        pushed_head=HEAD,
        pushed_at=_ago(4000),
    )

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_missing")
    [comment] = [c for _, c in ports.tracker.comments if c.startswith("Auto-merge refused:")]
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} ci_missing"
    assert "CI did not report: web, e2e" in comment
    assert "the report carried: other, lint" in comment


def test_the_poller_waiting_line_names_the_missing_and_the_carried_names_for_a_plan_gate():
    report = _report(_check("other"))
    facts = dataclasses.replace(
        _blank(),
        ci_required=("web",),
        ci_plan_only=("web",),
        ci_verdict=ci_state(report, ("web",)),
        ci_waited_s=60,
    )

    assert ci_wait_line(5, facts, report) == (
        "agentic: poll: auto-merge #5 -> waiting: ci missing web, the report carried other "
        "(60s of 3600)"
    )


def _slice_world():
    """The slice world whose stack declares no CI and whose plan requires `web`. The main pull
    request's `web` is green; the first tick opens the slice."""
    ports = _slice_ports(frozenset({"integration.py"}))
    ports.documents.save({**(ports.documents.load("GH-5") or {}), "ci_required": ["web"]})
    _slice_reports(ports, None)
    _slice_land(ports)
    return ports


def _slice_reports(ports, conclusion: str | None) -> None:
    """The slice's pull request reports `web` as `conclusion` (None: still running)."""
    status = "COMPLETED" if conclusion else "IN_PROGRESS"
    checks = (CiCheck("web", status, conclusion, "link"),)
    ports.tracker.ci_reports[SLICE_URL] = CiReport(
        SLICE_HEAD, None, None, "2026-10-22T00:00:00Z", checks
    )
    main = (CiCheck("web", "COMPLETED", "SUCCESS", "link"),)
    ports.tracker.ci_reports[URL.format(5)] = CiReport(
        HEAD, "master", MASTER, "2026-10-22T00:00:00Z", main
    )


def test_a_slice_waits_for_a_check_only_the_plan_required():
    ports = _slice_world()
    ports.tracker.ci_reads.clear()

    _slice_land(ports)

    assert SLICE_URL in ports.tracker.ci_reads
    assert ports.tracker.merge_calls == [] and ports.tracker.closed_pull_requests == []
    assert "resolved_at" not in _slice_doc(ports)["landing"][-1]


def test_a_slice_whose_plan_required_check_is_red_is_closed_and_not_merged():
    ports = _slice_world()
    _slice_reports(ports, "FAILURE")

    _slice_land(ports)

    assert ports.tracker.closed_pull_requests == [SLICE_URL] and ports.tracker.merge_calls == []


def test_a_slice_with_no_required_names_and_no_stack_ci_reads_no_tracker_ci():
    ports = _slice_ports(frozenset({"integration.py"}))

    _slice_land(ports)

    assert ports.tracker.ci_reads == []
    assert ports.tracker.merge_calls == [(SLICE_URL, SLICE_HEAD)]
