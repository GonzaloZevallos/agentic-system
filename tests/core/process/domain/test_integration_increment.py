"""The pure rule of an integration increment (GH-392): a resume from the document that finds the
task branch strictly past the integration's approved head, with nothing else wrong, reviews the
commits after it instead of refusing; every other moved head is a refusal."""

import pytest

from agentic.core.process.domain import rules

TASK = "GH-373"
H = "a" * 40
X = "b" * 40


def state(**more):
    base = {
        "type": "feature",
        "status": "waiting_on_human",
        "escalated": "the Developer failed",
        "escalation_class": "failure",
        "branch": rules.task_branch(TASK),
        "subtasks": {
            "api": {"status": "approved", "reviewed_head": "1" * 40, "branch": "agentic/GH-373.api"},
            "integration": {
                "status": "approved",
                "reviewed_head": H,
                "branch": rules.task_branch(TASK),
            },
        },
        "task_head": H,
        "full_checks": {"head": H, "passed": True},
    }
    return {**base, **more}


def unit(**more):
    sub = {**state()["subtasks"]["integration"], **more}
    return {"subtasks": {**state()["subtasks"], "integration": sub}}


def decide(s, head=X, descends=True):
    return rules.integration_increment(s, TASK, head, descends)


def test_a_strict_descendant_of_the_approved_head_is_an_increment():
    assert decide(state()) == "increment"


def test_the_same_commit_at_another_length_agrees():
    assert decide(state(), head=H[:12], descends=True) == "agree"
    assert decide(state(), head=H, descends=False) == "agree"


def test_a_head_that_does_not_descend_is_refused():
    assert decide(state(), descends=False) == "refuse"


@pytest.mark.parametrize(
    "more",
    [
        pytest.param({"pull_request": "https://example.test/pr/1"}, id="pull-request"),
        pytest.param(unit(status="delivered"), id="unit-not-approved"),
        pytest.param(unit(reviewed_head=None), id="unit-without-head"),
        pytest.param(unit(branch="agentic/GH-373.other"), id="unit-not-on-the-task-branch"),
        pytest.param(
            {
                "subtasks": {
                    **state()["subtasks"],
                    "api": {"status": "delivered", "branch": "agentic/GH-373.api"},
                }
            },
            id="another-unit-not-approved",
        ),
        pytest.param({"escalation_class": "questions"}, id="questions"),
        pytest.param({"escalation_class": "returns"}, id="pair-limit"),
        pytest.param({"escalation_class": "estimate"}, id="estimate"),
        pytest.param({"escalation_class": "cap"}, id="cap"),
        pytest.param({"questions_for_human": ["which one?"]}, id="pending-questions"),
        pytest.param({"fit_question": True}, id="fit-question"),
        pytest.param({"subtasks": {}}, id="no-reviewed-unit"),
    ],
)
def test_any_other_condition_failing_refuses_a_descendant(more):
    assert decide(state(**more)) == "refuse"


@pytest.mark.parametrize(
    "more",
    [
        pytest.param({"escalated": None, "escalation_class": None}, id="stopped-without-escalating"),
        pytest.param({"escalation_class": "failure"}, id="role-failure"),
    ],
)
def test_a_stopped_run_and_a_role_failure_are_both_increment_cases(more):
    assert decide(state(**more)) == "increment"


def test_a_single_unit_task_reviews_its_one_subtask():
    only = {"only": {"status": "approved", "reviewed_head": H, "branch": rules.task_branch(TASK)}}
    assert decide(state(subtasks=only)) == "increment"


def test_the_patch_delivers_the_reviewed_unit_and_keeps_its_head():
    before = state()
    patch = rules.increment_patch(before)
    # The reducer replaces a subtask whole, so the patch carries every key but the status.
    assert patch == {
        "subtasks": {"integration": {**before["subtasks"]["integration"], "status": "delivered"}}
    }
    assert patch["subtasks"]["integration"]["reviewed_head"] == H
    assert set(patch) == {"subtasks"}  # never task_head, full_checks, or the findings table
