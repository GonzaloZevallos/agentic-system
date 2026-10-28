"""`rules.auto_merge_decision` when the product declares its CI (GH-216, ADR-0045
amendment). Pure: each case is a `MergeFacts` and the `MergeDecision` it must give, one
row per new refusal and per cause of a wait, plus the proof that default facts decide
as they did before the CI facts existed (ADR-0023)."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiUnreadable, CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts
from agentic.core.process.domain.task import PullRequest

HEAD = "a" * 40
MASTER = "b" * 40
RED = CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")
WAIT = MergeDecision("wait", None)


def _verdict(state, failing=(), missing=()) -> CiVerdict:
    return CiVerdict(state, tuple(failing), tuple(missing))


def _facts(**over) -> MergeFacts:
    """A pull request whose CI is declared, green on this head and fresh: it merges."""
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
        touched_paths=("agentic/core/x.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("web",),
        ci_untrusted=False,
        ci_head=HEAD,
        ci_verdict=_verdict("green"),
        ci_fresh=True,
        ci_waited_s=10.0,
    )
    return replace(base, **over)


REFUSALS = [
    ("ci_failed", {"ci_verdict": _verdict("red", failing=(RED,))}),
    (
        "ci_missing",
        {
            "ci_verdict": _verdict("pending", missing=("web",)),
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
    (
        "ci_blocked_by_conflict",
        {"github_conflicting": True, "ci_verdict": _verdict("pending", missing=("web",))},
    ),
]

WAITS = [
    ("pending", {"ci_verdict": _verdict("pending", missing=("web",))}),
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


@pytest.mark.parametrize("conflicts", [None, False], ids=["unread", "clean"])
@pytest.mark.parametrize(("reason", "over"), REFUSALS, ids=[r for r, _ in REFUSALS])
def test_each_ci_refusal_has_the_input_that_provokes_it(reason, over, conflicts):
    assert rules.auto_merge_decision(_facts(conflicts=conflicts, **over)) == MergeDecision(
        "refuse", reason
    )
    assert rules.auto_merge_decision(_facts(conflicts=conflicts)) == MergeDecision("merge", None)


@pytest.mark.parametrize("conflicts", [None, False], ids=["unread", "clean"])
@pytest.mark.parametrize(("cause", "over"), WAITS, ids=[c for c, _ in WAITS])
def test_each_cause_of_a_wait_waits_and_never_refuses(cause, over, conflicts):
    assert rules.auto_merge_decision(_facts(conflicts=conflicts, **over)) == WAIT


def test_an_unknown_anchor_waits_on_a_pending_report_too():
    facts = _facts(ci_verdict=_verdict("pending"), ci_waited_s=None)
    assert rules.auto_merge_decision(facts) == WAIT


def test_a_head_that_is_not_the_reviewed_one_waits_even_over_a_red_report():
    facts = _facts(ci_head="c" * 40, ci_verdict=_verdict("red", failing=(RED,)))
    assert rules.auto_merge_decision(facts) == WAIT


def test_the_wait_bound_is_inclusive_of_the_refusal():
    pending = _verdict("pending", missing=("web",))
    under = _facts(ci_verdict=pending, ci_waited_s=ci.CI_WAIT_MAX_S - 1)
    assert rules.auto_merge_decision(under) == WAIT
    spent = _facts(ci_verdict=pending, ci_waited_s=ci.CI_WAIT_MAX_S)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_missing")


def test_a_ci_reason_never_hides_an_earlier_one():
    red = _verdict("red", failing=(RED,))
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
        {"ci_verdict": _verdict("red", failing=(RED,))},
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


@pytest.mark.parametrize("untrusted", [None, True], ids=["unknown_paths", "ci_definition_touched"])
def test_a_green_fresh_ci_never_decides_merge_when_its_definition_is_untrusted(untrusted):
    facts = _facts(ci_untrusted=untrusted)
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)
    covered = replace(facts, checks_head=HEAD, checks_master=MASTER, checks_passed=True)
    assert rules.auto_merge_decision(covered) == MergeDecision("merge", None)
    assert rules.auto_merge_decision(replace(covered, checks_passed=False)) == MergeDecision(
        "refuse", "checks_failed"
    )


def test_an_untrusted_ci_still_refuses_and_waits_as_before():
    red = _verdict("red", failing=(RED,))
    assert rules.auto_merge_decision(_facts(ci_untrusted=True, ci_verdict=red)) == MergeDecision(
        "refuse", "ci_failed"
    )
    assert rules.auto_merge_decision(_facts(ci_untrusted=None, ci_head=None)) == WAIT


# GH-395: GitHub runs no `pull_request` workflow on a head that conflicts with its base, so a
# required check that has no result there never comes.
BLOCKED = MergeDecision("refuse", "ci_blocked_by_conflict")
NO_RESULT = _verdict("pending", missing=("web",))


def _conflicting(**over) -> MergeFacts:
    """GitHub reads CONFLICTING on the head over a stale base: `conflicts` is clean, the
    required check has no result."""
    base = {
        "conflicts": False,
        "github_conflicting": True,
        "ci_verdict": NO_RESULT,
        "ci_waited_s": 10.0,
    }
    return _facts(**{**base, **over})


def test_a_conflicting_head_with_no_ci_result_is_refused_in_the_same_round():
    assert rules.auto_merge_decision(_conflicting()) == BLOCKED


def test_the_same_head_that_github_can_test_still_waits_then_refuses_ci_missing():
    mergeable = replace(_conflicting(), github_conflicting=False)
    assert rules.auto_merge_decision(mergeable) == WAIT
    spent = replace(mergeable, ci_waited_s=ci.CI_WAIT_MAX_S)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_missing")


def test_a_conflicting_head_refuses_blocked_also_past_the_wait_bound():
    assert rules.auto_merge_decision(_conflicting(ci_waited_s=ci.CI_WAIT_MAX_S)) == BLOCKED


def test_a_conflicting_head_with_a_red_check_is_still_ci_failed():
    red = _verdict("red", failing=(RED,))
    assert rules.auto_merge_decision(_conflicting(ci_verdict=red)) == MergeDecision(
        "refuse", "ci_failed"
    )


def test_a_conflicting_head_whose_check_is_pending_with_some_result_waits():
    one_ran = _verdict("pending", missing=("lint",))
    facts = _conflicting(ci_required=("web", "lint"), ci_verdict=one_ran)
    assert rules.auto_merge_decision(facts) == WAIT
    spent = replace(facts, ci_waited_s=ci.CI_WAIT_MAX_S)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_missing")


def test_a_conflicting_head_with_an_unreadable_ci_waits():
    facts = _conflicting(
        ci_verdict=None, ci_head=None, ci_unreadable=CiUnreadable("gh_unavailable")
    )
    assert rules.auto_merge_decision(facts) == WAIT


@pytest.mark.parametrize("head", ["c" * 40, HEAD[:10], None], ids=["other", "short", "none"])
def test_a_conflicting_head_whose_ci_report_is_of_another_head_waits(head):
    assert rules.auto_merge_decision(_conflicting(ci_head=head)) == WAIT


def test_a_conflicting_head_of_a_product_without_required_checks_is_unchanged():
    facts = _conflicting(ci_required=(), ci_verdict=None, ci_head=None)
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)


def test_ci_never_coming_is_false_for_default_facts_and_for_a_reported_name():
    assert not rules.ci_never_coming(_facts())
    assert not rules.ci_never_coming(_conflicting(ci_verdict=_verdict("green")))
    assert rules.ci_never_coming(_conflicting())


def test_a_definite_conflict_is_still_merge_conflict_ahead_of_the_ci():
    facts = _conflicting(conflicts=True)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "merge_conflict")


def _pr(mergeable, head_oid=HEAD, base_oid="d" * 40):
    return PullRequest(
        state="OPEN",
        created_at="t",
        merged_at=None,
        commits=(),
        head_oid=head_oid,
        mergeable=mergeable,
        base_oid=base_oid,
    )


@pytest.mark.parametrize("base_oid", [MASTER, "d" * 40, None], ids=["current", "stale", "absent"])
def test_github_conflicting_is_conflicting_on_the_head_whatever_the_base(base_oid):
    assert rules.github_conflicting(_pr("CONFLICTING", base_oid=base_oid), HEAD) is True


@pytest.mark.parametrize(
    "pull_request",
    [
        _pr("MERGEABLE"),
        _pr("UNKNOWN"),
        _pr(None),
        _pr("CONFLICTING", head_oid="c" * 40),
        _pr("CONFLICTING", head_oid=None),
        None,
    ],
    ids=["mergeable", "unknown", "unread", "stale_head", "no_head", "no_pull_request"],
)
def test_github_conflicting_is_false_for_anything_else(pull_request):
    assert rules.github_conflicting(pull_request, HEAD) is False
    assert rules.github_conflicting(_pr("CONFLICTING"), None) is False
