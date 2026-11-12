"""The dated arm of the landing table and the release of a numeric ADR question (GH-611, the
owner's rule): on a product that names ADRs by date a numbered ADR is renamed, not renumbered,
and nobody is asked. Pure: facts and the document in, a step or a patch out."""

from __future__ import annotations

from dataclasses import replace

from agentic.core.process.domain import adr_numbers, landing, rules
from agentic.core.process.domain.adr_dated import Undated
from agentic.core.process.domain.landing import LandingSpent, landing_step

UPSTREAM = "m" * 40
HEAD = "h" * 40
OWN = adr_numbers.OwnAdr("docs/adr/0085-x.md", "c1")


def _spent(**over) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)
    return replace(base, **over)


def _facts(naming="dated", collision=None) -> rules.MergeFacts:
    return rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("docs/adr/0085-x.md",),
        attempts=1,
        checks_head=HEAD,
        checks_master=UPSTREAM,
        checks_passed=True,
        current_master=UPSTREAM,
        adr_collision=collision if collision is not None else Undated((OWN,)),
        adr_naming=naming,
    )


def _step(facts, spent=None, **over):
    return landing_step("adr_number_taken", facts, spent or _spent(), (), turn="head", **over).kind


def test_a_dated_product_renames_under_the_cap():
    assert _step(_facts()) == "renumber"


def test_an_ambiguous_collision_on_a_dated_product_is_renamed_too():
    ambiguous = adr_numbers.Ambiguous("unclassified_token", "x", (("a", "b"),))
    assert _step(_facts(collision=ambiguous)) == "renumber"


def test_the_dated_arm_never_applies_a_decision_whatever_was_asked_before():
    every = _step(_facts(), _spent(renumbers=3), adr_decision_due=True, adr_auto_due=True)
    assert every == "escalate_adr"
    assert _step(_facts(), _spent(renumbers=3), adr_escalated=True, adr_decision_due=True) == "none"


def test_a_decline_is_final_on_the_head_then_one_question_then_nothing():
    spent = _spent(renumbers=1, declined_on_head=1)
    assert _step(_facts(), spent) == "escalate_adr"
    assert _step(_facts(), spent, adr_escalated=True) == "none"


def test_a_retry_under_the_cap_renames_again():
    assert _step(_facts(), _spent(renumbers=2, declined_on_head=0)) == "renumber"


def test_a_numbered_product_keeps_the_decision_the_ledger_asks_for():
    moves = (("docs/adr/0085-x.md", "docs/adr/0086-x.md"),)
    ambiguous = adr_numbers.Ambiguous("unclassified_token", "x", moves)
    facts = _facts(naming="numbered", collision=ambiguous)
    assert _step(facts, adr_decision_due=True) == "apply_adr_decision"


def _escalated_document(moves=True) -> dict:
    question = {
        "head": HEAD,
        "step": "escalate_adr",
        "refusal": "adr_number_taken",
        "at": "2026-11-12T10:00:00+00:00",
        "resolved_at": "2026-11-12T10:00:05+00:00",
    }
    if moves:
        question["moves"] = [["docs/adr/0085-x.md", "docs/adr/0086-x.md"]]
    return {"escalated": "ADR question", "escalation_class": "questions", "landing": [question]}


def test_an_unanswered_numeric_question_is_released_once_per_upstream():
    raw = _escalated_document()
    patch = landing.released_by_naming(raw, UPSTREAM, "2026-11-12T11:00:00+00:00")
    [entry] = patch["landing"]
    assert entry["naming_released"] == UPSTREAM and entry["released_at"]
    assert patch["escalated"] is None
    assert "sections" not in patch  # nobody decided anything
    again = {**raw, "landing": patch["landing"], "escalated": "ADR question"}
    assert landing.released_by_naming(again, UPSTREAM, "2026-11-12T12:00:00+00:00") == {}


def test_the_released_task_no_longer_waits_on_a_human():
    raw = _escalated_document()
    spent = _spent()

    def waits(state) -> bool:
        return landing.waits_on_human(
            state,
            HEAD,
            "adr_number_taken",
            spent=spent,
            stopped=False,
            needs_human=False,
            stranded=False,
        )

    assert waits(raw)
    patch = landing.released_by_naming(raw, UPSTREAM, "2026-11-12T11:00:00+00:00")
    assert not waits({**raw, "escalated": patch["escalated"], "landing": patch["landing"]})


def test_a_question_without_numeric_moves_is_not_released_by_naming():
    raw = _escalated_document(moves=False)
    assert landing.released_by_naming(raw, UPSTREAM, "2026-11-12T11:00:00+00:00") == {}


def test_a_released_numeric_question_is_not_the_pending_question_of_the_head():
    raw = _escalated_document()
    patch = landing.released_by_naming(raw, UPSTREAM, "2026-11-12T11:00:00+00:00")
    assert landing.adr_asked(raw["landing"], HEAD, "adr_number_taken")
    assert not landing.adr_asked(patch["landing"], HEAD, "adr_number_taken")
