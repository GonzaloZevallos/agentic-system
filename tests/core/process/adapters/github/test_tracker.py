"""The `gh` writer with the CLI replaced by a recorder."""

import json
import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import GhTracker
from agentic.core.process.adapters.github.tracker import issue_number
from agentic.core.process.domain.task import PullRequest, PullRequestCommit


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


def test_open_issue_uses_body_file_and_sends_no_label(tmp_path, monkeypatch):
    written = {}

    class RecordingRun(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if "--body-file" in args:
                path = args[args.index("--body-file") + 1]
                written["path"] = path
                written["body"] = Path(path).read_text(encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, self.stdout, "")

    run = RecordingRun()
    url = GhTracker(Path("/p"), run).open_issue("[needs-info] Stack review", "the body")
    assert url == "https://github.com/o/r/pull/7"
    [call] = run.calls
    assert call[:3] == ["gh", "issue", "create"]
    assert "--title" in call and "[needs-info] Stack review" in call
    assert "--body-file" in call
    assert "--label" not in call
    assert "--search" not in call
    assert written["body"] == "the body"
    # The temp file does not outlive the call.
    assert not Path(written["path"]).exists()


def test_a_task_without_an_issue_writes_nothing_to_the_issue():
    run = Recorder()
    tracker = GhTracker(Path("/p"), run)
    tracker.comment("T-abc", "x")
    tracker.label("T-abc", "needs-human")
    assert run.calls == []


def test_gh_failures_are_value_errors():
    with pytest.raises(ValueError, match="gh pr create failed: no auth"):
        GhTracker(Path("/p"), Recorder(fail=True)).open_pull_request("T-1", "b", "t", "x")


def _graphql_pull_request(state, merged_at, created_at, commits):
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "state": state,
                        "mergedAt": merged_at,
                        "createdAt": created_at,
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "messageHeadline": headline,
                                        "additions": additions,
                                        "deletions": deletions,
                                        "authors": {"nodes": [{"email": email}]},
                                    }
                                }
                                for email, headline, additions, deletions in commits
                            ]
                        },
                    }
                }
            }
        }
    )


def test_pull_request_never_calls_gh_bare_and_pins_the_reference_in_argv():
    run = Recorder(
        stdout=_graphql_pull_request(
            "MERGED",
            "2026-09-21T00:29:02Z",
            "2026-09-20T23:00:00Z",
            [("dev@example.com", "fix: x", 3, 1)],
        )
    )
    tracker = GhTracker(Path("/p"), run)
    pr = tracker.pull_request("https://github.com/o/r/pull/7")
    assert pr == PullRequest(
        state="MERGED",
        created_at="2026-09-20T23:00:00Z",
        merged_at="2026-09-21T00:29:02Z",
        commits=(
            PullRequestCommit(
                author_emails=("dev@example.com",),
                message_headline="fix: x",
                additions=3,
                deletions=1,
            ),
        ),
    )
    assert len(run.calls) == 1
    [call] = run.calls
    assert call[:2] == ["gh", "api"]
    assert "number=7" in call


def test_pull_request_maps_a_closed_unmerged_and_an_open_pull_request():
    tracker = GhTracker(
        Path("/p"),
        Recorder(stdout=_graphql_pull_request("CLOSED", None, "2026-09-20T23:00:00Z", [])),
    )
    closed = tracker.pull_request("https://github.com/o/r/pull/8")
    assert closed == PullRequest(
        state="CLOSED", created_at="2026-09-20T23:00:00Z", merged_at=None, commits=()
    )

    tracker = GhTracker(
        Path("/p"),
        Recorder(stdout=_graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", [])),
    )
    open_pr = tracker.pull_request("https://github.com/o/r/pull/9")
    assert open_pr == PullRequest(
        state="OPEN", created_at="2026-09-20T23:00:00Z", merged_at=None, commits=()
    )


def test_pull_request_unknown_reference_raises_a_value_error():
    run = Recorder(fail=True)
    with pytest.raises(ValueError, match="gh api graphql failed: no auth"):
        GhTracker(Path("/p"), run).pull_request("https://github.com/o/r/pull/404")


def test_pull_request_unparseable_url_raises_a_value_error():
    with pytest.raises(ValueError, match="not a pull request URL"):
        GhTracker(Path("/p"), Recorder()).pull_request("not a url")


def test_a_resume_unlabels_and_reads_the_comments():
    run = Recorder(stdout='{"comments": [{"body": "Escalated: x"}, {"body": "Use USD."}]}')
    tracker = GhTracker(Path("/p"), run)
    tracker.unlabel("GH-12", "needs-human")
    assert tracker.issue_comments("GH-12") == ["Escalated: x", "Use USD."]
    assert run.calls == [
        ["gh", "issue", "edit", "12", "--remove-label", "needs-human"],
        ["gh", "issue", "view", "12", "--json", "comments"],
    ]
    assert GhTracker(Path("/p"), Recorder(stdout='{"comments": []}')).issue_comments("GH-1") == []
    assert tracker.issue_comments("T-abc") == [] and len(run.calls) == 2
