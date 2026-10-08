"""`rules.auto_merge_decision` when the product declares its CI (GH-216, ADR-0045
amendment). Pure: each case is a `MergeFacts` and the `MergeDecision` it must give, one
row per new refusal and per cause of a wait, plus the proof that default facts decide
as they did before the CI facts existed (ADR-0023)."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiUnreadable, CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
RED = CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")
WAIT = MergeDecision("wait", None)


def _facts(**over) -> MergeFacts:
    """A pull request whose CI is declared, green on this head and fresh: it merges."""
    base = MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking_or_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/core/x.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("web",),
        ci_head=HEAD,
        ci_verdict=CiVerdict("green"),
        ci_fresh=True,
        ci_waited_s=10.0,
    )
    return replace(base, **over)


REFUSALS = [
    ("ci_failed", {"ci_verdict": CiVerdict("red", failing=(RED,))}),
    (
        "ci_missing",
        {
            "ci_verdict": CiVerdict("pending", missing=("web",)),
            "ci_waited_s": ci.CI_WAIT_MAX_S,
        },
    ),
    (
        "ci_unreadable",
        {
            "ci_verdict": None,
            "ci_head": None,
            "ci_unreadable": CiUnreadable("gh_unreadable"),
            "ci_waited_s": ci.CI_WAIT_MAX_S + 1,
        },
    ),
]

WAITS = [
    ("pending", {"ci_verdict": CiVerdict("pending", missing=("web",))}),
    (
        "unreadable",
        {"ci_verdict": None, "ci_head": None, "ci_unreadable": CiUnreadable("gh_unavailable")},
    ),
    (
        "unreadable_unknown_anchor",
        {"ci_verdict": None, "ci_unreadable": CiUnreadable("gh_unavailable"), "ci_waited_s": None},
    ),
    ("other_head", {"ci_head": "c" * 40}),
    ("abbreviated_head", {"ci_head": HEAD[:10]}),
    ("no_head", {"ci_head": None}),
]


@pytest.mark.parametrize(("reason", "over"), REFUSALS, ids=[r for r, _ in REFUSALS])
def test_each_ci_refusal_has_the_input_that_provokes_it(reason, over):
    assert rules.auto_merge_decision(_facts(**over)) == MergeDecision("refuse", reason)
    assert rules.auto_merge_decision(_facts()) == MergeDecision("merge", None)


@pytest.mark.parametrize(("cause", "over"), WAITS, ids=[c for c, _ in WAITS])
def test_each_cause_of_a_wait_waits_and_never_refuses(cause, over):
    assert rules.auto_merge_decision(_facts(**over)) == WAIT


def test_an_unknown_anchor_waits_on_a_pending_report_too():
    facts = _facts(ci_verdict=CiVerdict("pending"), ci_waited_s=None)
    assert rules.auto_merge_decision(facts) == WAIT


def test_a_head_that_is_not_the_reviewed_one_waits_even_over_a_red_report():
    facts = _facts(ci_head="c" * 40, ci_verdict=CiVerdict("red", failing=(RED,)))
    assert rules.auto_merge_decision(facts) == WAIT


def test_the_wait_bound_is_inclusive_of_the_refusal():
    pending = CiVerdict("pending", missing=("web",))
    under = _facts(ci_verdict=pending, ci_waited_s=ci.CI_WAIT_MAX_S - 1)
    assert rules.auto_merge_decision(under) == WAIT
    spent = _facts(ci_verdict=pending, ci_waited_s=ci.CI_WAIT_MAX_S)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_missing")


def test_a_ci_reason_never_hides_an_earlier_one():
    red = CiVerdict("red", failing=(RED,))
    assert rules.auto_merge_decision(_facts(ci_verdict=red, in_eval_set=True)) == MergeDecision(
        "refuse", "eval_set"
    )
    assert rules.auto_merge_decision(
        _facts(ci_verdict=red, merge_failures=rules.MERGE_CALLS_MAX)
    ) == MergeDecision("refuse", "merge_failed")


@pytest.mark.parametrize("fresh", [None, False])
def test_a_green_stale_or_unknown_ci_falls_to_the_local_checks(fresh):
    asked = rules.auto_merge_decision(_facts(ci_fresh=fresh))
    assert asked == MergeDecision("run_checks", None)
    covered = _facts(
        ci_fresh=fresh,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=False,
    )
    assert rules.auto_merge_decision(covered) == MergeDecision("refuse", "checks_failed")
    spent = _facts(ci_fresh=fresh, attempts=rules.MERGE_ATTEMPTS_MAX)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "attempts_exhausted")


def test_default_facts_decide_as_before_whatever_the_ci_fields_say_nothing_of():
    plain = _facts(
        ci_required=(),
        ci_head=None,
        ci_verdict=None,
        ci_fresh=None,
        ci_waited_s=None,
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
    )
    assert rules.auto_merge_decision(plain) == MergeDecision("merge", None)
    for over in (
        {"ci_verdict": CiVerdict("red", failing=(RED,))},
        {"ci_unreadable": CiUnreadable("gh_unreadable")},
        {"ci_head": "c" * 40},
    ):
        assert rules.auto_merge_decision(replace(plain, **over)) == MergeDecision("merge", None)
    assert rules.auto_merge_decision(replace(plain, checks_passed=False)) == MergeDecision(
        "refuse", "checks_failed"
    )
    assert rules.auto_merge_decision(replace(plain, checks_passed=None)) == MergeDecision(
        "run_checks", None
    )
