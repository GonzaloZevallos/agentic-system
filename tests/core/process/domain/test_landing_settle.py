"""GH-374, subtask landing-settle-guard: a refusal that is only `PM: final validation` ledger
state on an approved, green head is settled or escalated, never fixed, sliced or restarted.
Pure: facts, state and spent budgets in, a step out."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import ci, landing, plan_settlement, rules
from agentic.core.process.domain.landing import LandingSpent, LandingStep
from agentic.core.process.domain.roles import PAIR_PM_TECH_LEAD, PM_FINAL

PM = PM_FINAL.name
HEAD = "e9c47772" + "0" * 32
OTHER = "f" * 40
MASTER = "b" * 40
REFUSAL: rules.MergeRefusal = "open_findings_blocking"


def rec(location, claim, version, *, subtask="integration", scope="plan", severity="blocking"):
    return {
        "subtask": subtask,
        "version": version,
        "location": location,
        "claim": claim,
        "severity": severity,
        "scope": scope,
        "category": "implementation",
        "status": "open",
    }


def state(*, approved=True, extra=(), **more):
    runs = [{"role": "PM", "section": PM, "version": v} for v in (1, 2, 3)]
    history = [{"pair": PAIR_PM_TECH_LEAD.key, "category": "implementation", "reason": "x"}] * 2
    base = {
        "type": "feature",
        "flags": {},
        "subtasks": {"integration": {"status": "approved", "reviewed_head": HEAD}},
        "runs": runs if approved else runs[:2],
        "change_history": history,
        "findings": [rec(PM, "a", 1), rec(PM, "b", 2), *extra],
    }
    return {**base, **more}


def facts(**over) -> rules.MergeFacts:
    base = rules.MergeFacts(
        task_id="GH-374",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=2,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/x.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("test",),
        ci_head=HEAD,
        ci_verdict=ci.CiVerdict("green", (), ()),
    )
    return replace(base, **over)


def spent(**over) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)
    return replace(base, **over)


def step_for(s, f=None, refusal=REFUSAL, spent_=None, escalated=False, slicing=None):
    f = f or facts()
    return landing.landing_step(
        refusal,
        f,
        spent_ or spent(),
        landing.blocking_owners(s),
        slicing=slicing,
        ledger_only=plan_settlement.ledger_only(refusal, f, s),
        pending=plan_settlement.pending(s),
        escalated=escalated,
    )


# --- approved_green ----------------------------------------------------------------------------


def test_approved_green_needs_the_approved_reviewed_head_and_a_green_ci_on_it():
    assert plan_settlement.approved_green(facts())
    assert not plan_settlement.approved_green(facts(reviewed_unit_approved=False))
    assert not plan_settlement.approved_green(facts(reviewed_head=OTHER))
    assert not plan_settlement.approved_green(facts(pr_head="e9c47772"))
    assert not plan_settlement.approved_green(facts(ci_verdict=ci.CiVerdict("red", (), ())))
    assert not plan_settlement.approved_green(facts(ci_verdict=None))
    assert not plan_settlement.approved_green(facts(ci_head=OTHER))


def test_a_product_without_declared_ci_can_be_approved_green():
    assert plan_settlement.approved_green(facts(ci_required=(), ci_verdict=None, ci_head=None))


# --- ledger_only -------------------------------------------------------------------------------


def test_ledger_only_is_the_gh374_shape_and_nothing_else():
    assert plan_settlement.ledger_only(REFUSAL, facts(), state())
    assert not plan_settlement.ledger_only("checks_failed", facts(), state())
    assert not plan_settlement.ledger_only(REFUSAL, facts(reviewed_head=OTHER), state())
    assert not plan_settlement.ledger_only(REFUSAL, facts(ci_verdict=None), state())


def test_a_subtask_or_other_location_blocker_is_not_ledger_only():
    owned = state(extra=[rec("agentic/x.py", "bug", 1, scope="subtask", subtask="api")])
    assert not plan_settlement.ledger_only(REFUSAL, facts(), owned)
    perf = state(extra=[rec("QA Performance", "slow", 1)])
    assert not plan_settlement.ledger_only(REFUSAL, facts(), perf)


# --- landing_step ------------------------------------------------------------------------------


def test_the_gh374_shape_settles_and_then_the_refusal_is_gone():
    s = state()
    assert step_for(s) == LandingStep("settle", REFUSAL)
    settled = {**s, "plan_settled": plan_settlement.pending(s)}
    assert [r for r in rules.open_findings(settled) if r["severity"] == "blocking"] == []
    assert plan_settlement.pending(settled) == []


def test_without_a_live_approval_it_escalates_once_then_is_none_on_the_same_head():
    s = state(approved=False)
    assert plan_settlement.pending(s) == []
    assert step_for(s) == LandingStep("escalate", REFUSAL)
    assert step_for(s, escalated=True) == LandingStep("none", REFUSAL)


def test_a_subtask_blocking_row_among_them_is_a_fix_as_before():
    s = state(extra=[rec("agentic/x.py", "bug", 1, scope="subtask", subtask="integration")])
    assert step_for(s) == LandingStep("fix", REFUSAL, ("integration",))


@pytest.mark.parametrize("slicing", [None, "no_clean_slice", landing.Slice(("a", "b"))])
def test_an_exhausted_landing_under_ledger_only_never_slices_or_diagnoses(slicing):
    s = state(approved=False)
    full = spent(passes=landing.LANDING_PASSES_MAX)
    assert step_for(s, spent_=full, slicing=slicing).kind == "escalate"
    # A verdict sync that the budgets refuse is an exhaustion too.
    due = spent(
        diagnoses=1,
        last_verdict="sync",
        verdict_sync_due=True,
        syncs=landing.LANDING_SYNCS_MAX,
    )
    assert step_for(s, spent_=due, slicing=slicing).kind == "escalate"


def test_after_diagnosis_never_restarts_a_ledger_only_refusal():
    assert landing.after_diagnosis(None, spent(), 0) == "restart"
    assert landing.after_diagnosis(None, spent(), 0, ledger_only=True) == "escalate"
    assert landing.after_diagnosis("unlandable", spent(), 0, ledger_only=True) == "escalate"


@pytest.mark.parametrize(
    ("refusal", "over", "budget"),
    [
        ("merge_conflict", {}, {"conflicting_syncs_for_upstream": 2}),
        ("checks_failed", {"failed_step": "conflict"}, {"syncs": landing.LANDING_SYNCS_MAX}),
        ("attempts_exhausted", {}, {"syncs": landing.LANDING_SYNCS_MAX}),
    ],
)
def test_other_exhausted_refusals_on_an_approved_green_head_keep_their_outputs(
    refusal, over, budget
):
    s = state()
    f = facts(**over)
    assert step_for(s, f, refusal, spent(**budget)).kind == "diagnose"
    assert step_for(s, f, refusal, spent(**budget), slicing=landing.Slice(("a",))).kind == (
        "land_slice"
    )


def test_a_ledger_only_flag_on_another_refusal_changes_nothing():
    f = facts(failed_step="conflict")
    got = landing.landing_step(
        "merge_conflict", f, spent(syncs=landing.LANDING_SYNCS_MAX), (), ledger_only=True
    )
    assert got.kind == "diagnose"


def test_the_new_steps_spend_no_model():
    assert landing.run_kind_of("settle", REFUSAL) == "land_checks"
    assert landing.run_kind_of("escalate", REFUSAL) == "land_checks"
