"""`landing_plan.spent` counts the GH-330 budgets off the document's own `landing` list: conflicting
syncs and verdict syncs per upstream sha inside the `sync_epoch`, `passes` from `re_review` and
`fix` only, and what the latest delivered diagnosis reads as."""

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.domain import ci, landing, rules
from agentic.core.process.domain.task import VERDICT_SYNC, VERDICT_UNLANDABLE
from tests.core.process.landing_fixtures import (
    gh320_conflicting_facts,
    gh320_slice_inputs,
)

HEAD = "a" * 40
MASTER = "b" * 40
OLDER = "c" * 40


def _sync(upstream: str | None, refusal: str = "merge_conflict", **more) -> dict:
    entry = {"head": HEAD, "step": "sync_master", "refusal": refusal, **more}
    return {**entry, "upstream": upstream} if upstream is not None else entry


def _diagnose(verdict: str | None = None, upstream: str | None = None, **more) -> dict:
    entry = {"head": HEAD, "step": "diagnose", "refusal": "merge_failed", "delivered": True}
    entry.update(more)
    if verdict is not None:
        entry["verdict"] = verdict
    if upstream is not None:
        entry["upstream"] = upstream
    return entry


def _spent(entries: list[dict], upstream: str = MASTER):
    return landing_plan.spent({"landing": entries}, HEAD, None, upstream)


@pytest.mark.parametrize("refusal", landing.CONFLICTING_SYNC_REFUSALS)
def test_a_conflicting_sync_is_counted_per_upstream_and_never_as_a_pass(refusal):
    spent = _spent([_sync(MASTER, refusal), _sync(OLDER, refusal)])
    assert spent.passes == 0
    assert spent.syncs == 2
    assert spent.conflicting_syncs_for_upstream == 1


def test_a_new_master_sha_starts_a_fresh_conflict_budget():
    entries = [_sync(OLDER), _sync(OLDER)]
    assert (
        _spent(entries, OLDER).conflicting_syncs_for_upstream == landing.LANDING_CONFLICT_SYNCS_MAX
    )
    assert _spent(entries, MASTER).conflicting_syncs_for_upstream == 0
    assert _spent(entries, MASTER).syncs == 2


def test_a_clean_sync_counts_as_a_sync_but_not_as_a_conflicting_one():
    spent = _spent([_sync(MASTER, "attempts_exhausted")])
    assert (spent.syncs, spent.conflicting_syncs_for_upstream, spent.passes) == (1, 0, 0)


def test_the_passes_are_the_reviews_and_the_fixes_only():
    entries = [
        {"head": HEAD, "step": "re_review"},
        {"head": HEAD, "step": "fix"},
        {"head": HEAD, "step": "fix_important"},
        _sync(MASTER),
    ]
    assert _spent(entries).passes == 2


def test_the_sync_counts_restart_after_a_resolved_land_slice():
    slice_done = {"head": HEAD, "step": "land_slice", "resolved_at": "t"}
    spent = _spent([_sync(MASTER), slice_done, _sync(MASTER)])
    assert (spent.syncs, spent.conflicting_syncs_for_upstream) == (1, 1)


def test_a_task_with_no_diagnosis_reads_none():
    spent = _spent([_sync(MASTER)])
    assert (spent.diagnoses, spent.last_verdict, spent.verdict_sync_due) == (0, None, False)
    assert not spent.unlandable


def test_a_sync_verdict_for_the_current_sha_is_due_until_a_sync_follows_it():
    spent = _spent([_diagnose(VERDICT_SYNC, MASTER)])
    assert (spent.diagnoses, spent.last_verdict) == (1, VERDICT_SYNC)
    assert spent.verdict_sync_due and not spent.unlandable
    assert spent.verdict_syncs_for_upstream == 0
    served = _spent([_diagnose(VERDICT_SYNC, MASTER), _sync(MASTER)])
    assert not served.verdict_sync_due
    assert served.verdict_syncs_for_upstream == 1
    assert served.last_verdict == VERDICT_SYNC


def test_a_sync_verdict_for_another_sha_is_not_due():
    spent = _spent([_diagnose(VERDICT_SYNC, OLDER)])
    assert not spent.verdict_sync_due
    assert spent.last_verdict == VERDICT_SYNC


def test_an_earlier_sync_does_not_serve_a_later_verdict():
    spent = _spent([_sync(MASTER), _diagnose(VERDICT_SYNC, MASTER)])
    assert spent.verdict_sync_due


def test_a_second_diagnosis_for_a_served_sha_reads_as_unlandable_whatever_it_says():
    entries = [_diagnose(VERDICT_SYNC, MASTER), _sync(MASTER), _diagnose(VERDICT_SYNC, MASTER)]
    spent = _spent(entries)
    assert spent.last_verdict == VERDICT_UNLANDABLE
    assert spent.unlandable and not spent.verdict_sync_due
    assert spent.diagnoses == 2


def test_a_verdict_sync_closed_with_lacks_makes_the_next_diagnosis_unlandable():
    closed = _sync(MASTER, lacks=MASTER, closed_at="t")
    entries = [_diagnose(VERDICT_SYNC, MASTER), closed, _diagnose(VERDICT_SYNC, MASTER)]
    spent = _spent(entries)
    assert spent.unsynced_for_upstream == 1
    assert spent.last_verdict == VERDICT_UNLANDABLE


def test_a_new_master_sha_gives_a_new_verdict_sync():
    entries = [_diagnose(VERDICT_SYNC, OLDER), _sync(OLDER), _diagnose(VERDICT_SYNC, MASTER)]
    spent = _spent(entries)
    assert spent.last_verdict == VERDICT_SYNC and spent.verdict_sync_due
    assert spent.verdict_syncs_for_upstream == 0


def test_a_sync_verdict_with_no_sha_to_sync_to_reads_as_unlandable():
    assert _spent([_diagnose(VERDICT_SYNC)]).last_verdict == VERDICT_UNLANDABLE


@pytest.mark.parametrize("verdict", [VERDICT_UNLANDABLE, None])
def test_an_unlandable_or_unmarked_diagnosis_is_unlandable(verdict):
    spent = _spent([_diagnose(verdict, MASTER)])
    assert (spent.diagnoses, spent.last_verdict, spent.unlandable) == (1, VERDICT_UNLANDABLE, True)


def test_only_a_delivered_diagnosis_counts():
    attempt = _diagnose(failure="account_limit", delivered=False)
    spent = _spent([attempt])
    assert (spent.diagnoses, spent.last_verdict, spent.unlandable) == (0, None, False)


def test_the_document_before_verdicts_gets_the_step_it_got():
    """No `verdict`, no `upstream` on a diagnose or a sync: the step table reads as before."""
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
        current_master=MASTER,
        conflicts=True,
    )
    legacy = {"id": "GH-9", "title": "", "description": ""}
    diagnosed = {**legacy, "landing": [_diagnose()]}
    assert landing_plan.step_for(diagnosed, "merge_conflict", facts).kind == "none"
    syncing = {**legacy, "landing": [_sync(None)]}
    assert landing_plan.step_for(syncing, "merge_conflict", facts).kind == "sync_master"
    assert landing_plan.pending(syncing, "merge_conflict", facts, landing_plan.LIVE_STEPS) is None


def test_the_gh320_shape_is_a_diagnose_for_no_clean_slice_until_a_sync_verdict_is_due():
    facts, document = gh320_conflicting_facts()
    assert facts.conflicts is True
    assert facts.ci_waited_s is not None and facts.ci_waited_s >= ci.CI_WAIT_MAX_S
    assert landing.restart_depth(document) == 0
    inputs = gh320_slice_inputs()
    assert landing.slice_or_restart(inputs) == "no_clean_slice"
    assert landing.integration_blocked(inputs)
    spent = landing_plan.spent(document, HEAD, facts.ci_waited_s, MASTER)
    assert spent.conflicting_syncs_for_upstream == landing.LANDING_CONFLICT_SYNCS_MAX
    assert spent.passes == 0
    step = landing_plan.step_for(
        document, "merge_conflict", facts, inputs_slicing := "no_clean_slice"
    )
    assert (step.kind, step.restart_why) == ("diagnose", inputs_slicing)
    due = {**document, "landing": [*document["landing"], _diagnose(VERDICT_SYNC, MASTER)]}
    step = landing_plan.step_for(due, "merge_conflict", facts, "no_clean_slice")
    assert step.kind == "sync_master"
