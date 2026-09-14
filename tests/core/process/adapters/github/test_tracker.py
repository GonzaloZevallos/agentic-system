"""The `gh` writer with the CLI replaced by a recorder."""

import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import GhTracker
from agentic.core.process.adapters.github.tracker import issue_number


class Recorder:
    def __init__(self, stdout="https://github.com/o/r/pull/7\n", fail=False):
        self.calls = []
        self.stdout = stdout
        self.fail = fail

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(1, args, stderr="no auth")
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def test_issue_number_is_read_off_the_task_id():
    assert issue_number("GH-12") == 12 and issue_number("T-abc") is None


def test_pull_request_comment_and_label_go_through_gh():
    run = Recorder()
    tracker = GhTracker(Path("/p"), run)
    url = tracker.open_pull_request("GH-12", "agentic/GH-12", "Dashboard", "body")
    assert url == "https://github.com/o/r/pull/7"
    tracker.comment("GH-12", "PR: x")
    tracker.label("GH-12", "needs-human")
    assert run.calls == [
        [
            "gh",
            "pr",
            "create",
            "--head",
            "agentic/GH-12",
            "--title",
            "Dashboard",
            "--body",
            "Closes #12\n\nbody",
        ],
        ["gh", "issue", "comment", "12", "--body", "PR: x"],
        ["gh", "issue", "edit", "12", "--add-label", "needs-human"],
    ]


def test_a_task_without_an_issue_writes_nothing_to_the_issue():
    run = Recorder()
    tracker = GhTracker(Path("/p"), run)
    tracker.comment("T-abc", "x")
    tracker.label("T-abc", "needs-human")
    assert run.calls == []


def test_gh_failures_are_value_errors():
    with pytest.raises(ValueError, match="gh pr create failed: no auth"):
        GhTracker(Path("/p"), Recorder(fail=True)).open_pull_request("T-1", "b", "t", "x")
