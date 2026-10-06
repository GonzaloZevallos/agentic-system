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


def _graphql_pull_request(state, merged_at, created_at, commits, merge_oid=None, head_oid=...):
    head = {} if head_oid is ... else {"headRefOid": head_oid}
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        **head,
                        "state": state,
                        "mergedAt": merged_at,
                        "createdAt": created_at,
                        "mergeCommit": {"oid": merge_oid} if merge_oid else None,
                        "commits": {
                            "nodes": [
                                {
                                    "commit": {
                                        "messageHeadline": headline,
                                        "additions": additions,
                                        "deletions": deletions,
                                        "authors": {"nodes": [{"email": email}]},
                                        "parents": {"totalCount": parent_count},
                                    }
                                }
                                for email, headline, additions, deletions, parent_count in commits
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
            [("dev@example.com", "fix: x", 3, 1, 1)],
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
                parent_count=1,
            ),
        ),
    )
    assert len(run.calls) == 1
    [call] = run.calls
    assert call[:2] == ["gh", "api"]
    assert "number=7" in call


def test_pull_request_carries_the_merge_commit_only_when_it_merged():
    merged = GhTracker(
        Path("/p"),
        Recorder(
            stdout=_graphql_pull_request(
                "MERGED", "2026-09-21T00:29:02Z", "2026-09-20T23:00:00Z", [], merge_oid="af341d6"
            )
        ),
    ).pull_request("https://github.com/o/r/pull/7")
    assert merged is not None and merged.merge_commit == "af341d6"
    closed = GhTracker(
        Path("/p"),
        Recorder(stdout=_graphql_pull_request("CLOSED", None, "2026-09-20T23:00:00Z", [])),
    ).pull_request("https://github.com/o/r/pull/8")
    assert closed is not None and closed.merge_commit is None


def test_pull_request_reads_the_head_oid_and_maps_a_missing_one_to_none():
    with_head = GhTracker(
        Path("/p"),
        Recorder(
            stdout=_graphql_pull_request(
                "OPEN", None, "2026-09-20T23:00:00Z", [], head_oid="9f8e7d6"
            )
        ),
    ).pull_request("https://github.com/o/r/pull/7")
    assert with_head is not None and with_head.head_oid == "9f8e7d6"
    for stdout in (
        _graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", []),
        _graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", [], head_oid=None),
    ):
        without = GhTracker(Path("/p"), Recorder(stdout=stdout)).pull_request(
            "https://github.com/o/r/pull/7"
        )
        assert without is not None and without.head_oid is None


def test_pull_request_query_asks_for_the_head_oid():
    run = Recorder(stdout=_graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", []))
    GhTracker(Path("/p"), run).pull_request("https://github.com/o/r/pull/9")
    assert any("headRefOid" in part for part in run.calls[0])


def test_pull_request_query_asks_for_the_merge_commit_oid():
    run = Recorder(stdout=_graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", []))
    GhTracker(Path("/p"), run).pull_request("https://github.com/o/r/pull/9")
    assert any("mergeCommit { oid }" in part for part in run.calls[0])


def test_pull_request_query_asks_for_each_commits_parent_count():
    """GH-136: the fragment `metrics._is_human_commit` needs to tell a merge apart
    from an ordinary commit by its parent count, never by its headline."""
    run = Recorder(stdout=_graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", []))
    GhTracker(Path("/p"), run).pull_request("https://github.com/o/r/pull/9")
    assert any("parents(first: 1) { totalCount }" in part for part in run.calls[0])


def test_pull_request_reads_each_commits_own_parent_count():
    run = Recorder(
        stdout=_graphql_pull_request(
            "MERGED",
            "2026-09-21T00:29:02Z",
            "2026-09-20T23:00:00Z",
            [
                ("dev@example.com", "fix: x", 3, 1, 1),
                ("dev@example.com", "Merge master into GH-3", 9071, 36, 2),
            ],
        )
    )
    pr = GhTracker(Path("/p"), run).pull_request("https://github.com/o/r/pull/7")
    assert pr is not None
    assert [c.parent_count for c in pr.commits] == [1, 2]


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


# -- GH-137: the poller's own structured reads ---------------------------------------


def test_timestamped_comments_carries_each_bodys_own_timestamp():
    stdout = json.dumps(
        {
            "comments": [
                {"body": "Escalated: x", "createdAt": "2026-10-02T19:30:00Z"},
                {"body": "Auto-resumed: account limit", "createdAt": "2026-10-02T19:46:00Z"},
            ]
        }
    )
    tracker = GhTracker(Path("/p"), Recorder(stdout=stdout))
    comments = tracker.timestamped_comments("GH-19")
    assert [(c.body, c.created_at) for c in comments] == [
        ("Escalated: x", "2026-10-02T19:30:00Z"),
        ("Auto-resumed: account limit", "2026-10-02T19:46:00Z"),
    ]


def test_timestamped_comments_of_a_task_without_an_issue_is_empty():
    assert GhTracker(Path("/p"), Recorder()).timestamped_comments("T-abc") == []


def test_queue_is_every_open_agentic_issue_sorted_ascending():
    run = Recorder(stdout=json.dumps([{"number": 20}, {"number": 4}, {"number": 9}]))
    tracker = GhTracker(Path("/p"), run)
    assert tracker.queue() == [4, 9, 20]
    assert run.calls == [
        [
            "gh",
            "issue",
            "list",
            "--label",
            "agentic",
            "--state",
            "open",
            "--json",
            "number",
            "--limit",
            "500",
        ]
    ]


def test_issue_state_reads_blocked_by_state_reason_and_the_closing_merge_commit():
    class Run(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if args[:2] == ["gh", "issue"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps(
                        {
                            "state": "CLOSED",
                            "stateReason": "COMPLETED",
                            "blockedBy": {"nodes": [{"number": 3}], "totalCount": 1},
                            "closedByPullRequestsReferences": [
                                {"number": 7, "url": "https://github.com/o/r/pull/7"}
                            ],
                        }
                    ),
                    "",
                )
            return subprocess.CompletedProcess(args, 0, self.stdout, "")

    run = Run(stdout=_graphql_pull_request("MERGED", "2026-10-02", "2026-10-01", [], "deadbeef"))
    state = GhTracker(Path("/p"), run).issue_state(19)
    assert state.state == "CLOSED"
    assert state.state_reason == "COMPLETED"
    assert state.blocked_by == (3,)
    assert state.closing_merge_commit == "deadbeef"


def test_issue_state_of_an_open_issue_has_no_closing_merge_commit():
    run = Recorder(
        stdout=json.dumps(
            {
                "state": "OPEN",
                "stateReason": "",
                "blockedBy": {"nodes": [], "totalCount": 0},
                "closedByPullRequestsReferences": [],
            }
        )
    )
    state = GhTracker(Path("/p"), run).issue_state(20)
    assert (state.state, state.state_reason) == ("OPEN", "")
    assert state.blocked_by == ()
    assert state.closing_merge_commit is None


def test_issue_state_falls_back_when_gh_does_not_expose_blocked_by():
    """The rejected input of this predicate's own data source: an older `gh` that
    errors on the `blockedBy` JSON field must not crash the read -- `blocked_by` comes
    back `None`, the signal a caller falls back to the issue body's own line for."""

    class Run(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if "blockedBy" in args:
                raise subprocess.CalledProcessError(1, args, stderr="Unknown JSON field: blockedBy")
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "state": "OPEN",
                        "stateReason": "",
                        "closedByPullRequestsReferences": [],
                    }
                ),
                "",
            )

    state = GhTracker(Path("/p"), Run()).issue_state(20)
    assert state.blocked_by is None
