"""The checks the plan requires (GH-371, ADR-0045 addendum): `rules.ci_gate_names` joins
the stack's names with the plan's, and `auto_merge_decision` lets a plan-only name be a
necessary condition of a merge, never a sufficient one. Pure."""

from dataclasses import replace

import pytest

from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiVerdict
from agentic.core.process.domain.rules import CiGate, MergeDecision, MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
RED = CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")


def _facts(**over) -> MergeFacts:
    """The #432 shape: the plan requires `web` and `e2e`, the stack declares no CI, the
    local checks are recorded green on this head and master, the CI is green and fresh."""
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
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
        ci_required=("web", "e2e"),
        ci_plan_only=("web", "e2e"),
        ci_untrusted=False,
        ci_head=HEAD,
        ci_verdict=CiVerdict("green", (), ()),
        ci_fresh=True,
        ci_waited_s=10.0,
    )
    return replace(base, **over)


def test_a_red_plan_only_check_refuses_even_with_green_local_checks():
    red = CiVerdict("red", (RED,), ())
    assert rules.auto_merge_decision(_facts(ci_verdict=red)) == MergeDecision("refuse", "ci_failed")


def test_an_unread_plan_only_ci_waits_and_then_is_unreadable():
    unread = {
        "ci_verdict": None,
        "ci_head": None,
        "ci_unreadable": ci.CiUnreadable("gh_unavailable"),
    }
    assert rules.auto_merge_decision(_facts(**unread)) == MergeDecision("wait", None)
    spent = _facts(**unread, ci_waited_s=ci.CI_WAIT_MAX_S + 1)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_unreadable")


def test_a_pending_plan_only_check_waits_and_then_is_missing():
    pending = {"ci_verdict": CiVerdict("pending", (), ("web",))}
    assert rules.auto_merge_decision(_facts(**pending)) == MergeDecision("wait", None)
    spent = _facts(**pending, ci_waited_s=ci.CI_WAIT_MAX_S)
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "ci_missing")


def test_a_green_plan_only_ci_never_merges_on_its_own():
    no_checks = _facts(ci_required=("web",), ci_plan_only=("web",), checks_passed=None)
    assert rules.auto_merge_decision(no_checks) == MergeDecision("run_checks", None)
    no_checks_head = replace(no_checks, checks_head=None, checks_master=None)
    assert rules.auto_merge_decision(no_checks_head) == MergeDecision("run_checks", None)


def test_a_green_plan_only_ci_defers_to_failing_local_checks():
    facts = _facts(ci_required=("web",), ci_plan_only=("web",), checks_passed=False)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "checks_failed")


def test_a_green_plan_only_ci_with_green_local_checks_merges_through_the_checks():
    facts = _facts(ci_required=("web",), ci_plan_only=("web",))
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def test_a_declared_name_beside_a_plan_only_one_merges_on_a_green_ci_alone():
    facts = _facts(
        ci_required=("check", "web"),
        ci_plan_only=("web",),
        checks_head=None,
        checks_master=None,
        checks_passed=None,
    )
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def test_a_stale_green_ci_with_a_declared_name_still_goes_to_the_checks():
    facts = _facts(
        ci_required=("check", "web"),
        ci_plan_only=("web",),
        ci_fresh=False,
        checks_passed=None,
    )
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)


def test_facts_without_plan_names_decide_as_before():
    facts = _facts(ci_required=("web",), ci_plan_only=(), checks_passed=None)
    assert MergeFacts.__dataclass_fields__["ci_plan_only"].default == ()
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


@pytest.mark.parametrize(
    ("declared", "plan", "names", "plan_only"),
    [
        ((), [], (), ()),
        (("check",), [], ("check",), ()),
        ((), ["web", "e2e"], ("web", "e2e"), ("web", "e2e")),
        (("check", "test"), ["web", "check"], ("check", "test", "web"), ("web",)),
        (("test",), ["web", "web", "e2e", "web"], ("test", "web", "e2e"), ("web", "e2e")),
        (("a", "b"), ["b", "a"], ("a", "b"), ()),
    ],
)
def test_ci_gate_names_puts_the_declared_first_and_drops_duplicates(
    declared, plan, names, plan_only
):
    assert rules.ci_gate_names(declared, plan) == CiGate(names, plan_only)


def test_required_paths_missing_is_empty_when_nothing_is_required():
    assert rules.required_paths_missing([], ["a.py"]) == ()
    assert rules.required_paths_missing([], []) == ()


def test_required_paths_missing_is_empty_when_any_required_path_was_touched():
    required = [".agentic/stack.yaml", "web/app.ts"]
    assert rules.required_paths_missing(required, ["x.py", ".agentic/stack.yaml"]) == ()


def test_required_paths_missing_gives_the_required_paths_when_none_was_touched():
    required = [".agentic/stack.yaml", "web/app.ts"]
    assert rules.required_paths_missing(required, ["x.py"]) == tuple(required)
    assert rules.required_paths_missing(required, []) == tuple(required)
