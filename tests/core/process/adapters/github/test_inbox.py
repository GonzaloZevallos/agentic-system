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
