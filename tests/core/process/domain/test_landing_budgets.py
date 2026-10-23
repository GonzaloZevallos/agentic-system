"""Per-upstream landing budgets (GH-330, ADR-0048): a conflicting sync has its own budget per
upstream sha and never touches `LANDING_PASSES_MAX`; a `sync` verdict of the diagnoser leads to
one more `sync_master` before any restart. Pure: facts and spent budgets in, a step out."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import landing, rules
from agentic.core.process.domain.landing import LandingSpent, LandingStep
from agentic.core.process.domain.task import VERDICT_SYNC, VERDICT_UNLANDABLE

HEAD = "a" * 40
MASTER = "b" * 40
OLDER = "c" * 40


def _facts(**over) -> rules.MergeFacts:
    base = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
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
    )
    return replace(base, **over)


def _spent(**over) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)
    return replace(base, **over)


def _step(refusal, spent=None, **facts) -> LandingStep:
    return landing.landing_step(refusal, _facts(**facts), spent or _spent(), ("api",))


def test_the_budgets_are_the_plans():
    assert landing.LANDING_CONFLICT_SYNCS_MAX == 2
    assert landing.LANDING_VERDICT_SYNCS_MAX == 1
    assert landing.LANDING_SYNCS_MAX == 6
    assert landing.LANDING_PASSES_MAX == 3


@pytest.mark.parametrize("refusal", ["merge_conflict", "checks_failed"])
def test_a_conflicting_sync_is_refused_at_the_per_sha_cap_not_at_the_pass_cap(refusal):
    over = {"failed_step": "conflict"} if refusal == "checks_failed" else {}
    full_passes = _spent(passes=landing.LANDING_PASSES_MAX)
    assert _step(refusal, full_passes, **over).kind == "sync_master"
    one = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX - 1)
    assert _step(refusal, one, **over).kind == "sync_master"
    capped = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX)
    assert _step(refusal, capped, **over).kind == "diagnose"


def test_a_clean_sync_is_not_held_to_the_conflict_cap():
    capped = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX)
    assert _step("attempts_exhausted", capped).kind == "sync_master"


def test_the_seventh_sync_of_any_kind_exhausts():
    last = _spent(syncs=landing.LANDING_SYNCS_MAX - 1)
    assert _step("merge_conflict", last).kind == "sync_master"
    spent = _spent(syncs=landing.LANDING_SYNCS_MAX)
    assert _step("merge_conflict", spent).kind == "diagnose"
    assert _step("attempts_exhausted", spent).kind == "diagnose"


def test_a_sync_closed_without_its_upstream_twice_exhausts():
    spent = _spent(unsynced_for_upstream=landing.LANDING_UNSYNCED_MAX)
    assert _step("merge_conflict", spent).kind == "diagnose"


DUE = {
    "diagnoses": 1,
    "last_verdict": VERDICT_SYNC,
    "verdict_sync_due": True,
}


@pytest.mark.parametrize(
    ("refusal", "over"),
    [
        ("merge_conflict", {}),
        ("ci_missing", {"ci_waited_s": 10_000.0}),
        ("merge_failed", {}),
        ("adr_number_taken", {}),
    ],
)
def test_a_due_verdict_sync_is_the_step_whatever_the_refusal(refusal, over):
    assert _step(refusal, _spent(**DUE), **over).kind == "sync_master"


def test_a_due_verdict_sync_is_exempt_from_the_conflict_cap_but_not_from_the_other_two():
    capped = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX, **DUE)
    assert _step("merge_conflict", capped) == LandingStep("sync_master", "merge_conflict")
    assert _step("merge_conflict", replace(capped, syncs=landing.LANDING_SYNCS_MAX)).kind == (
        "diagnose"
    )
    unsynced = replace(capped, unsynced_for_upstream=landing.LANDING_UNSYNCED_MAX)
    assert _step("merge_conflict", unsynced).kind == "diagnose"


def test_a_sync_verdict_already_served_does_not_hold_the_table():
    served = _spent(diagnoses=1, last_verdict=VERDICT_SYNC, verdict_syncs_for_upstream=1)
    assert _step("merge_conflict", served).kind == "sync_master"
    assert _step("open_findings_blocking", served).kind == "fix"


@pytest.mark.parametrize("last", [VERDICT_UNLANDABLE, None])
@pytest.mark.parametrize("refusal", ["merge_conflict", "merge_failed", "ci_missing"])
def test_an_unlandable_or_unmarked_diagnosis_leaves_no_step(refusal, last):
    assert _step(refusal, _spent(diagnoses=1, last_verdict=last)).kind == "none"


def test_after_diagnosis_syncs_once_per_sha_then_restarts_or_escalates():
    fresh = _spent(diagnoses=1, verdict_sync_due=True)
    assert landing.after_diagnosis(VERDICT_SYNC, fresh, 0) == "sync_master"
    assert landing.after_diagnosis(VERDICT_SYNC, fresh, landing.RESTART_DEPTH_MAX) == "sync_master"
    served = _spent(diagnoses=2, verdict_syncs_for_upstream=landing.LANDING_VERDICT_SYNCS_MAX)
    assert landing.after_diagnosis(VERDICT_SYNC, served, 0) == "restart"
    assert landing.after_diagnosis(VERDICT_SYNC, served, landing.RESTART_DEPTH_MAX) == "escalate"


def test_after_diagnosis_rejects_a_sync_the_document_does_not_read_as_due():
    # A second `sync` for a sha with no sync between them reads as unlandable in the document.
    unread = _spent(diagnoses=2, verdict_sync_due=False)
    assert landing.after_diagnosis(VERDICT_SYNC, unread, 0) == "restart"
    assert landing.after_diagnosis(VERDICT_SYNC, unread, landing.RESTART_DEPTH_MAX) == "escalate"


@pytest.mark.parametrize("verdict", [VERDICT_UNLANDABLE, None])
def test_after_diagnosis_restarts_at_depth_zero_and_escalates_deeper(verdict):
    assert landing.after_diagnosis(verdict, _spent(), 0) == "restart"
    assert landing.after_diagnosis(verdict, _spent(), landing.RESTART_DEPTH_MAX) == "escalate"


def test_after_diagnosis_does_not_sync_past_the_task_wide_or_the_unsynced_budget():
    assert (
        landing.after_diagnosis(
            VERDICT_SYNC, _spent(syncs=landing.LANDING_SYNCS_MAX, verdict_sync_due=True), 0
        )
        == "restart"
    )
    assert (
        landing.after_diagnosis(
            VERDICT_SYNC,
            _spent(unsynced_for_upstream=landing.LANDING_UNSYNCED_MAX, verdict_sync_due=True),
            0,
        )
        == "restart"
    )


def test_an_entry_keeps_a_verdict_of_the_closed_set_and_drops_any_other():
    kept = landing.entry_of({"step": "diagnose", "verdict": VERDICT_SYNC, "upstream": MASTER})
    assert kept == {"step": "diagnose", "verdict": VERDICT_SYNC, "upstream": MASTER}
    assert "verdict" not in landing.entry_of({"step": "diagnose", "verdict": "maybe"})
    assert "verdict" not in landing.entry_of({"step": "diagnose", "verdict": 3})


def _sync(upstream=None, **more) -> dict:
    entry = {"head": HEAD, "step": "sync_master", **more}
    return {**entry, "upstream": upstream} if upstream is not None else entry


def test_a_sync_blocks_only_for_the_same_head_and_the_same_upstream():
    record = [_sync(OLDER)]
    assert landing.already_dispatched(record, HEAD, "sync_master", MASTER) is False
    assert landing.already_dispatched(record, HEAD, "sync_master", OLDER) is True
    assert landing.already_dispatched(record, "d" * 40, "sync_master", OLDER) is False


def test_a_sync_without_an_upstream_blocks_on_its_head():
    assert landing.already_dispatched([_sync()], HEAD, "sync_master", MASTER) is True
    assert landing.already_dispatched([_sync()], HEAD, "sync_master", None) is True


def test_a_sync_closed_with_lacks_still_does_not_block():
    record = [_sync(MASTER, lacks=MASTER)]
    assert landing.already_dispatched(record, HEAD, "sync_master", MASTER) is False


def test_other_steps_still_key_on_head_alone():
    record = [{"head": HEAD, "step": "fix", "upstream": OLDER}]
    assert landing.already_dispatched(record, HEAD, "fix", MASTER) is True


def test_a_delivered_verdict_sync_diagnose_does_not_block_the_next_diagnose():
    served = {"head": HEAD, "step": "diagnose", "delivered": True, "verdict": VERDICT_SYNC}
    assert landing.already_dispatched([served], HEAD, "diagnose", MASTER) is False
    unlandable = {**served, "verdict": VERDICT_UNLANDABLE}
    assert landing.already_dispatched([unlandable], HEAD, "diagnose", MASTER) is True
    unmarked = {"head": HEAD, "step": "diagnose", "delivered": True}
    assert landing.already_dispatched([unmarked], HEAD, "diagnose", MASTER) is True
