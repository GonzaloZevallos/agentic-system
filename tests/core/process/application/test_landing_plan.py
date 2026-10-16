"""`landing_plan.spent` (GH-239, GH-255): the budgets counted off the document's own
`landing` list. A `sync_master` that answered a conflict is a model pass; every other one is
a clean sync. Pure: a document in, the counts out."""

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.domain import landing, rules

HEAD = "a" * 40


def _sync(refusal: str) -> dict:
    return {"head": HEAD, "step": "sync_master", "refusal": refusal}


@pytest.mark.parametrize("refusal", landing.CONFLICTING_SYNC_REFUSALS)
def test_a_conflicting_sync_counts_as_a_pass_and_never_as_a_clean_sync(refusal):
    spent = landing_plan.spent({"landing": [_sync(refusal)]}, HEAD, None)
    assert (spent.passes, spent.syncs) == (1, 0)


def test_a_sync_answering_anything_else_is_a_clean_one():
    spent = landing_plan.spent({"landing": [_sync("attempts_exhausted")]}, HEAD, None)
    assert (spent.passes, spent.syncs) == (0, 1)


def test_the_loop_of_three_conflicting_syncs_reaches_the_diagnose():
    entries = [_sync("merge_conflict"), _sync("checks_failed"), _sync("merge_conflict")]
    raw = {"landing": entries}
    spent = landing_plan.spent(raw, HEAD, None)
    assert (spent.passes, spent.syncs) == (3, 0)
    facts = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master="b" * 40,
        conflicts=True,
    )
    step = landing_plan.step_for(
        {"id": "GH-9", "title": "", "description": "", **raw}, "merge_conflict", facts
    )
    assert step.kind == "diagnose"


def _diagnose(**more) -> dict:
    return {"head": HEAD, "step": "diagnose", "refusal": "merge_failed", **more}


def test_only_a_delivered_diagnosis_counts_as_diagnosed():
    attempt = _diagnose(failure="account_limit")
    assert not landing_plan.spent({"landing": [attempt]}, HEAD, None).diagnosed
    assert landing_plan.spent(
        {"landing": [attempt, _diagnose(delivered=True)]}, HEAD, None
    ).diagnosed


def test_a_redelivery_keeps_the_diagnosis_counted():
    resolved = landing.resolve_open([_diagnose(delivered=True)], "2026-10-15T13:00:00+00:00", 3.0)
    assert landing_plan.spent({"landing": resolved}, HEAD, None).diagnosed


def test_an_undelivered_diagnose_entry_is_not_a_dispatched_step():
    assert not landing.already_dispatched([_diagnose(failure="sdk")], HEAD, "diagnose")
    assert landing.already_dispatched([_diagnose(delivered=True)], HEAD, "diagnose")
    assert landing.already_dispatched([{**_sync("x"), "step": "fix"}], HEAD, "fix")


def test_the_gate_skips_a_retry_that_is_not_due_and_an_escalation_already_made():
    from datetime import UTC, datetime, timedelta

    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    raw = {"landing": [_diagnose(failure="account_limit", at=at.isoformat())]}
    wait = timedelta(minutes=rules.ACCOUNT_LIMIT_SCHEDULE[0])
    assert landing_plan.diagnose_gate(raw, "diagnose", at + wait - timedelta(seconds=1)) == "skip"
    assert landing_plan.diagnose_gate(raw, "diagnose", at + wait) == "go"
    assert landing_plan.diagnose_gate(raw, "diagnose", None) == "skip"
    assert landing_plan.diagnose_gate(raw, "fix", None) == "go"
    sdk = {"landing": [_diagnose(failure="sdk", at=at.isoformat())]}
    assert landing_plan.diagnose_gate(sdk, "diagnose", at) == "escalate"
    assert landing_plan.diagnose_gate({**sdk, "escalated": "x"}, "diagnose", at) == "skip"
