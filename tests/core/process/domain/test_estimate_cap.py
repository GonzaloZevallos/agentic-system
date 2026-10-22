"""The safety cap at twice the estimate (ADR-0054), pure: when it is reached, what the
stopped task proposes as the new estimate, and how a decided resume revises the estimate."""

import pytest

from agentic.core.process.domain import rules


def state(**more):
    return {"id": "T-1", "estimate": {"cost_usd": 40.0, "files": 10}, **more}


def run(section, cost):
    return {"role": "Developer", "section": section, "cost_usd": cost}


def test_the_cap_is_reached_at_twice_the_estimate_and_not_before():
    assert rules.estimate_cap_reached(80.0, state())
    assert not rules.estimate_cap_reached(79.0, state())


def test_a_document_without_an_estimate_is_never_stopped_by_it():
    # From before estimates: "no estimate", never zero (which would stop at the first dollar).
    assert not rules.estimate_cap_reached(10_000.0, {"id": "T-1"})


def test_the_estimate_in_force_governs_the_cap_not_the_plans_own():
    revised = state(estimate_revisions=[{"cost_usd": 100.0, "source": "human"}])
    assert not rules.estimate_cap_reached(150.0, revised)
    assert rules.estimate_cap_reached(200.0, revised)


def test_reestimate_is_spent_plus_pending_subtasks_at_the_mean_of_the_approved():
    s = state(
        cost_usd=60.0,
        subtasks={
            "a": {"id": "a", "status": "approved"},
            "b": {"id": "b", "status": "approved"},
            "c": {"id": "c", "status": "developing"},
            "d": {"id": "d", "status": "pending"},
        },
        runs=[
            run("Developer: a", 10.0),
            run("Tech Lead: review a", 5.0),
            run("Developer: b", 20.0),
            run("Tech Lead: review b", 5.0),
            run("Developer: c", 25.0),
        ],
    )
    # Approved cost 20 each (a: 15, b: 25), two pending: 60 + 2 * 20.
    assert rules.reestimate(s, history_median=999.0) == pytest.approx(100.0)


def test_reestimate_falls_back_to_the_bucket_median_when_no_subtask_finished():
    s = state(
        cost_usd=80.0,
        subtasks={"a": {"id": "a", "status": "developing"}, "b": {"id": "b", "status": "pending"}},
    )
    assert rules.reestimate(s, history_median=30.0) == pytest.approx(80.0 + 2 * 30.0)


def test_reestimate_without_a_subtask_or_a_median_doubles_what_was_spent():
    assert rules.reestimate(state(cost_usd=80.0), history_median=None) == pytest.approx(160.0)
    s = state(cost_usd=80.0, subtasks={"a": {"id": "a", "status": "developing"}})
    assert rules.reestimate(s, history_median=None) == pytest.approx(160.0)


def test_the_reason_names_what_was_spent_the_cap_what_is_left_and_the_new_estimate():
    reason = rules.estimate_cap_reached_reason("Developer", 82.5, 80.0, 3, 190.0)
    assert reason == (
        "Developer stopped by the estimate cap: 82.50 of 80.00 USD spent, "
        "3 subtask(s) left, proposed estimate 190.00 USD"
    )
    # Never read as the flat task cap: that phrase is the ADR-0004 cap's alone.
    assert rules.stopped_by_cap(state(**rules.escalation("estimate", reason))) is False
    assert rules.stopped_by_cap({"escalated": reason}) is False


def stopped(**more):
    return state(
        cost_usd=90.0,
        proposed_estimate=150.0,
        **rules.escalation("estimate", "stopped"),
        **more,
    )


def test_resuming_with_a_decision_puts_the_proposal_in_force_and_keeps_the_plans_estimate():
    patch = rules.resumed(stopped(), "Go on.")
    assert patch["estimate_revisions"] == [{"cost_usd": 150.0, "source": "proposed"}]
    assert patch["proposed_estimate"] is None
    assert "estimate" not in patch


def test_resuming_with_a_flag_puts_the_humans_value_in_force():
    patch = rules.resumed(stopped(), "Go on.", estimate=120.0)
    assert patch["estimate_revisions"] == [{"cost_usd": 120.0, "source": "human"}]
    assert patch["proposed_estimate"] is None


def test_a_revision_is_appended_to_the_earlier_ones():
    earlier = [{"cost_usd": 60.0, "source": "human"}]
    patch = rules.resumed(stopped(estimate_revisions=earlier), "Go on.")
    assert [r["cost_usd"] for r in patch["estimate_revisions"]] == [60.0, 150.0]


def test_without_a_decision_the_estimate_is_not_revised():
    # Invariant 7: the revisions grow only on a resumption with the human's decision.
    assert "estimate_revisions" not in rules.resumed(stopped(), None)
    assert "estimate_revisions" not in rules.resumed(stopped(), "   ")


def test_another_escalation_never_revises_the_estimate():
    cap = state(proposed_estimate=150.0, **rules.escalation("cap", "cap"))
    assert "estimate_revisions" not in rules.resumed(cap, "Go on.")
