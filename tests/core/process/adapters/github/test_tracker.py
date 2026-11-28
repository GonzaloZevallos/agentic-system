"""The `gh` writer with the CLI replaced by a recorder."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import GhTracker
from agentic.core.process.adapters.github import tracker as tracker_module
from agentic.core.process.adapters.github.run import GhRetry
from agentic.core.process.adapters.github.tracker import issue_number
from agentic.core.process.application.ports import (
    HeldListMayBeCut,
    MergeCallFailed,
    TrackerCallFailed,
    TrackerRateLimited,
    TrackerRefused,
    TrackerTimedOut,
    TrackerTransient,
    TrackerTurnRefused,
    TrackerUnreachable,
)
from agentic.core.process.domain import rules
from agentic.core.process.domain.ci import (
    CiCheck,
    CiReport,
    CiUnreadable,
    DispatchedRun,
    current_runs,
)
from agentic.core.process.domain.github_access import GithubAccess, Turn
from agentic.core.process.domain.hold import HELD_LIMIT, HOLD_LABEL
from agentic.core.process.domain.presubmit import ApiBreakLine
from agentic.core.process.domain.task import PullRequest, PullRequestCommit
from tests.core.process.adapters.github.gh_fake import (
    RecordingRun,
    ScriptedRun,
    gh_fixture,
    gh_response,
)


class Recorder:
    def __init__(self, stdout="https://github.com/o/r/pull/7\n", fail=False, fail_output=None):
        self.calls = []
        self.cwds = []
        self.stdout = stdout
        self.fail = fail
        self.fail_output = fail_output

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        self.cwds.append(cwd)
        if self.fail:
            raise subprocess.CalledProcessError(1, args, output=self.fail_output, stderr="no auth")
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def test_issue_number_is_read_off_the_task_id():
    assert issue_number("GH-12") == 12 and issue_number("T-abc") is None


def _plain(stdout: str = "") -> RecordingRun:
    """What `gh` prints for a call with no `-i`: the text alone."""
    return RecordingRun(stdout, "", 0)


def _http(status: int, body: str = "{}") -> str:
    """The stdout of a `gh api -i` call that answered `status` with `body`."""
    return f"HTTP/2.0 {status}\r\ncontent-type: application/json\r\n\r\n{body}"


def _option(argv: list[str], flag: str) -> list[str]:
    """The value after each `flag` in `argv`."""
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == flag]


def test_comment_and_label_go_through_gh():
    run = ScriptedRun(gh_fixture("comment_201.json"), _plain())
    tracker = GhTracker(Path("/p"), run)
    tracker.comment("GH-12", "PR: x")
    tracker.label("GH-12", "needs-human")
    [comment, label] = run.argv
    assert comment[:7] == [
        "gh",
        "api",
        "-i",
        "-X",
        "POST",
        "repos/{owner}/{repo}/issues/12/comments",
        "-F",
    ]
    assert comment[7].startswith("body=@") and len(comment) == 8
    assert run.bodies[0] == "PR: x"
    assert label == ["gh", "issue", "edit", "12", "--add-label", "needs-human"]


def test_open_pull_request_reads_the_base_then_posts_the_closes_prefixed_body_from_a_file():
    run = ScriptedRun(_plain("main\n"), gh_fixture("pulls_201.json"))
    body = "## PM\n\n" + "😀" * 30_000  # a multibyte body well past what one argv slot takes
    url = GhTracker(Path("/p"), run).open_pull_request("GH-12", "agentic/GH-12", "Dashboard", body)
    assert url == "https://github.com/o/r/pull/7"
    [read, post] = run.argv
    assert read == ["gh", "api", "repos/{owner}/{repo}", "--jq", ".default_branch"]
    assert post[:7] == ["gh", "api", "-i", "-X", "POST", "repos/{owner}/{repo}/pulls", "-f"]
    assert _option(post, "-f") == ["title=Dashboard", "head=agentic/GH-12", "base=main"]
    [body_option] = _option(post, "-F")
    assert body_option.startswith("body=@")
    assert run.bodies == [None, f"Closes #12\n\n{body}"]
    assert not any(body in argument for call in run.argv for argument in call)
    # The temp file does not outlive the call.
    assert not Path(body_option.removeprefix("body=@")).exists()


@pytest.mark.parametrize("title", ["@/etc/passwd", "true", "123", "@", "a=b", "-F x=@y"])
def test_open_pull_request_sends_every_text_as_a_raw_field_after_f_verbatim(title):
    run = ScriptedRun(_plain("main"), gh_fixture("pulls_201.json"))
    GhTracker(Path("/p"), run).open_pull_request("GH-12", "b", title, "x")
    post = run.argv[1]
    assert f"title={title}" in _option(post, "-f")
    assert [arg for arg in _option(post, "-F") if not arg.startswith("body=@")] == []
    assert not any(arg.startswith(("-F", "--field", "--input")) for arg in post if arg != "-F")


def test_open_pull_request_removes_its_file_and_fails_once_on_a_duplicate_head():
    run = ScriptedRun(_plain("main"), gh_fixture("pulls_422_duplicate_head.json"))
    with pytest.raises(ValueError) as raised:
        GhTracker(Path("/p"), run).open_pull_request("GH-12", "agentic/GH-12", "t", "x")
    message = str(raised.value)
    assert message.startswith("gh pr create failed: gh: Validation Failed (HTTP 422)")
    assert "A pull request already exists" in message and "(head agentic/GH-12)" in message
    assert type(raised.value) is ValueError
    assert len(run.argv) == 2  # the base read, then one POST: no lookup, no second try
    [body_option] = _option(run.argv[1], "-F")
    assert not Path(body_option.removeprefix("body=@")).exists()


def test_open_pull_request_does_not_read_stdout_for_its_failure_message():
    failing = gh_response(
        422, [], '{"message":"STDOUT-BODY"}', stderr="gh: Validation Failed (HTTP 422)"
    )
    with pytest.raises(ValueError) as raised:
        GhTracker(Path("/p"), ScriptedRun(_plain("main"), failing)).open_pull_request(
            "GH-12", "b", "t", "x"
        )
    assert "STDOUT-BODY" not in str(raised.value)


def test_a_403_with_retry_after_on_pr_create_is_one_post_under_a_policy():
    run = ScriptedRun(_plain("main"), gh_fixture("rest_secondary_limit_403.json"))
    policy, slept = _policy()
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy, access=GithubAccess())
    with pytest.raises(ValueError, match=r"^gh pr create failed: .*\(head b\)"):
        tracker.open_pull_request("GH-12", "b", "t", "x")
    assert len(run.argv) == 2 and slept == []


def test_a_primary_limit_on_pr_create_is_one_post_and_no_wait():
    run = ScriptedRun(_plain("main"), gh_fixture("rest_primary_limit_403.json"))
    policy, slept = _policy()
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy, access=GithubAccess())
    with pytest.raises(ValueError, match=r"^gh pr create failed: .*\(head b\)"):
        tracker.open_pull_request("GH-12", "b", "t", "x")
    assert len(run.argv) == 2 and slept == []


@pytest.mark.parametrize(
    "base_read",
    [
        pytest.param(gh_response(404, [], "{}", stderr="gh: Not Found (HTTP 404)"), id="refused"),
        pytest.param(_plain(""), id="empty"),
        pytest.param(_plain("\n"), id="blank"),
    ],
)
def test_a_failed_default_branch_read_never_reaches_the_post(base_read):
    run = ScriptedRun(base_read, gh_fixture("pulls_201.json"))
    with pytest.raises(ValueError) as raised:
        GhTracker(Path("/p"), run).open_pull_request("GH-12", "agentic/GH-12", "t", "x")
    assert str(raised.value).startswith(
        "gh pr create failed: could not read the default branch (head agentic/GH-12)"
    )
    assert len(run.argv) == 1 and run.argv[0][:3] == ["gh", "api", "repos/{owner}/{repo}"]


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(_plain("https://github.com/o/r/pull/7"), id="unreadable 201"),
        pytest.param(gh_response(201, [], "not json"), id="201 with no JSON"),
        pytest.param(gh_response(201, [], "{}"), id="201 without html_url"),
        pytest.param(gh_response(201, [], '{"html_url": 7}'), id="201 with a non-string url"),
        pytest.param(gh_response(201, [], "[]"), id="201 with a list"),
        pytest.param(gh_response(200, [], '{"html_url": "u"}'), id="200 where 201 is expected"),
        pytest.param(gh_response(202, [], '{"html_url": "u"}'), id="202 where 201 is expected"),
    ],
)
def test_a_pull_request_answer_that_is_not_the_201_with_a_url_may_have_applied_and_is_not_retried(
    answer,
):
    run = ScriptedRun(_plain("main"), answer)
    policy, slept = _policy()
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy)
    with pytest.raises(ValueError, match=r"^gh pr create failed: .*may have applied.*\(head b\)$"):
        tracker.open_pull_request("GH-12", "b", "t", "x")
    assert len(run.argv) == 2 and slept == []


class _Raises:
    """A `Run` that plays `steps` in order: an exception is raised, a `RecordingRun` answers."""

    def __init__(self, *steps):
        self.steps, self.calls = list(steps), 0

    def __call__(self, args, **kwargs):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step(args, **kwargs)


@pytest.mark.parametrize(
    ("run", "access", "cls", "phrase"),
    [
        pytest.param(
            lambda: _Raises(*[RecordingRun("", _BAD_GATEWAY, 1)] * 3),
            None,
            TrackerTransient,
            "met a transient GitHub error on each of 3 attempts",
            id="base read keeps failing with a 502",
        ),
        pytest.param(
            lambda: _Raises(*[subprocess.TimeoutExpired(["gh"], 60)] * 3),
            None,
            TrackerTimedOut,
            "timed out on each of 3 attempts",
            id="base read keeps timing out",
        ),
        pytest.param(
            lambda: _Raises(_plain("main"), subprocess.TimeoutExpired(["gh"], 60)),
            None,
            TrackerTimedOut,
            "timed out after 60 s",
            id="POST times out",
        ),
        pytest.param(
            lambda: _Raises(
                _plain("main"), RecordingRun("", "error connecting to api.github.com", 1)
            ),
            None,
            TrackerUnreachable,
            "could not reach GitHub",
            id="POST cannot reach GitHub",
        ),
        pytest.param(
            lambda: _Raises(_plain("main")),
            lambda: GithubAccess(gate=RefusingGate()),
            TrackerTurnRefused,
            "write turn refused",
            id="POST turn refused",
        ),
        pytest.param(
            lambda: _Raises(FileNotFoundError("gh")),
            None,
            ValueError,
            "gh is not installed",
            id="no gh",
        ),
    ],
)
def test_every_pull_request_failure_starts_gh_pr_create_failed_and_names_the_head(
    run, access, cls, phrase
):
    policy, _ = _policy()
    tracker = GhTracker(
        Path("/p"), run(), lambda line: None, retry=policy, access=access() if access else None
    )
    with pytest.raises(cls) as raised:
        tracker.open_pull_request("GH-12", "agentic/GH-12", "t", "x")
    message = str(raised.value)
    assert message.startswith("gh pr create failed: ") and phrase in message
    assert message.endswith("(head agentic/GH-12)")
    assert type(raised.value) is cls


def test_a_rate_limited_pull_request_keeps_its_reset_and_status_under_the_prefix():
    policy = GhRetry(max_wait_s=60, sleep=lambda seconds: None, now=lambda: 1_000.0)
    # The base read is retried under the policy, and its reset is over the cap.
    run = ScriptedRun(gh_fixture("rest_primary_limit_403.json"), _rate_limit_reads(9_000))
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy)
    with pytest.raises(TrackerRateLimited) as raised:
        tracker.open_pull_request("GH-12", "b", "t", "x")
    assert str(raised.value).startswith("gh pr create failed: met the GitHub rate limit")
    assert "(head b)" in str(raised.value)
    assert raised.value.status == 403 and raised.value.resets_at is not None


def test_open_pull_request_removes_its_file_when_gh_fails():
    seen = {}

    def run(args, cwd, capture_output, text, check):
        if "-F" in args:
            seen["path"] = args[args.index("-F") + 1].removeprefix("body=@")
            raise subprocess.CalledProcessError(1, args, stderr="Body is too long")
        return subprocess.CompletedProcess(args, 0, "main\n", "")

    with pytest.raises(ValueError, match=r"gh pr create failed: Body is too long \(head b\)"):
        GhTracker(Path("/p"), run).open_pull_request("GH-12", "b", "t", "x")
    assert not Path(seen["path"]).exists()


def test_a_task_without_an_issue_opens_its_pull_request_with_no_closes_prefix():
    run = ScriptedRun(_plain("main"), gh_fixture("pulls_201.json"))
    GhTracker(Path("/p"), run).open_pull_request("T-abc", "b", "t", "x")
    assert run.bodies[1] == "x"


def _body_written_by(**options) -> str | None:
    run = ScriptedRun(_plain("main"), gh_fixture("pulls_201.json"))
    GhTracker(Path("/p"), run).open_pull_request("GH-12", "b", "t", "x", **options)
    return run.bodies[1]


def test_a_pull_request_opened_without_closes_issue_has_no_closes_line():
    assert _body_written_by(closes_issue=False) == "x"


def test_a_pull_request_closes_its_issue_by_default_and_when_asked():
    assert _body_written_by() == "Closes #12\n\nx"
    assert _body_written_by(closes_issue=True) == "Closes #12\n\nx"


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


def test_comment_on_issue_posts_the_text_from_a_file_to_the_referenced_issue():
    run = ScriptedRun(gh_fixture("comment_201.json"))
    text = "flake `tests` seen 😀\n" + "x" * 10_000
    GhTracker(Path("/p"), run).comment_on_issue("https://github.com/other/repo/issues/9", text)
    [call] = run.argv
    assert call[:6] == ["gh", "api", "-i", "-X", "POST", "repos/other/repo/issues/9/comments"]
    [body_option] = _option(call, "-F")
    assert _option(call, "-f") == []
    assert run.bodies == [text]
    assert not any(text in argument for argument in call)
    # The temp file does not outlive the call.
    assert not Path(body_option.removeprefix("body=@")).exists()


def test_comment_on_issue_accepts_a_bare_issue_number_reference():
    run = ScriptedRun(gh_fixture("comment_201.json"))
    GhTracker(Path("/p"), run).comment_on_issue("42", "x")
    [call] = run.argv
    assert call[5] == "repos/{owner}/{repo}/issues/42/comments"


@pytest.mark.parametrize(
    "reference",
    [
        "https://github.com/o/r/pull/9",
        "#9",
        "https://github.com.evil/o/r/issues/9",
        "https://github.com/o/../issues/9",
        "https://github.com/o/r/issues/9?x=1",
        "09",
    ],
)
def test_a_reference_issue_target_does_not_parse_keeps_the_subcommand(reference):
    run = ScriptedRun(_plain())
    GhTracker(Path("/p"), run).comment_on_issue(reference, "x")
    [call] = run.argv
    assert call[:4] == ["gh", "issue", "comment", reference]
    assert call[4] == "--body-file" and len(call) == 6 and "api" not in call
    assert run.bodies == [None]


def test_comment_on_issue_raises_value_error_when_gh_fails_and_removes_its_file():
    run = ScriptedRun(gh_fixture("issue_404.json"))
    with pytest.raises(ValueError, match="^gh issue comment failed: gh: Not Found") as raised:
        GhTracker(Path("/p"), run).comment_on_issue("https://github.com/o/r/issues/9", "x")
    assert type(raised.value) is ValueError
    [body_option] = _option(run.argv[0], "-F")
    assert not Path(body_option.removeprefix("body=@")).exists()


def test_comment_on_issue_keeps_the_subcommand_failure_wording_for_an_unparsed_reference():
    def run(args, cwd, capture_output, text, check):
        raise subprocess.CalledProcessError(1, args, stderr="issue not found")

    with pytest.raises(ValueError, match="gh issue comment failed: issue not found"):
        GhTracker(Path("/p"), run).comment_on_issue("#9", "x")


def test_update_issue_body_patches_the_issue_with_the_body_in_a_file_and_removes_it():
    run = ScriptedRun(gh_fixture("issue_edit_200.json"))
    body = "Blocked by: #7, #9\n\nnaïve 😀\r\nend\n"
    GhTracker(Path("/p"), run).update_issue_body(12, body)
    [call] = run.argv
    [body_option] = _option(call, "-F")
    assert call == [
        "gh",
        "api",
        "-i",
        "-X",
        "PATCH",
        "repos/{owner}/{repo}/issues/12",
        "-F",
        body_option,
    ]
    assert run.bodies == [body]
    assert not Path(body_option.removeprefix("body=@")).exists()


@pytest.mark.parametrize("body", ["", "   ", "\n\t \r\n"])
def test_update_issue_body_refuses_an_empty_body_before_any_gh_call(body):
    run = Recorder()
    with pytest.raises(ValueError, match="empty body"):
        GhTracker(Path("/p"), run).update_issue_body(12, body)
    assert run.calls == []


def test_update_issue_body_raises_value_error_when_gh_fails_and_removes_its_file():
    run = ScriptedRun(gh_fixture("issue_404.json"))
    with pytest.raises(ValueError, match="^gh issue edit failed: gh: Not Found"):
        GhTracker(Path("/p"), run).update_issue_body(12, "x")
    [body_option] = _option(run.argv[0], "-F")
    assert not Path(body_option.removeprefix("body=@")).exists()


@pytest.mark.parametrize(
    ("write", "answer"),
    [
        pytest.param(
            lambda t: t.comment_on_issue("https://github.com/o/r/issues/9", "x"),
            gh_response(200, [], "{}"),
            id="comment_on_issue got 200",
        ),
        pytest.param(lambda t: t.comment("GH-12", "x"), gh_response(204, [], ""), id="comment 204"),
        pytest.param(lambda t: t.comment("GH-12", "x"), _plain("ok"), id="comment unreadable"),
        pytest.param(
            lambda t: t.update_issue_body(12, "x"), gh_response(201, [], "{}"), id="edit got 201"
        ),
        pytest.param(lambda t: t.update_issue_body(12, "x"), _plain("ok"), id="edit unreadable"),
    ],
)
def test_a_moved_write_that_answers_the_wrong_status_may_have_applied_and_is_not_retried(
    write, answer
):
    run = ScriptedRun(answer)
    policy, slept = _policy()
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy)
    with pytest.raises(ValueError, match=r"^gh issue (comment|edit) failed: .*may have applied$"):
        write(tracker)
    assert len(run.argv) == 1 and slept == []


def _rate_limit_reads(reset: int | None) -> RecordingRun:
    """The `gh api rate_limit` answer: spent until `reset`, or unreadable when `None`."""
    if reset is None:
        return RecordingRun("", "gh: Not Found (HTTP 404)", 1)
    return _plain(json.dumps({"resources": {"core": {"remaining": 0, "reset": reset}}}))


def test_a_primary_limit_on_a_comment_waits_and_then_lands():
    run = ScriptedRun(
        gh_fixture("rest_primary_limit_403.json"),
        _rate_limit_reads(None),
        gh_fixture("comment_201.json"),
    )
    slept: list[float] = []
    policy = GhRetry(max_wait_s=900, sleep=slept.append, now=lambda: 1_000.0)
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy, access=GithubAccess())
    tracker.comment("GH-12", "x")
    assert sum(slept) == 60  # the rate_limit read failed: the x-ratelimit-reset header says 60 s
    assert [call[4] for call in run.argv if call[1] == "api" and "-X" in call] == ["POST", "POST"]


def test_a_primary_limit_that_never_clears_on_a_comment_is_tracker_rate_limited():
    run = ScriptedRun(
        *[
            step
            for _ in range(3)
            for step in (gh_fixture("rest_primary_limit_403.json"), _rate_limit_reads(1_030))
        ]
    )
    slept: list[float] = []
    policy = GhRetry(max_wait_s=900, sleep=slept.append, now=lambda: 1_000.0)
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy)
    with pytest.raises(TrackerRateLimited) as raised:
        tracker.comment_on_issue("https://github.com/o/r/issues/9", "x")
    assert raised.value.status == 403 and raised.value.resets_at is not None
    assert str(raised.value).startswith("gh issue comment met the GitHub rate limit")
    assert sum(slept) == 60


def test_a_secondary_limit_on_a_comment_stays_a_refusal_with_one_call():
    run = ScriptedRun(gh_fixture("rest_secondary_limit_403.json"))
    policy, slept = _policy()
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy, access=GithubAccess())
    with pytest.raises(TrackerRefused) as raised:
        tracker.comment("GH-12", "x")
    assert raised.value.status == 403 and not isinstance(raised.value, TrackerRateLimited)
    assert len(run.argv) == 1 and slept == []


def test_a_task_without_an_issue_writes_nothing_to_the_issue():
    run = Recorder()
    tracker = GhTracker(Path("/p"), run)
    tracker.comment("T-abc", "x")
    tracker.label("T-abc", "needs-human")
    assert run.calls == []


def test_close_issue_closes_the_issue_as_completed_through_gh():
    run = Recorder()
    GhTracker(Path("/p"), run).close_issue("GH-12")
    assert run.calls == [["gh", "issue", "close", "12", "--reason", "completed"]]


def test_close_issue_of_a_task_without_an_issue_calls_nothing():
    run = Recorder()
    GhTracker(Path("/p"), run).close_issue("T-abc")
    assert run.calls == []


def test_a_failed_close_issue_is_a_value_error():
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), Recorder(fail=True)).close_issue("GH-12")


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


def _with_reading(stdout, **reading):
    document = json.loads(stdout)
    document["data"]["repository"]["pullRequest"].update(reading)
    return json.dumps(document)


def test_pull_request_reads_mergeable_and_base_oid_and_maps_anything_else_to_none():
    """GH-326."""
    plain = _graphql_pull_request("OPEN", None, "2026-09-20T23:00:00Z", [])
    url = "https://github.com/o/r/pull/7"

    def read(**reading):
        run = Recorder(stdout=_with_reading(plain, **reading))
        found = GhTracker(Path("/p"), run).pull_request(url)
        assert found is not None
        return found

    for value in ("MERGEABLE", "CONFLICTING", "UNKNOWN"):
        found = read(mergeable=value, baseRefOid="1234abc")
        assert (found.mergeable, found.base_oid) == (value, "1234abc")
    for value in ("DIRTY", "", None, 3):
        assert read(mergeable=value, baseRefOid=None).mergeable is None
    assert read(baseRefOid="").base_oid is None
    missing = GhTracker(Path("/p"), Recorder(stdout=plain)).pull_request(url)
    assert missing is not None and (missing.mergeable, missing.base_oid) == (None, None)
    run = Recorder(stdout=plain)
    GhTracker(Path("/p"), run).pull_request(url)
    query = " ".join(run.calls[0])
    assert "mergeable" in query and "baseRefOid" in query and "mergeStateStatus" not in query


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
    run = Recorder(stdout='[{"body": "Escalated: x"}, {"body": "Use USD."}]')
    tracker = GhTracker(Path("/p"), run)
    tracker.unlabel("GH-12", "needs-human")
    assert tracker.issue_comments("GH-12") == ["Escalated: x", "Use USD."]
    assert run.calls == [
        ["gh", "issue", "edit", "12", "--remove-label", "needs-human"],
        _COMMENTS_ARGV,
    ]
    assert GhTracker(Path("/p"), Recorder(stdout="[]")).issue_comments("GH-1") == []
    assert tracker.issue_comments("T-abc") == [] and len(run.calls) == 2


# -- GH-137: the poller's own structured reads ---------------------------------------


def test_timestamped_comments_carries_each_bodys_own_timestamp():
    stdout = json.dumps(
        [
            {"body": "Escalated: x", "created_at": "2026-10-02T19:30:00Z"},
            {"body": "Auto-resumed: account limit", "created_at": "2026-10-02T19:46:00Z"},
        ]
    )
    tracker = GhTracker(Path("/p"), Recorder(stdout=stdout))
    comments = tracker.timestamped_comments("GH-19")
    assert [(c.body, c.created_at) for c in comments] == [
        ("Escalated: x", "2026-10-02T19:30:00Z"),
        ("Auto-resumed: account limit", "2026-10-02T19:46:00Z"),
    ]


def test_timestamped_comments_carries_each_comments_author_and_url():
    # The REST shape names the author under `user.login`, the `#issuecomment-<id>` address
    # under `html_url`, the time under `created_at`. A comment saying none reads as empty
    # strings, never a crash.
    stdout = json.dumps(
        [
            {
                "body": "rest shape",
                "created_at": "2026-10-28T11:00:00Z",
                "user": {"login": "someone"},
                "html_url": "https://github.com/o/r/issues/19#issuecomment-222",
            },
            {"body": "bare", "created_at": "2026-10-28T12:00:00Z", "user": None},
            {"body": None},
        ]
    )
    comments = GhTracker(Path("/p"), Recorder(stdout=stdout)).timestamped_comments("GH-19")
    assert [(c.author, c.url) for c in comments] == [
        ("someone", "https://github.com/o/r/issues/19#issuecomment-222"),
        ("", ""),
        ("", ""),
    ]
    assert [(c.body, c.created_at) for c in comments][1:] == [
        ("bare", "2026-10-28T12:00:00Z"),
        ("", ""),
    ]


_COMMENTS_ARGV = ["gh", "api", "--paginate", "repos/{owner}/{repo}/issues/12/comments?per_page=100"]


def test_timestamped_comments_reads_the_thread_over_paginated_rest_in_the_product_dir():
    run = Recorder(stdout="[]")
    tracker = GhTracker(Path("/p"), run)
    assert tracker.timestamped_comments("GH-12") == []
    assert run.calls == [_COMMENTS_ARGV] and run.cwds == [Path("/p")]
    assert tracker.timestamped_comments("T-abc") == [] and len(run.calls) == 1


def test_the_old_and_the_new_item_shape_map_to_the_same_comment():
    old = {
        "body": "b",
        "createdAt": "2026-10-28T10:00:00Z",
        "author": {"login": "me"},
        "url": "https://github.com/o/r/issues/12#issuecomment-1",
    }
    new = {
        "body": "b",
        "created_at": "2026-10-28T10:00:00Z",
        "user": {"login": "me"},
        "html_url": "https://github.com/o/r/issues/12#issuecomment-1",
    }
    read = [
        GhTracker(Path("/p"), Recorder(stdout=json.dumps([item]))).timestamped_comments("GH-12")
        for item in (old, new)
    ]
    assert read[0] == read[1] and len(read[0]) == 1
    assert read[0][0].url.endswith("#issuecomment-1") and read[0][0].author == "me"


def test_an_item_with_only_a_url_gives_that_url():
    stdout = json.dumps([{"body": "b", "url": "https://x/1"}])
    [comment] = GhTracker(Path("/p"), Recorder(stdout=stdout)).timestamped_comments("GH-12")
    assert comment.url == "https://x/1"


def test_two_concatenated_pages_give_every_comment_in_order():
    first = json.dumps([{"body": f"c{i}"} for i in range(100)])
    second = json.dumps([{"body": "c100"}])
    tracker = GhTracker(Path("/p"), Recorder(stdout=first + second + "\n"))
    assert [c.body for c in tracker.timestamped_comments("GH-12")] == [f"c{i}" for i in range(101)]
    assert tracker.issue_comments("GH-12") == [f"c{i}" for i in range(101)]


def test_a_truncated_second_page_raises_instead_of_a_shorter_thread():
    stdout = json.dumps([{"body": "a"}]) + '[{"body": "b"}, {"bod'
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), Recorder(stdout=stdout)).timestamped_comments("GH-12")


def test_a_gh_that_fails_after_printing_a_full_page_returns_no_comments():
    run = Recorder(fail=True, fail_output=json.dumps([{"body": "page 1"}]))
    with pytest.raises(ValueError, match="^gh issue comments failed"):
        GhTracker(Path("/p"), run).timestamped_comments("GH-12")


def test_a_comment_read_that_times_out_raises_tracker_timed_out_under_its_own_name():
    class Stalls:
        def __call__(self, args, cwd, capture_output, text, check):
            raise subprocess.TimeoutExpired(args, 60)

    with pytest.raises(TrackerTimedOut, match="gh issue comments timed out"):
        GhTracker(Path("/p"), Stalls(), log=lambda line: None).timestamped_comments("GH-12")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[]", 0),
        ("[][]", 0),
        ('[{"a": 1}] [{"a": 2}]', 2),
        ('[{"a": 1}]\n\n[{"a": 2}]\n', 2),
        ('\n[{"a": 1}][{"a": 2}][]', 2),
    ],
)
def test_concatenated_pages_reads_one_or_several_arrays(raw, expected):
    assert len(tracker_module._concatenated_pages(raw)) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "\n", "  \n", "[{}]{", '{"a":1}', "[1]", '[{"a": 1}] garbage', '[{"a": 1}][', "[{}] null"],
)
def test_concatenated_pages_raises_on_anything_it_cannot_read_to_the_end(raw):
    with pytest.raises(ValueError):
        tracker_module._concatenated_pages(raw)


def test_concatenated_pages_error_does_not_embed_the_whole_raw():
    with pytest.raises(ValueError) as raised:
        tracker_module._concatenated_pages("[1]" + "x" * 5000)
    assert len(str(raised.value)) < 400


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


HELD_ARGV = [
    "gh",
    "issue",
    "list",
    "--label",
    HOLD_LABEL,
    "--state",
    "open",
    "--json",
    "number",
    "--limit",
    str(HELD_LIMIT),
]


def _held_rows(count: int) -> str:
    return json.dumps([{"number": number} for number in range(1, count + 1)])


def test_held_issues_asks_for_the_open_held_issues_in_one_bounded_page():
    run = Recorder(stdout=_held_rows(3))
    assert GhTracker(Path("/p"), run).held_issues() == frozenset({1, 2, 3})
    assert run.calls == [HELD_ARGV]


def test_no_held_issue_is_an_empty_set():
    assert GhTracker(Path("/p"), Recorder(stdout="[]")).held_issues() == frozenset()


def test_a_page_one_row_short_of_the_limit_is_a_complete_list():
    held = GhTracker(Path("/p"), Recorder(stdout=_held_rows(HELD_LIMIT - 1))).held_issues()
    assert len(held) == HELD_LIMIT - 1


def test_a_page_of_exactly_the_limit_may_be_cut_and_raises():
    with pytest.raises(HeldListMayBeCut):
        GhTracker(Path("/p"), Recorder(stdout=_held_rows(HELD_LIMIT))).held_issues()


def test_a_full_page_raises_even_when_it_holds_a_duplicate():
    rows = [{"number": number} for number in range(1, HELD_LIMIT)] + [{"number": 1}]
    assert len(rows) == HELD_LIMIT
    with pytest.raises(HeldListMayBeCut):
        GhTracker(Path("/p"), Recorder(stdout=json.dumps(rows))).held_issues()


def test_a_duplicate_inside_a_short_page_collapses():
    rows = [{"number": number} for number in range(1, HELD_LIMIT - 1)] + [{"number": 1}]
    assert len(rows) == HELD_LIMIT - 1
    held = GhTracker(Path("/p"), Recorder(stdout=json.dumps(rows))).held_issues()
    assert len(held) == HELD_LIMIT - 2


def test_a_failed_gh_call_raises_tracker_call_failed_naming_no_stderr():
    with pytest.raises(TrackerCallFailed) as raised:
        GhTracker(Path("/p"), Recorder(fail=True)).held_issues()
    assert not isinstance(raised.value, HeldListMayBeCut)
    assert "no auth" not in str(raised.value)
    assert isinstance(raised.value.__cause__, ValueError)


def test_a_stalled_gh_call_passes_through_as_tracker_timed_out():
    with pytest.raises(TrackerTimedOut):
        GhTracker(Path("/p"), _stalls_at("issue", "list")).held_issues()


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        "",
        json.dumps({"number": 3}),
        json.dumps([3]),
        json.dumps([{"title": "x"}]),
        json.dumps([{"number": "3"}]),
        json.dumps([{"number": True}]),
        json.dumps([{"number": 3.0}]),
    ],
)
def test_an_unreadable_or_mis_shaped_answer_raises_tracker_call_failed(stdout):
    with pytest.raises(TrackerCallFailed) as raised:
        GhTracker(Path("/p"), Recorder(stdout=stdout)).held_issues()
    assert type(raised.value) is TrackerCallFailed
    assert not isinstance(raised.value, HeldListMayBeCut)


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


def test_merge_with_method_merge_and_merge_commits_allowed_uses_merge_and_pins_the_head():
    run = MethodRecorder(True, True, True)
    assert GhTracker(Path("/p"), run).merge(PR_URL, "abc123", method="merge") == "merge"
    assert run.calls[-1] == ["gh", "pr", "merge", "7", "--merge", "--match-head-commit", "abc123"]


@pytest.mark.parametrize(
    "allowed", [(False, True, True), (False, False, True), (False, False, False)]
)
def test_merge_with_method_merge_without_merge_commits_raises_no_merge_method_and_merges_nothing(
    allowed,
):
    run = MethodRecorder(*allowed)
    with pytest.raises(MergeCallFailed) as raised:
        GhTracker(Path("/p"), run).merge(PR_URL, "abc123", method="merge")
    assert raised.value.failure == "no_merge_method"
    assert type(raised.value) is MergeCallFailed
    assert all(call[1:3] != ["pr", "merge"] for call in run.calls)
    assert run.calls[-1][1:3] == ["repo", "view"]


def test_merge_without_method_still_falls_back_to_squash_when_merge_commits_are_off():
    run = MethodRecorder(False, True, True)
    assert GhTracker(Path("/p"), run).merge(PR_URL, "abc123", method=None) == "squash"
    assert run.calls[-1][4] == "--squash"


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


# -- list_pull_requests -------------------------------------------------------------------


def _listed(**extra):
    row = {
        "number": 3,
        "url": "https://github.com/o/r/pull/3",
        "state": "OPEN",
        "body": "b",
        "mergeCommit": None,
        "headRefName": "agentic/playbook",
    }
    return json.dumps([{**row, **extra}])


def test_list_pull_requests_asks_for_is_cross_repository():
    run = Recorder(stdout=_listed())
    GhTracker(Path("/p"), run).list_pull_requests()
    fields = run.calls[0][run.calls[0].index("--json") + 1].split(",")
    assert "isCrossRepository" in fields


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"isCrossRepository": True}, True),
        ({"isCrossRepository": False}, False),
        ({}, None),
        ({"isCrossRepository": None}, None),
        ({"isCrossRepository": "false"}, None),
        ({"isCrossRepository": 0}, None),
    ],
    ids=["true", "false", "missing", "null", "string", "number"],
)
def test_list_pull_requests_maps_cross_repository_and_unreadable_values_to_none(extra, expected):
    run = Recorder(stdout=_listed(**extra))
    (listed,) = GhTracker(Path("/p"), run).list_pull_requests()
    assert listed.cross_repository is expected
    assert (listed.number, listed.branch, listed.state) == (3, "agentic/playbook", "OPEN")


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


def _suites(*statuses, actions=True, more=False):
    """The `checkSuites` field of a commit: one suite per status, each from a workflow run
    (`actions`) or from another app (no `workflowRun`)."""
    run = {"databaseId": 1} if actions else None
    nodes = [{"status": status, "workflowRun": run} for status in statuses]
    return {"pageInfo": {"hasNextPage": more}, "nodes": nodes}


def _graphql(
    nodes,
    *,
    oid="a" * 40,
    rollup=True,
    more=False,
    created="2026-10-07T10:00:00Z",
    suites=None,
):
    contexts = {"nodes": nodes, "pageInfo": {"hasNextPage": more}}
    commit = {"oid": oid, "statusCheckRollup": {"contexts": contexts} if rollup else None}
    if suites is not None:
        commit["checkSuites"] = suites
    pull_request = {
        "createdAt": created,
        "baseRefName": "master",
        "headRefOid": oid,
        "commits": {"nodes": [{"commit": commit}]},
    }
    return json.dumps({"data": {"repository": {"pullRequest": pull_request}}})


def _branch_graphql(nodes, *, oid="b" * 40, suites=None):
    contexts = {"nodes": nodes, "pageInfo": {"hasNextPage": False}}
    target = {"oid": oid, "statusCheckRollup": {"contexts": {**contexts}}}
    if suites is not None:
        target["checkSuites"] = suites
    return json.dumps({"data": {"repository": {"ref": {"target": target}}}})


def _run_node(name, status, conclusion, url="https://github.com/o/r/actions/runs/1/job/2"):
    return {
        "__typename": "CheckRun",
        "id": f"id-{name}",
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


def test_ci_report_carries_both_runs_of_a_name_and_leaves_the_latest_to_the_domain():
    """GH-606: the adapter reads every check run of the head, a superseded one included; which
    one is current is `ci.current_runs`'s decision, not the parser's."""
    old_url = "https://github.com/o/r/actions/runs/43156637045/job/1"
    new_url = "https://github.com/o/r/actions/runs/43165741456/job/2"
    nodes = [
        _run_node("docs", "COMPLETED", "FAILURE", url=old_url),
        _run_node("docs", "COMPLETED", "SUCCESS", url=new_url),
    ]
    report = GhTracker(Path("/p"), Recorder(stdout=_graphql(nodes))).ci_report(PR)
    assert isinstance(report, CiReport)
    assert [(c.conclusion, c.link) for c in report.checks] == [
        ("FAILURE", old_url),
        ("SUCCESS", new_url),
    ]
    assert [c.link for c in current_runs(report)] == [new_url]


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


def test_reopen_pull_request_reopens_by_number_and_is_not_a_note():
    run = Recorder()
    GhTracker(Path("/p"), run).reopen_pull_request(7)
    assert run.calls == [["gh", "pr", "reopen", "7"]]  # no comment, label or issue verb


def test_reopen_pull_request_on_an_open_pull_request_is_a_no_op_for_the_caller():
    """`gh pr reopen` answers "already open" on stderr and exits 0: nothing to raise."""

    class AlreadyOpen(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "! Pull request #7 is already open")

    run = AlreadyOpen()
    assert GhTracker(Path("/p"), run).reopen_pull_request(7) is None
    assert len(run.calls) == 1


def test_reopen_pull_request_refused_by_the_platform_raises_once_and_never_retries():
    """A merged pull request, or one whose base branch is gone: `gh` exits non-zero."""
    run = Recorder(fail=True)
    with pytest.raises(ValueError, match="gh pr reopen failed"):
        GhTracker(Path("/p"), run).reopen_pull_request(7)
    assert len(run.calls) == 1


def test_reopen_pull_request_calls_gh_exactly_once_on_a_502():
    policy, slept = _policy()
    run = Flaky(failing=9)
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), run, lambda line: None, retry=policy).reopen_pull_request(7)
    assert len(run.calls) == 1 and slept == []


def test_open_issue_sends_the_label_only_when_one_is_given():
    run = Recorder()
    GhTracker(Path("/p"), run).open_issue("t", "b", label="agentic")
    GhTracker(Path("/p"), run).open_issue("t", "b", label=None)
    labelled, plain = run.calls
    assert labelled[labelled.index("--label") + 1] == "agentic"
    assert "--label" not in plain


def _labels_sent(**kwargs):
    run = Recorder()
    GhTracker(Path("/p"), run).open_issue("t", "b", **kwargs)
    [call] = run.calls
    return [call[i + 1] for i, arg in enumerate(call) if arg == "--label"]


def test_open_issue_sends_one_label_flag_per_label_in_order_without_repeats():
    assert _labels_sent(label="agentic") == ["agentic"]
    assert _labels_sent(labels=["agentic", "bug"]) == ["agentic", "bug"]
    assert _labels_sent(label="agentic", labels=["bug", "agentic", "bug"]) == ["agentic", "bug"]
    assert _labels_sent(label=None, labels=()) == []


def test_open_issue_without_any_label_sends_the_argv_it_always_did():
    run = Recorder()
    GhTracker(Path("/p"), run).open_issue("t", "b", label=None, labels=())
    [call] = run.calls
    assert call[:6] == ["gh", "issue", "create", "--title", "t", "--body-file"]
    assert len(call) == 7


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


def test_changed_files_reads_the_count_gh_reports_for_the_pull_request():
    run = Scripted({"view": json.dumps({"changedFiles": 31})})
    count = GhTracker(Path("/p"), run).changed_files("https://github.com/o/r/pull/7")
    assert count == 31
    assert run.calls == [["gh", "pr", "view", "7", "--json", "changedFiles"]]


@pytest.mark.parametrize("stdout", ["not json", '{"changedFiles": "many"}', "[]", '{"x": 1}'])
def test_changed_files_rejects_an_answer_it_cannot_read(stdout):
    run = Scripted({"view": stdout})
    with pytest.raises(ValueError, match="changedFiles"):
        GhTracker(Path("/p"), run).changed_files("https://github.com/o/r/pull/7")


def test_changed_files_rejects_a_non_pull_request_reference_before_any_gh_call():
    run = Recorder()
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), run).changed_files("https://github.com/o/r/issues/7")
    assert run.calls == []


class LabelRun(Recorder):
    """`gh label list` answers `listed`; every other call answers an empty success."""

    def __init__(self, listed: str):
        super().__init__(stdout="")
        self.listed = listed

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        out = self.listed if args[:3] == ["gh", "label", "list"] else ""
        return subprocess.CompletedProcess(args, 0, out, "")


def test_ensure_label_that_exists_makes_exactly_the_list_call():
    run = LabelRun(json.dumps([{"name": "bug"}, {"name": "needs-triage"}]))
    GhTracker(Path("/p"), run).ensure_label("needs-triage")
    assert run.calls == [["gh", "label", "list", "--limit", "1000", "--json", "name"]]


def test_ensure_label_that_is_missing_is_created_without_force():
    run = LabelRun(json.dumps([{"name": "bug"}]))
    GhTracker(Path("/p"), run).ensure_label("needs-triage")
    assert run.calls == [
        ["gh", "label", "list", "--limit", "1000", "--json", "name"],
        ["gh", "label", "create", "needs-triage"],
    ]


def test_ensure_label_compares_the_exact_name_locally():
    run = LabelRun(json.dumps([{"name": "needs-triage-2"}, {"name": "Needs-Triage"}]))
    GhTracker(Path("/p"), run).ensure_label("needs-triage")
    assert run.calls[-1] == ["gh", "label", "create", "needs-triage"]


@pytest.mark.parametrize("listed", ["not json", "{}", '["needs-triage"]', '[{"color": "x"}]'])
def test_ensure_label_rejects_an_unreadable_list_without_creating(listed):
    run = LabelRun(listed)
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), run).ensure_label("needs-triage")
    assert [call[:3] for call in run.calls] == [["gh", "label", "list"]]


def test_ensure_label_never_searches_nor_forces():
    for listed in ("[]", json.dumps([{"name": "needs-triage"}])):
        run = LabelRun(listed)
        GhTracker(Path("/p"), run).ensure_label("needs-triage")
        assert not any(a in ("--search", "-S", "--force", "-f") for c in run.calls for a in c)


def test_ensure_label_gh_failure_is_a_value_error_without_create():
    run = Recorder(fail=True)
    with pytest.raises(ValueError, match="gh label list failed: no auth"):
        GhTracker(Path("/p"), run).ensure_label("needs-triage")
    assert len(run.calls) == 1


def _issue_json(**more):
    return json.dumps(
        {
            "state": "OPEN",
            "stateReason": "",
            "blockedBy": {"nodes": [], "totalCount": 0},
            "closedByPullRequestsReferences": [],
            **more,
        }
    )


def test_issue_state_reads_the_labels_of_the_issue():
    run = Recorder(stdout=_issue_json(labels=[{"name": "agentic"}, {"name": "needs-human"}]))
    state = GhTracker(Path("/p"), run).issue_state(20)
    assert state.labels == ("agentic", "needs-human")
    assert "labels" in run.calls[0][run.calls[0].index("--json") + 1].split(",")


def test_issue_state_with_no_labels_reads_an_empty_tuple_and_a_missing_field_reads_none():
    assert (
        GhTracker(Path("/p"), Recorder(stdout=_issue_json(labels=[]))).issue_state(1).labels == ()
    )
    assert GhTracker(Path("/p"), Recorder(stdout=_issue_json())).issue_state(1).labels is None
    junk = _issue_json(labels=[{"name": "ok"}, {"id": 3}])
    assert GhTracker(Path("/p"), Recorder(stdout=junk)).issue_state(1).labels is None
    assert (
        GhTracker(Path("/p"), Recorder(stdout=_issue_json(labels="x"))).issue_state(1).labels
        is None
    )


def test_the_fallback_read_of_an_old_gh_still_asks_for_the_labels():
    class Run(Recorder):
        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if "blockedBy" in args[args.index("--json") + 1]:
                raise subprocess.CalledProcessError(1, args, stderr="Unknown JSON field: blockedBy")
            old = {k: v for k, v in json.loads(_issue_json(labels=[])).items() if k != "blockedBy"}
            return subprocess.CompletedProcess(args, 0, json.dumps(old), "")

    run = Run()
    state = GhTracker(Path("/p"), run).issue_state(20)
    assert state.blocked_by is None and state.labels == ()
    assert "labels" in run.calls[-1][run.calls[-1].index("--json") + 1].split(",")


# --- the retry policy per verb (GH-389) --------------------------------------------------

_BAD_GATEWAY = "non-200 OK status code: 502 Bad Gateway body: <html>"


class Flaky:
    """Fails `failing` times with a 502, then answers; keeps every argv."""

    def __init__(self, failing, stdout="[]"):
        self.calls, self.failing, self.stdout = [], failing, stdout

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        if len(self.calls) <= self.failing:
            raise subprocess.CalledProcessError(1, args, stderr=_BAD_GATEWAY)
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def _policy():
    from agentic.core.process.adapters.github.run import GhRetry

    slept: list[float] = []
    return GhRetry(max_wait_s=900, sleep=slept.append), slept


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.comment("GH-12", "hi"),
        lambda t: t.label("GH-12", "x"),
        lambda t: t.unlabel("GH-12", "x"),
        lambda t: t.comment_on_issue("12", "hi"),
        lambda t: t.update_issue_body(12, "body"),
        lambda t: t.update_pull_request_body(3, "body"),
        lambda t: t.queue(),
        lambda t: t.issue_created_at("GH-12"),
    ],
)
def test_the_retrying_verbs_survive_a_502(call):
    class AnsweringRun(Flaky):
        """Answers a `gh api -i` write with the status it expects, as GitHub does."""

        def __call__(self, args, cwd, capture_output, text, check):
            answer = super().__call__(args, cwd, capture_output, text, check)
            if "-i" in args and answer.returncode == 0:
                answer.stdout = _http(200 if "PATCH" in args else 201)
            return answer

    policy, slept = _policy()
    run = AnsweringRun(failing=1, stdout="{}")
    call(GhTracker(Path("/p"), run, lambda line: None, retry=policy))
    assert len(run.calls) == 2 and slept == [1, 1]  # a 2 s backoff, slept in 1 s slices


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.open_issue("title", "body"),
        lambda t: t.merge("https://github.com/o/r/pull/7", "abc"),
        lambda t: t.close_issue("GH-12"),
        lambda t: t.close_pull_request("https://github.com/o/r/pull/7"),
        lambda t: t.rerun_failed_ci("https://github.com/o/r/pull/7", "abc"),
    ],
)
def test_the_verbs_that_create_or_merge_call_gh_exactly_once_on_a_502(call):
    policy, slept = _policy()
    run = Flaky(failing=9)
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy)
    with pytest.raises(ValueError):
        call(tracker)
    assert len(run.calls) == 1 and slept == []


def test_open_pull_request_posts_exactly_once_on_a_502_though_its_base_read_retries():
    policy, slept = _policy()
    run = ScriptedRun(
        RecordingRun("", _BAD_GATEWAY, 1),
        _plain("main"),
        *[RecordingRun("", _BAD_GATEWAY, 1)] * 5,
    )
    tracker = GhTracker(Path("/p"), run, lambda line: None, retry=policy)
    with pytest.raises(ValueError):
        tracker.open_pull_request("GH-12", "b", "title", "body")
    posts = [call for call in run.argv if "-X" in call]
    assert len(run.argv) == 3 and len(posts) == 1 and slept == [1, 1]  # the read's 2 s backoff


def test_a_write_timeout_is_not_retried_and_a_read_timeout_is():
    policy, _ = _policy()
    expired = subprocess.TimeoutExpired(["gh"], 60)

    class TimesOut:
        def __init__(self):
            self.calls = []

        def __call__(self, args, cwd, capture_output, text, check):
            self.calls.append(args)
            if len(self.calls) == 1:
                raise expired
            return subprocess.CompletedProcess(args, 0, "[]", "")

    write = TimesOut()
    with pytest.raises(TrackerTimedOut):
        GhTracker(Path("/p"), write, lambda line: None, retry=policy).comment("GH-12", "hi")
    assert len(write.calls) == 1
    read = TimesOut()
    assert GhTracker(Path("/p"), read, lambda line: None, retry=policy).queue() == []
    assert len(read.calls) == 2


def test_a_tracker_with_no_policy_fails_a_502_at_once():
    run = Flaky(failing=9)
    with pytest.raises(ValueError, match="502"):
        GhTracker(Path("/p"), run, lambda line: None).comment("GH-12", "hi")
    assert len(run.calls) == 1


# --- note / drop_label: the best-effort writes (GH-389) ----------------------------------

_RATE_LIMITED = "gh: API rate limit exceeded for user ID 1. (HTTP 403)"
_SECRET = "SECRET-STDERR-TEXT"


class Outcomes:
    """Raises (an exception) or answers (a string) per call, in order; keeps every argv."""

    def __init__(self, *outcomes):
        self.calls, self._outcomes = [], list(outcomes)

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(args, 0, outcome, "")


def _failed_gh(stderr, code=1):
    return subprocess.CalledProcessError(code, ["gh"], stderr=stderr)


def _quiet_policy(max_wait=900):
    from agentic.core.process.adapters.github.run import GhRetry

    return GhRetry(max_wait_s=max_wait, sleep=lambda seconds: None, now=lambda: 1_000)


_NOTES = {
    "comment": lambda tracker: tracker.note("GH-12", "Resumed"),
    "label": lambda tracker: tracker.drop_label("GH-12", "needs-human"),
}


def test_put_label_makes_the_same_gh_call_as_label_and_returns_none():
    run = Outcomes("")
    assert GhTracker(Path("/p"), run, lambda line: None).put_label("GH-12", "needs-human") is None
    assert run.calls == [["gh", "issue", "edit", "12", "--add-label", "needs-human"]]


def test_put_label_that_failed_returns_the_class_on_the_label_target_never_its_text():
    from agentic.core.process.domain.task import TrackerFailure

    run = Outcomes(_failed_gh("could not resolve to an Issue " + _SECRET))
    failure = GhTracker(Path("/p"), run, lambda line: None, retry=_quiet_policy()).put_label(
        "GH-12", "needs-human"
    )
    assert isinstance(failure, TrackerFailure)
    assert (failure.target, failure.cls) == ("label", "refused")
    assert _SECRET not in repr(failure)


def test_put_label_on_a_task_with_no_issue_calls_nothing_and_a_missing_gh_still_raises():
    run = Outcomes()
    assert GhTracker(Path("/p"), run).put_label("T-abc", "x") is None and run.calls == []
    missing = Outcomes(FileNotFoundError("gh"))
    with pytest.raises(ValueError, match="not installed"):
        GhTracker(Path("/p"), missing, lambda line: None, retry=_quiet_policy()).put_label(
            "GH-12", "x"
        )


@pytest.mark.parametrize("target", ["comment", "label"])
def test_a_note_that_lands_returns_none_and_makes_the_same_gh_call_as_comment_and_unlabel(target):
    run = Outcomes(_http(201) if target == "comment" else "")
    assert _NOTES[target](GhTracker(Path("/p"), run, lambda line: None)) is None
    expected = {
        "comment": [
            "gh",
            "api",
            "-i",
            "-X",
            "POST",
            "repos/{owner}/{repo}/issues/12/comments",
            "-F",
        ],
        "label": ["gh", "issue", "edit", "12", "--remove-label", "needs-human"],
    }[target]
    [call] = run.calls
    assert call[: len(expected)] == expected and len(call) == len(expected) + (target == "comment")


@pytest.mark.parametrize(
    ("cls", "build"),
    [
        pytest.param(
            "transient",
            lambda: (Outcomes(*[_failed_gh(_BAD_GATEWAY + _SECRET)] * 3), _quiet_policy()),
            id="a 502 on every attempt",
        ),
        pytest.param(
            "timed_out",
            lambda: (Outcomes(subprocess.TimeoutExpired(["gh"], 60)), _quiet_policy()),
            id="a write that timed out",
        ),
        pytest.param(
            "unreachable",
            lambda: (Outcomes(_failed_gh("error connecting to api.github.com " + _SECRET)), None),
            id="no policy, GitHub unreachable",
        ),
        pytest.param(
            "rate_limited",
            lambda: (
                Outcomes(
                    _failed_gh(_RATE_LIMITED + _SECRET),
                    '{"resources": {"core": {"remaining": 0, "reset": 9000}}}',
                ),
                _quiet_policy(max_wait=60),
            ),
            id="a reset over the cap",
        ),
        pytest.param(
            "refused",
            lambda: (
                Outcomes(_failed_gh("could not resolve to an Issue " + _SECRET)),
                _quiet_policy(),
            ),
            id="a refusal under a policy",
        ),
    ],
)
@pytest.mark.parametrize("target", ["comment", "label"])
def test_a_note_that_failed_returns_the_class_of_the_error_never_its_text(target, cls, build):
    from agentic.core.process.domain.task import TrackerFailure

    run, policy = build()
    lines: list[str] = []

    failure = _NOTES[target](GhTracker(Path("/p"), run, lines.append, retry=policy))

    assert isinstance(failure, TrackerFailure)
    assert (failure.target, failure.cls) == (target, cls)
    assert failure.at  # a timestamp, whichever the clock reads
    assert _SECRET not in repr(failure) and not any(_SECRET in line for line in lines)


def test_a_failed_note_writes_one_log_line_naming_the_call_and_the_class():
    lines: list[str] = []
    tracker = GhTracker(
        Path("/p"), Outcomes(_failed_gh("no auth")), lines.append, retry=_quiet_policy()
    )

    assert tracker.note("GH-12", "x") is not None

    named = [line for line in lines if "note" in line]
    assert len(named) == 1
    assert "comment" in named[0] and "refused" in named[0] and "no auth" not in named[0]


@pytest.mark.parametrize("target", ["comment", "label"])
def test_a_missing_gh_still_raises_from_a_note(target):
    run = Outcomes(FileNotFoundError("gh"))
    with pytest.raises(ValueError, match="not installed") as raised:
        _NOTES[target](GhTracker(Path("/p"), run, lambda line: None, retry=_quiet_policy()))
    assert isinstance(raised.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("target", ["comment", "label"])
def test_a_note_on_a_task_with_no_issue_calls_nothing(target):
    run = Outcomes()
    note = {
        "comment": lambda t: t.note("T-abc", "x"),
        "label": lambda t: t.drop_label("T-abc", "x"),
    }[target]
    assert note(GhTracker(Path("/p"), run)) is None and run.calls == []


@pytest.mark.parametrize("target", ["comment", "label"])
@pytest.mark.parametrize("code", [1, 4])
def test_a_refusal_with_no_policy_is_a_plain_value_error_and_raises_from_a_note(target, code):
    """Only the hierarchy and `TrackerRefused` are absorbed: with no policy `run_gh` keeps
    its plain `ValueError` (ADR-0050), an authentication failure (exit 4) included."""
    run = Outcomes(_failed_gh("no auth " + _SECRET, code))
    with pytest.raises(ValueError) as raised:
        _NOTES[target](GhTracker(Path("/p"), run, lambda line: None))
    assert type(raised.value) is ValueError


# -- dispatch_ci and dispatched_ci (GH-462) ------------------------------------------------

SHA = "a" * 40


def test_dispatch_ci_runs_the_workflow_on_the_ref_with_each_input_as_a_field():
    run = Recorder(stdout="")
    answer = GhTracker(Path("/p"), run).dispatch_ci("ci.yml", "master", {"sha": SHA})
    assert answer is None
    assert run.calls == [["gh", "workflow", "run", "ci.yml", "--ref", "master", "-f", f"sha={SHA}"]]


def test_dispatch_ci_with_no_ref_leaves_the_default_branch_to_gh():
    run = Recorder(stdout="")
    GhTracker(Path("/p"), run).dispatch_ci("ci.yml", "", {"sha": SHA})
    assert "--ref" not in run.calls[0]


def test_a_refused_dispatch_is_dispatch_refused_whatever_gh_printed():
    run = Recorder(fail=True)
    answer = GhTracker(Path("/p"), run).dispatch_ci("ci.yml", "master", {"sha": SHA})
    assert answer == CiUnreadable("dispatch_refused")
    assert len(run.calls) == 1  # not retried: no retry policy, and a refusal is final


def test_a_missing_gh_on_dispatch_is_unavailable_never_raised():
    answer = GhTracker(Path("/p"), Missing()).dispatch_ci("ci.yml", "master", {"sha": SHA})
    assert answer == CiUnreadable("gh_unavailable")


def test_a_gh_that_cannot_be_run_on_dispatch_is_unavailable_never_raised():
    answer = GhTracker(Path("/p"), NotExecutable()).dispatch_ci("ci.yml", "master", {"sha": SHA})
    assert answer == CiUnreadable("gh_unavailable")


def _listing(*runs):
    return json.dumps(
        [
            {
                "databaseId": run_id,
                "event": event,
                "displayTitle": title,
                "status": status,
                "createdAt": created,
                "url": f"https://github.com/o/r/actions/runs/{run_id}",
            }
            for run_id, event, title, status, created in runs
        ]
    )


def _jobs(*jobs):
    return json.dumps(
        {
            "jobs": [
                {"name": name, "status": status, "conclusion": conclusion, "url": f"u/{name}"}
                for name, status, conclusion in jobs
            ]
        }
    )


class Viewing(Recorder):
    """`gh run list` answers `listing`; `gh run view <id>` the jobs scripted per id."""

    def __init__(self, listing, jobs):
        super().__init__()
        self.listing = listing
        self.jobs = jobs

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        if args[2] == "list":
            return subprocess.CompletedProcess(args, 0, self.listing, "")
        return subprocess.CompletedProcess(args, 0, self.jobs[args[3]], "")


def test_dispatched_ci_lists_the_dispatch_runs_of_the_workflow_since_a_bound_with_their_jobs():
    run = Viewing(
        _listing((7, "workflow_dispatch", f"CI {SHA}", "completed", "2026-11-03T10:00:00Z")),
        {"7": _jobs(("unit", "completed", "success"), ("web", "in_progress", ""))},
    )
    answer = GhTracker(Path("/p"), run).dispatched_ci("ci.yml", "2026-11-03T09:58:00Z")
    assert answer == (
        DispatchedRun(
            run_id=7,
            event="workflow_dispatch",
            display_title=f"CI {SHA}",
            status="completed",
            created_at="2026-11-03T10:00:00Z",
            link="https://github.com/o/r/actions/runs/7",
            jobs=(
                CiCheck("unit", "COMPLETED", "SUCCESS", "u/unit"),
                CiCheck("web", "IN_PROGRESS", None, "u/web"),
            ),
        ),
    )
    listing, view = run.calls
    assert listing[:3] == ["gh", "run", "list"]
    assert listing[listing.index("--workflow") + 1] == "ci.yml"
    assert listing[listing.index("--event") + 1] == "workflow_dispatch"
    assert listing[listing.index("--created") + 1] == ">=2026-11-03T09:58:00Z"
    assert view == ["gh", "run", "view", "7", "--json", "jobs"]


def _job_with_steps(name, conclusion, steps):
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "url": f"u/{name}",
        "steps": steps,
    }


def _dispatched_jobs(*jobs):
    run = Viewing(
        _listing((9, "workflow_dispatch", f"CI {SHA}", "completed", "2026-11-03T10:00:00Z")),
        {"9": json.dumps({"jobs": list(jobs)})},
    )
    answer = GhTracker(Path("/p"), run).dispatched_ci("ci.yml", "2026-11-03T09:58:00Z")
    assert isinstance(answer, tuple)
    return answer[0].jobs


def _step(name, conclusion):
    return {"name": name, "status": "completed", "conclusion": conclusion, "number": 1}


@pytest.mark.parametrize(
    ("failing_step", "stage"),
    [
        ("Run actions/checkout@v7", "checkout"),
        ("Set up job", "setup"),
        ("Run uv run pytest", "suite"),
        ("Post Run actions/checkout@v7", "suite"),
    ],
)
def test_a_failed_step_of_a_dispatched_job_gives_the_stage_of_the_first_failure(
    failing_step, stage
):
    steps = [
        _step("Set up job", "success" if failing_step != "Set up job" else "failure"),
        _step("Run actions/checkout@v7", "success"),
        _step(failing_step, "failure"),
        _step("Complete job", "skipped"),
    ]
    (job,) = _dispatched_jobs(_job_with_steps("check", "failure", steps))
    assert job == CiCheck("check", "COMPLETED", "FAILURE", "u/check", failing_step)
    assert job.failed_stage == stage


def test_the_first_failing_step_decides_even_when_a_later_one_failed_too():
    steps = [
        _step("Run actions/checkout@v7", "failure"),
        _step("Run uv run pytest", "failure"),
    ]
    (job,) = _dispatched_jobs(_job_with_steps("check", "failure", steps))
    assert job.failed_stage == "checkout"


def test_a_job_without_a_failing_step_or_without_steps_has_no_stage_and_still_parses():
    green = [_step("Set up job", "success"), _step("Run actions/checkout@v7", "success")]
    with_steps, without_key = _dispatched_jobs(
        _job_with_steps("a", "success", green),
        {"name": "b", "status": "completed", "conclusion": "failure", "url": "u/b"},
    )
    assert with_steps.failed_stage is None
    assert without_key == CiCheck("b", "COMPLETED", "FAILURE", "u/b", None)


@pytest.mark.parametrize(
    "steps",
    [None, "steps", [1], [{"conclusion": "failure"}], [{"name": 3, "conclusion": "failure"}]],
    ids=["null", "string", "not-objects", "no-name", "name-not-text"],
)
def test_steps_it_cannot_read_leave_the_stage_unknown_and_never_make_the_job_unreadable(steps):
    (job,) = _dispatched_jobs(_job_with_steps("check", "failure", steps))
    assert job == CiCheck("check", "COMPLETED", "FAILURE", "u/check", None)


def test_a_dispatched_run_whose_only_failure_is_the_checkout_is_unreachable_without_a_return():
    """End to end over the adapter: the gh JSON of a failed checkout reaches
    `dispatch_verdict` as `unreachable/checkout_failed`, not red."""
    from agentic.core.process.domain import ci

    steps = [_step("Set up job", "success"), _step("Run actions/checkout@v7", "failure")]
    run = Viewing(
        _listing((9, "workflow_dispatch", f"CI {SHA}", "completed", "2026-11-03T10:00:00Z")),
        {"9": json.dumps({"jobs": [_job_with_steps("check", "failure", steps)]})},
    )
    answer = GhTracker(Path("/p"), run).dispatched_ci("ci.yml", "2026-11-03T09:58:00Z")
    assert isinstance(answer, tuple)
    verdict = ci.dispatch_verdict(answer, SHA, ("check",), 10.0, 180.0, 1500.0)
    assert (verdict.state, verdict.cause) == ("unreachable", "checkout_failed")


def test_a_push_run_that_names_the_sha_is_returned_as_what_it_is_and_the_verdict_drops_it():
    """The listing is not trusted to have filtered: the event stays on the run, and
    `ci.chosen_run` is what ignores a `push` whose title holds the sha."""
    from agentic.core.process.domain import ci

    run = Viewing(
        _listing((8, "push", f"fix {SHA}", "completed", "2026-11-03T10:00:00Z")),
        {"8": _jobs(("unit", "completed", "success"))},
    )
    answer = GhTracker(Path("/p"), run).dispatched_ci("ci.yml", "2026-11-03T09:58:00Z")
    assert isinstance(answer, tuple) and [r.event for r in answer] == ["push"]
    assert ci.chosen_run(answer, SHA) is None


@pytest.mark.parametrize(
    "listing",
    ["not json", json.dumps({"a": 1}), json.dumps([1]), json.dumps([{"databaseId": "7"}])],
    ids=["not-json", "not-a-list", "not-an-object", "bad-id"],
)
def test_dispatched_ci_never_reads_what_it_cannot_whole_as_runs(listing):
    run = Viewing(listing, {})
    answer = GhTracker(Path("/p"), run).dispatched_ci("ci.yml", "2026-11-03T09:58:00Z")
    assert answer == CiUnreadable("gh_unreadable")


def test_dispatched_ci_with_jobs_it_cannot_read_is_unreadable():
    run = Viewing(
        _listing((7, "workflow_dispatch", SHA, "completed", "t")), {"7": json.dumps({"jobs": [1]})}
    )
    assert GhTracker(Path("/p"), run).dispatched_ci("ci.yml", "s") == CiUnreadable("gh_unreadable")


def test_dispatched_ci_with_a_missing_gh_is_unavailable_and_a_failing_one_unreadable():
    assert GhTracker(Path("/p"), Missing()).dispatched_ci("ci.yml", "s") == CiUnreadable(
        "gh_unavailable"
    )
    assert GhTracker(Path("/p"), Recorder(fail=True)).dispatched_ci("ci.yml", "s") == CiUnreadable(
        "gh_unreadable"
    )


# -- GH-485: whether the commit's workflow runs are all completed ------------------------

_DONE = [_run_node("e2e (1)", "COMPLETED", "SUCCESS")]


@pytest.mark.parametrize(
    ("suites", "expected"),
    [
        (_suites("COMPLETED", "COMPLETED"), True),
        (_suites("COMPLETED", "IN_PROGRESS"), None),
        (_suites("QUEUED"), None),
        (_suites("COMPLETED", more=True), None),
        (_suites("COMPLETED", actions=False), None),
        (_suites(), None),
        (None, None),
        ({"pageInfo": {"hasNextPage": False}, "nodes": "x"}, None),
    ],
    ids=[
        "all-completed",
        "one-running",
        "queued",
        "more-pages",
        "no-actions-suite",
        "no-suites",
        "field-missing",
        "mistyped",
    ],
)
@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_runs_completed_is_true_only_for_a_whole_list_of_completed_actions_suites(
    read, suites, expected
):
    if read == "ci_report":
        stdout = _graphql(_DONE, suites=suites)
        report = GhTracker(Path("/p"), Recorder(stdout=stdout)).ci_report(PR)
    else:
        stdout = _branch_graphql(_DONE, suites=suites)
        report = GhTracker(Path("/p"), Recorder(stdout=stdout)).branch_ci("master")
    assert isinstance(report, CiReport)  # an absent or odd suites field is not unreadable
    assert report.runs_completed is expected


def _suite(run_id, status, conclusion=None, actions=True):
    workflow_run = {"databaseId": run_id} if actions else None
    return {"status": status, "conclusion": conclusion, "workflowRun": workflow_run}


def _read_ci(read, suites):
    page = {"pageInfo": {"hasNextPage": False}, "nodes": suites}
    if read == "ci_report":
        return GhTracker(Path("/p"), Recorder(stdout=_graphql(_DONE, suites=page))).ci_report(PR)
    stdout = _branch_graphql(_DONE, suites=page)
    return GhTracker(Path("/p"), Recorder(stdout=stdout)).branch_ci("master")


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_a_cancelled_suite_fills_cancelled_runs_and_a_running_one_running_runs(read):
    """GH-539: a payload written by hand to the GraphQL schema (no `gh` to read a real one)."""
    suites = [
        _suite(42393982482, "COMPLETED", "CANCELLED"),
        _suite(42394295598, "IN_PROGRESS"),
        _suite(7, "QUEUED"),
        _suite(8, "COMPLETED", "SUCCESS"),
        _suite(9, "COMPLETED", "CANCELLED", actions=False),  # another app's suite
    ]
    report = _read_ci(read, suites)
    assert isinstance(report, CiReport)
    assert report.cancelled_runs == frozenset({"42393982482"})
    assert report.running_runs == frozenset({"42394295598", "7"})


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
@pytest.mark.parametrize(
    "suites",
    [
        None,
        {"pageInfo": {"hasNextPage": False}},
        {"pageInfo": {"hasNextPage": False}, "nodes": "x"},
        {"pageInfo": {"hasNextPage": False}, "nodes": [{"status": "COMPLETED"}, "x", None]},
        {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"status": "COMPLETED", "conclusion": "CANCELLED", "workflowRun": {}}],
        },
        {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"status": 3, "conclusion": "CANCELLED", "workflowRun": {"databaseId": "12"}}
            ],
        },
    ],
    ids=["no-field", "no-nodes", "mistyped", "no-workflow-run", "no-id", "mistyped-id"],
)
def test_an_absent_or_mistyped_suite_field_leaves_both_sets_empty_and_is_not_unreadable(
    read, suites
):
    stdout = (
        _graphql(_DONE, suites=suites)
        if read == "ci_report"
        else _branch_graphql(_DONE, suites=suites)
    )
    tracker = GhTracker(Path("/p"), Recorder(stdout=stdout))
    report = tracker.ci_report(PR) if read == "ci_report" else tracker.branch_ci("master")
    assert isinstance(report, CiReport)
    assert report.cancelled_runs == frozenset()
    assert report.running_runs == frozenset()


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_the_ci_query_asks_for_the_conclusion_of_each_check_suite(read):
    run = Recorder(stdout=_graphql([]) if read == "ci_report" else _branch_graphql([]))
    getattr(GhTracker(Path("/p"), run), read)(PR if read == "ci_report" else "master")
    assert any("nodes { status conclusion workflowRun" in arg for arg in run.calls[0])


@pytest.mark.parametrize("read", ["ci_report", "branch_ci"])
def test_the_ci_query_asks_for_the_check_suites_of_the_same_commit(read):
    run = Recorder(stdout=_graphql([]) if read == "ci_report" else _branch_graphql([]))
    getattr(GhTracker(Path("/p"), run), read)(PR if read == "ci_report" else "master")
    assert any("checkSuites(first: 50)" in arg and "workflowRun" in arg for arg in run.calls[0])


# -- rerun_ci_run (GH-463) ------------------------------------------------------------------


def test_rerun_ci_run_reruns_the_failed_jobs_of_that_run_and_nothing_else():
    run = Recorder(stdout="")
    assert GhTracker(Path("/p"), run).rerun_ci_run("123456789") is None
    assert run.calls == [["gh", "run", "rerun", "123456789", "--failed"]]


def test_a_refused_rerun_is_dispatch_refused_whatever_gh_printed():
    answer = GhTracker(Path("/p"), Recorder(fail=True)).rerun_ci_run("123456789")
    assert answer == CiUnreadable("dispatch_refused")


def test_a_missing_gh_on_rerun_is_unavailable_never_raised():
    assert GhTracker(Path("/p"), Missing()).rerun_ci_run("1") == CiUnreadable("gh_unavailable")
    assert GhTracker(Path("/p"), NotExecutable()).rerun_ci_run("1") == CiUnreadable(
        "gh_unavailable"
    )


@pytest.mark.parametrize("run_id", ["", "abc", "12/34", "-1", "12 --web"])
def test_a_run_id_that_is_not_digits_is_refused_before_any_gh_call(run_id):
    run = Recorder(stdout="")
    assert GhTracker(Path("/p"), run).rerun_ci_run(run_id) == CiUnreadable("dispatch_refused")
    assert run.calls == []


# -- the instants of a check run and of a dispatched job (GH-467) --------------------------------


def test_the_rollup_asks_for_the_times_of_a_check_run_and_reads_them_when_given():
    timed = {
        **_run_node("web", "COMPLETED", "SUCCESS"),
        "startedAt": "2026-11-06T10:00:20Z",
        "completedAt": "2026-11-06T10:03:20Z",
    }
    status = {"__typename": "StatusContext", "context": "ci/legacy", "state": "SUCCESS"}
    run = Recorder(stdout=_graphql([timed, _run_node("e2e", "QUEUED", None), status]))
    report = GhTracker(Path("/p"), run).ci_report(PR)
    assert isinstance(report, CiReport)
    web, e2e, legacy = report.checks
    assert (web.started_at, web.completed_at) == ("2026-11-06T10:00:20Z", "2026-11-06T10:03:20Z")
    # A node without times, and a commit status, which has none: unknown, never an error.
    assert (e2e.started_at, e2e.completed_at) == (None, None)
    assert (legacy.started_at, legacy.completed_at) == (None, None)
    query = next(arg for arg in run.calls[0] if "statusCheckRollup" in arg)
    assert "startedAt completedAt" in query


def test_a_dispatched_job_carries_its_times_and_gh_s_zero_value_reads_as_unknown():
    jobs = _dispatched_jobs(
        {
            "name": "unit",
            "status": "completed",
            "conclusion": "success",
            "url": "u/unit",
            "startedAt": "2026-11-06T10:00:20Z",
            "completedAt": "2026-11-06T10:03:20Z",
        },
        {
            "name": "web",
            "status": "in_progress",
            "conclusion": "",
            "url": "u/web",
            "startedAt": "2026-11-06T10:00:30Z",
            "completedAt": "0001-01-01T00:00:00Z",
        },
        {"name": "old", "status": "completed", "conclusion": "success", "url": "u/old"},
    )
    unit, web, old = jobs
    assert (unit.started_at, unit.completed_at) == ("2026-11-06T10:00:20Z", "2026-11-06T10:03:20Z")
    assert (web.started_at, web.completed_at) == ("2026-11-06T10:00:30Z", None)
    assert (old.started_at, old.completed_at) == (None, None)


class _StepsRun:
    """A `run` that answers by what it is asked: the steps query by its `id`, the rest alike."""

    def __init__(self, ci, steps=None):
        self.calls = []
        self.ci = ci
        self.steps = steps

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        asked_steps = any(str(a).startswith("id=") for a in args)
        out = self.steps if asked_steps else self.ci
        return subprocess.CompletedProcess(args, 0, out, "")

    @property
    def step_queries(self):
        return [c for c in self.calls if any(str(a).startswith("id=") for a in c)]


def _steps_answer(*steps, more=False):
    nodes = [{"name": name, "conclusion": conclusion} for name, conclusion in steps]
    page = {"nodes": nodes, "pageInfo": {"hasNextPage": more}}
    return json.dumps({"data": {"node": {"steps": page}}})


DECLARED = ApiBreakLine("Breaking-API:", "docs", "api_breaks")


def _docs_report(run, declared=DECLARED):
    nodes = [_run_node("docs", "COMPLETED", "FAILURE"), _run_node("tests", "COMPLETED", "FAILURE")]
    run.ci = _graphql(nodes)
    return GhTracker(Path("/p"), run, api_break_line=lambda: declared).ci_report(PR)


def test_the_red_check_the_stack_names_gets_its_failed_step_from_the_steps_query():
    run = _StepsRun("", _steps_answer(("Set up job", "SUCCESS"), ("api_breaks", "FAILURE")))
    report = _docs_report(run)
    by_name = {c.name: c for c in report.checks}
    assert by_name["docs"].failed_step == "api_breaks"
    assert by_name["tests"].failed_step is None
    assert len(run.step_queries) == 1 and "id=id-docs" in run.step_queries[0]


def test_a_first_page_cut_without_a_failing_step_reads_no_step():
    run = _StepsRun("", _steps_answer(("Set up job", "SUCCESS"), more=True))
    assert _docs_report(run).checks[0].failed_step is None


@pytest.mark.parametrize("answer", ["not json", "{}", '{"data": {"node": null}}'])
def test_an_unreadable_steps_answer_reads_no_step_and_the_report_stays_readable(answer):
    report = _docs_report(_StepsRun("", answer))
    assert isinstance(report, CiReport) and report.checks[0].failed_step is None


def test_without_the_declaration_no_steps_query_is_made():
    run = _StepsRun("", _steps_answer(("api_breaks", "FAILURE")))
    report = _docs_report(run, declared=None)
    assert run.step_queries == [] and report.checks[0].failed_step is None


def test_a_green_check_of_the_declared_name_is_not_asked():
    run = _StepsRun("", _steps_answer(("api_breaks", "FAILURE")))
    run.ci = _graphql([_run_node("docs", "COMPLETED", "SUCCESS")])
    GhTracker(Path("/p"), run, api_break_line=lambda: DECLARED).ci_report(PR)
    assert run.step_queries == []


def test_pull_request_body_reads_the_body_of_the_number():
    run = Recorder(stdout=json.dumps({"body": "Breaking-API: x\n\nrest"}))
    assert GhTracker(Path("/p"), run).pull_request_body(PR) == "Breaking-API: x\n\nrest"
    assert run.calls[0][:4] == ["gh", "pr", "view", "7"]


@pytest.mark.parametrize("stdout", ["not json", "{}", '{"body": null}', "[]"])
def test_an_unreadable_body_raises_instead_of_reading_as_empty(stdout):
    with pytest.raises(ValueError):
        GhTracker(Path("/p"), Recorder(stdout=stdout)).pull_request_body(PR)


# -- GH-601: the watched branch read, whose suites also say how many check runs they have --


def _counted_suite(run_id, status, total):
    """An Actions suite as the watched query returns it: `total` is its `checkRuns.totalCount`
    (`None` leaves the field out)."""
    suite = _suite(run_id, status)
    if total is not None:
        suite["checkRuns"] = {"totalCount": total}
    return suite


def _watched(suites, *, whole=True):
    page = {"pageInfo": {"hasNextPage": not whole}, "nodes": suites}
    stdout = _branch_graphql(_DONE, suites=page)
    return GhTracker(Path("/p"), Recorder(stdout=stdout)).watched_branch_ci("master")


@pytest.mark.parametrize(
    ("status", "total", "expected"),
    [
        ("QUEUED", 0, frozenset({"7"})),
        ("QUEUED", 1, frozenset()),
        ("IN_PROGRESS", 0, frozenset()),
        ("WAITING", 0, frozenset()),
        ("PENDING", 0, frozenset()),
        ("REQUESTED", 0, frozenset()),
        ("COMPLETED", 0, frozenset()),
    ],
)
def test_watched_branch_ci_lists_only_a_queued_suite_with_no_check_runs(status, total, expected):
    report = _watched([_counted_suite(7, status, total)])
    assert isinstance(report, CiReport)
    assert report.queued_empty_runs == expected


def test_watched_branch_ci_lists_the_queued_empty_runs_beside_the_running_ones():
    report = _watched([_counted_suite(7, "QUEUED", 0), _counted_suite(8, "QUEUED", 2)])
    assert isinstance(report, CiReport)
    assert report.running_runs == frozenset({"7", "8"})
    assert report.queued_empty_runs == frozenset({"7"})


@pytest.mark.parametrize(
    "suite",
    [
        {"status": "QUEUED", "workflowRun": {"databaseId": 7}},
        {"status": "QUEUED", "workflowRun": {"databaseId": 7}, "checkRuns": None},
        {"status": "QUEUED", "workflowRun": {"databaseId": 7}, "checkRuns": {}},
        {"status": "QUEUED", "workflowRun": {"databaseId": 7}, "checkRuns": {"totalCount": "0"}},
        {"status": "QUEUED", "workflowRun": {"databaseId": 7}, "checkRuns": {"totalCount": False}},
        {"status": "QUEUED", "workflowRun": {"databaseId": 7}, "checkRuns": [0]},
        {"status": "QUEUED", "workflowRun": {"databaseId": "7"}, "checkRuns": {"totalCount": 0}},
        {"status": "QUEUED", "workflowRun": None, "checkRuns": {"totalCount": 0}},
        {"status": 3, "workflowRun": {"databaseId": 7}, "checkRuns": {"totalCount": 0}},
        "not a suite",
    ],
    ids=[
        "no-check-runs",
        "check-runs-null",
        "no-total",
        "total-is-text",
        "total-is-bool",
        "check-runs-is-list",
        "run-id-is-text",
        "not-actions",
        "status-is-int",
        "node-is-text",
    ],
)
def test_a_field_the_watched_read_cannot_parse_adds_nothing_and_stays_readable(suite):
    report = _watched([suite])
    assert isinstance(report, CiReport)  # never a CiUnreadable
    assert report.queued_empty_runs == frozenset()


def test_a_cut_suites_page_lists_no_queued_empty_run_even_with_a_queued_empty_suite():
    # GH-601: a page cut by `hasNextPage` can hide the live suite, so it never makes a ghost.
    report = _watched([_counted_suite(7, "QUEUED", 0)], whole=False)
    assert isinstance(report, CiReport)
    assert report.running_runs == frozenset({"7"})
    assert report.queued_empty_runs == frozenset()


def test_watched_branch_ci_without_a_suites_field_is_a_readable_report_with_nothing_queued():
    stdout = _branch_graphql(_DONE)
    report = GhTracker(Path("/p"), Recorder(stdout=stdout)).watched_branch_ci("master")
    assert isinstance(report, CiReport) and report.queued_empty_runs == frozenset()


def test_watched_branch_ci_is_branch_ci_plus_the_queued_empty_runs():
    page = {"pageInfo": {"hasNextPage": False}, "nodes": [_counted_suite(7, "QUEUED", 0)]}
    stdout = _branch_graphql(_DONE, suites=page)
    plain = GhTracker(Path("/p"), Recorder(stdout=stdout)).branch_ci("master")
    watched = GhTracker(Path("/p"), Recorder(stdout=stdout)).watched_branch_ci("master")
    assert isinstance(plain, CiReport) and isinstance(watched, CiReport)
    assert watched == replace(plain, queued_empty_runs=frozenset({"7"}))


def test_branch_ci_still_leaves_queued_empty_runs_empty_on_a_queued_suite_with_no_check_runs():
    page = {"pageInfo": {"hasNextPage": False}, "nodes": [_counted_suite(7, "QUEUED", 0)]}
    stdout = _branch_graphql(_DONE, suites=page)
    report = GhTracker(Path("/p"), Recorder(stdout=stdout)).branch_ci("master")
    assert isinstance(report, CiReport) and report.queued_empty_runs == frozenset()


@pytest.mark.parametrize(
    "stdout",
    [json.dumps({"data": {"repository": {"ref": None}}}), "not json"],
    ids=["no-branch", "not-json"],
)
def test_watched_branch_ci_that_cannot_be_read_is_unreadable(stdout):
    read = GhTracker(Path("/p"), Recorder(stdout=stdout)).watched_branch_ci("gone")
    assert read == CiUnreadable("gh_unreadable")


def test_watched_branch_ci_asks_the_watched_query_for_the_branch():
    run = Recorder(stdout=_branch_graphql(_DONE))
    GhTracker(Path("/p"), run).watched_branch_ci("master")
    [call] = run.calls
    assert "ref=refs/heads/master" in call
    assert "checkRuns(first: 0) { totalCount }" in " ".join(call)


def test_only_the_watched_query_counts_check_runs_per_suite():
    """The pull request and branch reads keep their shape (GH-601): the watched read is the one
    place a suite's check runs are counted."""
    assert "checkRuns" not in tracker_module._PULL_REQUEST_CI_QUERY
    assert "checkRuns" not in tracker_module._BRANCH_CI_QUERY
    assert "checkRuns" not in tracker_module._SUITES
    assert "checkRuns(first: 0) { totalCount }" in tracker_module._WATCHED_BRANCH_CI_QUERY


def test_close_issue_with_a_comment_is_one_gh_call_carrying_it():
    run = Recorder()
    GhTracker(Path("/p"), run).close_issue("GH-12", comment="All parts merged")
    assert run.calls == [
        ["gh", "issue", "close", "12", "--reason", "completed", "--comment", "All parts merged"]
    ]


# --- the access keyword (GH-1091, ADR-0107) ----------------------------------------------


def _spied_access(monkeypatch) -> list:
    seen: list = []
    real = tracker_module.run_gh

    def spy(*args, **kwargs):
        seen.append(kwargs["access"])
        return real(*args, **kwargs)

    monkeypatch.setattr(tracker_module, "run_gh", spy)
    return seen


def test_a_tracker_passes_its_access_to_every_run_gh_call(monkeypatch):
    seen = _spied_access(monkeypatch)
    access = GithubAccess()
    tracker = GhTracker(Path("/p"), Recorder(stdout=_http(201)), access=access)
    tracker.comment("GH-12", "PR: x")  # a retrying write
    tracker.open_issue("T", "body")  # the door with no retry
    assert seen == [access, access]


def test_a_tracker_with_no_access_passes_none(monkeypatch):
    seen = _spied_access(monkeypatch)
    GhTracker(Path("/p"), Recorder(stdout=_http(201))).comment("GH-12", "PR: x")
    assert seen == [None]


class RefusingGate:
    """A fake `WriteGate` that refuses the turn of every write."""

    cap_s = 60.0

    def __init__(self, outcome="refused"):
        self.outcome = outcome
        self.asked = 0

    def take(self, remaining_s):
        self.asked += 1
        return Turn(self.outcome, 0.0)


def test_a_refused_turn_on_pr_merge_raises_turn_refused_not_gh_unreadable_and_never_runs_it():
    run = MethodRecorder(True, True, True)
    gate = RefusingGate()
    tracker = GhTracker(Path("/p"), run, log=lambda line: None, access=GithubAccess(gate=gate))
    with pytest.raises(TrackerTurnRefused) as raised:
        tracker.merge(PR_URL, "abc123")
    assert not isinstance(raised.value, MergeCallFailed)
    assert gate.asked == 1  # `repo view` is a read: only the merge asked for a turn
    assert [call[1:3] for call in run.calls] == [["repo", "view"]]  # `gh pr merge` never ran


def test_a_stopped_turn_on_pr_merge_is_refused_the_same_way():
    run = MethodRecorder(True, True, True)
    tracker = GhTracker(
        Path("/p"), run, log=lambda line: None, access=GithubAccess(gate=RefusingGate("stopped"))
    )
    with pytest.raises(TrackerTurnRefused):
        tracker.merge(PR_URL, "abc123")
    assert all(call[1:3] != ["pr", "merge"] for call in run.calls)


def test_a_refused_turn_makes_a_note_a_transient_failure_and_raises_nothing():
    run = Recorder()
    tracker = GhTracker(
        Path("/p"),
        run,
        log=lambda line: None,
        now=lambda: "2026-11-26T00:00:00+00:00",
        access=GithubAccess(gate=RefusingGate()),
    )
    failure = tracker.note("GH-7", "a note")
    assert failure is not None and failure.cls == "transient" and failure.target == "comment"
    assert run.calls == []


# -- GH-1151: a bounded page loop over the CI connections, and what `gh` said quoted -------------


def _commit_page(
    checks,
    *,
    next_checks=None,
    suites=None,
    next_suites=None,
    oid="c" * 40,
):
    """One page of a commit's CI. `next_checks` / `next_suites` are the `endCursor` a connection
    still has to follow (`None`: that connection is whole)."""
    contexts = {
        "nodes": checks,
        "pageInfo": {"hasNextPage": next_checks is not None, "endCursor": next_checks},
    }
    commit: dict = {"oid": oid, "statusCheckRollup": {"contexts": contexts}}
    if suites is not None:
        commit["checkSuites"] = {
            "nodes": suites,
            "pageInfo": {"hasNextPage": next_suites is not None, "endCursor": next_suites},
        }
    return commit


def _page_suite(run_id, status="COMPLETED", conclusion="SUCCESS", checks=1):
    return {
        "status": status,
        "conclusion": conclusion,
        "workflowRun": {"databaseId": run_id},
        "checkRuns": {"totalCount": checks},
    }


def _as_pull_request(commit):
    pull_request = {
        "createdAt": "2026-10-07T10:00:00Z",
        "baseRefName": "master",
        "headRefOid": commit["oid"],
        "commits": {"nodes": [{"commit": commit}]},
    }
    return {"data": {"repository": {"pullRequest": pull_request}}}


def _as_branch(commit):
    return {"data": {"repository": {"ref": {"target": commit}}}}


# (how a page is wrapped, how the reader is asked): the three readers that follow the pages.
_PAGED_READERS = pytest.mark.parametrize(
    ("wrap", "read"),
    [
        (_as_pull_request, lambda tracker: tracker.ci_report(PR)),
        (_as_branch, lambda tracker: tracker.branch_ci("master")),
        (_as_branch, lambda tracker: tracker.watched_branch_ci("master")),
    ],
    ids=["ci_report", "branch_ci", "watched_branch_ci"],
)


def _scripted(wrap, *pages):
    return ScriptedRun(*(_plain(json.dumps(wrap(page))) for page in pages))


def _after(argv, variable):
    """The value `-f <variable>=...` carried in one `gh` call, `None` when it had none."""
    values = [arg.split("=", 1)[1] for arg in argv if arg.startswith(f"{variable}=")]
    return values[0] if values else None


@_PAGED_READERS
def test_a_whole_first_page_is_one_call_with_no_cursor(wrap, read):
    run = _scripted(wrap, _commit_page([_run_node("ci-ok", "COMPLETED", "SUCCESS")], suites=[]))
    report = read(GhTracker(Path("/p"), run))
    assert isinstance(report, CiReport) and [c.name for c in report.checks] == ["ci-ok"]
    [argv] = run.argv
    assert _after(argv, "contextsAfter") is None and _after(argv, "suitesAfter") is None


@_PAGED_READERS
def test_checks_on_a_second_page_are_merged_into_one_report(wrap, read):
    first = _commit_page([_run_node("unit", "COMPLETED", "SUCCESS")], next_checks="cursor-1")
    second = _commit_page([_run_node("ci-ok", "COMPLETED", "FAILURE")])
    run = _scripted(wrap, first, second)
    report = read(GhTracker(Path("/p"), run))
    assert isinstance(report, CiReport)
    assert [(c.name, c.conclusion) for c in report.checks] == [
        ("unit", "SUCCESS"),
        ("ci-ok", "FAILURE"),
    ]
    assert [_after(argv, "contextsAfter") for argv in run.argv] == [None, "cursor-1"]


@_PAGED_READERS
def test_suites_on_a_second_page_are_merged_before_the_runs_are_read(wrap, read):
    first = _commit_page(
        [_run_node("unit", "COMPLETED", "SUCCESS")],
        suites=[_page_suite(1)],
        next_suites="suite-cursor",
    )
    second = _commit_page(
        [_run_node("unit", "COMPLETED", "SUCCESS")],
        suites=[_page_suite(2, conclusion="CANCELLED")],
    )
    run = _scripted(wrap, first, second)
    report = read(GhTracker(Path("/p"), run))
    assert isinstance(report, CiReport)
    assert report.cancelled_runs == frozenset({"2"}) and report.runs_completed is True
    # The checks were whole on page one: only the suites asked for a next page, and the checks
    # the second answer repeats are not added twice.
    assert [c.name for c in report.checks] == ["unit"]
    assert [_after(argv, "suitesAfter") for argv in run.argv] == [None, "suite-cursor"]
    assert _after(run.argv[1], "contextsAfter") is None


@_PAGED_READERS
def test_both_connections_are_followed_together_one_call_per_page(wrap, read):
    pages = [
        _commit_page(
            [_run_node(f"c{n}", "COMPLETED", "SUCCESS")],
            next_checks=f"k{n}" if n < 3 else None,
            suites=[_page_suite(n)],
            next_suites=f"s{n}" if n < 2 else None,
        )
        for n in (1, 2, 3)
    ]
    run = _scripted(wrap, *pages)
    report = read(GhTracker(Path("/p"), run))
    assert isinstance(report, CiReport) and [c.name for c in report.checks] == ["c1", "c2", "c3"]
    assert len(run.argv) == 3


@_PAGED_READERS
def test_more_than_five_pages_of_checks_is_unreadable_naming_the_bound_never_a_part(wrap, read):
    pages = [
        _commit_page([_run_node(f"c{n}", "COMPLETED", "FAILURE")], next_checks=f"k{n}")
        for n in range(1, 7)
    ]
    run = _scripted(wrap, *pages)
    got = read(GhTracker(Path("/p"), run))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable) and "contexts" in got.detail and "5 pages" in got.detail
    assert len(run.argv) == 5  # no sixth call


@_PAGED_READERS
def test_five_pages_of_checks_that_end_on_the_fifth_are_read_whole(wrap, read):
    pages = [
        _commit_page(
            [_run_node(f"c{n}", "COMPLETED", "SUCCESS")], next_checks=f"k{n}" if n < 5 else None
        )
        for n in range(1, 6)
    ]
    report = read(GhTracker(Path("/p"), _scripted(wrap, *pages)))
    assert isinstance(report, CiReport) and len(report.checks) == 5


@_PAGED_READERS
def test_more_than_three_pages_of_suites_is_unreadable_naming_the_bound(wrap, read):
    pages = [
        _commit_page(
            [_run_node("unit", "COMPLETED", "SUCCESS")],
            suites=[_page_suite(n)],
            next_suites=f"s{n}",
        )
        for n in range(1, 5)
    ]
    run = _scripted(wrap, *pages)
    got = read(GhTracker(Path("/p"), run))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable) and "checkSuites" in got.detail and "3 pages" in got.detail
    assert len(run.argv) == 3


@_PAGED_READERS
def test_a_cut_list_of_checks_with_no_cursor_is_unreadable_never_followed(wrap, read):
    page = _commit_page([_run_node("unit", "COMPLETED", "SUCCESS")], next_checks="x")
    page["statusCheckRollup"]["contexts"]["pageInfo"]["endCursor"] = None
    run = _scripted(wrap, page)
    assert read(GhTracker(Path("/p"), run)) == CiUnreadable("gh_unreadable")
    assert len(run.argv) == 1


@_PAGED_READERS
def test_a_later_page_that_says_more_and_gives_no_cursor_is_unreadable_never_whole(wrap, read):
    first = _commit_page([_run_node("unit", "COMPLETED", "SUCCESS")], next_checks="k1")
    second = _commit_page([_run_node("ci-ok", "COMPLETED", "FAILURE")], next_checks="x")
    second["statusCheckRollup"]["contexts"]["pageInfo"]["endCursor"] = None
    run = _scripted(wrap, first, second)
    got = read(GhTracker(Path("/p"), run))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable) and "cursor" in got.detail
    assert len(run.argv) == 2


@_PAGED_READERS
def test_a_next_page_of_another_commit_is_unreadable_never_mixed(wrap, read):
    first = _commit_page([_run_node("unit", "COMPLETED", "SUCCESS")], next_checks="k1")
    second = _commit_page([_run_node("web", "COMPLETED", "SUCCESS")], oid="d" * 40)
    assert read(GhTracker(Path("/p"), _scripted(wrap, first, second))) == CiUnreadable(
        "gh_unreadable"
    )


@_PAGED_READERS
def test_a_next_page_that_cannot_be_read_is_that_unreadable_never_the_first_page_alone(wrap, read):
    first = _commit_page([_run_node("unit", "COMPLETED", "SUCCESS")], next_checks="k1")
    run = ScriptedRun(_plain(json.dumps(wrap(first))), _plain("<html>502 Bad Gateway</html>"))
    got = read(GhTracker(Path("/p"), run))
    assert isinstance(got, CiUnreadable) and "502 Bad Gateway" in got.detail


def test_the_three_queries_that_follow_pages_ask_for_the_cursor_and_the_next_page_takes_it():
    for query in (
        tracker_module._PULL_REQUEST_CI_QUERY,
        tracker_module._BRANCH_CI_QUERY,
        tracker_module._WATCHED_BRANCH_CI_QUERY,
    ):
        assert query.count("endCursor") == 2 and "after:" not in query
        after = tracker_module._page_after(query)
        assert "$contextsAfter: String, $suitesAfter: String" in after
        assert "contexts(first: 100, after: $contextsAfter)" in after
        assert "checkSuites(first: 50, after: $suitesAfter)" in after


@_PAGED_READERS
def test_non_json_from_gh_is_unreadable_and_quotes_what_gh_returned(wrap, read):
    got = read(GhTracker(Path("/p"), Recorder(stdout="<html>502 Bad Gateway</html>")))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable) and got.detail == "<html>502 Bad Gateway</html>"


@_PAGED_READERS
def test_a_gh_that_exits_non_zero_quotes_the_first_line_of_its_message_sanitised(wrap, read):
    stderr = "HTTP 401: bad credentials ghp_AbC123xyz `oops`\nsecond line"
    got = read(GhTracker(Path("/p"), ScriptedRun(RecordingRun("", stderr, 1))))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable)
    assert got.detail == "gh api graphql failed: HTTP 401: bad credentials [redacted] oops"


@_PAGED_READERS
def test_null_data_is_unreadable_and_says_what_it_could_not_find(wrap, read):
    got = read(GhTracker(Path("/p"), Recorder(stdout=json.dumps({"data": None}))))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable) and got.detail != ""


@_PAGED_READERS
def test_a_cancelled_check_run_with_every_nullable_field_null_is_a_readable_failure_input(
    wrap, read
):
    """GH-1133's shape, pinned: `conclusion`, `detailsUrl` and both times may be null on a check
    run cut at its limit; none of them makes the read unreadable."""
    cancelled = {
        "__typename": "CheckRun",
        "id": "id-1",
        "name": "e2e-shard (1)",
        "status": "COMPLETED",
        "conclusion": "CANCELLED",
        "detailsUrl": None,
        "startedAt": None,
        "completedAt": None,
    }
    nodes = [cancelled, _run_node("ci-ok", "COMPLETED", "FAILURE")]
    report = read(
        GhTracker(Path("/p"), _scripted(wrap, _commit_page(nodes, suites=[_page_suite(1)])))
    )
    assert isinstance(report, CiReport)
    assert report.checks[0] == CiCheck("e2e-shard (1)", "COMPLETED", "CANCELLED", "")
    assert report.checks[1].conclusion == "FAILURE" and report.runs_completed is True


@_PAGED_READERS
def test_a_check_run_with_a_null_name_is_unreadable_naming_the_field(wrap, read):
    nameless = {**_run_node("x", "COMPLETED", "CANCELLED"), "name": None}
    got = read(GhTracker(Path("/p"), _scripted(wrap, _commit_page([nameless]))))
    assert got == CiUnreadable("gh_unreadable")
    assert isinstance(got, CiUnreadable) and "check field" in got.detail
