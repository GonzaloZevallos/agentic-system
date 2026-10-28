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
            "api": {
                "status": "approved",
                "reviewed_head": "1" * 40,
                "branch": "agentic/GH-373.api",
            },
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


def decide(s, head=X, descends=True, commits=()):
    return rules.integration_increment(s, TASK, head, descends, commits)


def test_a_strict_descendant_of_the_approved_head_is_an_increment():
    assert decide(state()) == "increment"


def test_the_same_commit_at_another_length_agrees():
    assert decide(state(), head=H[:12], descends=True) == "agree"
    assert decide(state(), head=H, descends=False) == "agree"


DOCS = "c" * 40


def docs_record(**more):
    return {"parent": H, "head": DOCS, "paths": ["docs/product/dashboard.md"], **more}


def test_the_close_time_docs_commit_on_the_approved_head_agrees_not_an_increment():
    assert decide(state(close_docs=docs_record()), head=DOCS) == "agree"


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(docs_record(parent="d" * 40), id="foreign-parent"),
        pytest.param(docs_record(paths=["agentic/x.py"]), id="path-outside-docs-product"),
        pytest.param(docs_record(head="e" * 40), id="stale-record"),
    ],
)
def test_a_docs_record_that_does_not_cover_the_head_leaves_it_an_increment(record):
    assert decide(state(close_docs=record), head=DOCS) == "increment"


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
        pytest.param(
            {"escalated": None, "escalation_class": None}, id="stopped-without-escalating"
        ),
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


# --- GH-408: merges of approved subtasks while others are returned ------------------------------

API = "1" * 40  # the approved `api` subtask's reviewed head
M1 = "d" * 40
M2 = "e" * 40


def commit(sha, *parents):
    return rules.Commit(sha, tuple(parents))


def merge_of_api(first=H, sha=M1):
    return commit(sha, first, API)


def returned_siblings(**more):
    """GH-373's shape: the approved `api` merged on top, two subtasks still returned."""
    subtasks = {
        **state()["subtasks"],
        "sheets": {"status": "returned", "branch": "agentic/GH-373.sheets"},
        "stock": {"status": "returned", "branch": "agentic/GH-373.stock"},
    }
    return state(**{"subtasks": subtasks, **more})


def test_a_merge_of_an_approved_subtask_with_returned_siblings_is_a_reintegration():
    assert decide(returned_siblings(), commits=(merge_of_api(),)) == "reintegrate"


def test_several_merges_on_the_first_parent_line_are_a_reintegration():
    subtasks = {
        **returned_siblings()["subtasks"],
        "db": {"status": "approved", "reviewed_head": "2" * 40, "branch": "agentic/GH-373.db"},
    }
    commits = (merge_of_api(), commit(M2, M1, "2" * 40))
    assert decide(returned_siblings(subtasks=subtasks), commits=commits) == "reintegrate"


def test_a_stopped_run_without_escalation_is_a_reintegration_too():
    s = returned_siblings(escalated=None, escalation_class=None)
    assert decide(s, commits=(merge_of_api(),)) == "reintegrate"


def test_a_shorter_recorded_head_still_names_the_merged_parent():
    commits = (commit(M1, H[:12], API[:12]),)
    assert decide(returned_siblings(), commits=commits) == "reintegrate"


@pytest.mark.parametrize(
    "commits",
    [
        pytest.param((), id="empty-range"),
        pytest.param((commit(M1, H),), id="plain-commit"),
        pytest.param((commit(M1),), id="root-commit"),
        pytest.param((commit(M1, H, API, "2" * 40),), id="octopus"),
        pytest.param((commit(M1, H, "3" * 40),), id="second-parent-past-reviewed-head"),
        pytest.param((commit(M1, H, "1" * 12 + "f" * 28),), id="second-parent-off-reviewed-head"),
        pytest.param((commit(M1, H, "0" * 40),), id="second-parent-ancestor-of-reviewed-head"),
        pytest.param((commit(M1, H, "9" * 40),), id="unknown-branch"),
        pytest.param((merge_of_api(first="f" * 40),), id="approved-head-off-the-first-parent-line"),
        pytest.param((merge_of_api(), commit(M2, "f" * 40, API)), id="later-merge-off-the-line"),
        pytest.param((merge_of_api(), commit(M2, M1)), id="plain-commit-after-a-merge"),
    ],
)
def test_any_other_history_is_refused_with_returned_siblings(commits):
    assert decide(returned_siblings(), commits=commits) == "refuse"


@pytest.mark.parametrize("status", ["returned", "delivered", "pending", "open"])
def test_a_merge_of_a_branch_that_is_not_approved_is_refused(status):
    subtasks = {
        **returned_siblings()["subtasks"],
        "db": {"status": status, "reviewed_head": "2" * 40, "branch": "agentic/GH-373.db"},
    }
    commits = (commit(M1, H, "2" * 40),)
    assert decide(returned_siblings(subtasks=subtasks), commits=commits) == "refuse"


@pytest.mark.parametrize("status", ["delivered", "pending"])
def test_a_sibling_that_is_neither_approved_nor_returned_refuses(status):
    subtasks = {
        **returned_siblings()["subtasks"],
        "stock": {"status": status, "branch": "agentic/GH-373.stock"},
    }
    assert decide(returned_siblings(subtasks=subtasks), commits=(merge_of_api(),)) == "refuse"


def test_the_reviewed_unit_s_own_head_is_not_a_subtask_to_merge():
    commits = (commit(M1, H, H),)
    assert decide(returned_siblings(), commits=commits) == "refuse"


@pytest.mark.parametrize(
    "more",
    [
        pytest.param({"pull_request": "https://example.test/pr/1"}, id="pull-request"),
        pytest.param({"escalation_class": "questions"}, id="questions"),
        pytest.param({"escalation_class": "returns"}, id="pair-limit"),
        pytest.param({"questions_for_human": ["which one?"]}, id="pending-questions"),
        pytest.param({"fit_question": True}, id="fit-question"),
        pytest.param({"escalation_class": None}, id="escalated-without-class"),
    ],
)
def test_the_gh392_conditions_still_refuse_a_reintegration(more):
    assert decide(returned_siblings(**more), commits=(merge_of_api(),)) == "refuse"


def test_a_reviewed_unit_that_is_not_approved_refuses_a_reintegration():
    s = returned_siblings()
    s["subtasks"]["integration"] = {**s["subtasks"]["integration"], "status": "delivered"}
    assert decide(s, commits=(merge_of_api(),)) == "refuse"


def test_a_not_descendant_head_is_refused_even_with_good_looking_merges():
    assert decide(returned_siblings(), descends=False, commits=(merge_of_api(),)) == "refuse"


def test_the_commits_are_not_read_when_every_unit_is_approved_or_the_head_agrees():
    assert decide(state(), commits=(commit(M1, H),)) == "increment"
    assert decide(state(), head=H, commits=(commit(M1, H),)) == "agree"


def test_the_first_refused_commit_is_named_with_its_reason():
    s = returned_siblings()
    plain = commit(M2, M1)
    assert rules.refused_commit(s, (merge_of_api(), plain)) == f"{M2} is not a merge"
    assert "no commit" in rules.refused_commit(s, ())
    assert rules.refused_commit(s, (commit(M1, H, API, "2" * 40),)) == (
        f"{M1} is an octopus merge of 3 parents"
    )
    assert "not the reviewed head of an approved subtask" in rules.refused_commit(
        s, (commit(M1, H, "9" * 40),)
    )
    assert "first-parent line" in rules.refused_commit(s, (merge_of_api(first="f" * 40),))
    assert rules.refused_commit(s, (merge_of_api(),)) is None


def test_the_reintegration_patch_holds_the_integration_back_on_the_returned_subtasks():
    before = returned_siblings()
    patch = rules.reintegrate_patch(before)
    assert patch == {
        "subtasks": {
            "integration": {
                **before["subtasks"]["integration"],
                "status": "pending",
                "depends_on": ["sheets", "stock"],
            }
        }
    }
    assert patch["subtasks"]["integration"]["reviewed_head"] == H
    assert set(patch) == {"subtasks"}  # never task_head, full_checks, status or the findings table


def test_the_reintegration_patch_names_a_pending_sibling_too_and_skips_approved_ones():
    subtasks = {
        "a": {"status": "approved"},
        "b": {"status": "pending"},
        "integration": state()["subtasks"]["integration"],
        "c": {"status": "returned"},
    }
    patch = rules.reintegrate_patch(state(subtasks=subtasks))
    assert patch["subtasks"]["integration"]["depends_on"] == ["b", "c"]


def test_the_refusal_names_a_commit_only_when_the_commits_are_all_that_is_wrong():
    s = returned_siblings()
    plain = (commit(M1, H),)
    assert rules.refused_reintegration(s, TASK, True, plain) == f"{M1} is not a merge"
    assert rules.refused_reintegration(s, TASK, True, (merge_of_api(),)) is None
    assert rules.refused_reintegration(state(), TASK, True, plain) is None  # all approved
    assert rules.refused_reintegration(s, TASK, False, plain) is None
    assert (
        rules.refused_reintegration(returned_siblings(pull_request="u"), TASK, True, plain) is None
    )
    assert (
        rules.refused_reintegration(
            returned_siblings(escalation_class="questions"), TASK, True, plain
        )
        is None
    )
