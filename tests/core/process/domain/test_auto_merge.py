"""`rules.auto_merge_decision` (GH-178, ADR-0045): the one place every condition of the
poller merging a pull request on its own lives. Pure: each case is a `MergeFacts` and the
`MergeDecision` it must give, one row per refusal with the input that provokes it
(ADR-0023), never a poll round."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import get_args

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.rules import MergeDecision, MergeFacts

HEAD = "a" * 40
MASTER = "b" * 40
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _facts(**over) -> MergeFacts:
    """A pull request that merges: every fact in the accepting position, checks already
    green on this head and this master."""
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
        attempts=1,
        checks_head=HEAD,
        checks_master=MASTER,
        checks_passed=True,
        current_master=MASTER,
    )
    return replace(base, **over)


REFUSALS = [
    ("not_ready", {"status": "developing"}),
    ("pr_not_open", {"pr_state": "CLOSED"}),
    ("not_approved", {"reviewed_unit_approved": False}),
    ("head_not_reviewed", {"reviewed_head": "c" * 40}),
    ("open_findings", {"open_blocking_or_important": 1}),
    ("eval_set", {"in_eval_set": True}),
    ("eval_set_unreadable", {"in_eval_set": None}),
    ("guarded_path", {"touched_paths": ("agentic/core/x.py", ".github/workflows/ci.yml")}),
    (
        "attempts_exhausted",
        {"checks_master": "d" * 40, "attempts": rules.MERGE_ATTEMPTS_MAX},
    ),
    ("checks_failed", {"checks_passed": False}),
    ("merge_failed", {"merge_failures": rules.MERGE_CALLS_MAX}),
]


@pytest.mark.parametrize(("reason", "over"), REFUSALS, ids=[r for r, _ in REFUSALS])
def test_each_refusal_has_the_input_that_provokes_it(reason, over):
    assert rules.auto_merge_decision(_facts(**over)) == MergeDecision("refuse", reason)
    assert rules.auto_merge_decision(_facts()) == MergeDecision("merge", None)


def test_every_refusal_the_decision_gives_is_in_the_closed_set_and_master_moved_is_the_verbs():
    given = {reason for reason, _ in REFUSALS}
    # The CI's own reasons are given, one row each, by `test_auto_merge_ci.py`.
    ci_reasons = {"ci_failed", "ci_missing", "ci_unreadable"}
    # The ADR number's own are given, one row each, by `test_auto_merge_adr.py`.
    adr_reasons = {"adr_number_taken"}
    assert given | ci_reasons | adr_reasons | {"master_moved"} == set(get_args(rules.MergeRefusal))


EARLIER_FAILURES = [
    {"reviewed_unit_approved": False},
    {"reviewed_head": "c" * 40},
    {"open_blocking_or_important": 1},
]


@pytest.mark.parametrize("pr_state", ["CLOSED", None])
@pytest.mark.parametrize("earlier", EARLIER_FAILURES, ids=lambda over: next(iter(over)))
def test_a_pull_request_that_is_not_open_is_refused_as_such_before_any_review_reason(
    earlier, pr_state
):
    facts = _facts(pr_state=pr_state, **earlier)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "pr_not_open")


def test_a_task_not_ready_with_a_closed_pull_request_is_refused_as_not_ready():
    facts = _facts(status="developing", pr_state="CLOSED")
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "not_ready")


@pytest.mark.parametrize(
    ("state", "abandoned"),
    [
        ("CLOSED", True),
        ("OPEN", False),
        ("MERGED", False),
        (None, False),
        ("closed", False),
        ("Closed", False),
    ],
)
def test_pr_abandoned_is_true_only_for_the_closed_state(state, abandoned):
    assert rules.pr_abandoned(state) is abandoned


def test_a_head_with_no_review_head_on_either_side_is_not_reviewed():
    facts = _facts(reviewed_head=None, pr_head=None, checks_head=None)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_checks_absent_ask_for_a_run():
    facts = _facts(checks_head=None, checks_master=None, checks_passed=None, attempts=0)
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)


def test_checks_of_an_older_master_ask_for_a_run_while_attempts_remain():
    facts = _facts(checks_master="d" * 40, attempts=rules.MERGE_ATTEMPTS_MAX - 1)
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)


def test_checks_of_another_head_ask_for_a_run():
    facts = _facts(checks_head="c" * 40, attempts=0)
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)


def test_the_attempt_cap_is_three_and_the_boundary_is_two_against_three():
    assert rules.MERGE_ATTEMPTS_MAX == 3
    stale = {"checks_master": "d" * 40}
    assert rules.auto_merge_decision(_facts(attempts=2, **stale)).kind == "run_checks"
    assert rules.auto_merge_decision(_facts(attempts=3, **stale)) == MergeDecision(
        "refuse", "attempts_exhausted"
    )


def test_a_third_run_that_passed_on_the_current_master_still_merges():
    assert rules.auto_merge_decision(_facts(attempts=3)) == MergeDecision("merge", None)


def test_an_unknown_current_master_never_matches_the_checks():
    facts = _facts(current_master=None, checks_master=None)
    assert rules.auto_merge_decision(facts) == MergeDecision("run_checks", None)


def test_the_first_failing_condition_is_the_one_reported():
    facts = _facts(status="developing", in_eval_set=True, pr_state="CLOSED")
    assert rules.auto_merge_decision(facts).reason == "not_ready"
    assert rules.auto_merge_decision(_facts(in_eval_set=True, checks_passed=False)).reason == (
        "eval_set"
    )


def test_a_poller_side_decision_with_unknown_paths_does_not_refuse_on_them():
    assert rules.auto_merge_decision(_facts(touched_paths=None)) == MergeDecision("merge", None)


@pytest.mark.parametrize(
    ("path", "guarded"),
    [
        (".agentic/tasks/GH-9.md", False),  # the task's own document
        (".agentic/tasks/GH-10.md", True),  # another task's
        (".agentic/tasks/GH-9.md.bak", True),
        (".agentic/stack.yaml", True),
        (".agentic/eval-set.yaml", True),
        ("CODING_STANDARDS.md", True),
        (".github/workflows/ci.yml", True),
        ("agentic/core/x.py", False),
        ("docs/CODING_STANDARDS.md", False),
    ],
)
def test_guarded_path(path, guarded):
    assert rules.guarded_path(path, "GH-9") is guarded


def _unit(**more):
    return {"id": "x", "status": "approved", **more}


def test_the_reviewed_unit_is_the_integration_when_there_is_one():
    state = {"subtasks": {"a": _unit(id="a"), "b": _unit(id="b"), "integration": _unit(id="i")}}
    assert rules.reviewed_unit(state) == _unit(id="i")


def test_the_reviewed_unit_is_the_only_subtask_when_there_is_one():
    assert rules.reviewed_unit({"subtasks": {"a": _unit(id="a")}}) == _unit(id="a")


def test_several_subtasks_without_integration_have_no_reviewed_unit():
    assert rules.reviewed_unit({"subtasks": {"a": _unit(), "b": _unit()}}) is None
    assert rules.reviewed_unit({}) is None


def test_poll_plan_places_one_merge_after_the_launch_with_a_slot_left():
    actions = rules.poll_plan(
        closes=[],
        resumes=[],
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)],
        available_slots=2,
        now=NOW,
        merges=[9, 7],
    )
    assert actions == [rules.PollAction("launch", 4), rules.PollAction("merge", 7)]


def test_poll_plan_spends_no_slot_on_a_merge_when_the_launch_took_the_last_one():
    actions = rules.poll_plan(
        closes=[],
        resumes=[],
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)],
        available_slots=1,
        now=NOW,
        merges=[7],
    )
    assert actions == [rules.PollAction("launch", 4)]


def test_poll_plan_with_no_slot_places_no_merge_and_with_no_merges_nothing_changes():
    assert (
        rules.poll_plan(closes=[], resumes=[], launches=[], available_slots=0, now=NOW, merges=[7])
        == []
    )
    assert rules.poll_plan(closes=[3], resumes=[], launches=[], available_slots=1, now=NOW) == [
        rules.PollAction("close", 3)
    ]


def test_poll_plan_a_retake_takes_the_slot_a_merge_would_have_used():
    actions = rules.poll_plan(
        closes=[],
        resumes=[],
        launches=[],
        available_slots=1,
        now=NOW,
        retakes=[2],
        merges=[7],
    )
    assert actions == [rules.PollAction("retake", 2)]


def test_merge_calls_below_the_budget_still_merge_and_the_budget_refuses():
    assert rules.MERGE_CALLS_MAX == 3
    below = _facts(merge_failures=rules.MERGE_CALLS_MAX - 1, merge_failure="gh_rejected")
    assert rules.auto_merge_decision(below) == MergeDecision("merge", None)
    spent = _facts(merge_failures=rules.MERGE_CALLS_MAX, merge_failure="gh_rejected")
    assert rules.auto_merge_decision(spent) == MergeDecision("refuse", "merge_failed")


def test_a_spent_merge_budget_is_not_sent_back_to_run_checks_when_master_moved():
    facts = _facts(merge_failures=rules.MERGE_CALLS_MAX, checks_master="d" * 40, attempts=0)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "merge_failed")


def test_an_earlier_refusal_wins_over_a_spent_merge_budget():
    facts = _facts(merge_failures=rules.MERGE_CALLS_MAX, pr_state="MERGED")
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "pr_not_open")
    guarded = _facts(merge_failures=rules.MERGE_CALLS_MAX, touched_paths=(".github/x.yml",))
    assert rules.auto_merge_decision(guarded).reason == "guarded_path"


def test_the_decision_never_reads_the_failure_class():
    facts = _facts(merge_failures=rules.MERGE_CALLS_MAX)
    assert rules.auto_merge_decision(replace(facts, merge_failure="no_merge_method")) == (
        rules.auto_merge_decision(replace(facts, merge_failure=None))
    )


def test_every_merge_failure_class_has_advice_and_the_terminal_ones_are_classes():
    classes = set(get_args(rules.MergeFailure))
    assert set(rules.MERGE_FAILURE_ADVICE) == classes
    assert all(rules.MERGE_FAILURE_ADVICE[c].strip() for c in classes)
    assert rules.TERMINAL_MERGE_FAILURES <= classes
    assert rules.MERGE_FAILURE_UNKNOWN_ADVICE.strip()


FULL_HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"


def test_a_short_reviewed_head_that_prefixes_the_full_pr_head_is_reviewed():
    facts = _facts(reviewed_head=FULL_HEAD[:7], pr_head=FULL_HEAD, checks_head=FULL_HEAD)
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


def test_a_different_short_reviewed_head_is_not_reviewed():
    facts = _facts(reviewed_head="1234567", pr_head=FULL_HEAD, checks_head=FULL_HEAD)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_a_sha256_pr_head_equal_to_the_reviewed_head_is_reviewed():
    head = "c" * 64
    facts = _facts(reviewed_head=head, pr_head=head, checks_head=head)
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)


@pytest.mark.parametrize("length", [39, 41])
def test_a_pr_head_that_is_not_a_full_id_is_not_reviewed_even_when_equal(length):
    head = "a" * length
    facts = _facts(reviewed_head=head, pr_head=head, checks_head=head)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


def test_a_short_pr_head_is_not_reviewed_even_when_the_reviewed_head_is_full_and_prefixed():
    facts = _facts(reviewed_head=FULL_HEAD, pr_head=FULL_HEAD[:7], checks_head=FULL_HEAD)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")


@pytest.mark.parametrize("reviewed", [None, "", "  "])
def test_an_empty_reviewed_head_is_not_reviewed(reviewed):
    facts = _facts(reviewed_head=reviewed)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", "head_not_reviewed")
