"""The landing table (GH-239, ADR-0048): `landing_step` maps every `MergeRefusal` onto one
closed step, the predicates around it say when a step may be taken, and the record keys
are the ones a poller tick reads back. Pure: facts in, a step out."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args, get_type_hints

import pytest

from agentic.core.process.domain import ci, document, landing, rules
from agentic.core.process.domain.landing import LandingSpent, LandingStep
from agentic.core.process.domain.rules import MergeFacts
from agentic.core.process.domain.task import (
    Carried,
    DiagnosisVerdict,
    FailureReason,
    LandedSliceRef,
    LandingEntry,
)
from tests.gh150_stranded import GH150_STRANDED

HEAD = "a" * 40
MASTER = "b" * 40


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
        touched_paths=("agentic/x.py",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
    )
    return replace(base, **over)


def _spent(**over) -> LandingSpent:
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnoses=0, waited_s=None)
    return replace(base, **over)


def _step(refusal, spent=None, owners=(), **facts) -> LandingStep:
    return landing.landing_step(refusal, _facts(**facts), spent or _spent(), owners)


EXPECTED = {
    "not_ready": "none",
    "pr_not_open": "none",
    "eval_set": "none",
    "eval_set_unreadable": "wait",
    "ci_missing": "wait",
    "ci_unreadable": "wait",
    "ci_blocked_by_conflict": "sync_master",
    "not_approved": "re_review",
    "head_not_reviewed": "re_review",
    "open_findings_blocking": "fix",
    "open_findings": "file_followups_and_land",
    "ci_failed": "rerun",
    "checks_failed": "rerun",
    "attempts_exhausted": "sync_master",
    "merge_conflict": "sync_master",
    "merge_failed": "diagnose",
    "master_moved": "wait",
    "adr_number_taken": "escalate_adr",
    "perf_unverified": "none",
    "criteria_over_cap": "none",
    "criteria_unreadable": "none",
}


def test_the_table_covers_exactly_the_closed_refusal_set():
    assert set(EXPECTED) == set(get_args(rules.MergeRefusal))
    assert set(EXPECTED.values()) <= set(get_args(landing.LandingStepKind))


@pytest.mark.parametrize("refusal", get_args(rules.MergeRefusal))
def test_every_refusal_has_a_step_under_fresh_budgets(refusal):
    step = _step(refusal, owners=("api",))
    assert step == LandingStep(EXPECTED[refusal], refusal, step.units)
    assert step.kind in get_args(landing.LandingStepKind)


@pytest.mark.parametrize("refusal", get_args(rules.MergeRefusal))
def test_every_refusal_still_has_a_step_when_every_budget_is_spent(refusal):
    spent = _spent(
        passes=landing.LANDING_PASSES_MAX, syncs=landing.LANDING_SYNCS_MAX, reruns_on_head=1
    )
    step = _step(refusal, spent, owners=("api",))
    assert isinstance(step, LandingStep) and step.refusal == refusal
    assert step.kind in {"none", "wait", "diagnose", "file_followups_and_land", "escalate_adr"}


@pytest.mark.parametrize("refusal", get_args(rules.MergeRefusal))
def test_a_diagnosed_task_has_nothing_left_to_do(refusal):
    assert _step(refusal, _spent(diagnoses=1), owners=("api",)).kind == "none"


def test_a_blocking_record_of_a_sibling_reopens_that_unit_not_the_approved_one():
    step = _step("open_findings_blocking", owners=("api",), open_blocking=1)
    assert step == LandingStep("fix", "open_findings_blocking", ("api",))


def test_a_pass_that_would_exceed_the_budget_diagnoses_instead():
    spent = _spent(passes=landing.LANDING_PASSES_MAX)
    assert _step("open_findings_blocking", spent, ("api",)).kind == "diagnose"
    assert _step("head_not_reviewed", spent).kind == "diagnose"
    below = _spent(passes=landing.LANDING_PASSES_MAX - 1)
    assert _step("head_not_reviewed", below).kind == "re_review"


def test_a_sync_that_would_exceed_the_budget_diagnoses_instead():
    assert _step("attempts_exhausted", _spent(syncs=landing.LANDING_SYNCS_MAX - 1)).kind == (
        "sync_master"
    )
    assert _step("attempts_exhausted", _spent(syncs=landing.LANDING_SYNCS_MAX)).kind == "diagnose"


def test_a_conflicting_checks_failure_syncs_and_needs_the_sync_budgets():
    assert _step("checks_failed", failed_step="conflict").kind == "sync_master"
    capped = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX)
    assert _step("checks_failed", capped, failed_step="conflict").kind == "diagnose"
    syncs = _spent(syncs=landing.LANDING_SYNCS_MAX)
    assert _step("checks_failed", syncs, failed_step="conflict").kind == "diagnose"


def test_a_definite_conflict_syncs_while_both_budgets_last_then_diagnoses():
    assert _step("merge_conflict").kind == "sync_master"
    last = _spent(
        conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX - 1,
        syncs=landing.LANDING_SYNCS_MAX - 1,
    )
    assert _step("merge_conflict", last) == LandingStep("sync_master", "merge_conflict")
    capped = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX)
    assert _step("merge_conflict", capped).kind == "diagnose"
    syncs = _spent(syncs=landing.LANDING_SYNCS_MAX)
    assert _step("merge_conflict", syncs).kind == "diagnose"
    assert _step("merge_conflict", _spent(diagnoses=1)).kind == "none"


def test_a_ci_blocked_by_a_conflict_syncs_once_per_pair_without_the_conflict_cap():
    """GH-395: GitHub runs no CI on a conflicting head, so the sync is the step; the per-sha
    conflict cap is not consulted, only the pair's own counter, then the diagnose."""
    assert _step("ci_blocked_by_conflict") == LandingStep("sync_master", "ci_blocked_by_conflict")
    capped = _spent(conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX)
    assert _step("ci_blocked_by_conflict", capped).kind == "sync_master"
    again = _spent(conflict_ci_syncs_on_pair=1)
    assert _step("ci_blocked_by_conflict", again).kind == "diagnose"
    assert _step("ci_blocked_by_conflict", _spent(conflict_ci_syncs_on_pair=3)).kind == "diagnose"


def test_a_ci_blocked_by_a_conflict_never_waits_and_keeps_the_task_wide_sync_budgets():
    for waited in (None, 0.0, ci.CI_WAIT_MAX_S + 1):
        assert _step("ci_blocked_by_conflict", _spent(waited_s=waited)).kind == "sync_master"
    assert _step("ci_blocked_by_conflict", _spent(syncs=landing.LANDING_SYNCS_MAX)).kind == (
        "diagnose"
    )
    unsynced = _spent(unsynced_for_upstream=landing.LANDING_UNSYNCED_MAX)
    assert _step("ci_blocked_by_conflict", unsynced).kind == "diagnose"


def test_a_ci_blocked_by_a_conflict_keeps_the_unlandable_and_verdict_sync_precedence():
    assert _step("ci_blocked_by_conflict", _spent(diagnoses=1)).kind == "none"
    due = _spent(verdict_sync_due=True, conflict_ci_syncs_on_pair=1)
    assert _step("ci_blocked_by_conflict", due).kind == "sync_master"


def test_the_refusals_that_make_a_sync_a_conflicting_one_are_the_two_conflict_reasons():
    assert landing.CONFLICTING_SYNC_REFUSALS == ("checks_failed", "merge_conflict")
    assert set(landing.CONFLICTING_SYNC_REFUSALS) <= set(get_args(rules.MergeRefusal))


@pytest.mark.parametrize("refusal", ["ci_failed", "checks_failed"])
def test_a_red_head_is_rerun_once_then_fixed_then_diagnosed(refusal):
    assert _step(refusal).kind == "rerun"
    assert _step(refusal, _spent(reruns_on_head=1), ("integration",)).kind == "fix"
    spent = _spent(reruns_on_head=1, passes=landing.LANDING_PASSES_MAX)
    assert _step(refusal, spent, ("integration",)).kind == "diagnose"


def test_a_red_head_whose_gate_already_reran_it_goes_straight_to_a_fix():
    """ADR-0066: the gate reran the red check on this head, so the landing's own rerun would be
    the third run of a failure that repeated. Without the fact (a record from before) the
    table is the one above; the CI keeps its rerun."""
    reran = _spent(checks_rerun_on_head=True)
    assert _step("checks_failed", reran, ("integration",)) == LandingStep(
        "fix", "checks_failed", ("integration",)
    )
    assert _step("checks_failed", _spent(), ("integration",)).kind == "rerun"
    assert _step("ci_failed", reran).kind == "rerun"
    spent = _spent(checks_rerun_on_head=True, passes=landing.LANDING_PASSES_MAX)
    assert _step("checks_failed", spent, ("integration",)).kind == "diagnose"
    conflict = _step("checks_failed", reran, failed_step="conflict")
    assert conflict.kind == "sync_master"


@pytest.mark.parametrize("refusal", ["eval_set_unreadable", "ci_missing", "ci_unreadable"])
def test_an_undecidable_ci_waits_until_the_bound_then_diagnoses(refusal):
    assert _step(refusal, _spent(waited_s=None)).kind == "wait"
    assert _step(refusal, _spent(waited_s=ci.CI_WAIT_MAX_S - 1)).kind == "wait"
    assert _step(refusal, _spent(waited_s=ci.CI_WAIT_MAX_S)).kind == "diagnose"


def test_findings_outside_the_task_are_filed_whatever_the_pass_budget():
    spent = _spent(passes=landing.LANDING_PASSES_MAX)
    assert _step("open_findings", spent, open_outside=1).kind == "file_followups_and_land"


def test_the_budgets_are_the_plans():
    assert (landing.LANDING_PASSES_MAX, landing.LANDING_SYNCS_MAX) == (3, 6)
    assert landing.RESTART_DEPTH_MAX == 1


@pytest.mark.parametrize(
    ("conflicted", "merge_paths", "branch_paths", "carries"),
    [
        (False, ["docs/a.md"], ["agentic/x.py"], True),
        (False, [], ["agentic/x.py"], True),
        (True, ["docs/a.md"], ["agentic/x.py"], False),
        (False, ["agentic/x.py", "docs/a.md"], ["agentic/x.py"], False),
    ],
    ids=["disjoint", "nothing_merged", "conflicted", "overlapping"],
)
def test_a_review_is_carried_only_by_a_clean_merge_of_disjoint_paths(
    conflicted, merge_paths, branch_paths, carries
):
    assert landing.review_carries(conflicted, iter(merge_paths), iter(branch_paths)) is carries


def test_a_step_is_dispatched_once_per_head():
    record = [{"head": HEAD, "step": "sync_master"}, {"head": "c" * 40, "step": "fix"}]
    assert landing.already_dispatched(record, HEAD, "sync_master", MASTER) is True
    assert landing.already_dispatched(record, "d" * 40, "sync_master", MASTER) is False
    assert landing.already_dispatched(record, HEAD, "fix", MASTER) is False
    assert landing.already_dispatched([], HEAD, "fix", MASTER) is False


def _state(entries, *statuses):
    return {
        "landing": entries,
        "subtasks": {
            f"s{n}": {"id": f"s{n}", "status": status} for n, status in enumerate(statuses)
        },
    }


@pytest.mark.parametrize(
    ("state", "is_open"),
    [
        (_state([{"head": HEAD, "step": "fix"}], "approved", "returned"), True),
        (_state([{"head": HEAD, "step": "fix"}], "approved", "pending"), True),
        (_state([{"head": HEAD, "step": "fix", "resolved_at": "t"}], "returned"), False),
        (_state([{"head": HEAD, "step": "re_review"}], "approved", "delivered"), True),
        (_state([{"head": HEAD, "step": "fix"}], "approved", "approved"), False),
        (_state([], "returned"), False),
        ({"subtasks": {"a": {"id": "a", "status": "returned"}}}, False),
        (
            _state(
                [{"head": "x", "step": "fix", "resolved_at": "t"}, {"head": HEAD, "step": "fix"}],
                "pending",
            ),
            True,
        ),
    ],
    ids=[
        "returned",
        "pending",
        "resolved",
        "reopened_for_review",
        "all_approved",
        "no_entries",
        "no_key",
        "last_wins",
    ],
)
def test_landing_is_open_while_the_last_entry_is_unresolved_and_a_unit_is_reopened(state, is_open):
    assert landing.landing_open(state) is is_open


def test_the_followup_marker_roundtrips_byte_for_byte_and_matches_only_its_task():
    marker = landing.followup_marker("GH-9", 1)
    assert marker == "<!-- agentic:followup task=GH-9 lineage=1 -->"
    body = f"intro\n{marker}\nmore"
    assert landing.marker_lineage(body, "GH-9") == 1
    assert landing.marker_lineage(body, "GH-90") is None
    assert landing.marker_lineage(body, "GH-1") is None
    assert landing.marker_lineage("no marker", "GH-9") is None
    assert landing.marker_lineage("<!-- agentic:followup task=GH-9 lineage=x -->", "GH-9") is None
    assert landing.marker_lineage("<!--agentic:followup task=GH-9 lineage=1-->", "GH-9") is None


@pytest.mark.parametrize(
    ("raw", "depth"),
    [
        ({}, 0),
        ({"landing_restarts": 1}, 1),
        ({"landing_restarts": "1"}, 0),
        ({"landing_restarts": True}, 0),
    ],
)
def test_restart_depth_is_read_with_a_default_and_only_as_an_int(raw, depth):
    assert landing.restart_depth(raw) == depth


def test_blocking_owners_are_the_subtasks_of_open_blocking_records_only():
    def record(subtask, severity, status="open", scope="subtask"):
        return {
            "subtask": subtask,
            "version": 1,
            "location": f"{subtask}.py:1",
            "claim": f"{subtask} {severity}",
            "severity": severity,
            "scope": scope,
            "category": "spec-behaviour",
            "status": status,
        }

    state = {
        "subtasks": {
            "api": {"id": "api", "status": "returned"},
            "integration": {"id": "integration", "status": "approved"},
        },
        "findings": [
            record("api", "blocking"),
            record("integration", "important"),
            record("integration", "blocking", status="dismissed"),
        ],
    }
    assert landing.blocking_owners(state) == ("api",)
    assert landing.blocking_owners({}) == ()


def test_marker_parent_names_the_task_that_wrote_the_follow_up_and_rejects_anything_else():
    marker = landing.followup_marker("GH-5", 2)
    assert landing.marker_parent(f"{marker}\nbody") == ("GH-5", 2)
    assert landing.marker_parent("no marker here") is None
    assert landing.marker_parent("<!-- agentic:followup task=GH-5 -->") is None


NOW = datetime(2026, 10, 14, 12, 30, 0, tzinfo=UTC)


def _owed_raw(spawned_ago=None, *, spawns=None, runs=(), **over):
    """A document with a reopened landing; `spawned_ago` seconds since the last spawn."""
    entry: dict = {"head": HEAD, "step": "re_review", "refusal": "head_not_reviewed"}
    if spawned_ago is not None:
        entry["resume_spawned_at"] = (NOW - timedelta(seconds=spawned_ago)).isoformat()
    if spawns is not None:
        entry["resume_spawns"] = spawns
    raw = _state([entry], "approved", "returned")
    raw["runs"] = list(runs)
    raw.update(over)
    return raw


def _spawn_at(raw):
    return datetime.fromisoformat(raw["landing"][-1]["resume_spawned_at"])


def test_the_resume_constants_are_the_plans():
    assert landing.LANDING_RESUME_SPAWNS_MAX == 2
    assert landing.LANDING_RESUME_GRACE_S == 300


def test_a_reopened_landing_that_never_spawned_owes_a_resume():
    assert landing.resume_owed(_owed_raw(), NOW) is True


def test_the_gh_227_shape_owes_a_resume():
    raw = _state(
        [{"head": HEAD, "step": "re_review", "refusal": "head_not_reviewed", "cost_base": 27.9}],
        "approved",
        "returned",
    )
    assert landing.resume_owed(raw, NOW) is True


@pytest.mark.parametrize(("ago", "owed"), [(299, False), (301, True)])
def test_a_resume_is_owed_again_only_after_the_grace_period(ago, owed):
    assert landing.resume_owed(_owed_raw(ago, spawns=1), NOW) is owed


def test_a_run_started_after_the_spawn_in_the_same_second_means_the_child_is_alive():
    raw = _owed_raw(400, spawns=1)
    started = _spawn_at(raw).replace(microsecond=500_000)
    raw["runs"] = [{"started_at": started.isoformat(timespec="milliseconds")}]
    assert landing.resume_owed(raw, NOW) is False


def test_a_run_from_before_the_spawn_does_not_count():
    raw = _owed_raw(400, spawns=1)
    before = _spawn_at(raw) - timedelta(seconds=1)
    raw["runs"] = [{"started_at": before.isoformat()}]
    assert landing.resume_owed(raw, NOW) is True


def test_runs_without_a_start_are_skipped():
    raw = _owed_raw(400, spawns=1, runs=[{"role": "developer"}, {"started_at": None}])
    assert landing.resume_owed(raw, NOW) is True


def test_a_run_started_in_another_offset_is_compared_as_an_instant():
    raw = _owed_raw(400, spawns=1)
    after = (_spawn_at(raw) + timedelta(seconds=1)).astimezone(timezone(timedelta(hours=2)))
    raw["runs"] = [{"started_at": after.isoformat()}]
    assert landing.resume_owed(raw, NOW) is False


def test_an_unparseable_spawn_time_owes_nothing():
    raw = _owed_raw(400, spawns=1)
    raw["landing"][-1]["resume_spawned_at"] = "yesterday-ish"
    assert landing.resume_owed(raw, NOW) is False


def test_a_naive_spawn_time_owes_nothing():
    raw = _owed_raw(400, spawns=1)
    raw["landing"][-1]["resume_spawned_at"] = "2026-10-14T12:00:00"
    assert landing.resume_owed(raw, NOW) is False


@pytest.mark.parametrize("started", ["2026-10-14T13:00:00", "not a time", 12])
def test_a_naive_or_unparseable_run_start_owes_nothing(started):
    raw = _owed_raw(400, spawns=1, runs=[{"started_at": started}])
    assert landing.resume_owed(raw, NOW) is False


def test_an_escalated_task_owes_nothing():
    assert landing.resume_owed(_owed_raw(escalated=True), NOW) is False


def test_a_spent_resume_budget_owes_nothing():
    assert landing.resume_owed(_owed_raw(400, spawns=2), NOW) is False
    assert landing.resume_owed(_owed_raw(400, spawns=1), NOW) is True


@pytest.mark.parametrize(
    "raw",
    [
        _state([{"head": HEAD, "step": "fix", "resolved_at": "t"}], "returned"),
        _state([{"head": HEAD, "step": "fix"}], "approved", "approved"),
        _state([], "returned"),
    ],
    ids=["resolved", "no_reopened_subtask", "no_entries"],
)
def test_a_landing_that_is_not_open_owes_nothing(raw):
    assert landing.resume_owed(raw, NOW) is False


_SAMPLE: dict[object, object] = {
    str: "x",
    int: 1,
    float: 1.5,
    bool: True,
    list[str]: ["a"],
    list[dict[str, object]]: [{"job": "check", "bytes": 1, "cause": "read"}],
    FailureReason: "account_limit",
    DiagnosisVerdict: "sync",
}


def test_entry_of_keeps_every_key_landing_entry_declares():
    hints = get_type_hints(LandingEntry)
    assert {"resume_spawns", "resume_spawned_at"} <= hints.keys()
    full = {key: _SAMPLE[hint] for key, hint in hints.items()}
    kept = landing.entry_of(full)
    assert set(kept) == set(hints)


def test_entry_of_does_not_take_a_bool_for_the_spawn_count():
    assert "resume_spawns" not in landing.entry_of({"resume_spawns": True})
    assert "resume_spawned_at" not in landing.entry_of({"resume_spawned_at": 3})
    assert landing.entry_of({"resume_spawns": 2})["resume_spawns"] == 2


_RESUME = {"resume_spawns": 1, "resume_spawned_at": "2026-10-14T12:00:00+00:00"}


def test_with_cost_base_preserves_the_resume_keys():
    entries = [{"head": HEAD, "step": "re_review", **_RESUME, "cost_base": 1.0}]
    (out,) = landing.with_cost_base(entries, HEAD, "re_review", 2.5)
    assert out["cost_base"] == 2.5
    assert out["resume_spawns"] == 1
    assert out["resume_spawned_at"] == _RESUME["resume_spawned_at"]


def test_resolve_open_preserves_the_resume_keys_and_the_cost_base():
    entries = [{"head": HEAD, "step": "re_review", **_RESUME, "cost_base": 1.0}]
    (out,) = landing.resolve_open(entries, "2026-10-14T13:00:00+00:00", 3.0)
    assert out["resume_spawns"] == 1
    assert out["resume_spawned_at"] == _RESUME["resume_spawned_at"]
    assert out["cost_base"] == 1.0
    assert out["cost_usd"] == 2.0


# --- GH-257: a failed diagnosis is an attempt on the entry, retried on the shared calendar ----

AT = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _attempt(failure=None, at=AT, **more):
    entry = {"head": HEAD, "step": "diagnose", "refusal": "merge_failed", "at": at.isoformat()}
    if failure is not None:
        entry["failure"] = failure
    return {**entry, **more}


def _minutes(schedule, index):
    return timedelta(minutes=schedule[index])


def test_entry_of_keeps_delivered_only_as_a_bool_and_failure_only_as_a_reason():
    assert landing.entry_of({"delivered": True, "failure": "account_limit"}) == {
        "delivered": True,
        "failure": "account_limit",
    }
    assert "delivered" not in landing.entry_of({"delivered": 1})
    assert "delivered" not in landing.entry_of({"delivered": "true"})
    assert "failure" not in landing.entry_of({"failure": "gh_rejected"})
    assert "failure" not in landing.entry_of({"failure": 429})


def test_every_failure_reason_the_type_names_is_kept_by_entry_of():
    for reason in get_args(FailureReason):
        assert landing.entry_of({"failure": reason}) == {"failure": reason}


def test_resolve_open_returns_delivered_and_failure_unchanged():
    entries = [_attempt("account_limit", delivered=True, cost_base=1.0)]
    (out,) = landing.resolve_open(entries, "2026-10-15T13:00:00+00:00", 3.0)
    assert out["delivered"] is True and out["failure"] == "account_limit"


def test_unescalated_is_the_one_patch_that_clears_the_escalation():
    raw = {
        "id": "GH-5",
        "status": "waiting_on_human",
        "escalated": "x",
        "escalation_class": "failure",
        "pull_request": "https://example.test/pull/5",
        "landing": [_attempt()],
        "cost_usd": 4.0,
    }
    patch = landing.unescalated(raw)
    assert patch == {
        "escalated": None,
        "escalation_class": None,
        "status": "ready_for_review",
        "pause_log": [{"kind": "resumed"}],
    }
    assert "cost_usd" not in patch


def test_diagnose_retry_is_none_without_an_undelivered_attempt():
    assert landing.diagnose_retry({}, AT) == "none"
    assert landing.diagnose_retry({"landing": [{"step": "fix", "head": HEAD}]}, AT) == "none"
    delivered = {"landing": [_attempt("account_limit"), _attempt(delivered=True)]}
    assert landing.diagnose_retry(delivered, AT) == "none"


def test_diagnose_retry_escalates_an_entry_with_no_failure_or_a_non_external_one():
    assert landing.diagnose_retry({"landing": [_attempt()]}, AT) == "escalate"
    assert landing.diagnose_retry({"landing": [_attempt("sdk")]}, AT) == "escalate"
    assert landing.diagnose_retry({"landing": [_attempt("schema")]}, AT) == "escalate"


def test_diagnose_retry_first_slot_is_due_at_exactly_its_minutes_and_not_a_second_before():
    raw = {"landing": [_attempt("account_limit")]}
    wait = _minutes(rules.ACCOUNT_LIMIT_SCHEDULE, 0)
    assert landing.diagnose_retry(raw, AT + wait - timedelta(seconds=1)) == "waiting"
    assert landing.diagnose_retry(raw, AT + wait) == "due"


def test_diagnose_retry_runs_the_flat_calendar_to_its_last_slot_then_escalates():
    schedule = rules.ACCOUNT_LIMIT_SCHEDULE
    last = len(schedule) - 1
    late = AT + timedelta(days=30)
    still = {"landing": [_attempt("account_limit") for _ in range(last)]}
    assert landing.diagnose_retry(still, late) == "due"
    spent = {"landing": [_attempt("account_limit") for _ in range(len(schedule))]}
    assert landing.diagnose_retry(spent, late) == "escalate"


def test_diagnose_retry_counts_only_the_attempts_after_the_last_delivered_one():
    spent = [_attempt("account_limit") for _ in range(len(rules.ACCOUNT_LIMIT_SCHEDULE))]
    raw = {"landing": [*spent, _attempt(delivered=True), _attempt("account_limit")]}
    assert landing.diagnose_retry(raw, AT + timedelta(days=30)) == "due"


def test_diagnose_retry_measures_from_the_last_attempt():
    first, second = AT, AT + timedelta(hours=3)
    raw = {"landing": [_attempt("account_limit", first), _attempt("account_limit", second)]}
    wait = _minutes(rules.ACCOUNT_LIMIT_SCHEDULE, 2 - 1)
    assert landing.diagnose_retry(raw, second + wait - timedelta(seconds=1)) == "waiting"
    assert landing.diagnose_retry(raw, second + wait) == "due"


def test_diagnose_retry_of_an_upstream_failure_follows_the_growing_calendar():
    schedule = rules.RETAKE_SCHEDULE
    raw = {"landing": [_attempt("upstream")]}
    wait = _minutes(schedule, 1)
    assert landing.diagnose_retry(raw, AT + wait - timedelta(seconds=1)) == "waiting"
    assert landing.diagnose_retry(raw, AT + wait) == "due"
    spent = {"landing": [_attempt("upstream") for _ in range(len(schedule))]}
    assert landing.diagnose_retry(spent, AT + timedelta(days=30)) == "escalate"


def test_diagnose_retry_never_reaches_the_hourly_week():
    hourly = len(rules.ACCOUNT_LIMIT_SCHEDULE) + 1
    assert hourly <= rules.ACCOUNT_LIMIT_CHASE_MAX
    raw = {"landing": [_attempt("account_limit") for _ in range(hourly)]}
    assert landing.diagnose_retry(raw, AT + timedelta(days=30)) == "escalate"


@pytest.mark.parametrize("at", ["not a time", "2026-10-15T12:00:00", 5, None])
def test_diagnose_retry_waits_when_the_attempt_time_is_unreadable_or_naive(at):
    entry = _attempt("account_limit")
    entry["at"] = at
    assert landing.diagnose_retry({"landing": [entry]}, AT + timedelta(days=30)) == "waiting"


def test_diagnose_retry_waits_when_now_is_naive():
    raw = {"landing": [_attempt("account_limit")]}
    assert landing.diagnose_retry(raw, datetime(2030, 1, 1)) == "waiting"


# --- GH-257: the stranded legacy diagnose document and its one migration ------------------

ESCALATED_PREFIX = "Escalated: "
DIAGNOSER = "Landing Diagnoser"


def _real():
    return document.parse(GH150_STRANDED)


def _window(migrated):
    """The instant the freed diagnosis becomes due, read off the shared calendar from the
    fixture's own last diagnose `at`: the schedule is `rules.retake_calendar`'s, never a number
    typed here, and no instant is a literal."""
    since = datetime.fromisoformat(landing.diagnose_at(migrated))
    reason = migrated["landing"][-1]["failure"]
    count = len(landing.diagnose_attempts(migrated))
    limit = rules.auto_resume_limit(landing._landing_view(migrated), reason)
    schedule, prior, _ = rules.retake_calendar(reason, count, count, limit)
    assert prior < len(schedule)  # the calendar is not spent: `due` and `waiting` both exist
    return since + timedelta(minutes=schedule[prior])


def _freed():
    raw = _real()
    return {**raw, **landing.migration(raw)}


def _stranded(**over):
    """GH-150 as the Landing Diagnoser stranded it: its real frontmatter (`GH150_STRANDED`),
    a failed run that escalated through the run_role patch, leaving its entry with no
    `delivered` and no `failure`. `over` is applied on top."""
    return {**_real(), **over}


@dataclass(frozen=True)
class _Comment:
    body: str
    created_at: str


def _comment(body, at):
    return _Comment(body=body, created_at=at.isoformat())


def test_stranded_diagnose_recognises_the_legacy_document():
    assert landing.stranded_diagnose(_stranded(), False) is True


def test_stranded_diagnose_is_false_when_an_escalation_comment_came_after():
    assert landing.stranded_diagnose(_stranded(), True) is False


def _without(key):
    raw = _stranded()
    del raw[key]
    return raw


def _epoch(*entries):
    return {"pause_log": [{"kind": "resumed"}, *entries]}


_FAILED = {"kind": "failure", "value": "account_limit", "section": DIAGNOSER}
_ESCALATION = {"kind": "escalation", "value": "failure"}


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"status": "ready_for_review"}, id="not-waiting"),
        pytest.param({"escalation_class": "questions"}, id="questions"),
        pytest.param({"escalation_class": "cap"}, id="cap"),
        pytest.param({"escalation_class": None}, id="no-class"),
        pytest.param({"current_role": "Developer"}, id="graph-role"),
        pytest.param({"pull_request": None}, id="no-pull-request"),
        pytest.param({"pull_request": ""}, id="empty-pull-request"),
        pytest.param(_epoch(_ESCALATION), id="depth-one-escalation-without-failures"),
        pytest.param(_epoch(_FAILED), id="no-escalation-entry"),
        pytest.param(_epoch(_FAILED, {**_FAILED, "value": "sdk"}, _ESCALATION), id="sdk"),
        pytest.param(_epoch(_FAILED, {**_FAILED, "value": "other"}, _ESCALATION), id="other"),
        pytest.param(
            _epoch({**_FAILED, "section": "Developer: land-101"}, _ESCALATION),
            id="failure-of-a-graph-role",
        ),
        pytest.param(
            _epoch(_FAILED, {"kind": "escalation", "value": "cap"}, _ESCALATION), id="cap-entry"
        ),
        pytest.param({"pause_log": "garbage"}, id="unreadable-log"),
        pytest.param({"landing": [_attempt(failure="account_limit")]}, id="already-stamped"),
        pytest.param({"landing": [_attempt(delivered=True)]}, id="delivered"),
        pytest.param({"landing": [{"step": "fix", "head": HEAD}]}, id="no-diagnose-entry"),
        pytest.param({"landing": "garbage"}, id="unreadable-landing"),
    ],
)
def test_stranded_diagnose_refuses_every_other_shape(over):
    assert landing.stranded_diagnose(_stranded(**over), False) is False


def test_stranded_diagnose_reads_only_the_epoch_after_the_last_resumed():
    older_sdk = [{**_FAILED, "value": "sdk"}, _ESCALATION, {"kind": "resumed"}]
    raw = _stranded(pause_log=[*older_sdk, _FAILED, _ESCALATION])
    assert landing.stranded_diagnose(raw, False) is True


def test_escalated_after_is_true_only_for_an_escalated_comment_created_after_the_entry():
    at = AT.isoformat()
    later = _comment(ESCALATED_PREFIX + "x", AT + timedelta(seconds=1))
    earlier = _comment(ESCALATED_PREFIX + "x", AT - timedelta(seconds=1))
    same = _comment(ESCALATED_PREFIX + "x", AT)
    other = _comment("Landing: something", AT + timedelta(hours=1))
    assert landing.escalated_after([later], at, ESCALATED_PREFIX) is True
    assert landing.escalated_after([earlier, same, other], at, ESCALATED_PREFIX) is False
    assert landing.escalated_after([], at, ESCALATED_PREFIX) is False


def test_escalated_after_reads_a_z_suffixed_timestamp_as_utc():
    later = _Comment(ESCALATED_PREFIX + "x", "2026-10-15T12:00:01Z")
    assert landing.escalated_after([later], AT.isoformat(), ESCALATED_PREFIX) is True


@pytest.mark.parametrize(
    ("comment_at", "entry_at"),
    [
        ("garbage", AT.isoformat()),
        ("2026-10-15T13:00:00", AT.isoformat()),
        ((AT + timedelta(hours=1)).isoformat(), "2026-10-15T12:00:00"),
        ((AT + timedelta(hours=1)).isoformat(), "garbage"),
        ((AT + timedelta(hours=1)).isoformat(), None),
    ],
)
def test_escalated_after_keeps_the_document_escalated_on_a_time_it_cannot_compare(
    comment_at, entry_at
):
    comments = [_Comment(ESCALATED_PREFIX + "x", comment_at)]
    assert landing.escalated_after(comments, entry_at, ESCALATED_PREFIX) is True


def test_an_unreadable_time_on_a_comment_that_is_not_an_escalation_is_ignored():
    comments = [_Comment("hello", "garbage")]
    assert landing.escalated_after(comments, AT.isoformat(), ESCALATED_PREFIX) is False


def test_the_domain_prefix_is_the_one_the_application_posts():
    from agentic.core.process.application import escalate

    assert landing.ESCALATED_COMMENT == escalate.ESCALATED


def test_migration_clears_the_escalation_and_stamps_the_failure_in_one_patch():
    raw = _stranded()
    patch = landing.migration(raw)
    assert patch["escalated"] is None and patch["escalation_class"] is None
    assert patch["status"] == "ready_for_review"
    # closure replaces a key whole: the history stays and `resumed` closes the epoch
    assert patch["pause_log"] == [*raw["pause_log"], {"kind": "resumed"}]
    assert patch["landing"][:-1] == raw["landing"][:-1]
    assert patch["landing"][-1] == {**raw["landing"][-1], "failure": "account_limit"}
    assert "cost_usd" not in patch
    assert raw == _stranded()  # the input is not touched


def test_migration_takes_the_failure_from_the_last_failure_of_the_closing_epoch():
    raw = _stranded(
        pause_log=[
            {"kind": "failure", "value": "network", "section": DIAGNOSER},
            {"kind": "failure", "value": "upstream", "section": DIAGNOSER},
            _ESCALATION,
        ]
    )
    assert landing.migration(raw)["landing"][-1]["failure"] == "upstream"


def test_migration_leaves_diagnose_retry_due_or_waiting_never_escalate():
    """The window sits a minute either side, never on the boundary: `next_attempt_at` is
    inclusive, and `rules`' own tests own that edge."""
    raw = _stranded()
    migrated = {**raw, **landing.migration(raw)}
    window = _window(migrated)
    margin = timedelta(minutes=1)

    assert landing.diagnose_retry(migrated, window - margin) == "waiting"
    assert landing.diagnose_retry(migrated, window + margin) == "due"
    assert landing.diagnose_retry(raw, window - margin) == "escalate"  # before: stuck
    assert landing.diagnose_retry(raw, window + margin) == "escalate"


def test_migration_stamps_only_the_last_diagnose_entry():
    earlier = _attempt(delivered=True, at=AT - timedelta(days=1))
    raw = _stranded(landing=[earlier, _attempt(at=AT)])
    stamped = landing.migration(raw)["landing"]
    assert stamped[0] == earlier
    assert stamped[1]["failure"] == "account_limit"


# --- GH-259: the same migration on GH-150's real text -----------------------------------------


def _closing_start(log):
    return max(i for i, e in enumerate(log) if e["kind"] == "resumed") + 1


def _stamped(raw):
    """The `failure` the migration stamps on the last diagnose entry, `None` when it stamps none."""
    return landing.migration(raw)["landing"][-1].get("failure")


def test_a_pause_log_cut_at_its_last_resumed_is_not_stranded_and_stays_stuck():
    raw = _real()
    cut = {**raw, "pause_log": raw["pause_log"][: _closing_start(raw["pause_log"])]}

    assert landing.stranded_diagnose(cut, False) is False
    assert _stamped(cut) is None
    after = {**cut, **landing.migration(cut)}
    assert landing.diagnose_retry(after, _window(_freed()) + timedelta(minutes=1)) == "escalate"


def test_another_roles_failure_in_the_closing_epoch_is_not_stranded_and_stamps_nothing():
    raw = _real()
    log = list(raw["pause_log"])
    mixed_log = [
        *log[:-1],
        {"kind": "failure", "value": "account_limit", "section": "Developer: land-101"},
        log[-1],
    ]
    mixed = {**raw, "pause_log": mixed_log}

    assert landing.stranded_diagnose(mixed, False) is False
    assert _stamped(mixed) is None


def test_a_comment_after_the_entry_keeps_the_real_document_escalated():
    assert landing.stranded_diagnose(_real(), True) is False


@pytest.mark.parametrize(
    ("step", "refusal", "kind"),
    [
        ("rerun", "checks_failed", "land_checks"),
        ("file_followups_and_land", None, "land_checks"),
        ("sync_master", "attempts_exhausted", "land_model"),
        ("sync_master", "checks_stale", "land_model"),
        ("sync_master", "checks_failed", "land_model"),
        ("sync_master", "merge_conflict", "land_model"),
        ("re_review", None, "land_model"),
        ("fix", "checks_failed", "land_model"),
        ("diagnose", None, "land_model"),
        ("wait", None, "land_model"),
        ("none", None, "land_model"),
        (None, None, "land_model"),
    ],
)
def test_a_land_step_registers_under_the_kind_that_says_whether_it_can_spend_a_model(
    step, refusal, kind
):
    assert landing.run_kind_of(step, refusal) == kind


def test_every_sync_master_is_a_model_step_because_a_clean_one_can_still_spawn_a_resume():
    for refusal in get_args(rules.MergeRefusal):
        assert landing.run_kind_of("sync_master", refusal) == "land_model"
        assert not landing.run_kind_admits("land_checks", "sync_master", refusal)
    assert landing.run_kind_of("sync_master", None) == "land_model"


def test_a_model_registered_child_admits_every_step_and_the_recoveries():
    for step in get_args(landing.LandingStepKind):
        for refusal in (None, "checks_failed"):
            assert landing.run_kind_admits("land_model", step, refusal)
    assert landing.run_kind_admits("land_model", None, None)


@pytest.mark.parametrize("registered", ["land_checks"])
@pytest.mark.parametrize(
    ("step", "refusal", "admitted"),
    [
        ("rerun", "checks_failed", True),
        ("file_followups_and_land", "open_findings", True),
        ("sync_master", "master_moved", False),
        ("sync_master", "checks_failed", False),
        ("sync_master", "merge_conflict", False),
        ("fix", "checks_failed", False),
        ("re_review", "head_not_reviewed", False),
        ("diagnose", "checks_failed", False),
        ("wait", "ci_unreadable", False),
        ("none", "checks_failed", False),
        (None, None, False),
    ],
)
def test_a_checks_child_admits_only_the_steps_that_run_no_model(
    registered, step, refusal, admitted
):
    assert landing.run_kind_admits(registered, step, refusal) is admitted


@pytest.mark.parametrize("registered", ["role", "merge"])
def test_a_kind_a_land_child_never_registers_under_admits_nothing_that_needs_a_model(registered):
    assert not landing.run_kind_admits(registered, "fix", "checks_failed")
    assert not landing.run_kind_admits(registered, None, None)


# --- GH-267: a sync is resolved only when its head holds the upstream it was sent for ---

UPSTREAM = "c" * 40


def _sync_entry(**over) -> dict:
    return {"head": HEAD, "step": "sync_master", "upstream": UPSTREAM, **over}


@pytest.mark.parametrize(
    ("entry", "contains", "verdict"),
    [
        ({"head": HEAD, "step": "fix", "upstream": UPSTREAM}, True, "not_a_sync"),
        ({"head": HEAD, "step": "sync_master"}, True, "not_a_sync"),
        ({"head": HEAD, "step": "sync_master", "upstream": 3}, True, "not_a_sync"),
        (_sync_entry(), True, "synced"),
        (_sync_entry(), False, "unsynced"),
        (_sync_entry(), None, "unsynced"),
    ],
    ids=["other_step", "legacy_entry", "unreadable_upstream", "contained", "lacking", "unknown"],
)
def test_sync_verdict_is_synced_only_when_the_head_contains_the_upstream(entry, contains, verdict):
    assert landing.sync_verdict(entry, contains) == verdict


def test_close_unsynced_closes_without_resolving_and_records_what_was_lacking():
    entries = [_sync_entry(cost_base=1.0)]
    (out,) = landing.close_unsynced(entries, "t1", 3.5, UPSTREAM)
    assert out["closed_at"] == "t1"
    assert out["lacks"] == UPSTREAM
    assert out["cost_usd"] == 2.5
    assert "resolved_at" not in out
    assert out["upstream"] == UPSTREAM


def test_close_unsynced_takes_the_cost_now_as_base_when_none_was_written():
    (out,) = landing.close_unsynced([_sync_entry()], "t1", 3.5, UPSTREAM)
    assert out["cost_usd"] == 0.0


@pytest.mark.parametrize("closed", [{"resolved_at": "r"}, {"closed_at": "c", "lacks": "x"}])
def test_close_unsynced_leaves_an_already_closed_last_entry_untouched(closed):
    entries = [_sync_entry(cost_usd=1.0, **closed)]
    assert landing.close_unsynced(entries, "t1", 9.0, UPSTREAM) == entries
    assert landing.close_unsynced([], "t1", 9.0, UPSTREAM) == []


@pytest.mark.parametrize(
    ("entry", "closed"),
    [
        ({"resolved_at": "t"}, True),
        ({"closed_at": "t"}, True),
        ({}, False),
        ({"resolved_at": ""}, False),
        ({"closed_at": 3}, False),
    ],
    ids=["resolved", "closed", "open", "empty", "not_a_string"],
)
def test_entry_closed_reads_resolved_at_or_closed_at(entry, closed):
    assert rules.entry_closed(entry) is closed
    assert landing.entry_closed(entry) is closed


def test_landing_is_not_open_after_an_unsynced_close():
    entries = landing.close_unsynced([_sync_entry()], "t1", 1.0, UPSTREAM)
    assert landing.landing_open(_state(entries, "approved", "returned")) is False
    assert landing.landing_open(_state([_sync_entry()], "approved", "returned")) is True


def test_entry_of_keeps_the_sync_keys_only_when_they_are_strings():
    kept = landing.entry_of({"upstream": "u", "closed_at": "c", "lacks": "l"})
    assert kept == {"upstream": "u", "closed_at": "c", "lacks": "l"}
    assert landing.entry_of({"upstream": 1, "closed_at": 2, "lacks": None}) == {}


_OLDER = "d" * 40
_FOREIGN = "e" * 40


@pytest.mark.parametrize(
    ("merge_head", "owner"),
    [
        (None, "none"),
        (UPSTREAM, "system"),
        (HEAD, "system"),
        (_OLDER, "system"),
        (UPSTREAM[:12], "system"),
        (_FOREIGN, "foreign"),
        (UPSTREAM[:3], "foreign"),
    ],
    ids=["none", "upstream", "head", "older_recorded", "prefix", "foreign", "too_short_prefix"],
)
def test_merge_owner_names_a_merge_by_the_shas_the_system_recorded(merge_head, owner):
    entries = [{"head": "f" * 40, "step": "sync_master", "upstream": _OLDER}]
    assert landing.merge_owner(merge_head, UPSTREAM, HEAD, entries) == owner


def test_merge_owner_knows_the_head_of_an_earlier_entry_and_nothing_without_facts():
    entries = [{"head": _OLDER, "step": "fix"}]
    assert landing.merge_owner(_OLDER, None, None, entries) == "system"
    assert landing.merge_owner(_FOREIGN, None, None, []) == "foreign"


def test_a_closed_unsynced_entry_is_not_a_dispatched_step():
    entries = [_sync_entry(closed_at="t", lacks=UPSTREAM)]
    assert landing.already_dispatched(entries, HEAD, "sync_master", UPSTREAM) is False
    assert landing.already_dispatched([_sync_entry()], HEAD, "sync_master", UPSTREAM) is True
    resolved = [_sync_entry(resolved_at="t")]
    assert landing.already_dispatched(resolved, HEAD, "sync_master", UPSTREAM) is True


def test_with_cost_base_writes_only_the_last_entry_and_only_when_it_matches():
    entries = [
        {"head": HEAD, "step": "sync_master", "cost_usd": 1.0},
        {"head": HEAD, "step": "sync_master"},
    ]
    first, last = landing.with_cost_base(entries, HEAD, "sync_master", 7.0)
    assert "cost_base" not in first
    assert last["cost_base"] == 7.0
    untouched = landing.with_cost_base(entries, HEAD, "fix", 7.0)
    assert all("cost_base" not in e for e in untouched)
    assert landing.with_cost_base([entries[0]], "z" * 40, "sync_master", 7.0) == [entries[0]]


def test_the_second_unsynced_pass_for_one_upstream_is_the_diagnose():
    assert landing.LANDING_UNSYNCED_MAX == 2
    one = _spent(unsynced_for_upstream=1)
    two = _spent(unsynced_for_upstream=2)
    assert _step("merge_conflict", one).kind == "sync_master"
    assert _step("merge_conflict", two).kind == "diagnose"
    assert _step("attempts_exhausted", two).kind == "diagnose"
    assert _step("checks_failed", two, failed_step="conflict").kind == "diagnose"


def test_a_busy_master_reaches_the_diagnose_only_through_the_conflict_budget():
    busy = _spent(
        conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX - 1,
        unsynced_for_upstream=0,
    )
    assert _step("merge_conflict", busy).kind == "sync_master"
    spent = _spent(
        conflicting_syncs_for_upstream=landing.LANDING_CONFLICT_SYNCS_MAX,
        unsynced_for_upstream=0,
    )
    assert _step("merge_conflict", spent).kind == "diagnose"


# GH-294 pin: with no `important` open `landing_step` answers what it answered before the
# final important pass existed. Written first, never edited by that change.

NO_IMPORTANT_STEPS = [
    ("blocking, fresh", "open_findings_blocking", {}, {}, ("fix", ("api",))),
    (
        "blocking, passes spent",
        "open_findings_blocking",
        {"passes": landing.LANDING_PASSES_MAX},
        {},
        ("diagnose", ()),
    ),
    ("ci failed, first", "ci_failed", {}, {}, ("rerun", ())),
    ("ci failed, rerun spent", "ci_failed", {"reruns_on_head": 1}, {}, ("fix", ("api",))),
    ("checks failed, first", "checks_failed", {}, {}, ("rerun", ())),
    (
        "checks failed, rerun spent",
        "checks_failed",
        {"reruns_on_head": 1},
        {},
        ("fix", ("api",)),
    ),
    (
        "checks failed on a conflict",
        "checks_failed",
        {},
        {"failed_step": "conflict"},
        ("sync_master", ()),
    ),
    ("merge conflict", "merge_conflict", {}, {}, ("sync_master", ())),
    ("attempts exhausted", "attempts_exhausted", {}, {}, ("sync_master", ())),
    ("merge failed", "merge_failed", {}, {}, ("diagnose", ())),
    ("master moved", "master_moved", {}, {}, ("wait", ())),
    ("adr number taken, ambiguous", "adr_number_taken", {}, {}, ("escalate_adr", ())),
    ("ci missing", "ci_missing", {}, {}, ("wait", ())),
    ("ci unreadable", "ci_unreadable", {}, {}, ("wait", ())),
    ("diagnosed", "merge_conflict", {"diagnoses": 1}, {}, ("none", ())),
]


@pytest.mark.parametrize(
    ("refusal", "spent", "facts", "expected"),
    [(r, s, f, e) for _, r, s, f, e in NO_IMPORTANT_STEPS],
    ids=[n for n, *_ in NO_IMPORTANT_STEPS],
)
def test_with_no_important_open_the_landing_step_is_what_it_was(refusal, spent, facts, expected):
    step = _step(refusal, _spent(**spent), owners=("api",), **facts)
    assert (step.kind, step.units) == expected
    assert step.refusal == refusal


def test_the_findings_refusal_without_important_files_the_followups_as_before():
    assert _step("open_findings") == LandingStep("file_followups_and_land", "open_findings", ())


# GH-294: the final important pass.


def _important(spent=None, units=("api",), **facts) -> LandingStep:
    return landing.landing_step(
        "open_findings",
        _facts(**facts),
        spent or _spent(),
        (),
        important_units=units,
    )


def test_the_task_s_own_important_rows_are_a_pass_on_their_owners_first():
    assert _important(open_important=2) == LandingStep("fix_important", "open_findings", ("api",))


def test_the_pass_is_not_taken_twice_nor_once_it_is_resolved():
    spent = _spent(important_passes=landing.LANDING_IMPORTANT_PASSES_MAX)
    assert landing.LANDING_IMPORTANT_PASSES_MAX == 1
    assert _important(spent, open_important=1).kind == "file_followups_and_land"
    assert _important(open_important=1, important_pass_done=True).kind == "file_followups_and_land"


def test_outside_rows_alone_are_the_follow_up_and_never_the_pass():
    assert _important(open_outside=1).kind == "file_followups_and_land"
    assert _important(open_important=0, open_outside=2, units=("api",)).units == ()


def test_the_pass_does_not_draw_on_the_landing_pass_budget():
    spent = _spent(passes=landing.LANDING_PASSES_MAX)
    assert _important(spent, open_important=1).kind == "fix_important"
    assert _important(_spent(diagnoses=1), open_important=1).kind == "none"


def test_the_pass_runs_a_model():
    assert landing.run_kind_of("fix_important", "open_findings") == "land_model"
    assert landing.run_kind_of("file_followups_and_land", "open_findings") == "land_checks"
    assert not landing.run_kind_admits("land_checks", "fix_important", "open_findings")
    assert landing.run_kind_admits("land_model", "fix_important", "open_findings")


def _doc(subtasks, *rows):
    return {
        "subtasks": {name: {"status": "approved"} for name in subtasks},
        "findings": [
            {
                "subtask": who,
                "version": 1,
                "location": "x.py:1",
                "claim": claim,
                "severity": "important",
                "scope": scope,
                "category": "spec-behaviour",
                "status": "open",
            }
            for who, claim, scope in rows
        ],
    }


def test_the_pass_reopens_the_owners_and_the_reviewed_unit_for_a_plan_row():
    state = _doc(["api", "domain", "integration"], ("domain", "a", "subtask"), ("api", "b", "plan"))
    assert landing.important_owners(state) == ("domain", "integration")


def test_a_plan_row_of_a_one_subtask_task_goes_to_that_subtask():
    assert landing.important_owners(_doc(["api"], ("api", "b", "plan"))) == ("api",)


def test_an_outside_row_owns_no_unit():
    assert landing.important_owners(_doc(["api"], ("api", "o", "outside"))) == ()


# --- GH-297: `carry_over` reads what a landing restart carries from the archived document --------

TWO_DECISIONS = (
    "### Decision 1\n\nEscalated: the plan did not fit\n\nSplit it in two.\n\n"
    "### Decision 2\n\nKeep the CLI as is."
)


def test_carry_over_moves_the_depth_cost_and_diagnosis_of_a_restarted_run():
    raw = {
        "landing_restarts": 1,
        "landing_cost_carried": 3.25,
        "sections": {"Landing Diagnoser": "It keeps failing."},
    }
    assert landing.carry_over(raw) == Carried(1, 3.25, "It keeps failing.", "", ())


def test_carry_over_carries_the_human_section_with_or_without_a_restart():
    assert landing.carry_over({"sections": {"Human": TWO_DECISIONS}}) == Carried(
        0, 0.0, "", TWO_DECISIONS, ()
    )
    restarted = landing.carry_over(
        {"landing_restarts": 2, "sections": {"Human": TWO_DECISIONS, "Landing Diagnoser": "x"}}
    )
    assert restarted is not None and restarted.human == TWO_DECISIONS


def test_carry_over_ignores_the_diagnosis_of_a_run_the_landing_never_restarted():
    raw = {"sections": {"Human": "h", "Landing Diagnoser": "stale"}}
    carried = landing.carry_over(raw)
    assert carried is not None and carried.diagnosis == ""


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"cost_usd": 4.0},
        {"landing_restarts": "1", "landing_cost_carried": "2"},
        {"landing_restarts": True},
        {"sections": {"Human": ""}},
    ],
)
def test_carry_over_is_none_when_nothing_is_carried(raw):
    assert landing.carry_over(raw) is None


def test_carry_over_reads_the_cost_defensively():
    carried = landing.carry_over({"landing_restarts": 1, "landing_cost_carried": "lots"})
    assert carried is not None and carried.cost == 0.0
    carried = landing.carry_over({"landing_restarts": 1, "landing_cost_carried": True})
    assert carried is not None and carried.cost == 0.0


def test_carry_over_reads_the_landed_slices_and_skips_what_it_cannot_read():
    raw = {
        "sections": {"Human": "h"},
        "landed_slices": [
            {"units": ["a", "b"], "pull_request": "#7", "merged_sha": "s"},
            {"units": "not-a-list", "pull_request": "#8"},
            {"units": ["c"]},
            "junk",
            {"units": ["d"], "pull_request": 9},
        ],
    }
    carried = landing.carry_over(raw)
    assert carried is not None
    assert carried.landed == (LandedSliceRef(("a", "b"), "#7"),)
    assert landing.carry_over({"landed_slices": "junk"}) is None


# --- GH-328: a human decision releases an escalated landing diagnose -------------------------


def _escalated_doc(**over):
    """A landing diagnose `escalate_failed_diagnosis` left: the shape `released` answers."""
    raw = {
        "id": "GH-5",
        "status": "waiting_on_human",
        "escalated": "Landing Diagnoser did not deliver after 1 attempt: max_turns",
        "escalation_class": "failure",
        "pull_request": "https://example.test/pull/5",
        "landing": [
            {"head": HEAD, "step": "fix", "at": AT.isoformat()},
            _attempt("max_turns"),
        ],
        "pause_log": [{"kind": "escalation", "value": "failure"}],
        "cost_usd": 4.0,
    }
    return {**raw, **over}


def test_released_is_unescalated_plus_the_stamp_and_the_decision_as_the_human_section():
    raw = _escalated_doc()
    patch = landing.released(raw, "merge master in", "2026-10-23T07:00:00+00:00")

    assert patch["escalated"] is None and patch["escalation_class"] is None
    assert patch["status"] == "ready_for_review"
    assert patch["pause_log"] == [{"kind": "resumed"}]
    assert "cost_usd" not in patch
    human = patch["sections"][document.HUMAN]
    assert human.startswith("### Decision 1") and "merge master in" in human
    assert raw["escalated"] in human
    stamped = [e for e in patch["landing"] if e["step"] == "diagnose"]
    assert [e.get("released_at") for e in stamped] == ["2026-10-23T07:00:00+00:00"]
    assert "released_at" not in patch["landing"][0]
    assert "released_at" not in raw["landing"][1]


def test_released_stamps_only_the_last_diagnose_entry():
    raw = _escalated_doc(
        landing=[_attempt("account_limit"), {"head": HEAD, "step": "fix"}, _attempt("sdk")]
    )
    patch = landing.released(raw, "go", "T")
    assert [e.get("released_at") for e in patch["landing"]] == [None, None, "T"]


def test_released_answers_nothing_for_a_document_that_is_not_escalated():
    assert landing.released(_escalated_doc(escalated=None), "go", "T") == {}
    assert landing.released({}, "go", "T") == {}


def test_two_releases_leave_one_decision_and_one_resumed_marker():
    from agentic.core.process.domain.task import apply

    raw = _escalated_doc()
    once = apply(raw, landing.released(raw, "merge master in", "T"))
    twice = apply(once, landing.released(once, "merge master in", "T"))

    assert twice == once
    assert once["sections"][document.HUMAN].count("### Decision ") == 1
    assert once["pause_log"] == [{"kind": "escalation", "value": "failure"}, {"kind": "resumed"}]
    assert not once["escalated"] and not once["escalation_class"]


def test_released_numbers_a_second_decision_after_the_first():
    raw = _escalated_doc(sections={"Human": "### Decision 1\n\nolder", "Other": "kept"})
    from agentic.core.process.domain.task import apply

    after = apply(raw, landing.released(raw, "newer", "T"))
    assert after["sections"]["Other"] == "kept"
    assert "### Decision 1\n\nolder" in after["sections"]["Human"]
    assert "### Decision 2" in after["sections"]["Human"]


def test_entry_of_keeps_released_at_only_as_text():
    assert landing.entry_of({"released_at": "T"}) == {"released_at": "T"}
    assert landing.entry_of({"released_at": 3}) == {}


def test_diagnose_attempts_ignore_every_entry_up_to_and_including_a_released_one():
    raw = {"landing": [_attempt("max_turns"), _attempt("max_turns", released_at="T")]}
    assert landing.diagnose_attempts(raw) == []
    assert landing.diagnose_retry(raw, AT) == "none"

    later = {"landing": [*raw["landing"], _attempt("max_turns")]}
    assert [e.get("released_at") for e in landing.diagnose_attempts(later)] == [None]
    assert landing.diagnose_retry(later, AT) == "escalate"


def test_a_released_entry_is_still_not_a_taken_step():
    entries = [_attempt("max_turns", released_at="T")]
    assert not landing.already_dispatched(entries, HEAD, "diagnose", None)
