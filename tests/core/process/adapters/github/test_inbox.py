import json
import subprocess

import pytest

from agentic.core.process.adapters.github import GhInbox
from agentic.core.process.adapters.github import inbox as module


def fake_gh(monkeypatch, *, stdout="", returncode=0, stderr=""):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def test_issue_feeds_title_body_and_id(monkeypatch, tmp_path):
    calls = fake_gh(
        monkeypatch, stdout=json.dumps({"title": " Metrics ", "body": "How is my gym\n"})
    )
    task = GhInbox(tmp_path).task(12)
    assert task == {"id": "GH-12", "title": "Metrics", "description": "How is my gym"}
    ((cmd, kwargs),) = calls
    assert cmd[:4] == ["gh", "issue", "view", "12"]
    assert kwargs["cwd"] == tmp_path  # gh resolves the repo from the product checkout


def test_the_task_id_and_the_issue_map_both_ways_without_gh(tmp_path):
    inbox = GhInbox(tmp_path)
    assert inbox.task_id(12) == "GH-12" and inbox.issue_of("GH-12") == 12
    assert inbox.issue_of("T-abc") is None


def test_issue_without_body_is_rejected(monkeypatch, tmp_path):
    fake_gh(monkeypatch, stdout=json.dumps({"title": "Empty", "body": None}))
    with pytest.raises(ValueError, match="no body"):
        GhInbox(tmp_path).task(3)


def test_gh_failure_carries_its_stderr(monkeypatch, tmp_path):
    fake_gh(monkeypatch, returncode=1, stderr="GraphQL: Could not resolve to an Issue")
    with pytest.raises(ValueError, match="Could not resolve"):
        GhInbox(tmp_path).task(999)


def test_missing_gh_is_explained(monkeypatch, tmp_path):
    def run(cmd, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="gh is not installed"):
        GhInbox(tmp_path).task(1)


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


def test_task_still_uses_the_bare_subprocess_call_when_run_is_injected(monkeypatch, tmp_path):
    # `task()` keeps its own inline `subprocess.run` (ADR-0034: old form stays old
    # form); an injected `run` must not silently reroute it.
    calls = fake_gh(monkeypatch, stdout=json.dumps({"title": "T", "body": "d"}))
    never_called = _Recorder()
    task = GhInbox(tmp_path, run=never_called).task(1)
    assert task == {"id": "GH-1", "title": "T", "description": "d"}
    assert calls and never_called.calls == []


def test_open_issue_bodies_is_the_same_unfiltered_read_as_open_issues(tmp_path):
    run = _Recorder(stdout=json.dumps([{"url": "https://x/1", "body": "b1"}]))
    assert GhInbox(tmp_path, run=run).open_issue_bodies() == [("https://x/1", "b1")]
    [call] = run.calls
    assert "--search" not in call


def test_open_issue_bodies_failure_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="gh issue list failed: no auth"):
        GhInbox(tmp_path, run=_Recorder(fail=True, stderr="no auth")).open_issue_bodies()
