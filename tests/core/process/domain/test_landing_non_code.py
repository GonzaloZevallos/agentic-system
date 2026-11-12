"""A head that fails for something outside its code is never restarted (GH-610): the pull request
body that lacks the product's `Breaking-API:` line, a CI that did not run. Seams:
`non_code_failure`, `landing_step`, `after_diagnosis` and `api_break_answer`. Pure: facts in, a
step out."""

from dataclasses import dataclass, replace
from typing import get_args

import pytest

from agentic.core.process.domain import ci, landing, rules
from agentic.core.process.domain.landing import (
    LandingStep,
    NonCodeState,
    api_break_answer,
    non_code_failure,
)
from agentic.core.process.domain.task import VERDICT_SYNC
from tests.core.process.landing_fixtures import API_BREAK_LINE, HEAD, gh686_body_only

REOPENED = "2026-11-11T09:00:00+00:00"
BEFORE = "2026-11-10T10:00:00Z"
AFTER = "2026-11-11T09:30:00Z"
PREFIX = "Breaking-API:"


def cause(facts, *, declares=False, reopened_at=None, refusal="ci_failed", line=API_BREAK_LINE):
    return non_code_failure(
        refusal, facts, api_break_line=line, body_declares=declares, reopened_at=reopened_at
    )


def step_of(refusal="ci_failed", *, facts=None, spent=None, slicing="no_clean_slice", **state):
    default_facts, default_spent = gh686_body_only()
    named = NonCodeState(cause="pr_body", runs_completed=True, **state)
    return landing.landing_step(
        refusal,
        facts or default_facts,
        spent or default_spent,
        (),
        turn="head",
        slicing=slicing,
        non_code=named,
    )


# --- non_code_failure -----------------------------------------------------------------------


def test_docs_red_at_the_declared_step_with_the_line_missing_is_the_body():
    facts, _ = gh686_body_only()
    assert cause(facts) == "pr_body"


@pytest.mark.parametrize("docs_step", [None, "Run pytest", "api_breaks (retry)"])
def test_a_missing_or_other_step_is_not_the_body(docs_step):
    facts, _ = gh686_body_only(docs_step=docs_step)
    assert cause(facts) is None


def test_a_product_that_declares_no_line_has_no_body_cause():
    facts, _ = gh686_body_only()
    assert cause(facts, line=None) is None
    assert cause(replace(facts, ci_verdict=None)) is None


def test_the_check_is_recognised_by_name_not_by_log_text():
    facts, _ = gh686_body_only()
    other = replace(API_BREAK_LINE, check="lint")
    assert cause(facts, line=other) is None


@pytest.mark.parametrize(
    ("declares", "reopened_at", "started", "expected"),
    [
        (True, REOPENED, BEFORE, "pr_body"),  # the red run read the old body
        (True, REOPENED, AFTER, None),  # a run began after the reopening: a real failure
        (True, None, AFTER, "pr_body"),  # declared by hand, never reopened by the system
        (False, REOPENED, AFTER, "pr_body"),  # the fix did not stick
    ],
)
def test_before_and_after_the_fresh_run(declares, reopened_at, started, expected):
    facts, _ = gh686_body_only(started=started)
    assert cause(facts, declares=declares, reopened_at=reopened_at) == expected


@pytest.mark.parametrize("refusal", ["ci_missing", "ci_unreadable", "eval_set_unreadable"])
def test_a_ci_that_did_not_run_on_a_head_outside_github_is_ci_not_run(refusal):
    facts, _ = gh686_body_only()
    assert cause(facts, refusal=refusal, line=None) == "ci_not_run"
    on_ci = replace(facts, touched_paths=("src/a.py", ".github/workflows/ci.yml"))
    assert cause(on_ci, refusal=refusal, line=None) is None
    assert cause(replace(facts, touched_paths=None), refusal=refusal, line=None) is None


# --- landing_step --------------------------------------------------------------------------


def test_gh686_with_a_recorded_reason_writes_the_line_instead_of_restarting():
    assert step_of(reason_recorded=True) == LandingStep("fix_pr_body", "ci_failed")


def test_gh686_without_a_reason_asks_the_owner_once_per_head():
    assert step_of().kind == "escalate_api_break"
    assert step_of(asked=True).kind == "none"


def test_a_fix_is_chosen_once_per_head_and_then_waits_for_its_fresh_run():
    assert step_of(reason_recorded=True, fix_done=True, body_declares=True).kind == "wait"
    assert step_of(reason_recorded=True, fix_done=True, body_declares=False).kind == "escalate"
    _, spent = gh686_body_only()
    late = replace(spent, waited_s=float(ci.CI_WAIT_MAX_S))
    assert step_of(reason_recorded=True, fix_done=True, body_declares=True, spent=late).kind == (
        "escalate"
    )


def test_a_released_base_red_with_the_body_as_cause_never_reruns_the_old_event():
    _, spent = gh686_body_only()
    released = replace(spent, ci_release_due=True, head_has_upstream=True)
    assert step_of(reason_recorded=True, spent=released).kind == "fix_pr_body"
    assert step_of(spent=released).kind == "escalate_api_break"
    # Not the body: the released base is still the one rerun for the pair, as before.
    code = landing.landing_step(
        "ci_failed",
        gh686_body_only()[0],
        released,
        (),
        turn="head",
        slicing="no_clean_slice",
        non_code=NonCodeState(cause=None, runs_completed=True),
    )
    assert code.kind == "rerun"


@pytest.mark.parametrize("completed", [False, None])
def test_live_or_unknown_runs_are_a_wait_never_a_fix_or_a_question(completed):
    facts, spent = gh686_body_only()
    for recorded in (True, False):
        named = NonCodeState("pr_body", reason_recorded=recorded, runs_completed=completed)
        got = landing.landing_step(
            "ci_failed", facts, spent, (), turn="head", slicing="no_clean_slice", non_code=named
        )
        assert got.kind == "wait"


def test_docs_and_a_test_red_together_fix_the_body_first_then_fall_back_to_the_code_path():
    facts, _ = gh686_body_only(started=BEFORE)
    fresh = LandingStep("fix_pr_body", "ci_failed")
    fresh_spent = replace(gh686_body_only()[1], passes=0, syncs=0, reruns_on_head=0)
    assert step_of(reason_recorded=True, facts=facts, spent=fresh_spent) == fresh
    # After the fresh run docs is still red at the step with the line there: no cause, the code's.
    after, _ = gh686_body_only(started=AFTER)
    assert cause(after, declares=True, reopened_at=REOPENED) is None
    plain = landing.landing_step(
        "ci_failed", after, fresh_spent, (), turn="head", non_code=NonCodeState()
    )
    assert plain.kind == "rerun"


def test_without_a_declared_line_the_ci_failed_steps_are_byte_for_byte_todays():
    facts, spent = gh686_body_only()
    fresh = replace(spent, passes=0, reruns_on_head=0)
    for what in (fresh, replace(fresh, reruns_on_head=1), spent):
        assert landing.landing_step(
            "ci_failed", facts, what, ("api",), turn="head", slicing="no_clean_slice"
        ) == landing.landing_step(
            "ci_failed",
            facts,
            what,
            ("api",),
            turn="head",
            slicing="no_clean_slice",
            non_code=NonCodeState(),
        )


@pytest.mark.parametrize("refusal", ["ci_missing", "ci_unreadable", "eval_set_unreadable"])
def test_an_exhausted_ci_that_did_not_run_escalates_unless_the_head_touches_github(refusal):
    facts, spent = gh686_body_only()
    waited = replace(spent, waited_s=float(ci.CI_WAIT_MAX_S))
    outside = cause(facts, refusal=refusal, line=None)
    named = NonCodeState(cause=outside)
    got = landing.landing_step(refusal, facts, waited, (), turn="head", non_code=named)
    assert got.kind == "escalate"
    on_ci = replace(facts, touched_paths=(".github/workflows/ci.yml",))
    inside = NonCodeState(cause=cause(on_ci, refusal=refusal, line=None))
    assert landing.landing_step(refusal, on_ci, waited, (), turn="head", non_code=inside).kind == (
        "diagnose"
    )


def test_no_refusal_and_no_spent_budget_ends_in_a_restart_while_there_is_a_cause():
    facts, spent = gh686_body_only()
    exhausted = replace(
        spent,
        waited_s=float(ci.CI_WAIT_MAX_S),
        conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX,
        unsynced_for_upstream=landing.LANDING_UNSYNCED_MAX,
        conflict_ci_syncs_on_pair=1,
    )
    slices = ("no_clean_slice", "single_unit", "unreadable", landing.Slice(("api",)))
    for refusal in get_args(rules.MergeRefusal):
        for named in ("pr_body", "ci_not_run"):
            for recorded in (True, False):
                for slicing in slices:
                    got = landing.landing_step(
                        refusal,
                        facts,
                        exhausted,
                        ("api",),
                        turn="head",
                        slicing=slicing,
                        non_code=NonCodeState(named, reason_recorded=recorded, runs_completed=True),
                    )
                    assert got.kind not in ("diagnose", "land_slice"), (refusal, named)


def test_after_diagnosis_never_restarts_a_non_code_failure():
    spent = replace(gh686_body_only()[1], diagnoses=1)
    assert landing.after_diagnosis(None, spent, 0) == "restart"
    assert landing.after_diagnosis(None, spent, 0, non_code=True) == "escalate"
    due = replace(spent, verdict_sync_due=True, syncs=0)
    assert landing.after_diagnosis(VERDICT_SYNC, due, 0, non_code=True) == "sync_master"


# --- the owner's answer: the six states of escalate_api_break --------------------------------

ASKED_AT = "2026-11-11T10:00:00+00:00"
ANSWER_AT = "2026-11-11T11:00:00+00:00"


def asked_document(**entry):
    return {
        "escalated": "api break missing",
        "escalation_class": "questions",
        "status": "ready_for_review",
        "landing": [
            {
                "head": HEAD,
                "step": "escalate_api_break",
                "refusal": "ci_failed",
                "at": ASKED_AT,
                "resolved_at": ASKED_AT,
                **entry,
            }
        ],
    }


@dataclass
class Comment:
    body: str
    created_at: str


def test_state_1_and_2_the_question_is_published_once_and_then_waits():
    assert step_of().kind == "escalate_api_break"  # state 1
    document = asked_document()
    assert landing.waits_on_human(
        document,
        HEAD,
        "ci_failed",
        spent=gh686_body_only()[1],
        stopped=False,
        needs_human=False,
        stranded=False,
    )
    assert step_of(asked=True).kind == "none"  # state 2
    assert not landing.already_dispatched(
        [{"head": HEAD, "step": "escalate_api_break"}], HEAD, "escalate_api_break", None
    )
    assert landing.already_dispatched(document["landing"], HEAD, "escalate_api_break", None)


def test_state_3_a_valid_answer_records_the_reason_releases_and_the_fix_follows():
    answer = api_break_answer(
        asked_document(), "ok\nBreaking-API: drops the v1 route\n", ANSWER_AT, prefix=PREFIX
    )
    assert answer.outcome == "decided"
    (entry,) = answer.patch["landing"]
    assert entry["api_break_decided"] == "drops the v1 route"
    assert entry["released_at"] == ANSWER_AT
    assert answer.patch["escalated"] is None
    decided = {"landing": answer.patch["landing"]}
    assert landing.recorded_api_break("", decided["landing"]) == "drops the v1 route"
    assert step_of(reason_recorded=True).kind == "fix_pr_body"


def test_state_4_an_invalid_answer_is_reasked_once_with_its_reason_and_stays_escalated():
    answer = api_break_answer(
        asked_document(), "Breaking-API: uses `code`", ANSWER_AT, prefix=PREFIX
    )
    assert (answer.outcome, answer.reason) == ("reasked", "forbidden_char")
    (entry,) = answer.patch["landing"]
    assert (entry["rejected"], entry["reasks"]) == ("forbidden_char", 1)
    assert "escalated" not in answer.patch and "released_at" not in entry


def test_state_5_an_invalid_answer_after_the_reask_is_annotated_and_nothing_is_posted():
    spent = asked_document(reasks=landing.LANDING_API_BREAK_REASKS_MAX, rejected="too_long")
    answer = api_break_answer(spent, "Breaking-API: " + "x" * 300, ANSWER_AT, prefix=PREFIX)
    assert (answer.outcome, answer.reason) == ("rejected", "too_long")
    (entry,) = answer.patch["landing"]
    assert (entry["rejected"], entry["reasks"]) == ("too_long", 1)


def test_state_6_a_valid_answer_after_a_rejection_is_the_same_as_the_first():
    document = asked_document(reasks=1, rejected="too_long")
    answer = api_break_answer(document, "Breaking-API: now short", ANSWER_AT, prefix=PREFIX)
    assert answer.outcome == "decided"
    assert answer.patch["landing"][0]["api_break_decided"] == "now short"


@pytest.mark.parametrize("comment", ["no line here", "see Breaking-API: not at the start", ""])
def test_a_comment_without_the_line_is_not_an_answer(comment):
    assert api_break_answer(asked_document(), comment, ANSWER_AT, prefix=PREFIX).outcome == "none"
    assert (
        api_break_answer({"landing": []}, "Breaking-API: x", ANSWER_AT, prefix=PREFIX).patch == {}
    )


def test_the_answer_is_told_by_the_skew_boundary_of_the_question():
    document = asked_document()
    skew = landing.ADR_RELEASE_SKEW
    asked = landing._aware(ASKED_AT)
    assert asked is not None
    just_before = Comment("Breaking-API: x", (asked - skew + (skew / 4)).isoformat())
    too_early = Comment("Breaking-API: x", (asked - skew - (skew / 4)).isoformat())
    exact = Comment("Breaking-API: x", (asked - skew).isoformat())
    assert landing.owner_told(document, [just_before], PREFIX)
    assert not landing.owner_told(document, [too_early], PREFIX)
    assert not landing.owner_told(document, [exact], PREFIX)  # strictly after


def test_recorded_reason_prefers_the_plan_and_reads_nothing_else_from_entries():
    entries = [{"step": "escalate_api_break", "api_break_decided": "late answer"}]
    assert landing.recorded_api_break("plan reason", entries) == "plan reason"
    assert landing.recorded_api_break("", entries) == "late answer"
    assert landing.recorded_api_break("  ", []) is None


def test_the_reopening_of_the_head_is_the_resolved_at_of_its_finished_fix():
    entries = [
        {"head": "b" * 40, "step": "fix_pr_body", "resolved_at": "x"},
        {"head": HEAD, "step": "fix_pr_body", "at": "t0"},
    ]
    assert landing.pr_body_reopened_at(entries, HEAD) is None
    entries[1]["resolved_at"] = REOPENED
    assert landing.pr_body_reopened_at(entries, HEAD) == REOPENED


def test_a_pull_request_closed_by_an_open_body_fix_is_not_a_closed_result():
    open_fix = {"landing": [{"head": HEAD, "step": "fix_pr_body", "at": "t"}]}
    assert rules.closed_for_body_fix(open_fix, HEAD)
    assert not rules.closed_for_body_fix(open_fix, "b" * 40)
    assert not rules.closed_for_body_fix(open_fix, None)
    finished = {"landing": [{"head": HEAD, "step": "fix_pr_body", "resolved_at": "t"}]}
    assert not rules.closed_for_body_fix(finished, HEAD)
    assert not rules.closed_for_body_fix({"landing": []}, HEAD)


def test_the_new_steps_spend_no_model():
    assert landing.run_kind_of("fix_pr_body", "ci_failed") == "land_checks"
    assert landing.run_kind_of("escalate_api_break", "ci_failed") == "land_checks"
