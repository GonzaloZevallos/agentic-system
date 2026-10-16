"""`rules.auto_merge_decision` (GH-178, ADR-0045): the one place every condition of the
poller merging a pull request on its own lives. Pure: each case is a `MergeFacts` and the
`MergeDecision` it must give, one row per refusal with the input that provokes it
(ADR-0023), never a poll round."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.domain import ci, rules
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
        open_blocking=0,
        open_important=0,
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
    ("open_findings_blocking", {"open_blocking": 1}),
    ("open_findings", {"open_important": 1}),
    ("eval_set", {"in_eval_set": True}),
    ("eval_set_unreadable", {"in_eval_set": None}),
    (
        "attempts_exhausted",
        {"checks_master": "d" * 40, "attempts": rules.MERGE_ATTEMPTS_MAX},
    ),
    ("checks_failed", {"checks_passed": False}),
    ("merge_failed", {"merge_failures": rules.MERGE_CALLS_MAX}),
]


@pytest.mark.parametrize("conflicts", [None, False], ids=["unread", "clean"])
@pytest.mark.parametrize(("reason", "over"), REFUSALS, ids=[r for r, _ in REFUSALS])
def test_each_refusal_has_the_input_that_provokes_it(reason, over, conflicts):
    assert rules.auto_merge_decision(_facts(conflicts=conflicts, **over)) == MergeDecision(
        "refuse", reason
    )
    assert rules.auto_merge_decision(_facts(conflicts=conflicts)) == MergeDecision("merge", None)


def test_every_refusal_the_decision_gives_is_in_the_closed_set_and_master_moved_is_the_verbs():
    given = {reason for reason, _ in REFUSALS}
    # The CI's own reasons are given, one row each, by `test_auto_merge_ci.py`.
    ci_reasons = {"ci_failed", "ci_missing", "ci_unreadable"}
    # `merge_conflict` has its own table below: it needs `conflicts=True`, no `over` row.
    # The ADR collision's is given by `test_adr_refusal.py`.
    others = {"master_moved", "merge_conflict", "adr_number_taken"}
    assert given | ci_reasons | others == set(get_args(rules.MergeRefusal))


EARLIER_FAILURES = [
    {"reviewed_unit_approved": False},
    {"reviewed_head": "c" * 40},
    {"open_blocking": 1},
    {"open_important": 1},
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


# GH-263 (ADR-0045 addendum): a merge child runs no model, so it takes no slot of the cap.
# Each test below used to assert "a merge only goes into a spare slot"; they now assert the
# opposite and name the rule that replaced it.


def _plan(**kwargs) -> rules.PollPlan:
    base: dict = {"closes": [], "resumes": [], "launches": [], "now": NOW}
    return rules.poll_plan(**{**base, **kwargs})


def test_poll_plan_places_one_merge_after_the_launch_and_defers_the_other():
    plan = _plan(
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)], available_slots=2, merges=[9, 7]
    )
    assert plan.actions == [rules.PollAction("launch", 4), rules.PollAction("merge", 7)]
    assert plan.deferred == [rules.Deferred("merge", 9, "exempt_busy")]


def test_poll_plan_still_places_the_merge_when_the_launch_took_the_last_slot():
    # Was `spends_no_slot_on_a_merge_when_the_launch_took_the_last_one`: the merge no
    # longer needs a slot, so it goes out beside the launch.
    plan = _plan(
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)], available_slots=1, merges=[7]
    )
    assert plan.actions == [rules.PollAction("launch", 4), rules.PollAction("merge", 7)]
    assert plan.deferred == []


def test_poll_plan_with_no_slot_still_places_the_merge_and_with_no_merges_nothing_changes():
    # Was `with_no_slot_places_no_merge`: the cap guards the model session, not the merge.
    assert _plan(available_slots=0, merges=[7]).actions == [rules.PollAction("merge", 7)]
    plan = rules.poll_plan(closes=[3], resumes=[], launches=[], available_slots=1, now=NOW)
    assert plan == rules.PollPlan([rules.PollAction("close", 3)], [])


def test_poll_plan_a_retake_and_a_merge_both_go_out_the_merge_last():
    # Was `a_retake_takes_the_slot_a_merge_would_have_used`: they no longer compete.
    plan = _plan(available_slots=1, retakes=[2], merges=[7])
    assert plan.actions == [rules.PollAction("retake", 2), rules.PollAction("merge", 7)]


def test_poll_plan_the_merge_is_emitted_last_with_every_slot_taken():
    resume = rules.ResumeCandidate(3, "account_limit", NOW - timedelta(days=1), 1, 5)
    plan = _plan(
        available_slots=0,
        retakes=[2],
        resumes=[resume],
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)],
        merges=[7],
    )
    assert plan.actions == [rules.PollAction("merge", 7)]
    two = _plan(
        available_slots=2,
        retakes=[2],
        resumes=[resume],
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)],
        merges=[7],
    )
    assert two.actions == [
        rules.PollAction("retake", 2),
        rules.PollAction("resume", 3),
        rules.PollAction("merge", 7),
    ]


def test_poll_plan_a_live_exempt_child_defers_the_merge_and_leaves_the_launch_alone():
    plan = _plan(
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)],
        available_slots=1,
        merges=[7],
        live_exempt=1,
    )
    assert plan.actions == [rules.PollAction("launch", 4)]
    assert plan.deferred == [rules.Deferred("merge", 7, "exempt_busy")]


def test_poll_plan_a_merge_and_a_checks_land_start_only_the_merge():
    checks = rules.LandCandidate(4, "land_checks")
    plan = _plan(available_slots=0, merges=[7], lands=[checks])
    assert plan.actions == [rules.PollAction("merge", 7)]
    assert plan.deferred == [rules.Deferred("land", 4, "exempt_busy")]


def test_poll_plan_a_checks_land_starts_when_no_merge_waits_and_one_per_round():
    lands = [rules.LandCandidate(9, "land_checks"), rules.LandCandidate(4, "land_checks")]
    plan = _plan(available_slots=0, lands=lands)
    assert plan.actions == [rules.PollAction("land", 4)]
    assert plan.deferred == [rules.Deferred("land", 9, "exempt_busy")]
    busy = _plan(available_slots=0, lands=lands, live_exempt=1)
    assert busy.actions == []
    assert busy.deferred == [
        rules.Deferred("land", 4, "exempt_busy"),
        rules.Deferred("land", 9, "exempt_busy"),
    ]


def test_poll_plan_a_model_land_without_a_slot_is_deferred_no_slot():
    plan = _plan(available_slots=0, lands=[rules.LandCandidate(4, "land_model")])
    assert plan.actions == []
    assert plan.deferred == [rules.Deferred("land", 4, "no_slot")]


def test_poll_plan_a_model_land_with_a_slot_goes_out_before_the_exempt_action():
    lands = [rules.LandCandidate(9, "land_model"), rules.LandCandidate(4, "land_model")]
    plan = _plan(available_slots=1, merges=[7], lands=lands)
    assert plan.actions == [rules.PollAction("land", 4), rules.PollAction("merge", 7)]
    assert plan.deferred == [rules.Deferred("land", 9, "no_slot")]


@pytest.mark.parametrize("slots", [0, 1, 2, 3])
@pytest.mark.parametrize("live_exempt", [0, 1])
def test_poll_plan_no_exempt_action_precedes_a_slot_taking_one(slots, live_exempt):
    resume = rules.ResumeCandidate(3, "account_limit", NOW - timedelta(days=1), 1, 5)
    plan = _plan(
        available_slots=slots,
        live_exempt=live_exempt,
        retakes=[2],
        resumes=[resume],
        launches=[rules.LaunchCandidate(4, False, None, 0, 3)],
        merges=[7],
        lands=[rules.LandCandidate(5, "land_model"), rules.LandCandidate(6, "land_checks")],
    )
    exempt = [i for i, a in enumerate(plan.actions) if a in (rules.PollAction("merge", 7),)]
    exempt += [i for i, a in enumerate(plan.actions) if a == rules.PollAction("land", 6)]
    taking = [i for i, a in enumerate(plan.actions) if i not in exempt]
    assert not exempt or not taking or min(exempt) > max(taking)
    assert len(exempt) <= 1 and (live_exempt == 0 or not exempt)


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
    findings = _facts(merge_failures=rules.MERGE_CALLS_MAX, open_blocking=1)
    assert rules.auto_merge_decision(findings).reason == "open_findings_blocking"


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


def test_blocking_findings_refuse_before_important_ones_and_a_filed_followup_skips_only_those():
    both = _facts(open_blocking=1, open_important=2)
    assert rules.auto_merge_decision(both) == MergeDecision("refuse", "open_findings_blocking")
    filed = replace(both, followups_filed=True)
    assert rules.auto_merge_decision(filed) == MergeDecision("refuse", "open_findings_blocking")
    important = _facts(open_important=2, followups_filed=True)
    assert rules.auto_merge_decision(important) == MergeDecision("merge", None)
    assert rules.auto_merge_decision(replace(important, followups_filed=False)) == MergeDecision(
        "refuse", "open_findings"
    )


@pytest.mark.parametrize(
    "path",
    [".github/workflows/ci.yml", ".agentic/stack.yaml", "CODING_STANDARDS.md"],
)
def test_a_guarded_path_no_longer_refuses_it_is_recorded_instead(path):
    facts = _facts(touched_paths=("agentic/core/x.py", path))
    assert rules.auto_merge_decision(facts) == MergeDecision("merge", None)
    assert "guarded_path" not in get_args(rules.MergeRefusal)


@pytest.mark.parametrize(
    ("paths", "untrusted"),
    [
        (None, None),
        ((), False),
        (("agentic/core/x.py",), False),
        ((".agentic/stack.yaml",), False),
        (("agentic/x.py", ".github/workflows/ci.yml"), True),
        ((".github/actions/setup/action.yml",), True),
        (("docs/.github/x.md",), False),
    ],
)
def test_ci_untrusted_is_unknown_for_unknown_paths_and_true_under_the_ci_definition(
    paths, untrusted
):
    assert rules.ci_untrusted(paths) is untrusted


def test_the_ci_definition_prefix_is_the_one_both_predicates_read():
    assert rules.CI_DEFINITION_PREFIX == ".github/"
    assert rules.guarded_path(".github/x.yml", "GH-9") is True
    assert rules.ci_untrusted((".github/x.yml",)) is True


# A definite conflict of the head with the current master (GH-255): refused ahead of the CI
# facts and the checks, behind every review and eval-set reason.

CI_RED = ci.CiCheck("web", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1")
CI_DECLARED = {"ci_required": ("web",), "ci_untrusted": False, "ci_head": HEAD, "ci_waited_s": 10.0}


def _ci(state, **kw) -> dict:
    return {**CI_DECLARED, "ci_verdict": ci.CiVerdict(state, (), ()), **kw}


CONFLICT_BEATS = [
    (
        "ci_unreadable",
        {
            **CI_DECLARED,
            "ci_unreadable": ci.CiUnreadable("gh_unreadable"),
            "ci_verdict": None,
            "ci_head": None,
            "ci_waited_s": ci.CI_WAIT_MAX_S + 1,
        },
    ),
    ("ci_wait", _ci("pending")),
    ("ci_failed", {**_ci("red"), "ci_verdict": ci.CiVerdict("red", (CI_RED,), ())}),
    ("ci_missing", _ci("pending", ci_waited_s=ci.CI_WAIT_MAX_S)),
    ("ci_green_fresh_merge", _ci("green", ci_fresh=True)),
    ("checks_failed", {"checks_passed": False}),
    ("attempts_exhausted", {"checks_master": "d" * 40, "attempts": rules.MERGE_ATTEMPTS_MAX}),
    ("run_checks", {"checks_head": None, "checks_master": None, "checks_passed": None}),
    ("merge", {}),
    ("merge_failed", {"merge_failures": rules.MERGE_CALLS_MAX}),
]


@pytest.mark.parametrize(("name", "over"), CONFLICT_BEATS, ids=[n for n, _ in CONFLICT_BEATS])
def test_a_definite_conflict_is_refused_ahead_of_everything_from_merge_failed_down(name, over):
    facts = _facts(**over)
    assert rules.auto_merge_decision(facts) != MergeDecision("refuse", "merge_conflict")
    assert rules.auto_merge_decision(replace(facts, conflicts=True)) == MergeDecision(
        "refuse", "merge_conflict"
    )


@pytest.mark.parametrize(("reason", "over"), REFUSALS[:8], ids=[r for r, _ in REFUSALS[:8]])
def test_every_review_and_eval_set_reason_still_beats_a_definite_conflict(reason, over):
    facts = _facts(conflicts=True, **over)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", reason)


def test_the_first_eight_rows_are_the_reasons_ahead_of_the_conflict():
    assert [r for r, _ in REFUSALS[:8]] == [
        "not_ready",
        "pr_not_open",
        "not_approved",
        "head_not_reviewed",
        "open_findings_blocking",
        "open_findings",
        "eval_set",
        "eval_set_unreadable",
    ]


# `conflict_probe_wanted` is the one answer to "should the probe run", derived from the order.

PROBE_NOT_WANTED = REFUSALS[:8]
PROBE_WANTED = CONFLICT_BEATS


@pytest.mark.parametrize(("reason", "over"), PROBE_NOT_WANTED, ids=[r for r, _ in PROBE_NOT_WANTED])
def test_the_probe_is_not_wanted_for_a_pull_request_a_git_free_refusal_already_rules_out(
    reason, over
):
    facts = _facts(**over)
    assert rules.auto_merge_decision(facts) == MergeDecision("refuse", reason)
    assert rules.conflict_probe_wanted(facts) is False


@pytest.mark.parametrize(("name", "over"), PROBE_WANTED, ids=[n for n, _ in PROBE_WANTED])
def test_the_probe_is_wanted_when_the_first_decision_is_one_a_conflict_would_overrule(name, over):
    assert rules.conflict_probe_wanted(_facts(**over)) is True


def test_the_probe_gate_ignores_the_conflict_fact_it_is_asked_to_derive():
    facts = _facts(checks_passed=False)
    for known in (None, False, True):
        assert rules.conflict_probe_wanted(replace(facts, conflicts=known)) is True
