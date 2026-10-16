"""The landing table (GH-239, ADR-0048): `landing_step` maps every `MergeRefusal` onto one
closed step, the predicates around it say when a step may be taken, and the record keys
are the ones a poller tick reads back. Pure: facts in, a step out."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import get_args, get_type_hints

import pytest

from agentic.core.process.domain import ci, landing, rules
from agentic.core.process.domain.landing import LandingSpent, LandingStep
from agentic.core.process.domain.rules import MergeFacts
from agentic.core.process.domain.task import LandingEntry

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
    base = LandingSpent(passes=0, syncs=0, reruns_on_head=0, diagnosed=False, waited_s=None)
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
    "not_approved": "re_review",
    "head_not_reviewed": "re_review",
    "open_findings_blocking": "fix",
    "open_findings": "file_followups_and_land",
    "ci_failed": "rerun",
    "checks_failed": "rerun",
    "attempts_exhausted": "sync_master",
    "merge_failed": "diagnose",
    "master_moved": "wait",
    "adr_number_taken": "diagnose",
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
    assert step.kind in {"none", "wait", "diagnose", "file_followups_and_land"}


@pytest.mark.parametrize("refusal", get_args(rules.MergeRefusal))
def test_a_diagnosed_task_has_nothing_left_to_do(refusal):
    assert _step(refusal, _spent(diagnosed=True), owners=("api",)).kind == "none"


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


def test_a_conflicting_checks_failure_syncs_and_needs_both_budgets():
    assert _step("checks_failed", failed_step="conflict").kind == "sync_master"
    passes = _spent(passes=landing.LANDING_PASSES_MAX)
    assert _step("checks_failed", passes, failed_step="conflict").kind == "diagnose"
    syncs = _spent(syncs=landing.LANDING_SYNCS_MAX)
    assert _step("checks_failed", syncs, failed_step="conflict").kind == "diagnose"


@pytest.mark.parametrize("refusal", ["ci_failed", "checks_failed"])
def test_a_red_head_is_rerun_once_then_fixed_then_diagnosed(refusal):
    assert _step(refusal).kind == "rerun"
    assert _step(refusal, _spent(reruns_on_head=1), ("integration",)).kind == "fix"
    spent = _spent(reruns_on_head=1, passes=landing.LANDING_PASSES_MAX)
    assert _step(refusal, spent, ("integration",)).kind == "diagnose"


@pytest.mark.parametrize("refusal", ["eval_set_unreadable", "ci_missing", "ci_unreadable"])
def test_an_undecidable_ci_waits_until_the_bound_then_diagnoses(refusal):
    assert _step(refusal, _spent(waited_s=None)).kind == "wait"
    assert _step(refusal, _spent(waited_s=ci.CI_WAIT_MAX_S - 1)).kind == "wait"
    assert _step(refusal, _spent(waited_s=ci.CI_WAIT_MAX_S)).kind == "diagnose"


def test_important_findings_are_filed_whatever_the_pass_budget():
    spent = _spent(passes=landing.LANDING_PASSES_MAX)
    assert _step("open_findings", spent, open_important=1).kind == "file_followups_and_land"


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
    assert landing.already_dispatched(record, HEAD, "sync_master") is True
    assert landing.already_dispatched(record, "d" * 40, "sync_master") is False
    assert landing.already_dispatched(record, HEAD, "fix") is False
    assert landing.already_dispatched([], HEAD, "fix") is False


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


_SAMPLE: dict[type, object] = {
    str: "x",
    int: 1,
    float: 1.5,
    list[str]: ["a"],
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
