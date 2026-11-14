"""The decorators of an evaluation root (GH-470, part 2): an evaluation run reads the tracker
and the repository but never writes to the first nor pushes from the second, and its full
check always runs locally. Each member of `Tracker`, `Workspace` and `Inbox` is classified
here -- a member added to a port and left unclassified fails the introspection tests."""

from typing import Protocol, get_protocol_members

import pytest

from agentic.core.process.adapters.evaluation import (
    EvaluationStack,
    EvaluationTracker,
    EvaluationWorkspace,
    EvaluationWrite,
)
from agentic.core.process.application.ports import Tracker, Workspace
from tests.fakes import MemoryStack, MemoryTracker, MemoryWorkspace

# Every `Tracker` member the decorator refuses, with arguments that would reach a real tracker.
REFUSED_TRACKER_CALLS = {
    "open_pull_request": ("EVAL-1-aaaaaaaa-default-1", "branch", "t", "b"),
    "close_issue": ("EVAL-1-aaaaaaaa-default-1",),
    "comment_on_issue": ("7", "text"),
    "update_issue_body": (7, "body"),
    "rerun_failed_ci": ("https://github.com/o/r/pull/7", "abc"),
    "rerun_ci_run": ("123456",),
    "close_pull_request": ("https://github.com/o/r/pull/7",),
    "reopen_pull_request": (7,),
    "update_pull_request_body": (7, "body"),
    "dispatch_ci": ("ci.yml", "ref", {}),
    "merge": ("https://github.com/o/r/pull/7", "abc"),
}
# The writes keyed by task id the decorator swallows, and what each answers.
SWALLOWED_TRACKER_CALLS = {
    "comment": ("GH-1", "text"),
    "label": ("GH-1", "needs-human"),
    "unlabel": ("GH-1", "needs-human"),
    "note": ("GH-1", "text"),
    "drop_label": ("GH-1", "needs-human"),
}
READS_TRACKER = (
    "branch_ci",
    "changed_files",
    "ci_report",
    "dispatched_ci",
    "issue_created_at",
    "issue_state",
    "list_pull_requests",
    "pull_request",
    "pull_request_body",
    "pull_requests_on",
    "queue",
    "watched_branch_ci",
)


def test_a_tracker_write_keyed_by_task_id_does_nothing_and_reaches_no_tracker():
    inner = MemoryTracker()
    tracker = EvaluationTracker(inner)

    for name, args in SWALLOWED_TRACKER_CALLS.items():
        assert getattr(tracker, name)(*args) is None

    assert (inner.comments, inner.labels, inner.unlabeled) == ([], [], [])


def test_open_issue_with_labels_is_still_refused_as_a_value_error():
    inner = MemoryTracker()

    with pytest.raises(ValueError, match="evaluation runs write nothing"):
        EvaluationTracker(inner).open_issue("t", "b", label="agentic", labels=["bug"])

    assert inner.opened_issues == [] and inner.opened_label_sets == []


def test_ensure_label_is_refused_as_a_value_error_and_creates_nothing():
    inner = MemoryTracker()

    with pytest.raises(ValueError, match="evaluation runs write nothing"):
        EvaluationTracker(inner).ensure_label("needs-triage")

    assert inner.created_labels == []


@pytest.mark.parametrize("name", sorted(REFUSED_TRACKER_CALLS))
def test_every_other_tracker_write_raises_evaluation_write(name):
    inner = MemoryTracker()
    tracker = EvaluationTracker(inner)

    with pytest.raises(EvaluationWrite, match=name):
        getattr(tracker, name)(*REFUSED_TRACKER_CALLS[name])

    assert inner.pull_requests == []
    assert inner.opened_issues == []
    assert inner.closed_issues == []


def test_the_comments_of_the_real_issue_are_never_the_input_of_an_evaluation():
    inner = MemoryTracker()
    inner.earlier = ["a human said: do it differently"]
    tracker = EvaluationTracker(inner)

    assert tracker.issue_comments("GH-1") == []
    assert tracker.timestamped_comments("GH-1") == []
    assert inner.issue_comments("GH-1") == ["a human said: do it differently"]


def test_a_tracker_read_passes_through_to_the_real_tracker():
    inner = MemoryTracker()
    tracker = EvaluationTracker(inner)

    assert tracker.changed_files("https://github.com/o/r/pull/7") == 12
    assert inner.changed_files_asked == ["https://github.com/o/r/pull/7"]
    assert tracker.queue() == inner.queue()


def test_a_name_the_decorator_never_classified_is_refused_instead_of_delegated():
    tracker = EvaluationTracker(MemoryTracker())

    with pytest.raises(EvaluationWrite, match="a_future_write"):
        tracker.a_future_write("GH-1")  # type: ignore[attr-defined]


def test_a_private_name_is_not_an_evaluation_write():
    with pytest.raises(AttributeError):
        EvaluationTracker(MemoryTracker())._private  # noqa: B018


def classify(protocol: type[Protocol], decorator: type) -> list[str]:  # type: ignore[valid-type]
    """The members of `protocol` that `decorator` neither lists as allowed nor overrides."""
    allowed = set(decorator.ALLOWED) | set(getattr(decorator, "REFUSED", ()))
    overridden = {
        name for name, value in vars(decorator).items() if callable(value) and name[0] != "_"
    }
    return sorted(get_protocol_members(protocol) - allowed - overridden)


def test_every_tracker_member_is_an_allowed_read_or_overridden():
    assert classify(Tracker, EvaluationTracker) == []
    assert set(EvaluationTracker.ALLOWED) == set(READS_TRACKER)
    assert set(EvaluationTracker.REFUSED) == set(REFUSED_TRACKER_CALLS)


def test_the_classifier_reports_a_member_added_to_a_port_and_left_unclassified():
    class Grown(Tracker, Protocol):
        def label_all(self, task_id: str) -> None: ...

    assert classify(Grown, EvaluationTracker) == ["label_all"]


def test_every_workspace_member_is_allowed_or_overridden():
    assert classify(Workspace, EvaluationWorkspace) == []
    assert not set(EvaluationWorkspace.ALLOWED) - get_protocol_members(Workspace)


REFUSED_WORKSPACE_CALLS = {
    "push": ("agentic/GH-1",),
    "publish_ci_ref": (None, "agentic-ci/GH-1"),
    "delete_ci_ref": (None, "agentic-ci/GH-1"),
    "publish_dispatch_ref": (None, "GH-1"),
    "delete_dispatch_ref": ("GH-1",),
    "delete_remote_ref": ("agentic-ci/GH-1",),
}


@pytest.mark.parametrize("name", sorted(REFUSED_WORKSPACE_CALLS))
def test_no_ref_is_pushed_published_or_deleted_remotely(name):
    inner = MemoryWorkspace()
    workspace = EvaluationWorkspace(inner)

    with pytest.raises(EvaluationWrite, match=name):
        getattr(workspace, name)(*REFUSED_WORKSPACE_CALLS[name])

    assert inner.pushed == []


def test_a_local_workspace_call_delegates():
    inner = MemoryWorkspace()
    workspace = EvaluationWorkspace(inner)

    assert workspace.branches("agentic/") == inner.branches("agentic/")
    made = workspace.task_checkout("EVAL-1-aaaaaaaa-default-1")
    # The checkout exists in the real workspace now: the call went through it.
    assert made.created is True
    assert inner.task_checkout("EVAL-1-aaaaaaaa-default-1").created is False


def test_the_stack_of_an_evaluation_declares_no_ci_and_delegates_the_rest():
    inner = MemoryStack(checks={"tests": "pytest"}, ci=("tests",))
    stack = EvaluationStack(inner)

    assert inner.ci("HEAD") == ("tests",)
    assert stack.ci("HEAD") == ()
    assert stack.checks("HEAD") == {"tests": "pytest"}


def test_every_inbox_member_is_a_read_the_frozen_inbox_and_the_closed_one_both_hold():
    from agentic.core.process.adapters.evaluation import ClosedInbox
    from agentic.core.process.adapters.filesystem.file_inbox import FileInbox
    from agentic.core.process.application.ports import Inbox

    reads = {"task", "task_id", "issue_of", "open_issues", "open_issue_bodies", "open_issue_refs"}

    assert get_protocol_members(Inbox) == reads
    for implementation in (FileInbox, ClosedInbox):
        assert not reads - set(dir(implementation))
