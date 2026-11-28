"""`run_gh(calls=)` and `stream_gh` count each attempted `gh` launch by kind (GH-990)."""

import json
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from agentic.core.process.adapters.github import run as run_module
from agentic.core.process.adapters.github.run import GhRetry, StreamRead, run_gh, stream_gh
from agentic.core.process.application.ports import TrackerTurnRefused, Turn
from agentic.core.process.domain.github_access import GithubAccess
from tests.core.process.adapters.github.gh_fake import StubGate


class MemoryLog:
    """An in-memory `GhCallLog` that keeps the order of the kinds recorded."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    def record(self, kind):
        self.kinds.append(kind)

    def drain(self):
        counts = Counter(self.kinds)
        self.kinds = []
        return counts


class ScriptedRun:
    """A `Run` that plays one answer per call: a string is stdout, an exception is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.argv: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.argv.append(list(args))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return subprocess.CompletedProcess(args, 0, answer, "")


RATE_LIMITED = subprocess.CalledProcessError(
    1, ["gh"], output="", stderr="gh: API rate limit exceeded (HTTP 403)"
)
GRAPHQL_RATE_LIMITED = subprocess.CalledProcessError(
    1, ["gh"], output="", stderr="GraphQL: API rate limit already exceeded for user ID 123."
)
SPENT = json.dumps({"resources": {"core": {"remaining": 0, "reset": 1001}}})


def _call(run, calls, *args, **over):
    return run_gh(
        run,
        args or ("issue", "view", "7"),
        cwd=Path("."),
        not_installed="no gh",
        failed=lambda stderr: f"failed: {stderr}",
        log=lambda line: None,
        latch=None,
        calls=calls,
        **over,
    )


def test_one_call_is_counted_without_a_policy():
    calls = MemoryLog()
    assert _call(ScriptedRun("ok\n"), calls) == "ok"
    assert calls.kinds == ["issue_view"]


def test_no_log_records_nothing_and_changes_nothing():
    run = ScriptedRun("ok\n")
    assert _call(run, None) == "ok"
    assert run.argv == [["gh", "issue", "view", "7"]]


def test_every_attempt_under_a_policy_is_counted_with_its_probe_as_api():
    calls = MemoryLog()
    run = ScriptedRun(RATE_LIMITED, SPENT, RATE_LIMITED, SPENT, "done")
    retry = GhRetry(max_wait_s=60, sleep=lambda s: None, now=lambda: 1000.0)
    assert _call(run, calls, retry=retry) == "done"
    assert calls.kinds == ["issue_view", "api", "issue_view", "api", "issue_view"]
    assert dict(calls.drain()) == {"issue_view": 3, "api": 2}


def test_the_graphql_message_counts_like_the_rest_one_under_a_policy():
    calls = MemoryLog()
    run = ScriptedRun(GRAPHQL_RATE_LIMITED, SPENT, "done")
    retry = GhRetry(max_wait_s=60, sleep=lambda s: None, now=lambda: 1000.0)
    assert _call(run, calls, "issue", "list", retry=retry) == "done"
    assert calls.kinds == ["issue_list", "api", "issue_list"]


def test_a_failed_attempt_is_still_counted_without_a_policy():
    calls = MemoryLog()
    run = ScriptedRun(subprocess.CalledProcessError(1, ["gh"], output="", stderr="boom"))
    with pytest.raises(ValueError, match="failed: boom"):
        _call(run, calls)
    assert calls.kinds == ["issue_view"]


def test_a_missing_gh_counts_once():
    calls = MemoryLog()
    with pytest.raises(ValueError, match="no gh"):
        _call(ScriptedRun(FileNotFoundError("gh")), calls)
    assert calls.kinds == ["issue_view"]


class RefusingGate:
    cap_s = 30.0

    def __init__(self, outcome):
        self.outcome = outcome

    def take(self, remaining_s):
        return Turn(self.outcome, 0.0)


@pytest.mark.parametrize("outcome", ["refused", "stopped"])
def test_a_refused_write_turn_records_nothing(outcome):
    calls = MemoryLog()
    run = ScriptedRun("never")
    access = GithubAccess(gate=RefusingGate(outcome))
    with pytest.raises(TrackerTurnRefused):
        _call(run, calls, "issue", "close", "7", access=access)
    assert calls.kinds == []
    assert run.argv == []


def test_a_granted_write_turn_records_after_the_turn():
    calls = MemoryLog()
    access = GithubAccess(gate=StubGate())
    assert _call(ScriptedRun("ok"), calls, "issue", "close", "7", access=access) == "ok"
    assert calls.kinds == ["other"]


def test_stream_gh_records_one_call_and_prefixes_gh(monkeypatch):
    seen = []
    result = StreamRead(b"x", 0, 0, False)

    def spy(args, **kwargs):
        seen.append((list(args), kwargs))
        return result

    monkeypatch.setattr(run_module, "bounded_stream", spy)
    calls = MemoryLog()
    got = stream_gh(
        ["run", "view", "5", "--log"],
        cwd=Path("."),
        max_bytes=10,
        timeout_s=3,
        tail=True,
        calls=calls,
    )
    assert got is result
    assert calls.kinds == ["other"]
    assert seen == [
        (
            ["gh", "run", "view", "5", "--log"],
            {"cwd": Path("."), "max_bytes": 10, "timeout_s": 3, "tail": True},
        )
    ]


def test_stream_gh_without_a_log_streams_all_the_same(monkeypatch):
    monkeypatch.setattr(
        run_module, "bounded_stream", lambda args, **kw: StreamRead(b"", 0, 0, False)
    )
    assert stream_gh(["api", "x"], cwd=None, max_bytes=1, timeout_s=1).returncode == 0


def test_stream_gh_rejects_args_that_start_with_gh(monkeypatch):
    def never(args, **kwargs):
        raise AssertionError("launched")

    monkeypatch.setattr(run_module, "bounded_stream", never)
    calls = MemoryLog()
    with pytest.raises(ValueError, match="leading gh"):
        stream_gh(["gh", "run", "view"], cwd=None, max_bytes=1, timeout_s=1, calls=calls)
    assert calls.kinds == []
