"""`landing_plan.spent` (GH-239, GH-255): the budgets counted off the document's own
`landing` list. A `sync_master` that answered a conflict is a model pass; every other one is
a clean sync. Pure: a document in, the counts out."""

from datetime import datetime

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.application.ports import IssueComment, IssueState
from agentic.core.process.domain import landing, rules
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryTracker, memory_ports

HEAD = "a" * 40


def _sync(refusal: str) -> dict:
    return {"head": HEAD, "step": "sync_master", "refusal": refusal}


@pytest.mark.parametrize("refusal", landing.CONFLICTING_SYNC_REFUSALS)
def test_a_conflicting_sync_is_a_sync_of_its_own_and_never_a_pass(refusal):
    spent = landing_plan.spent({"landing": [_sync(refusal)]}, HEAD, None)
    assert (spent.passes, spent.syncs) == (0, 1)


def test_a_sync_answering_anything_else_is_a_clean_one():
    spent = landing_plan.spent({"landing": [_sync("attempts_exhausted")]}, HEAD, None)
    assert (spent.passes, spent.syncs) == (0, 1)


def test_the_loop_of_two_conflicting_syncs_for_one_sha_reaches_the_diagnose():
    entries = [
        {**_sync("merge_conflict"), "upstream": "b" * 40},
        {**_sync("checks_failed"), "upstream": "b" * 40},
    ]
    raw = {"landing": entries}
    spent = landing_plan.spent(raw, HEAD, None, "b" * 40)
    assert (spent.passes, spent.syncs, spent.conflicting_syncs_for_upstream) == (0, 2, 2)
    facts = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master="b" * 40,
        conflicts=True,
    )
    step = landing_plan.step_for(
        {"id": "GH-9", "title": "", "description": "", **raw}, "merge_conflict", facts
    )
    assert step.kind == "diagnose"


def _diagnose(**more) -> dict:
    return {"head": HEAD, "step": "diagnose", "refusal": "merge_failed", **more}


def test_only_a_delivered_diagnosis_counts_as_diagnosed():
    attempt = _diagnose(failure="account_limit")
    assert landing_plan.spent({"landing": [attempt]}, HEAD, None).diagnoses == 0
    both = {"landing": [attempt, _diagnose(delivered=True)]}
    assert landing_plan.spent(both, HEAD, None).diagnoses == 1


def test_a_redelivery_keeps_the_diagnosis_counted():
    resolved = landing.resolve_open([_diagnose(delivered=True)], "2026-10-15T13:00:00+00:00", 3.0)
    assert landing_plan.spent({"landing": resolved}, HEAD, None).diagnoses == 1


def test_an_undelivered_diagnose_entry_is_not_a_dispatched_step():
    assert not landing.already_dispatched([_diagnose(failure="sdk")], HEAD, "diagnose", None)
    assert landing.already_dispatched([_diagnose(delivered=True)], HEAD, "diagnose", None)
    assert landing.already_dispatched([{**_sync("x"), "step": "fix"}], HEAD, "fix", None)


def test_the_gate_skips_a_retry_that_is_not_due_and_an_escalation_already_made():
    from datetime import UTC, datetime, timedelta

    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    raw = {"landing": [_diagnose(failure="account_limit", at=at.isoformat())]}
    wait = timedelta(minutes=rules.ACCOUNT_LIMIT_SCHEDULE[0])
    assert landing_plan.diagnose_gate(raw, "diagnose", at + wait - timedelta(seconds=1)) == "skip"
    assert landing_plan.diagnose_gate(raw, "diagnose", at + wait) == "go"
    assert landing_plan.diagnose_gate(raw, "diagnose", None) == "skip"
    assert landing_plan.diagnose_gate(raw, "fix", None) == "go"
    sdk = {"landing": [_diagnose(failure="sdk", at=at.isoformat())]}
    assert landing_plan.diagnose_gate(sdk, "diagnose", at) == "escalate"
    assert landing_plan.diagnose_gate({**sdk, "escalated": "x"}, "diagnose", at) == "skip"


# --- GH-328: releasing an escalated landing diagnose --------------------------------------------

ATTEMPT_AT = "2026-10-23T06:00:00+00:00"
ESCALATED_AT = "2026-10-23T06:05:00+00:00"
ANSWERED_AT = "2026-10-23T06:47:00+00:00"


def _escalated(**over) -> dict:
    raw = {
        "id": "GH-5",
        "status": "waiting_on_human",
        "escalated": "Landing Diagnoser did not deliver after 1 attempt: max_turns",
        "escalation_class": "failure",
        "pull_request": "https://github.com/o/r/pull/5",
        "landing": [
            {
                "head": HEAD,
                "step": "diagnose",
                "refusal": "merge_failed",
                "at": ATTEMPT_AT,
                "failure": "max_turns",
            }
        ],
        "cost_usd": 4.0,
    }
    return {**raw, **over}


class _ReadingTracker(MemoryTracker):
    """A tracker that remembers what was read from it."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        self.reads.append(task_id)
        return super().timestamped_comments(task_id)


def _world(
    comments=("Escalated: x", "merge master in"),
    *,
    labels: tuple[str, ...] | None = (),
    state="OPEN",
):
    ports = memory_ports(tracker=_ReadingTracker())
    ports.tracker.timestamped["GH-5"] = [
        IssueComment(body, (ESCALATED_AT, ANSWERED_AT)[min(i, 1)])
        for i, body in enumerate(comments)
    ]
    ports.tracker.states[5] = IssueState(state, "", (), None, labels)
    return ports


def _release(ports, raw=None, cfg=None):
    cfg = cfg or Configuration(product_dir="/p")
    return landing_plan.release(ports, cfg, "GH-5", raw or _escalated(), 5)


def test_a_human_answer_after_the_escalation_releases_it():
    found = _release(_world())
    assert (found.decision, found.why) == ("merge master in", "released")


def test_without_a_human_comment_after_the_escalation_it_waits_on_a_human():
    found = _release(_world(("Escalated: x", "Auto-merge refused: ci_missing")))
    assert (found.decision, found.why) == (None, "waiting_on_human")


@pytest.mark.parametrize(
    "world",
    [
        dict(labels=("needs-human",)),
        dict(labels=None),
        dict(state="CLOSED"),
    ],
    ids=["label_still_there", "labels_unreadable", "issue_closed"],
)
def test_a_comment_does_not_release_while_a_condition_fails(world):
    found = _release(_world(**world))
    assert (found.decision, found.why) == (None, "waiting_on_human")


def test_a_label_that_was_never_added_counts_as_removed():
    assert _release(_world(labels=("agentic",))).why == "released"


def test_a_tracker_that_cannot_answer_does_not_release():
    ports = _world()
    del ports.tracker.states[5]  # `issue_state` raises
    ports.tracker.state_errors.add(5)
    assert _release(ports).why == "waiting_on_human"


def test_a_spent_cap_does_not_release_and_is_its_own_reason():
    cfg = Configuration(product_dir="/p", task_max_budget_usd=10.0)
    raw = _escalated(cost_usd=6.0, landing_cost_carried=4.0)
    found = _release(_world(), raw, cfg)
    assert (found.decision, found.why) == (None, "task_cap")
    under = _release(_world(), _escalated(cost_usd=6.0, landing_cost_carried=3.0), cfg)
    assert under.why == "released"


def test_an_escalation_after_a_delivered_diagnosis_is_the_humans_to_restart():
    delivered = {**_escalated()["landing"][0], "delivered": True}
    ports = _world()
    found = _release(ports, _escalated(landing=[delivered]))
    assert (found.decision, found.why) == (None, "needs_restart")
    # Nothing was read for it: the shape alone says no.
    assert ports.tracker.reads == []


def test_only_a_failure_class_escalation_is_releasable():
    assert _release(_world(), _escalated(escalation_class="questions")).why == "needs_restart"


def test_pending_counts_a_released_diagnose_as_go_and_still_skips_an_unreleased_one():
    facts = rules.MergeFacts(
        task_id="GH-5",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=None,
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master="b" * 40,
        merge_failures=3,
        merge_failure="gh_rejected",
    )
    live = landing_plan.LIVE_STEPS
    now = datetime.fromisoformat("2026-10-23T07:00:00+00:00")
    raw = _escalated()
    assert landing_plan.pending(raw, "merge_failed", facts, live, now) is None
    step = landing_plan.pending(raw, "merge_failed", facts, live, now, released=True)
    assert step is not None and step.kind == "diagnose"
    # Released has no effect on any other step.
    other = landing_plan.pending({}, "master_moved", facts, live, now, released=True)
    assert other is None


def _checks(head: str, *entries: dict) -> dict:
    return {"merge_checks": {"head": head, "passed": False, "checks": list(entries)}}


@pytest.mark.parametrize(
    ("raw", "reran"),
    [
        (_checks(HEAD, {"name": "tests", "passed": False, "tail": "x", "rerun": True}), True),
        (_checks(HEAD, {"name": "tests", "passed": False, "tail": "x"}), False),
        (_checks("c" * 40, {"name": "tests", "passed": False, "tail": "x", "rerun": True}), False),
        (_checks(HEAD, {"name": "tests", "passed": True, "flake": True}), False),
        ({}, False),
        ({"merge_checks": "garbage"}, False),
        ({"merge_checks": {"head": HEAD, "checks": "garbage"}}, False),
    ],
    ids=[
        "rerun_row",
        "before_the_change",
        "other_head",
        "flake_only",
        "absent",
        "text",
        "bad_list",
    ],
)
def test_a_red_check_with_a_rerun_row_on_the_head_is_a_fact_of_the_plan(raw, reran):
    assert landing_plan.spent(raw, HEAD, None).checks_rerun_on_head is reran
