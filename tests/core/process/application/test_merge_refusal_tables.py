"""Every `MergeRefusal` is accounted for by the merge verb and by the landing (GH-859): either
the verb comments it (`COMMENTED`) or it says so in `UNCOMMENTED`; and `landing_step` answers it
with a case of its own, never the catch-all that answers `none` to what nobody listed. The three
stacking values are the ones this fixes: a fourth value that is in neither table fails here."""

from dataclasses import replace
from typing import get_args

import pytest

from agentic.core.process.application import merge, refusal_note
from agentic.core.process.domain import exempt_queue, landing, rules
from agentic.core.process.domain.landing import LandingSpent
from agentic.core.process.domain.rules import MergeFacts

ALL: tuple[str, ...] = get_args(rules.MergeRefusal)
STACK = ("stack_parent_unmerged", "stack_restack_due", "stack_base_orphaned")
HEAD = "a" * 40


def _facts() -> MergeFacts:
    return MergeFacts(
        open_escalation=False,
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
        current_master="b" * 40,
    )


def _spent() -> LandingSpent:
    return LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)


def _tables_miss(commented: tuple[str, ...], uncommented: tuple[str, ...]) -> list[str]:
    """The refusals in neither table, and those in both: each must be in exactly one."""
    return [r for r in ALL if (r in commented) == (r in uncommented)]


def test_every_refusal_is_commented_or_explicitly_uncommented():
    assert _tables_miss(merge.COMMENTED, merge.UNCOMMENTED) == []
    assert set(ALL) <= set(merge.COMMENTED) | set(merge.UNCOMMENTED)


def test_the_check_rejects_a_refusal_the_tables_forgot_or_list_twice():
    assert _tables_miss(
        merge.COMMENTED, tuple(r for r in merge.UNCOMMENTED if r != "not_ready")
    ) == ["not_ready"]
    assert _tables_miss(merge.COMMENTED + ("not_ready",), merge.UNCOMMENTED) == ["not_ready"]


@pytest.mark.parametrize("refusal", ALL)
def test_landing_step_answers_every_refusal_with_a_case_of_its_own(refusal):
    step = landing.landing_step(refusal, _facts(), _spent(), (), turn="head")
    owned = refusal in landing.HUMAN_OWNED_REFUSALS or refusal == "not_ready"
    # `none` comes from the catch-all unless a person owns the refusal; no other step does.
    assert step.kind != "none" or owned


def test_the_three_stacking_values_are_handled_in_one_place_and_in_no_list_that_acts_on_them():
    # GH-859: they are cases of `landing_step` (wait, sync_master) or `HUMAN_OWNED_REFUSALS`.
    for refusal in STACK:
        assert refusal in ALL
        assert not {refusal} & exempt_queue.BACKOFF_REASONS
        assert refusal not in landing.CONFLICTING_SYNC_REFUSALS
    assert landing.HUMAN_OWNED_REFUSALS & set(STACK) == {"stack_base_orphaned"}
    assert set(merge.COMMENTED) & set(STACK) == {"stack_base_orphaned"}
    assert set(merge.UNCOMMENTED) & set(STACK) == {"stack_parent_unmerged", "stack_restack_due"}
    assert exempt_queue.BACKOFF_REASONS == {"ci_untrusted_no_dispatch"}


def test_each_stacking_value_has_a_fixed_evidence_phrase_and_escalation_text():
    assert set(refusal_note.STACK_EVIDENCE) == set(STACK)
    assert set(landing.STACK_ESCALATION) == set(STACK)
    for table in (refusal_note.STACK_EVIDENCE, landing.STACK_ESCALATION):
        assert all(text and "{" not in text for text in table.values())


def test_a_stacked_exhaustion_reaches_the_escalation_text_of_its_refusal():
    spent = replace(_spent(), syncs=landing.LANDING_SYNCS_MAX)
    from agentic.core.process.domain import stacking

    facts = replace(_facts(), stack=stacking.unread_facts())
    step = landing.landing_step("stack_restack_due", facts, spent, (), turn="head")
    assert step.kind == "escalate"
    assert step.refusal in landing.STACK_ESCALATION


def test_every_human_owned_refusal_is_a_known_refusal_with_text_and_stack_ones_have_escalation():
    # GH-838: the landing says nothing for these, so the verb or the poller must say it for it.
    assert landing.HUMAN_OWNED_REFUSALS <= set(ALL)
    assert all(r in merge.COMMENTED or r in merge.UNCOMMENTED for r in landing.HUMAN_OWNED_REFUSALS)
    for refusal in landing.HUMAN_OWNED_REFUSALS & set(STACK):
        assert landing.STACK_ESCALATION[refusal]
