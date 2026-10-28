"""GH-406, subtask landing-readers: a task restored from its archive lands on its own live
entries. The shape is apostrophe GH-374's: an approved, delivered task whose archived run left a
delivered `diagnose` at the head and twenty open `PM: final validation` plan rows, its pull request
reopened and conflicting with master. Every refusal comes from `rules.auto_merge_decision` over the
fixture's facts, never from the test."""

from dataclasses import replace
from datetime import datetime

from agentic.core.process.application import landing_plan, merge
from agentic.core.process.domain import ci, landing, plan_settlement, rules
from agentic.core.process.domain.landing import LandingStep
from agentic.core.process.domain.roles import PAIR_PM_TECH_LEAD, PM_FINAL
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryStack, MemoryTracker, memory_ports

PM = PM_FINAL.name
HEAD = "e9c47772" + "0" * 32
MASTER = "b" * 40
NEW_MASTER = "c" * 40
DIAGNOSE = {
    "head": HEAD,
    "step": "diagnose",
    "refusal": "merge_failed",
    "delivered": True,
    "verdict": "unlandable",
    "at": "2026-10-20T00:00:00+00:00",
}


def _row(claim: str, version: int) -> dict:
    return {
        "subtask": "integration",
        "version": version,
        "location": PM,
        "claim": claim,
        "severity": "blocking",
        "scope": "plan",
        "category": "implementation",
        "status": "open",
    }


def document(*, landing_entries=(DIAGNOSE,), cut: int | None = 1, **more) -> dict:
    """The restored document: the archived entries first, the restoration cutting after them."""
    runs = [{"role": "PM", "section": PM, "version": v} for v in (1, 2, 3)]
    history = [{"pair": PAIR_PM_TECH_LEAD.key, "category": "implementation", "reason": "x"}] * 2
    base: dict = {
        "id": "GH-374",
        "type": "feature",
        "flags": {},
        "status": "ready_for_review",
        "subtasks": {"integration": {"status": "approved", "reviewed_head": HEAD}},
        "runs": runs,
        "change_history": history,
        "findings": [_row("a", 1), _row("b", 2)],
        "landing": list(landing_entries),
    }
    if cut is not None:
        base["restorations"] = [
            {
                "archive": "archive/run1-93e1891",
                "head": HEAD,
                "upstream": MASTER,
                "at": "2026-10-28T00:00:00+00:00",
                "landing_cut": cut,
            }
        ]
    return {**base, **more}


def facts(state: dict, **over) -> rules.MergeFacts:
    """The facts of a reopened pull request that conflicts with master and has no CI."""
    open_blocking = sum(1 for row in rules.open_findings(state) if row["severity"] == "blocking")
    base = rules.MergeFacts(
        task_id="GH-374",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=open_blocking,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/x.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=NEW_MASTER,
        conflicts=True,
        ci_required=("test",),
        ci_head=None,
        ci_verdict=None,
    )
    return replace(base, **over)


def step(state: dict, **over) -> LandingStep:
    """The step for the refusal `rules.auto_merge_decision` gives over the facts."""
    f = facts(state, **over)
    decision = rules.auto_merge_decision(f)
    assert decision.reason is not None
    return landing_plan.step_for(state, decision.reason, f)


def test_a_diagnosis_before_the_restoration_does_not_end_the_landing():
    state = document()
    assert rules.auto_merge_decision(facts(state)).reason == "open_findings_blocking"
    assert step(state) == LandingStep("settle", "open_findings_blocking")


def test_after_the_settlement_the_conflict_is_synced_not_diagnosed_again():
    state = document()
    settled = {**state, "plan_settled": plan_settlement.pending(state)}
    assert step(settled) == LandingStep("sync_master", "merge_conflict")


def test_without_a_restoration_the_same_document_has_no_step():
    state = document(cut=None)
    assert step(state).kind == "none"


def test_a_diagnosis_delivered_after_the_restoration_still_ends_it():
    state = document(landing_entries=(DIAGNOSE, {**DIAGNOSE, "at": "2026-10-29T00:00:00+00:00"}))
    state["restorations"][0]["landing_cut"] = 1
    settled = {**state, "plan_settled": plan_settlement.pending(state)}
    assert step(settled).kind == "none"


def test_the_green_ci_is_not_needed_to_settle_but_the_merge_still_asks_for_it():
    state = document()
    green = {"ci_head": HEAD, "ci_verdict": ci.CiVerdict("green", (), ())}
    assert plan_settlement.settleable(facts(state), True)
    assert not plan_settlement.settleable(facts(state), False)
    assert plan_settlement.settleable(facts(state, **green), False)
    assert landing.LandingStep("settle", "open_findings_blocking") == step(state, **green)


def _gate_record(**over) -> dict:
    """A `merge_checks` record whose last row is the gate's own rerun and still red."""
    checks = [{"name": "test", "passed": False, "rerun": True}]
    return {"head": HEAD, "master": MASTER, "passed": False, "checks": checks, **over}


def _reran(state: dict) -> bool:
    return landing_plan.spent(state, HEAD, None, NEW_MASTER).checks_rerun_on_head


def test_a_red_gate_rerun_recorded_before_the_restoration_does_not_count():
    archived = document(merge_checks=_gate_record())
    assert not _reran(archived)
    before = document(merge_checks=_gate_record(at="2026-10-20T00:00:00+00:00"))
    assert not _reran(before)
    plain = {k: v for k, v in before.items() if k != "restorations"}
    assert _reran(plain)
    assert _reran({k: v for k, v in archived.items() if k != "restorations"})


def test_a_red_gate_rerun_recorded_after_the_restoration_counts():
    after = document(merge_checks=_gate_record(at="2026-10-28T01:00:00+00:00"))
    assert _reran(after)
    unreadable = document(merge_checks=_gate_record(at="yesterday"))
    assert not _reran(unreadable)


def _ran_checks(raw: dict) -> dict:
    """The `merge_checks` record `merge.run_checks` writes for `raw` over the fakes."""
    ports = memory_ports(
        tracker=MemoryTracker(),
        stack=MemoryStack(checks={"tests": "pytest"}),
        commands=MemoryCommands(),
    )
    ports.workspace.trees[ports.workspace.checkout_path(merge._name("GH-374"))] = "tree"
    ports.documents.save({"id": "GH-374", "status": "ready_for_review"})
    cfg = Configuration(product_dir=ports.workspace.ROOT)
    merge.run_checks("GH-374", raw, HEAD, NEW_MASTER, "base", cfg, ports, None, lambda _: None)
    return ports.documents.load("GH-374")["merge_checks"]


def test_only_the_checks_of_a_restored_task_are_dated_so_the_landing_can_tell_them_apart():
    restored = _ran_checks(document())
    assert _aware_after(restored["at"], "2026-10-28T00:00:00+00:00")
    assert "at" not in _ran_checks(document(cut=None))


def _aware_after(written: str, restored: str) -> bool:
    return datetime.fromisoformat(written) >= datetime.fromisoformat(restored)
