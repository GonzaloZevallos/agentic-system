"""`landing.landing_step` by turn (GH-588): a pull request that waits for its turn in the
landing order is left as it is, whatever the refusal and the budgets; the head's table is the
one it always was. Pure."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.domain import adr_numbers, ci, landing, rules
from agentic.core.process.domain.landing import HUMAN_OWNED_REFUSALS, LandingStep
from tests.core.process.domain.test_landing import _facts, _spent

ALL_REFUSALS: tuple[rules.MergeRefusal, ...] = get_args(rules.MergeRefusal)
RENAMING = adr_numbers.Renaming((("docs/adr/0049-x.md", "docs/adr/0050-x.md"),), {})
NOT_WAITED_ON = HUMAN_OWNED_REFUSALS | {"not_ready"}

# Budgets every one of which has run out, and the flags that lead to a sync, a release or a
# decision: no combination may take a waiting task anywhere but `wait` / `none`.
EXHAUSTED = _spent(
    passes=landing.LANDING_PASSES_MAX,
    syncs=99,
    reruns_on_head=99,
    diagnoses=1,
    waited_s=ci.CI_WAIT_MAX_S + 1,
    renumbers=99,
    unsynced_for_upstream=99,
    conflicting_syncs_for_upstream=99,
    conflict_ci_syncs_on_pair=99,
    important_passes=99,
    last_verdict="sync",
    ci_release_due=True,
    head_has_upstream=False,
    checks_rerun_on_head=True,
)


def _step(refusal, turn, spent=None, **kwargs) -> LandingStep:
    over = kwargs.pop("facts", {})
    return landing.landing_step(
        refusal, _facts(**over), spent or _spent(), ("api",), turn=turn, **kwargs
    )


def _expected(refusal) -> str:
    return "none" if refusal in NOT_WAITED_ON else "wait"


@pytest.mark.parametrize("refusal", ALL_REFUSALS)
def test_a_waiting_task_gets_wait_or_none_for_every_refusal_under_fresh_budgets(refusal):
    step = _step(refusal, "waiting")
    assert step == LandingStep(_expected(refusal), refusal)


@pytest.mark.parametrize("refusal", ALL_REFUSALS)
def test_a_waiting_task_with_every_budget_spent_is_left_alone_too(refusal):
    step = _step(refusal, "waiting", EXHAUSTED)
    assert step == LandingStep(_expected(refusal), refusal)
    assert step.restart_why is None
    assert step.units == ()
    assert step.names == ()


@pytest.mark.parametrize("refusal", ALL_REFUSALS)
def test_a_waiting_task_never_asks_for_the_slice_inputs(refusal):
    def provider():
        raise AssertionError("a waiting task must not read the slice inputs")

    assert _step(refusal, "waiting", EXHAUSTED, slicing=provider).kind in {"wait", "none"}


def test_the_waiting_branch_answers_the_refusals_the_head_diagnoses_or_syncs_with_wait():
    cases = [
        ("merge_conflict", {}, _spent(), {}),
        ("attempts_exhausted", {}, _spent(), {}),
        ("ci_stale_exhausted", {}, _spent(), {}),
        ("checks_failed", {"failed_step": "conflict"}, _spent(), {}),
        ("ci_missing", {}, _spent(waited_s=ci.CI_WAIT_MAX_S), {}),
        ("ci_failed", {}, _spent(ci_release_due=True, head_has_upstream=True), {}),
        ("ci_blocked_by_conflict", {}, _spent(), {}),
        ("merge_failed", {}, _spent(), {}),
    ]
    for refusal, facts, spent, kwargs in cases:
        assert _step(refusal, "waiting", spent, facts=facts, **kwargs).kind == "wait", refusal


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"adr_escalated": True},
        {"adr_decision_due": True},
    ],
    ids=["renaming", "escalated", "decision_due"],
)
def test_an_adr_collision_waits_whatever_its_state(kwargs):
    facts = {"adr_collision": RENAMING}
    assert _step("adr_number_taken", "waiting", facts=facts, **kwargs).kind == "wait"


@pytest.mark.parametrize(
    ("kwargs", "spent", "kind"),
    [
        ({}, _spent(), "renumber"),
        ({}, _spent(renumbers=3), "escalate_adr"),
        ({"adr_escalated": True}, _spent(renumbers=3), "none"),
        ({"adr_decision_due": True}, _spent(renumbers=3), "apply_adr_decision"),
    ],
    ids=["renumber", "escalate_adr", "asked", "apply_adr_decision"],
)
def test_the_head_turn_keeps_the_adr_collision_steps(kwargs, spent, kind):
    facts = {"adr_collision": RENAMING}
    assert _step("adr_number_taken", "head", spent, facts=facts, **kwargs).kind == kind


def test_the_planning_defect_escalates_at_the_head_and_waits_when_waiting():
    facts = {"ci_unreported": frozenset({"web"})}
    assert _step("ci_unknown_names", "head", facts=facts).kind == "escalate"
    assert _step("ci_unknown_names", "waiting", facts=facts).kind == "wait"


@pytest.mark.parametrize("turn", ["head", "waiting"])
def test_an_unlandable_diagnosis_is_none_at_either_turn(turn):
    assert _step("merge_conflict", turn, _spent(diagnoses=1, last_verdict=None)).kind == "none"


@pytest.mark.parametrize("refusal", ALL_REFUSALS)
def test_the_head_turn_is_the_table_as_it_was(refusal):
    from tests.core.process.domain.test_landing import EXPECTED

    assert _step(refusal, "head").kind == EXPECTED[refusal]


def test_turn_has_no_default():
    with pytest.raises(TypeError, match="turn"):
        landing.landing_step("merge_conflict", _facts(), _spent(), ())  # type: ignore[call-arg]
