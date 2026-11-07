"""The `single` review arm's pure rules (GH-473): which branches the system approves, the one
approval patch both approval paths write, the pair a red suite counts on, and how many shadow
reviews may run at once. Nothing here reads a configuration: the mode is a parameter."""

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import pair_for
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN, apply

_STAMP = "2026-11-06T12:00:00+00:00"

SUITE_PAIR = "Tech Lead→Developer:integration-full_checks"


def sub(id_, status="delivered", **more):
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
        "branch": f"agentic/GH-1.{id_}",
        "base": "base1",
        **more,
    }


def state(*names, **more):
    return {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "subtasks": {name: sub(name) for name in names},
        **more,
    }


@pytest.mark.parametrize(
    ("names", "subtask_id", "mode", "expected"),
    [
        (("a", "b"), "a", "single", True),
        (("a", "b", INTEGRATION), "b", "single", True),
        (("a", "b", INTEGRATION), INTEGRATION, "single", False),
        (("a",), "a", "single", False),
        (("a", INTEGRATION), "a", "single", False),
        (("a", "b"), "a", "full", False),
        (("a", "b"), "a", "light", False),
    ],
)
def test_system_approves_truth_table(names, subtask_id, mode, expected):
    assert rules.system_approves(state(*names), subtask_id, mode) is expected


FINDING = {
    "subtask": "a",
    "version": 1,
    "location": "x.py:1",
    "claim": "wrong",
    "severity": "blocking",
    "scope": "subtask",
    "category": "spec-behaviour",
    "status": "open",
}


def test_a_plain_approval_is_what_the_reviewer_always_wrote():
    base = state("a", "b")
    delivered = base["subtasks"]["a"]
    patch = rules.approval_patch(base, "a", delivered, 1, [], "head1")
    assert patch == {
        "approval_settled": [],
        "subtasks": {"a": {**delivered, "reviewed_head": "head1", "status": "approved"}},
    }


def test_an_ordinary_return_keeps_its_reason_on_approval():
    returned = sub("a", "returned", reason="fix it", change_category="spec-behaviour")
    base = {**state("a", "b"), "findings": [FINDING]}
    patch = rules.approval_patch(base, "a", returned, 2, [], "head2")
    approved = patch["subtasks"]["a"]
    assert approved["status"] == "approved" and approved["reason"] == "fix it"
    assert [row["claim"] for row in patch["approval_settled"]] == ["wrong"]


def test_a_subtask_returned_by_final_validation_is_cleared_when_the_system_approves_it():
    returned = sub(
        "a",
        "returned",
        reason="PM: wrong screen",
        change_category="spec-behaviour",
        returned_by="final_validation",
        returned_with=["qa_performance"],
    )
    base = state("a", "b")
    base["subtasks"]["a"] = returned
    patch = rules.approval_patch(base, "a", returned, 1, [], "head3")
    approved = patch["subtasks"]["a"]
    assert approved["status"] == "approved" and approved["reviewed_head"] == "head3"
    for key in ("returned_by", "returned_with", "reason", "change_category"):
        assert key not in approved
    # Folded into the state, nothing reads the subtask as returned by anyone.
    folded = apply(base, patch)
    assert rules.returned_by_all(folded["subtasks"]["a"]) == ("review",)
    assert folded["subtasks"]["a"]["status"] == "approved"


@pytest.mark.parametrize("mode", ["full", "light"])
def test_full_checks_pair_outside_single_is_the_integrations_pair(mode):
    assert rules.full_checks_pair(INTEGRATION, mode) == pair_for(INTEGRATION)


def test_full_checks_pair_under_single_has_its_own_name():
    pair = rules.full_checks_pair(INTEGRATION, "single")
    assert pair.key == SUITE_PAIR
    assert pair.key != pair_for(INTEGRATION).key


RED = {
    "head": "a" * 40,
    "version": 1,
    "passed": False,
    "results": [{"name": "tests", "passed": False}],
}


def integration_state(**more):
    return state("a", "b", INTEGRATION, **more)


def test_under_single_the_suite_does_not_spend_the_integrations_review_rounds():
    base = integration_state()
    returned = apply(base, rules.full_checks_return(base, RED, 2, at=_STAMP, mode="single"))
    assert returned["changes_requested"] == {SUITE_PAIR: 1}
    # Then the review returns it once: the unit still has one round left.
    review_return = {"changes_requested": rules.record_return(returned, pair_for(INTEGRATION))}
    again = apply(returned, review_return)
    assert rules.remaining_changes(again, pair_for(INTEGRATION), 2) == 1


def test_under_full_the_suite_and_the_review_share_the_integrations_pair():
    base = integration_state()
    returned = apply(base, rules.full_checks_return(base, RED, 2, at=_STAMP))
    assert returned["changes_requested"] == {pair_for(INTEGRATION).key: 1}
    review_return = {"changes_requested": rules.record_return(returned, pair_for(INTEGRATION))}
    again = apply(returned, review_return)
    assert rules.remaining_changes(again, pair_for(INTEGRATION), 2) == 0


def test_under_single_the_suite_at_its_limit_escalates_with_returns_and_resumes_its_own_pair():
    spent = integration_state(changes_requested={SUITE_PAIR: 2, pair_for(INTEGRATION).key: 1})
    patch = rules.full_checks_return(spent, RED, 2, at=_STAMP, mode="single")
    assert patch["escalation_class"] == "returns"
    assert patch["status"] == WAITING_ON_HUMAN
    assert patch["escalated"].startswith(f"{SUITE_PAIR} hit 2 returns: ")
    stopped = apply(spent, patch)
    resumed = apply(stopped, rules.resumed(stopped, "go on", at=_STAMP))
    assert resumed["changes_requested"] == {SUITE_PAIR: 0, pair_for(INTEGRATION).key: 1}


@pytest.mark.parametrize(
    ("parallel", "roles", "expected"),
    [(2, 4, 2), (2, 2, 1), (5, 3, 2), (2, 1, 0), (1, 1, 0), (3, 0, 0), (1, 8, 1)],
)
def test_shadow_slots_leave_one_role_slot_free(parallel, roles, expected):
    assert rules.shadow_slots(parallel, roles) == expected
