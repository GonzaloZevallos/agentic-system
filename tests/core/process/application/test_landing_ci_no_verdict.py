"""GH-463, subtask landing-and-docs: the release of a `ci_no_verdict` escalation (once per
(head, upstream) pair), `released_step` after it, and `landing_fix.rerun` for a fresh run."""

from __future__ import annotations

from dataclasses import replace

from agentic.core.process.application import landing_fix, landing_plan
from agentic.core.process.application.landing_plan import LandingContext
from agentic.core.process.application.ports import IssueComment, IssueState, Upstream
from agentic.core.process.domain import ci, landing
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.gh374_shape import report, web
from tests.core.process.application.test_poll_merge import _ports
from tests.core.process.domain.test_landing import HEAD, MASTER, _facts
from tests.core.process.domain.test_landing_ci_no_verdict import AT, NEW_MASTER, _escalated, _pair
from tests.fakes import MemoryStack, MemoryTracker, memory_ports

CFG = Configuration(product_dir="/p")


def _world(*, comments=(), labels=(), base=None):
    ports = memory_ports(
        tracker=MemoryTracker(), stack=MemoryStack(checks={"tests": "t"}, ci=("web",))
    )
    if base is not None:
        ports.tracker.branch_reports["master"] = base
    ports.tracker.timestamped["GH-9"] = [
        IssueComment(text, "2026-11-03T08:00:00+00:00") for text in comments
    ]
    ports.tracker.states[9] = IssueState("OPEN", "", (), None, labels)
    ports.workspace.upstream_answer = Upstream("origin/master", NEW_MASTER, None)
    return ports


def _release(ports, raw=None, **kw):
    return landing_plan.release(ports, CFG, "GH-9", raw or _escalated(), 9, **kw)


def test_a_new_upstream_releases_the_escalation_with_no_decision():
    found = _release(_world(), head=HEAD, upstream=NEW_MASTER)
    assert (found.released, found.why, found.decision) == (True, "released_no_verdict", None)


def test_the_same_upstream_waits_even_when_the_base_is_green():
    green = report(web("SUCCESS"), head=HEAD)
    found = _release(_world(base=green), head=HEAD, upstream=MASTER)
    assert (found.released, found.why) == (False, "waiting_on_upstream")


def test_a_pair_already_released_for_that_upstream_waits():
    raw = _escalated()
    stamped = apply(raw, landing.released_no_verdict(raw, NEW_MASTER, AT))
    again = {**stamped, "escalated": "again", "escalation_class": "questions"}
    assert _release(_world(), again, upstream=NEW_MASTER).released is False


def test_an_owner_comment_releases_the_same_upstream_and_stamps_the_pair():
    ports = _world(comments=("Escalated: x", "go on"))
    raw = _escalated(cost_usd=1.0)
    found = _release(ports, raw, head=HEAD, upstream=MASTER)
    assert (found.released, found.why, found.decision) == (True, "released", "go on")
    patch = landing_plan.release_patch(raw, found, MASTER, AT)
    assert patch["merge_ci"][0]["released_at"] == AT
    assert patch["landing"][0]["released_at"] == AT


def test_the_released_step_is_a_wait_and_the_pair_is_persisted_by_the_patch():
    raw = _escalated()
    found = _release(_world(), raw, head=HEAD, upstream=NEW_MASTER)
    facts = replace(_facts(), current_master=NEW_MASTER)
    step = landing_plan.released_step(raw, found, facts, "ci_no_verdict", at=AT)
    assert step.kind == "wait"
    assert landing_plan.release_patch(raw, found, NEW_MASTER, AT)["merge_ci"]


def test_release_is_asked_for_the_escalate_step_of_this_shape_only():
    assert landing_plan.release_asked(_escalated(), "escalate")
    assert not landing_plan.release_asked({**_escalated(), "escalated": None}, "escalate")
    assert not landing_plan.release_asked({"escalated": "x", "landing": []}, "escalate")


def _context(ports, *, refusal="ci_failed", subject="tested", pairs=(), tmp_path=None):
    ports.documents.record_closure(
        "GH-9", {"pull_request": "https://github.com/o/r/pull/9", "merge_ci": list(pairs)}
    )
    verdict = ci.CiVerdict("red", (ci.CiCheck("web", "COMPLETED", "FAILURE", "u"),), ())
    facts = replace(_facts(), ci_subject=subject, ci_verdict=verdict, ci_required=("web",))
    return LandingContext(
        "GH-9",
        CFG,
        ports,
        landing.LandingStep("rerun", refusal),
        HEAD,
        Upstream("origin/master", MASTER, None),
        facts,
        [].append,
    )


def test_a_red_fresh_run_is_rerun_by_its_id_and_never_through_the_pull_request():
    ports = _ports(9)
    landing_fix.rerun(_context(ports, pairs=[_pair(outcome="red", run_id="777")]))
    assert ports.tracker.run_reruns == ["777"]
    assert ports.tracker.rerun_calls == []


def test_a_red_fresh_run_without_a_run_id_takes_no_rerun_step():
    ports = _ports(9)
    landing_fix.rerun(_context(ports, pairs=[_pair(outcome="red")]))
    assert ports.tracker.run_reruns == [] and ports.tracker.rerun_calls == []


def test_the_pull_request_run_is_rerun_for_the_pr_head_subject_only():
    ports = _ports(9)
    landing_fix.rerun(_context(ports, subject="pr_head", pairs=[_pair(run_id="777")]))
    assert ports.tracker.run_reruns == [] and len(ports.tracker.rerun_calls) == 1


def test_a_ci_product_never_runs_local_checks_on_the_checks_failed_path():
    ports = _ports(9)
    context = _context(ports, refusal="checks_failed", pairs=[_pair()])
    before = (ports.documents.load("GH-9") or {}).get("merge_checks")
    landing_fix.rerun(context)
    assert ports.tracker.run_reruns == [] and ports.tracker.rerun_calls == []
    assert (ports.documents.load("GH-9") or {}).get("merge_checks") == before
