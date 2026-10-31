"""A run killed during a landing pass with every unit approved (GH-450): the last `landing`
entry is open, nothing is reopened, the stage above the units was cut. One classification
(`rules.landing_pass`) tells `resume_target`, `resumed_status`, `resume_owed` and
`already_dispatched` what the pass is; apostrophe#484's frontmatter is the evidence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest

from agentic.core.process.domain import document, landing, rules
from agentic.core.process.domain.landing import LandingStepKind

FIXTURE = Path(__file__).parent / "fixtures" / "GH-484.yaml"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
NOT_REOPENING = (
    "wait",
    "rerun",
    "file_followups_and_land",
    "land_slice",
    "diagnose",
    "settle",
    "escalate_ledger",
    "escalate_adr",
    "escalate",
    "none",
)
KINDS = get_args(LandingStepKind)


def killed() -> dict[str, Any]:
    """apostrophe#484 as the kill left it, through the production document loader."""
    return document.parse(f"---\n{FIXTURE.read_text()}---\n\n# GH-484\n")


def with_entry(raw: dict[str, Any], **entry: object) -> dict[str, Any]:
    return {**raw, "landing": [*raw["landing"][:-1], {**raw["landing"][-1], **entry}]}


def test_the_step_table_classifies_every_kind_once():
    assert set(NOT_REOPENING).isdisjoint(rules.STAGE_REOPENING_STEPS)
    assert set(NOT_REOPENING) | rules.STAGE_REOPENING_STEPS == set(KINDS)
    assert rules.STAGE_REOPENING_STEPS <= set(KINDS)


def test_the_fixture_is_the_pre_merge_state_of_apostrophe_484():
    raw = killed()
    assert raw["status"] == "developing" and raw["pull_request"]
    assert "merge_commit" not in raw and raw["landing"][-1]["step"] == "fix"
    assert not rules.landing_open(raw)  # the one predicate that read the task as ended
    assert rules.landing_pass(raw) == "unfinished" and rules.landing_pass_live(raw)
    assert rules.resume_target(raw) == "integrate"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("unit", ["approved", "returned", "pending", "delivered"])
@pytest.mark.parametrize("based", [True, False])
def test_each_kind_by_unit_state_has_one_target(kind, unit, based):
    raw = killed()
    entry: dict[str, Any] = {"step": kind}
    if not based:
        entry["cost_base"] = None
    raw = with_entry(raw, **entry)
    if not based:
        del raw["landing"][-1]["cost_base"]
    raw["subtasks"] = {name: {**sub, "status": unit} for name, sub in raw["subtasks"].items()}
    if unit != "approved":
        expected = ("reopened", "integrate")
    elif kind in rules.STAGE_REOPENING_STEPS:
        expected = ("unfinished" if based else "unstarted", "integrate" if based else None)
    else:
        expected = (None, None)
    assert (rules.landing_pass(raw), rules.resume_target(raw)) == expected
    assert rules.landing_pass_live(raw) is (expected[0] in ("reopened", "unfinished"))


def test_an_open_pass_never_resumes_a_task_that_is_not_a_pull_request_task():
    raw = killed()
    assert rules.landing_pass({**raw, "pull_request": None}) is None


def test_a_resolved_entry_or_one_before_the_pull_request_is_no_pass():
    raw = killed()
    assert rules.landing_pass(with_entry(raw, resolved_at="2026-10-30T11:00:00+00:00")) is None
    assert rules.landing_pass(with_entry(raw, before_pr=True)) is None
    assert rules.resume_target(with_entry(raw, closed_at="2026-10-30T11:00:00+00:00")) is None


@pytest.mark.parametrize("status", ["split", "tracker_only", "discarded"])
def test_an_ended_task_with_a_resolved_pass_resumes_nowhere(status):
    raw = with_entry(killed(), resolved_at="2026-10-30T11:00:00+00:00")
    assert rules.resume_target({**raw, "status": status}) is None
    assert rules.resume_target({**raw, "status": "ready_for_review"}) is None


def test_an_escalated_task_resumes_nowhere_and_keeps_developing_when_the_pass_is_live():
    raw = {**killed(), "escalated": "why", "status": "waiting_on_human"}
    assert rules.resume_target(raw) is None
    assert rules.resumed_status(raw) == "developing"
    ended = with_entry(raw, step="rerun")
    assert rules.landing_pass(ended) is None
    assert rules.resumed_status(ended) == "ready_for_review"
    assert rules.resumed_status(with_entry(raw, step="escalate")) == "ready_for_review"


def test_an_owed_publication_resumes_at_deliver_even_with_an_open_reopening_entry():
    raw = killed()
    head = raw["task_head"]
    owed = {
        **raw,
        "full_checks": {**raw["full_checks"], "head": head, "passed": True},
        "publish_owed": head,
    }
    assert rules.landing_open(owed) is False
    assert rules.resume_target(owed) == "deliver"
    assert rules.resumed_status(owed) == "developing"
    assert rules.publication_candidate(owed) == head


def test_resume_owed_reads_the_unfinished_pass_without_a_run_since_the_spawn():
    raw = killed()
    assert landing.resume_owed(raw, NOW)
    spawned = (NOW - timedelta(hours=1)).isoformat()
    # a role run after the spawn says nothing when every unit is approved
    old = with_entry(raw, resume_spawns=1, resume_spawned_at=spawned)
    assert landing.resume_owed({**old, "runs": [{"started_at": NOW.isoformat()}]}, NOW)
    recent = with_entry(
        raw, resume_spawns=1, resume_spawned_at=(NOW - timedelta(minutes=1)).isoformat()
    )
    assert not landing.resume_owed(recent, NOW)
    spent = with_entry(raw, resume_spawns=landing.LANDING_RESUME_SPAWNS_MAX)
    assert not landing.resume_owed(spent, NOW)
    assert not landing.resume_owed({**raw, "escalated": "x"}, NOW)


def test_resume_owed_still_wants_no_run_since_for_a_reopened_pass():
    raw = killed()
    raw["subtasks"] = {n: {**s, "status": "returned"} for n, s in raw["subtasks"].items()}
    spawned = (NOW - timedelta(hours=1)).isoformat()
    old = with_entry(raw, resume_spawns=1, resume_spawned_at=spawned)
    assert landing.resume_owed({**old, "runs": []}, NOW)
    assert not landing.resume_owed({**old, "runs": [{"started_at": NOW.isoformat()}]}, NOW)


def test_an_unstarted_pass_is_owed_to_land_and_is_not_taken():
    raw = killed()
    del raw["landing"][-1]["cost_base"]
    assert rules.landing_pass(raw) == "unstarted" and not landing.resume_owed(raw, NOW)
    assert rules.resume_target(raw) is None
    head, entries = raw["landing"][-1]["head"], raw["landing"]
    assert landing.already_dispatched(entries, head, "fix", None) is True  # unaware of units
    assert landing.already_dispatched(entries, head, "fix", None, approved=True) is False
    # an entry the executor stamped, or one for another head, is as before
    started = killed()["landing"]
    assert landing.already_dispatched(started, head, "fix", None, approved=True) is True
    assert landing.already_dispatched(entries, "d" * 40, "fix", None, approved=True) is False
