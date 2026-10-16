import json
import subprocess

import pytest

from agentic.core.process.adapters.github import GhInbox
from agentic.core.process.application.ports import TrackerTimedOut


def fake_gh(*, stdout="", returncode=0, stderr=""):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return calls, run


def test_issue_feeds_title_body_and_id(tmp_path):
    calls, run = fake_gh(stdout=json.dumps({"title": " Metrics ", "body": "How is my gym\n"}))
    task = GhInbox(tmp_path, run=run).task(12)
    assert task == {"id": "GH-12", "title": "Metrics", "description": "How is my gym"}
    ((cmd, kwargs),) = calls
    assert cmd[:4] == ["gh", "issue", "view", "12"]
    assert kwargs["cwd"] == tmp_path  # gh resolves the repo from the product checkout


def test_the_task_id_and_the_issue_map_both_ways_without_gh(tmp_path):
    inbox = GhInbox(tmp_path)
    assert inbox.task_id(12) == "GH-12" and inbox.issue_of("GH-12") == 12
    assert inbox.issue_of("T-abc") is None


def test_issue_without_body_is_rejected(tmp_path):
    _, run = fake_gh(stdout=json.dumps({"title": "Empty", "body": None}))
    with pytest.raises(ValueError, match="no body"):
        GhInbox(tmp_path, run=run).task(3)


def test_gh_failure_carries_its_stderr(tmp_path):
    _, run = fake_gh(returncode=1, stderr="GraphQL: Could not resolve to an Issue")
    with pytest.raises(
        ValueError, match="^gh could not read issue #999: GraphQL: Could not resolve to an Issue$"
    ):
        GhInbox(tmp_path, run=run).task(999)


def test_missing_gh_is_explained(tmp_path):
    def run(cmd, **kwargs):
        raise FileNotFoundError("gh")

    with pytest.raises(ValueError, match="^gh is not installed; --issue needs the GitHub CLI$"):
        GhInbox(tmp_path, run=run).task(1)


class _Recorder:
    def __init__(self, stdout="[]", fail=False, stderr=""):
        self.calls = []
        self.stdout = stdout
        self.fail = fail
        self.stderr = stderr

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(1, args, stderr=self.stderr)
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


def test_open_issues_lists_open_issues_as_url_body_pairs_never_searching(tmp_path):
    run = _Recorder(
        stdout=json.dumps([{"url": "https://x/1", "body": "b1"}, {"url": "https://x/2"}])
    )
    inbox = GhInbox(tmp_path, run=run)
    assert inbox.open_issues() == [("https://x/1", "b1"), ("https://x/2", "")]
    [call] = run.calls
    assert call[:3] == ["gh", "issue", "list"]
    assert "--state" in call and "open" in call
    assert "--json" in call and "url,body" in call
    assert "--search" not in call


def test_open_issues_failure_is_a_value_error(tmp_path):
    run = _Recorder(fail=True, stderr="no auth")
    with pytest.raises(ValueError, match="gh issue list failed: no auth"):
        GhInbox(tmp_path, run=run).open_issues()


def test_task_goes_through_the_injected_run_like_open_issues(tmp_path):
    run = _Recorder(stdout=json.dumps({"title": "T", "body": "d"}))
    task = GhInbox(tmp_path, run=run).task(1)
    assert task == {"id": "GH-1", "title": "T", "description": "d"}
    assert run.calls == [["gh", "issue", "view", "1", "--json", "title,body"]]


def test_open_issue_bodies_is_the_same_unfiltered_read_as_open_issues(tmp_path):
    run = _Recorder(stdout=json.dumps([{"url": "https://x/1", "body": "b1"}]))
    assert GhInbox(tmp_path, run=run).open_issue_bodies() == [("https://x/1", "b1")]
    [call] = run.calls
    assert "--search" not in call


def test_open_issue_bodies_failure_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="gh issue list failed: no auth"):
        GhInbox(tmp_path, run=_Recorder(fail=True, stderr="no auth")).open_issue_bodies()


def test_a_follow_up_is_found_locally_from_the_marker_in_the_open_issue_bodies(tmp_path):
    from agentic.core.process.domain import landing

    marker = landing.followup_marker("GH-5", 1)
    run = _Recorder(
        stdout=json.dumps(
            [
                {"url": "https://x/1", "body": "unrelated"},
                {"url": "https://x/2", "body": f"{marker}\nOpen `important` findings"},
                {"url": "https://x/3", "body": landing.followup_marker("GH-6", 1)},
            ]
        )
    )
    bodies = GhInbox(tmp_path, run=run).open_issue_bodies()
    found = [landing.marker_lineage(body, "GH-5") for _, body in bodies]
    assert found == [None, 1, None]  # only the issue naming GH-5 matches, byte for byte
    assert all("--search" not in call for call in run.calls)


def _stalled(cmd, **kwargs):
    raise subprocess.TimeoutExpired(cmd, 60, output="the issue body", stderr="secret")


@pytest.mark.parametrize(
    ("call", "wording"),
    [
        (lambda inbox: inbox.task(229), "gh issue view timed out after 60 s"),
        (lambda inbox: inbox.open_issues(), "gh issue list timed out after 60 s"),
        (lambda inbox: inbox.open_issue_bodies(), "gh issue list timed out after 60 s"),
    ],
)
def test_a_stalled_gh_call_raises_tracker_timed_out_and_logs_one_line(tmp_path, call, wording):
    lines: list[str] = []
    inbox = GhInbox(tmp_path, run=_stalled, log=lines.append)
    with pytest.raises(TrackerTimedOut) as raised:
        call(inbox)
    assert str(raised.value) == wording
    assert lines == [f"agentic: {wording}; this read fails and the next round retries"]
    assert not any("secret" in line or "issue body" in line for line in lines)
