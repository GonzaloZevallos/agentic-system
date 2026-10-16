"""The `adr_number_taken` refusal (GH-229, ADR-0049) in the decision and the landing table:
where it sits in the order, that no collision changes nothing, and where it leads."""

from dataclasses import replace
from typing import get_args

import pytest

from agentic.core.process.domain import adr_numbers, landing, rules
from agentic.core.process.domain.landing import LandingSpent, LandingStep
from agentic.core.process.domain.rules import MergeDecision, MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
RENAMING = adr_numbers.Renaming((("docs/adr/0049-x.md", "docs/adr/0050-x.md"),), {})
AMBIGUOUS = adr_numbers.Ambiguous("unclassified_token", "docs/a.md:3: 0049")


def _facts(**over) -> MergeFacts:
    base = MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("docs/adr/0049-x.md",),
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
    )
    return replace(base, **over)


@pytest.mark.parametrize("collision", [RENAMING, AMBIGUOUS], ids=["renaming", "ambiguous"])
def test_a_collision_refuses_adr_number_taken(collision):
    decision = rules.auto_merge_decision(_facts(adr_collision=collision))
    assert decision == MergeDecision("refuse", "adr_number_taken")


def test_no_collision_changes_nothing():
    assert rules.auto_merge_decision(_facts(adr_collision=None)) == MergeDecision("merge", None)
    assert rules.auto_merge_decision(_facts()) == rules.auto_merge_decision(
        _facts(adr_collision=None)
    )


@pytest.mark.parametrize(
    "later",
    [
        {"reviewed_unit_approved": False},
        {"reviewed_head": "c" * 40},
        {"open_blocking": 1},
        {"open_important": 1},
        {"in_eval_set": True},
        {"merge_failures": rules.MERGE_CALLS_MAX},
        {"checks_passed": False},
        {"checks_head": None, "attempts": rules.MERGE_ATTEMPTS_MAX},
    ],
    ids=lambda over: next(iter(over)),
)
def test_the_refusal_comes_before_every_review_findings_and_checks_reason(later):
    facts = _facts(adr_collision=RENAMING, **later)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "adr_number_taken")


@pytest.mark.parametrize(
    ("earlier", "reason"),
    [({"status": "developing"}, "not_ready"), ({"pr_state": "CLOSED"}, "pr_not_open")],
)
def test_not_ready_and_a_closed_pull_request_are_refused_as_such_first(earlier, reason):
    facts = _facts(adr_collision=RENAMING, **earlier)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", reason)


def test_the_literal_lists_the_refusal_right_after_pr_not_open():
    names = list(get_args(rules.MergeRefusal))
    assert names[names.index("pr_not_open") + 1] == "adr_number_taken"


def test_the_landing_sends_the_refusal_to_diagnose_and_a_spent_task_has_none_left():
    spent = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnosed=False, waited_s=None)
    step = landing.landing_step("adr_number_taken", _facts(adr_collision=AMBIGUOUS), spent, ())
    assert step == LandingStep("diagnose", "adr_number_taken")
    done = replace(spent, diagnosed=True)
    assert landing.landing_step("adr_number_taken", _facts(), done, ()).kind == "none"
