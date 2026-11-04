"""`rules.auto_merge_decision` for a required CI name no check run carries (GH-485): the
apostrophe GH-543 shape through the merge gate. Pure: each case is a `MergeFacts`."""

from dataclasses import replace

from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiVerdict
from agentic.core.process.domain.rules import MergeDecision, MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
FLOOR = 2 * ci.CI_POLL_S
RED = CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")


def _facts(**over) -> MergeFacts:
    """GH-543: `e2e` is required, the report carries `e2e (1)`..`e2e (4)` only (green, so the
    verdict is pending on the missing name), and every workflow run is completed."""
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
        ci_required=("e2e",),
        ci_untrusted=False,
        ci_head=HEAD,
        ci_verdict=CiVerdict("pending", (), ("e2e",)),
        ci_unreported=("e2e",),
        ci_reported=("e2e (1)", "e2e (2)", "e2e (3)", "e2e (4)"),
        ci_fresh=True,
        ci_waited_s=FLOOR,
    )
    return replace(base, **over)


def test_a_name_no_check_run_carries_is_refused_once_the_wait_is_confirmed():
    assert rules.auto_merge_decision(_facts()) == MergeDecision("refuse", "ci_unknown_names")


def test_below_the_confirmation_floor_or_without_an_anchor_it_still_waits():
    wait = MergeDecision("wait", None)
    assert rules.auto_merge_decision(_facts(ci_waited_s=FLOOR - 1)) == wait
    assert rules.auto_merge_decision(_facts(ci_waited_s=None)) == wait


def test_no_unreported_name_is_the_ordinary_wait_until_the_bound():
    pending = _facts(ci_unreported=())
    assert rules.auto_merge_decision(pending) == MergeDecision("wait", None)
    spent = replace(pending, ci_waited_s=ci.CI_WAIT_MAX_S)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_missing")


def test_red_wins_over_an_unknown_name():
    red = _facts(ci_verdict=CiVerdict("red", (RED,), ("e2e",)))
    assert rules.auto_merge_decision(red) == MergeDecision("refuse", "ci_failed")


def test_a_conflicting_head_with_no_suites_is_blocked_by_the_conflict_not_unknown():
    conflicting = _facts(github_conflicting=True, ci_unreported=(), ci_reported=())
    assert rules.auto_merge_decision(conflicting) == MergeDecision(
        "refuse", "ci_blocked_by_conflict"
    )
