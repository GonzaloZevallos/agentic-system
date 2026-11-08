"""Per-upstream landing budgets (GH-330, ADR-0048): a conflicting sync has its own budget per
upstream sha and never touches `LANDING_PASSES_MAX`; a `sync` verdict of the diagnoser leads to
one more `sync_master` before any restart. Pure: facts and spent budgets in, a step out."""

from dataclasses import replace
from typing import get_args

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


# -- GH-544: the decision slots and the review an applied decision owes -------------------------

T0, T1, T2 = (f"2026-11-07T0{n}:00:00+00:00" for n in range(3))
MOVES = [["docs/adr/0070-x.md", "docs/adr/0074-x.md"]]


def _apply(outcome="applied", head=HEAD, at=T0) -> dict:
    return {
        "head": head,
        "step": "apply_adr_decision",
        "refusal": "adr_number_taken",
        "decision_at": at,
        "outcome": outcome,
        "moves": MOVES,
    }


def _review(head=HEAD) -> dict:
    return {"head": head, "step": "re_review", "refusal": "head_not_reviewed"}


def _ask(at=T0, head=HEAD, moves=MOVES) -> dict:
    """The released `escalate_adr` question an apply answers: `released_at` is its `decision_at`."""
    entry: dict = {
        "head": head,
        "step": "escalate_adr",
        "refusal": "adr_number_taken",
        "released_at": at,
    }
    if moves is not None:
        entry["moves"] = moves
    return entry


OTHER_MOVES = [["docs/adr/0071-y.md", "docs/adr/0075-y.md"]]
MOVES_KEY = tuple(tuple(pair) for pair in MOVES)


def test_the_decision_cap_is_two_and_only_the_closed_outcomes_but_stale_use_a_slot():
    assert landing.LANDING_ADR_DECISIONS_MAX == 2
    spending = ["applied", "out_of_bounds", "role_failed", "checks_red", "still_colliding"]
    for outcome in spending:
        entries = [_ask(), _apply(outcome)]
        assert landing.adr_decisions_spent(entries, HEAD, MOVES_KEY) == 1
    stale = [_ask(), _apply("stale_moves"), _apply("stale_moves")]
    assert landing.adr_decisions_spent(stale, HEAD, MOVES_KEY) == 0
    mixed = [_ask(), _apply("stale_moves"), _apply("applied")]
    assert landing.adr_decisions_spent(mixed, HEAD, MOVES_KEY) == 1


def test_a_slot_is_spent_per_head_and_per_moves_not_per_task():
    entries = [_ask(T0), _apply("checks_red", at=T0), _ask(T1, moves=OTHER_MOVES)]
    entries.append(_apply("applied", at=T1))
    assert landing.adr_decisions_spent(entries, HEAD, MOVES_KEY) == 1
    assert (
        landing.adr_decisions_spent(entries, HEAD, (("docs/adr/0071-y.md", "docs/adr/0075-y.md"),))
        == 1
    )
    assert landing.adr_decisions_spent(entries, "d" * 40, MOVES_KEY) == 0
    assert landing.adr_decisions_spent(entries, HEAD, (("x", "y"),)) == 0


def test_yaml_lists_and_a_recomputed_renaming_compare_equal():
    entries = [_ask(moves=[["a", "b"], ["c", "d"]]), _apply("checks_red")]
    assert landing.adr_decisions_spent(entries, HEAD, (("a", "b"), ("c", "d"))) == 1
    assert landing.adr_decisions_spent(entries, HEAD, [["a", "b"], ["c", "d"]]) == 1
    assert landing.adr_decisions_spent(entries, HEAD, (("a", "b"),)) == 0


def test_an_apply_that_cannot_be_matched_counts_against_every_key_of_its_head():
    no_decision_at = {**_apply("applied"), "decision_at": None}
    unknown_question = _apply("applied", at=T2)
    question_without_moves = [_ask(moves=None), _apply("applied")]
    for entries in (
        [_ask(), no_decision_at],
        [_ask(), unknown_question],
        question_without_moves,
        [no_decision_at],
    ):
        assert landing.adr_decisions_spent(entries, HEAD, MOVES_KEY) == 1
        assert landing.adr_decisions_spent(entries, HEAD, (("x", "y"),)) == 1
        assert landing.adr_decisions_spent(entries, "d" * 40, MOVES_KEY) == 0


def test_an_apply_without_a_head_counts_against_every_key():
    headless = {"step": "apply_adr_decision", "outcome": "applied"}
    assert landing.adr_decisions_spent([headless], HEAD, MOVES_KEY) == 1
    assert landing.adr_decisions_spent([headless], "d" * 40, (("x", "y"),)) == 1


def test_an_entry_written_before_the_step_existed_spends_no_slot_and_owes_no_review():
    legacy = [{"head": HEAD, "step": "escalate_adr", "resolved_at": T0}, _review()]
    assert landing.adr_decisions_spent(legacy, HEAD, MOVES_KEY) == 0
    assert landing.adr_review_owed(legacy) is False
    assert landing.adr_decisions_spent([{"step": "apply_adr_decision"}], HEAD, MOVES_KEY) == 0


def test_the_cap_spent_asks_the_human_again_instead_of_applying():
    asked = _ask(T2)
    ambiguous = landing.Ambiguous("unclassified_token", "a:1", MOVES_KEY)
    one = [_ask(T0), _apply("checks_red", at=T0), asked]
    two = [_ask(T0), _apply("checks_red", at=T0), _apply("applied", at=T1), asked]
    assert landing.adr_decision_due([asked], HEAD, ambiguous, one)
    assert not landing.adr_decision_due([asked], HEAD, ambiguous, two)
    # Another head's spent slots leave this one free.
    other = [_ask(T0, head=OLDER), _apply("applied", head=OLDER), _apply("applied", head=OLDER)]
    assert landing.adr_decision_due([asked], HEAD, ambiguous, [*other, asked])


def test_a_restoration_does_not_give_a_slot_back_but_hides_the_idempotency_key():
    # The cap reads all entries; the idempotency only the live ones.
    asked = _ask(T1)
    ambiguous = landing.Ambiguous("unclassified_token", "a:1", MOVES_KEY)
    archived = [_ask(T0), _apply("applied"), _apply("checks_red", at=T1)]
    assert landing.adr_decisions_spent(archived, HEAD, MOVES_KEY) == 2
    assert landing.adr_decisions_spent(archived[1:], HEAD, MOVES_KEY) == 2
    assert not landing.adr_decision_due([asked], HEAD, ambiguous, [*archived, asked])


def _review_step(spent) -> LandingStep:
    return landing.landing_step("head_not_reviewed", _facts(), spent, ())


def test_the_owed_review_is_not_refused_by_the_pass_budget_but_the_next_one_is():
    full = landing.LANDING_PASSES_MAX
    assert _review_step(_spent(passes=full)).kind == "diagnose"
    owed = _review_step(_spent(passes=full, adr_review_owed=True))
    assert owed == LandingStep("re_review", "head_not_reviewed")
    assert _review_step(_spent(passes=full + 1, adr_review_owed=True)).kind == "re_review"


def test_the_review_is_owed_until_a_re_review_is_recorded_after_the_applied_decision():
    owed = [_apply("applied")]
    assert landing.adr_review_owed(owed) is True
    assert landing.adr_review_owed([*owed, _review("c" * 40)]) is False


def test_a_sync_master_in_between_does_not_lose_the_owed_review():
    sync = {"head": HEAD, "step": "sync_master", "refusal": "merge_conflict"}
    assert landing.adr_review_owed([_apply("applied"), sync]) is True
    assert landing.adr_review_owed([_apply("applied"), sync, _review()]) is False


def test_a_decision_that_failed_owes_no_review_and_a_review_before_it_does_not_pay_it():
    assert landing.adr_review_owed([_apply("checks_red")]) is False
    assert landing.adr_review_owed([_review(), _apply("applied")]) is True
    later = [_apply("applied"), _review(), _apply("applied", at=T1)]
    assert landing.adr_review_owed(later) is True


def test_once_the_review_is_recorded_the_pass_budget_decides_again():
    full = landing.LANDING_PASSES_MAX
    owed = landing.adr_review_owed([_apply("applied")])
    assert _review_step(_spent(passes=full, adr_review_owed=owed)).kind == "re_review"
    paid = landing.adr_review_owed([_apply("applied"), _review()])
    assert _review_step(_spent(passes=full, adr_review_owed=paid)).kind == "diagnose"
    assert _review_step(_spent(passes=full - 1, adr_review_owed=paid)).kind == "re_review"


def test_the_outcome_is_the_closed_set_the_plan_names_and_entry_of_drops_any_other():
    assert set(get_args(landing.AdrDecisionOutcome)) == {
        "applied",
        "stale_moves",
        "out_of_bounds",
        "role_failed",
        "checks_red",
        "still_colliding",
    }
    kept = landing.entry_of({"step": "apply_adr_decision", "outcome": "exploded", "moves": "x"})
    assert "outcome" not in kept and "moves" not in kept
    full = landing.entry_of({**_apply("stale_moves"), "head_after": "c" * 40})
    assert full["outcome"] == "stale_moves" and full["moves"] == MOVES
    assert full["decision_at"] == T0 and full["head_after"] == "c" * 40
