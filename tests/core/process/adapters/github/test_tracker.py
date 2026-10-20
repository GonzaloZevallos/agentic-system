"""The `gh` writer with the CLI replaced by a recorder."""

import json
import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import GhTracker
from agentic.core.process.adapters.github.tracker import issue_number
from agentic.core.process.application.ports import (
    MergeCallFailed,
    TrackerTimedOut,
    TrackerUnreachable,
)
from agentic.core.process.domain import rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
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


def test_comment_and_label_go_through_gh():
    run = Recorder()
    tracker = GhTracker(Path("/p"), run)
    tracker.comment("GH-12", "PR: x")
    tracker.label("GH-12", "needs-human")
    assert run.calls == [
        ["gh", "issue", "comment", "12", "--body", "PR: x"],
        ["gh", "issue", "edit", "12", "--add-label", "needs-human"],
    ]


def test_open_pull_request_sends_the_closes_prefixed_body_in_a_file_never_in_argv():
    written = {}

    class RecordingRun(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            path = args[args.index("--body-file") + 1]
            written["path"] = path
            written["body"] = Path(path).read_text(encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, self.stdout, "")

    run = RecordingRun()
    body = "## PM\n\n" + "😀" * 30_000  # a multibyte body well past what one argv slot takes
    url = GhTracker(Path("/p"), run).open_pull_request("GH-12", "agentic/GH-12", "Dashboard", body)
    assert url == "https://github.com/o/r/pull/7"
    [call] = run.calls
    assert call[:3] == ["gh", "pr", "create"]
    assert call[call.index("--head") + 1] == "agentic/GH-12"
    assert call[call.index("--title") + 1] == "Dashboard"
    assert "--body-file" in call and "--body" not in call
    assert written["body"] == f"Closes #12\n\n{body}"
    assert not any(body in argument for argument in call)
    # The temp file does not outlive the call.
    assert not Path(written["path"]).exists()


def test_open_pull_request_removes_its_file_when_gh_fails():
    seen = {}

    def run(args, cwd, capture_output, text, check):
        seen["path"] = args[args.index("--body-file") + 1]
        raise subprocess.CalledProcessError(1, args, stderr="Body is too long")

    with pytest.raises(ValueError, match="gh pr create failed: Body is too long"):
        GhTracker(Path("/p"), run).open_pull_request("GH-12", "b", "t", "x")
    assert not Path(seen["path"]).exists()


def test_a_task_without_an_issue_opens_its_pull_request_with_no_closes_prefix():
    seen = {}

    def run(args, cwd, capture_output, text, check):
        seen["body"] = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "https://github.com/o/r/pull/7\n", "")

    GhTracker(Path("/p"), run).open_pull_request("T-abc", "b", "t", "x")
    assert seen["body"] == "x"


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


def test_a_closed_pull_request_as_the_adapter_parses_it_is_abandoned_for_the_poller():
    tracker = GhTracker(
        Path("/p"),
        Recorder(stdout=_graphql_pull_request("CLOSED", None, "2026-09-20T23:00:00Z", [])),
    )
    closed = tracker.pull_request("https://github.com/o/r/pull/8")
    assert closed is not None
    assert rules.pr_abandoned(closed.state) is True
    merged = GhTracker(
        Path("/p"),
        Recorder(stdout=_graphql_pull_request("MERGED", "2026-09-21", "2026-09-20", [], "abc")),
    ).pull_request("https://github.com/o/r/pull/7")
    assert merged is not None
    assert rules.pr_abandoned(merged.state) is False


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


def test_issue_created_at_reads_the_issues_own_creation_time():
    run = Recorder(stdout=json.dumps({"createdAt": "2026-09-13T10:00:00Z"}))
    tracker = GhTracker(Path("/p"), run)
    assert tracker.issue_created_at("GH-19") == "2026-09-13T10:00:00Z"
    assert run.calls == [["gh", "issue", "view", "19", "--json", "createdAt"]]


def test_issue_created_at_of_a_task_without_an_issue_is_none_and_reads_nothing():
    run = Recorder()
    assert GhTracker(Path("/p"), run).issue_created_at("T-abc") is None
    assert run.calls == []


def test_issue_created_at_is_none_when_the_issue_carries_no_creation_time():
    tracker = GhTracker(Path("/p"), Recorder(stdout="{}"))
    assert tracker.issue_created_at("GH-19") is None


def test_issue_created_at_lets_a_failed_read_propagate_as_a_value_error():
    with pytest.raises(ValueError, match="gh issue view failed: no auth"):
        GhTracker(Path("/p"), Recorder(fail=True)).issue_created_at("GH-19")


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


class MethodRecorder(Recorder):
    """`gh repo view` answers which merge methods the repository allows."""

    def __init__(self, merge: bool, squash: bool, rebase: bool):
        super().__init__()
        self.allowed = {
            "mergeCommitAllowed": merge,
            "squashMergeAllowed": squash,
            "rebaseMergeAllowed": rebase,
        }

    def __call__(self, args, cwd, capture_output, text, check):
        result = super().__call__(args, cwd, capture_output, text, check)
        if args[1:3] == ["repo", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(self.allowed), "")
        return result


PR_URL = "https://github.com/o/r/pull/7"


@pytest.mark.parametrize(
    ("allowed", "method"),
    [
        ((True, True, True), "merge"),
        ((False, True, True), "squash"),
        ((False, False, True), "rebase"),
    ],
)
def test_merge_picks_the_first_allowed_method_and_pins_the_head(allowed, method):
    run = MethodRecorder(*allowed)
    assert GhTracker(Path("/p"), run).merge(PR_URL, "abc123") == method
    assert run.calls[-1] == [
        "gh",
        "pr",
        "merge",
        "7",
        f"--{method}",
        "--match-head-commit",
        "abc123",
    ]


def test_merge_targets_the_number_in_the_reference_and_never_the_url_or_another_number():
    run = MethodRecorder(True, True, True)
    GhTracker(Path("/p"), run).merge("https://github.com/o/r/pull/210", "abc123")
    assert run.calls[-1] == ["gh", "pr", "merge", "210", "--merge", "--match-head-commit", "abc123"]
    assert all("209" not in part and "github.com" not in part for part in run.calls[-1])


@pytest.mark.parametrize("reference", ["not-a-url", "", "209", "https://github.com/o/r/issues/209"])
def test_merge_with_an_unparsable_reference_raises_gh_unreadable_before_any_gh_call(reference):
    run = MethodRecorder(True, True, True)
    with pytest.raises(MergeCallFailed) as raised:
        GhTracker(Path("/p"), run).merge(reference, "abc123")
    assert raised.value.failure == "gh_unreadable"
    assert type(raised.value) is MergeCallFailed
    assert run.calls == []  # not even `gh repo view`


def test_merge_with_no_allowed_method_raises_and_merges_nothing():
    run = MethodRecorder(False, False, False)
    with pytest.raises(ValueError, match="no_merge_method"):
        GhTracker(Path("/p"), run).merge(PR_URL, "abc123")
    assert all(call[1:3] != ["pr", "merge"] for call in run.calls)


def test_merge_never_asks_for_auto_or_branch_deletion():
    run = MethodRecorder(True, True, True)
    GhTracker(Path("/p"), run).merge(PR_URL, "abc123")
    assert "--auto" not in run.calls[-1] and "--delete-branch" not in run.calls[-1]


class _Step(MethodRecorder):
    """One `gh` step fails, or answers with something that is not what it should be."""

    def __init__(self, fail_at=None, error=None, repo_view_stdout=None, allowed=(True, True, True)):
        super().__init__(*allowed)
        self.fail_at = fail_at
        self.error = error
        self.repo_view_stdout = repo_view_stdout

    def __call__(self, args, cwd, capture_output, text, check):
        if self.fail_at and tuple(args[1:3]) == self.fail_at:
            self.calls.append(args)
            raise self.error
        if args[1:3] == ["repo", "view"] and self.repo_view_stdout is not None:
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, self.repo_view_stdout, "")
        return super().__call__(args, cwd, capture_output, text, check)


_REJECTED = subprocess.CalledProcessError(1, "gh", stderr="Pull request is not mergeable: secret")


def ids_without_cause(run):
    """The two rejections that are not a `gh` failure: nothing was raised to chain."""
    return run.repo_view_stdout == "[]" or run.allowed["mergeCommitAllowed"] is False


@pytest.mark.parametrize(
    ("run", "failure"),
    [
        (_Step(("repo", "view"), FileNotFoundError("gh")), "gh_unavailable"),
        (_Step(("repo", "view"), _REJECTED), "gh_unreadable"),
        (_Step(repo_view_stdout="not json"), "gh_unreadable"),
        (_Step(repo_view_stdout="[]"), "gh_unreadable"),
        (_Step(allowed=(False, False, False)), "no_merge_method"),
        (_Step(("pr", "merge"), _REJECTED), "gh_rejected"),
        (_Step(("pr", "merge"), FileNotFoundError("gh")), "gh_unavailable"),
    ],
    ids=[
        "repo_view_gh_missing",
        "repo_view_error",
        "bad_json",
        "not_an_object",
        "no_method",
        "pr_merge_rejected",
        "pr_merge_gh_missing",
    ],
)
def test_merge_raises_only_merge_call_failed_with_the_class_of_the_step_that_failed(run, failure):
    with pytest.raises(MergeCallFailed) as raised:
        GhTracker(Path("/p"), run).merge(PR_URL, "abc123")
    assert raised.value.failure == failure
    assert type(raised.value) is MergeCallFailed
    assert isinstance(raised.value, ValueError)
    assert raised.value.__cause__ is not None or ids_without_cause(run)


def test_merge_call_failed_carries_the_raw_error_as_its_message_never_as_its_class():
    run = _Step(("pr", "merge"), _REJECTED)
    with pytest.raises(MergeCallFailed, match="not mergeable") as raised:
        GhTracker(Path("/p"), run).merge(PR_URL, "abc123")
    assert raised.value.failure == "gh_rejected"
    assert str(raised.value) == "gh pr merge failed: Pull request is not mergeable: secret"


# -- pull_requests_on ---------------------------------------------------------------------


def _rows(*states, start=1):
    return json.dumps(
        [
            {"number": start + i, "state": state, "url": f"https://github.com/o/r/pull/{start + i}"}
            for i, state in enumerate(states)
        ]
    )


def test_pull_requests_on_reads_the_three_states_from_one_gh_call():
    run = Recorder(stdout=_rows("OPEN", "CLOSED", "MERGED"))
    found = GhTracker(Path("/p"), run).pull_requests_on("agentic/GH-59")
    assert [(pr.number, pr.state) for pr in found] == [(1, "OPEN"), (2, "CLOSED"), (3, "MERGED")]
    assert found[0].url == "https://github.com/o/r/pull/1"
    assert run.calls == [
        [
            "gh",
            "pr",
            "list",
            "--head",
            "agentic/GH-59",
            "--state",
            "all",
            "--json",
            "number,state,url",
            "--limit",
            "50",
        ]
    ]


def test_pull_requests_on_a_branch_without_any_is_an_empty_list():
    assert GhTracker(Path("/p"), Recorder(stdout="[]\n")).pull_requests_on("b") == []


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        '{"number": 1}',
        '[{"number": 1, "state": "OPEN"}]',
        '[{"number": "1", "state": "OPEN", "url": "u"}]',
        '[{"number": 1, "state": "DRAFT", "url": "u"}]',
        '[{"number": 1, "state": "open", "url": "u"}]',
        '["x"]',
        _rows(*["CLOSED"] * 50),
        '[{"number": true, "state": "OPEN", "url": "u"}]',
        '[{"number": 1, "state": "OPEN", "url": 7}]',
        '[{"number": 1, "url": "u"}]',
    ],
    ids=[
        "not-json",
        "not-a-list",
        "missing-key",
        "number-not-int",
        "unknown-state",
        "lowercase-state",
        "row-not-object",
        "exactly-the-limit",
        "number-is-bool",
        "url-not-string",
        "state-missing",
    ],
)
def test_pull_requests_on_raises_on_anything_unreadable_never_an_empty_list(stdout):
    with pytest.raises(ValueError, match="gh pr list on b"):
        GhTracker(Path("/p"), Recorder(stdout=stdout)).pull_requests_on("b")


def test_pull_requests_on_raises_when_gh_fails():
    with pytest.raises(ValueError, match="gh pr list failed: no auth"):
        GhTracker(Path("/p"), Recorder(fail=True)).pull_requests_on("b")


def test_pull_requests_on_just_under_the_limit_is_returned_whole():
    assert (
        len(GhTracker(Path("/p"), Recorder(stdout=_rows(*["OPEN"] * 49))).pull_requests_on("b"))
        == 49
    )


# -- GH-216: the CI of a pull request and of a branch, read in one GraphQL query ---------


def _graphql(nodes, *, oid="a" * 40, rollup=True, more=False, created="2026-10-07T10:00:00Z"):
    contexts = {"nodes": nodes, "pageInfo": {"hasNextPage": more}}
    commit = {"oid": oid, "statusCheckRollup": {"contexts": contexts} if rollup else None}
    pull_request = {
        "createdAt": created,
        "baseRefName": "master",
        "headRefOid": oid,
        "commits": {"nodes": [{"commit": commit}]},
    }
    return json.dumps({"data": {"repository": {"pullRequest": pull_request}}})


def _branch_graphql(nodes, *, oid="b" * 40):
    contexts = {"nodes": nodes, "pageInfo": {"hasNextPage": False}}
    target = {"oid": oid, "statusCheckRollup": {"contexts": {**contexts}}}
    return json.dumps({"data": {"repository": {"ref": {"target": target}}}})


def _run_node(name, status, conclusion, url="https://github.com/o/r/actions/runs/1/job/2"):
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "detailsUrl": url,
    }


PR = "https://github.com/o/r/pull/7"


def test_ci_report_reads_the_head_and_its_checks_from_one_query_in_the_product_checkout():
    nodes = [
        _run_node("check", "COMPLETED", "SUCCESS"),
        _run_node("e2e", "IN_PROGRESS", None, url=None),
        {
            "__typename": "StatusContext",
            "context": "ci/legacy",
            "state": "FAILURE",
            "targetUrl": "u",
        },
        {
            "__typename": "StatusContext",
            "context": "ci/wait",
            "state": "PENDING",
            "targetUrl": None,
        },
    ]
    run = Recorder(stdout=_graphql(nodes, oid="c" * 40))
    report = GhTracker(Path("/p"), run).ci_report(PR)
    assert report == CiReport(
        head_sha="c" * 40,
        base_ref="master",
        base_sha=None,
        opened_at="2026-10-07T10:00:00Z",
        checks=(
            CiCheck("check", "COMPLETED", "SUCCESS", "https://github.com/o/r/actions/runs/1/job/2"),
            CiCheck("e2e", "IN_PROGRESS", None, ""),
            CiCheck("ci/legacy", "COMPLETED", "FAILURE", "u"),
            CiCheck("ci/wait", "PENDING", "PENDING", ""),
        ),
    )
    [call] = run.calls
    assert call[:3] == ["gh", "api", "graphql"] and "number=7" in call
    assert any("statusCheckRollup" in arg for arg in call)


def test_ci_report_keeps_a_red_run_as_the_raw_enums_it_was_reported_with():
    run = Recorder(stdout=_graphql([_run_node("web", "COMPLETED", "TIMED_OUT")]))
    report = GhTracker(Path("/p"), run).ci_report(PR)
    assert isinstance(report, CiReport)
    assert (report.checks[0].status, report.checks[0].conclusion) == ("COMPLETED", "TIMED_OUT")


def test_ci_report_of_a_head_with_no_checks_at_all_is_a_readable_empty_report():
    report = GhTracker(Path("/p"), Recorder(stdout=_graphql([], rollup=False))).ci_report(PR)
    assert isinstance(report, CiReport) and report.checks == ()


class Missing(Recorder):
    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        raise FileNotFoundError("gh")


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_a_missing_gh_is_unreadable_as_unavailable_never_raised(read):
    answer = getattr(GhTracker(Path("/p"), Missing()), read)(PR if read == "ci_report" else "x")
    assert answer == CiUnreadable("gh_unavailable")


class NotExecutable(Recorder):
    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        raise PermissionError("gh")


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_a_gh_that_cannot_be_run_is_unreadable_as_unavailable_never_raised(read):
    answer = getattr(GhTracker(Path("/p"), NotExecutable()), read)(
        PR if read == "ci_report" else "x"
    )
    assert answer == CiUnreadable("gh_unavailable")


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_a_failing_gh_or_unparsable_json_is_unreadable_as_unreadable_never_raised(read):
    ref = PR if read == "ci_report" else "master"
    assert getattr(GhTracker(Path("/p"), Recorder(fail=True)), read)(ref) == CiUnreadable(
        "gh_unreadable"
    )
    assert getattr(GhTracker(Path("/p"), Recorder(stdout="not json")), read)(ref) == CiUnreadable(
        "gh_unreadable"
    )


@pytest.mark.parametrize(
    "stdout",
    [
        json.dumps({"data": {"repository": {"pullRequest": None}}}),
        json.dumps({"data": None}),
        json.dumps([]),
        json.dumps({"data": {"repository": {"pullRequest": {"createdAt": "t"}}}}),
        _graphql([{"__typename": "CheckRun", "status": "COMPLETED"}]),
        _graphql([_run_node("check", "COMPLETED", "SUCCESS")], more=True),
        _graphql([_run_node("check", "COMPLETED", "SUCCESS")], oid=""),
    ],
    ids=["no-pr", "null-data", "list", "missing-field", "run-without-name", "truncated", "no-oid"],
)
def test_ci_report_never_reads_what_it_cannot_whole_as_a_report(stdout):
    assert GhTracker(Path("/p"), Recorder(stdout=stdout)).ci_report(PR) == CiUnreadable(
        "gh_unreadable"
    )


def test_ci_report_with_a_bad_reference_reaches_no_gh_call():
    run = Recorder(stdout=_graphql([]))
    assert GhTracker(Path("/p"), run).ci_report("not-a-url") == CiUnreadable("gh_unreadable")
    assert run.calls == []


def test_ci_report_never_asks_where_the_base_was_because_actions_do_not_say():
    """The base_sha gate (GH-216) failed: `pull_requests` is `[]` on every run of this
    repo's CI, so no `actions/runs` read is made and `base_sha` stays None."""
    run = Recorder(stdout=_graphql([_run_node("check", "COMPLETED", "SUCCESS")]))
    GhTracker(Path("/p"), run).ci_report(PR)
    assert len(run.calls) == 1


def test_branch_ci_reads_the_latest_commit_of_a_branch_with_no_pull_request_facts():
    run = Recorder(stdout=_branch_graphql([_run_node("check", "COMPLETED", "FAILURE")]))
    report = GhTracker(Path("/p"), run).branch_ci("master")
    assert report == CiReport(
        head_sha="b" * 40,
        base_ref=None,
        base_sha=None,
        opened_at=None,
        checks=(
            CiCheck("check", "COMPLETED", "FAILURE", "https://github.com/o/r/actions/runs/1/job/2"),
        ),
    )
    [call] = run.calls
    assert "ref=refs/heads/master" in call


def test_branch_ci_of_a_branch_that_does_not_exist_is_unreadable():
    stdout = json.dumps({"data": {"repository": {"ref": None}}})
    assert GhTracker(Path("/p"), Recorder(stdout=stdout)).branch_ci("gone") == CiUnreadable(
        "gh_unreadable"
    )


# -- the landing's verbs (GH-239) ----------------------------------------------------


class Scripted(Recorder):
    """Answers each `gh` call from `answers` by its second word (`list`, `rerun`...)."""

    def __init__(self, answers):
        super().__init__()
        self.answers = answers

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, self.answers.get(args[2], ""), "")


def test_rerun_failed_ci_reruns_only_the_failed_runs_of_the_head_and_returns_their_links():
    runs = [
        {"url": "https://github.com/o/r/actions/runs/11", "conclusion": "failure"},
        {"url": "https://github.com/o/r/actions/runs/12", "conclusion": "success"},
        {"url": "https://github.com/o/r/actions/runs/13", "conclusion": "timed_out"},
    ]
    run = Scripted({"list": json.dumps(runs)})
    links = GhTracker(Path("/p"), run).rerun_failed_ci("https://github.com/o/r/pull/7", "abc")
    assert links == [
        "https://github.com/o/r/actions/runs/11",
        "https://github.com/o/r/actions/runs/13",
    ]
    assert run.calls[0][:5] == ["gh", "run", "list", "--commit", "abc"]
    assert run.calls[1:] == [
        ["gh", "run", "rerun", "11", "--failed"],
        ["gh", "run", "rerun", "13", "--failed"],
    ]


def test_rerun_failed_ci_rejects_a_reference_that_is_not_a_pull_request_before_any_gh_call():
    run = Recorder()
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), run).rerun_failed_ci("https://github.com/o/r/issues/7", "abc")
    assert run.calls == []


@pytest.mark.parametrize("stdout", ["not json", '{"url": "x"}'])
def test_rerun_failed_ci_rejects_a_run_list_it_cannot_read_and_reruns_nothing(stdout):
    run = Scripted({"list": stdout})
    with pytest.raises(ValueError, match="gh run list"):
        GhTracker(Path("/p"), run).rerun_failed_ci("https://github.com/o/r/pull/7", "abc")
    assert [call[2] for call in run.calls] == ["list"]


def test_rerun_failed_ci_with_nothing_failed_reruns_nothing():
    run = Scripted({"list": json.dumps([{"url": "u/1", "conclusion": "success"}])})
    assert GhTracker(Path("/p"), run).rerun_failed_ci("https://github.com/o/r/pull/7", "a") == []
    assert len(run.calls) == 1


def test_close_pull_request_closes_by_number():
    run = Recorder()
    GhTracker(Path("/p"), run).close_pull_request("https://github.com/o/r/pull/7")
    assert run.calls == [["gh", "pr", "close", "7"]]


def test_close_pull_request_rejects_a_non_pull_request_reference_before_any_gh_call():
    run = Recorder()
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), run).close_pull_request("https://github.com/o/r/issues/7")
    assert run.calls == []


def test_open_issue_sends_the_label_only_when_one_is_given():
    run = Recorder()
    GhTracker(Path("/p"), run).open_issue("t", "b", label="agentic")
    GhTracker(Path("/p"), run).open_issue("t", "b", label=None)
    labelled, plain = run.calls
    assert labelled[labelled.index("--label") + 1] == "agentic"
    assert "--label" not in plain


# -- a stalled gh call (GH-266) ------------------------------------------------------------


def _stalls_at(*step, then=None):
    """A `run` whose call whose argv starts with `step` runs out of time (every call is
    kept in `.calls`); any other call answers `then` or an empty JSON object."""

    class Run:
        def __init__(self):
            self.calls = []

        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if tuple(args[1 : 1 + len(step)]) == step:
                raise subprocess.TimeoutExpired(args, 60, output="issue body", stderr="secret")
            if tuple(args[1:3]) == ("repo", "view"):
                allowed = {"mergeCommitAllowed": True}
                return subprocess.CompletedProcess(args, 0, json.dumps(allowed), "")
            return subprocess.CompletedProcess(args, 0, then or "{}", "")

    return Run()


def test_every_gh_call_of_the_tracker_that_stalls_raises_tracker_timed_out():
    run = _stalls_at("pr", "list")
    lines: list[str] = []
    with pytest.raises(TrackerTimedOut) as raised:
        GhTracker(Path("/p"), run, log=lines.append).list_pull_requests()
    assert str(raised.value) == "gh pr list timed out after 60 s"
    assert lines == [
        "agentic: gh pr list timed out after 60 s; this read fails and the next round retries"
    ]
    assert "secret" not in str(raised.value) + lines[0]


def test_issue_state_after_a_timeout_makes_no_second_call_and_never_reads_as_an_old_gh():
    run = _stalls_at("issue", "view")
    with pytest.raises(TrackerTimedOut):
        GhTracker(Path("/p"), run, log=lambda line: None).issue_state(229)
    assert len(run.calls) == 1  # the "older gh" fallback would have been a second call


def test_issue_state_still_falls_back_on_a_plain_failure_not_a_timeout():
    # The rejected input of the narrowed clause: a non-timeout `ValueError` keeps its path.
    class Run(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if "blockedBy" in args[-1]:
                raise subprocess.CalledProcessError(1, args, stderr="Unknown JSON field")
            return subprocess.CompletedProcess(args, 0, json.dumps({"state": "OPEN"}), "")

    run = Run()
    assert GhTracker(Path("/p"), run).issue_state(1).blocked_by is None
    assert len(run.calls) == 2


def test_merge_pr_merge_timeout_is_gh_unreadable_never_gh_rejected():
    run = _stalls_at("pr", "merge")
    with pytest.raises(MergeCallFailed) as raised:
        GhTracker(Path("/p"), run, log=lambda line: None).merge(PR_URL, "abc123")
    assert raised.value.failure == "gh_unreadable"
    assert type(raised.value) is MergeCallFailed
    assert isinstance(raised.value.__cause__, TrackerTimedOut)
    assert str(raised.value) == "gh pr merge timed out after 60 s"


def test_merge_repo_view_timeout_is_gh_unreadable_and_merges_nothing():
    run = _stalls_at("repo", "view")
    with pytest.raises(MergeCallFailed) as raised:
        GhTracker(Path("/p"), run, log=lambda line: None).merge(PR_URL, "abc123")
    assert raised.value.failure == "gh_unreadable"
    assert [call[1:3] for call in run.calls] == [["repo", "view"]]


def test_ci_report_timeout_is_ci_unreadable_gh_unreadable():
    run = _stalls_at("api", "graphql")
    report = GhTracker(Path("/p"), run, log=lambda line: None).ci_report(
        "https://github.com/o/r/pull/7"
    )
    assert report == CiUnreadable("gh_unreadable")


# -- gh cannot reach GitHub (GH-290) -------------------------------------------------------

# Captured from the pinned gh 2.100.0 on exit 1 (`GH_HOST=nonexistent.invalid gh api /`).
_UNREACHABLE = (
    "error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com"
)


def _unreachable_at(*step):
    """Like `_stalls_at`, but the matching call exits 1 with the incident's stderr; every
    other call answers well, so a fallback call would succeed if it were made."""

    class Run:
        def __init__(self):
            self.calls = []

        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if tuple(args[1 : 1 + len(step)]) == step:
                raise subprocess.CalledProcessError(1, args, stderr=_UNREACHABLE)
            if tuple(args[1:3]) == ("repo", "view"):
                allowed = {"mergeCommitAllowed": True}
                return subprocess.CompletedProcess(args, 0, json.dumps(allowed), "")
            return subprocess.CompletedProcess(args, 0, json.dumps({"state": "OPEN"}), "")

    return Run()


@pytest.mark.parametrize("failure", ["timed_out", "unreachable"])
def test_issue_state_after_a_transport_failure_never_falls_back_to_an_old_gh(failure):
    run = (
        _stalls_at("issue", "view") if failure == "timed_out" else _unreachable_at("issue", "view")
    )
    lines: list[str] = []
    with pytest.raises((TrackerTimedOut, TrackerUnreachable)) as raised:
        GhTracker(Path("/p"), run, log=lines.append).issue_state(229)
    assert type(raised.value) is (TrackerTimedOut if failure == "timed_out" else TrackerUnreachable)
    assert len(run.calls) == 1  # the fallback would have answered: it is never made
    assert len(lines) == 1


def test_merge_repo_view_unreachable_propagates_unwrapped_and_merges_nothing():
    run = _unreachable_at("repo", "view")
    with pytest.raises(TrackerUnreachable) as raised:
        GhTracker(Path("/p"), run, log=lambda line: None).merge(PR_URL, "abc123")
    assert not isinstance(raised.value, MergeCallFailed)
    assert [call[1:3] for call in run.calls] == [["repo", "view"]]


def test_merge_pr_merge_unreachable_is_gh_unreadable_never_gh_rejected():
    run = _unreachable_at("pr", "merge")
    with pytest.raises(MergeCallFailed) as raised:
        GhTracker(Path("/p"), run, log=lambda line: None).merge(PR_URL, "abc123")
    assert raised.value.failure == "gh_unreadable"
    assert type(raised.value) is MergeCallFailed
    assert isinstance(raised.value.__cause__, TrackerUnreachable)
