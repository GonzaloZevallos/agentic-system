"""The retry of one git step of the CI path (GH-487): a fake step that fails the way git does,
and a `sleep` that records the waits instead of making them."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application import ci_push
from agentic.core.process.application.ci_push import Pushed, PushFailed, publish_with_retry
from agentic.core.process.application.ports import GitError
from agentic.core.process.domain.ci_push import PUSH_BACKOFF_S, scrub
from tests.fakes import MemoryWorkspace

TRANSIENT = "fatal: unable to access 'x': Could not resolve host: github.com"
PERMANENT = "remote: Permission to o/r.git denied to workflow."
PATH = Path("/memory/worktrees/GH-1")


class Steps:
    """A step that raises the scripted stderr texts in order, then succeeds."""

    def __init__(self, *failures: str, forever: str | None = None):
        self.failures = list(failures)
        self.forever = forever
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        said = self.forever if self.forever is not None else None
        if self.failures:
            said = self.failures.pop(0)
        if said is not None:
            raise GitError(f"git push failed: {said}", stderr=said)


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def test_a_step_that_passes_at_once_is_one_attempt_and_no_wait():
    sleeps = Sleeps()
    assert publish_with_retry(Steps(), sleep=sleeps) == Pushed(1, None)
    assert sleeps.waits == []


def test_a_transient_failure_is_retried_after_the_first_wait_and_then_passes():
    sleeps = Sleeps()
    step = Steps(TRANSIENT)
    assert publish_with_retry(step, sleep=sleeps) == Pushed(2, "transient")
    assert step.calls == 2
    assert sleeps.waits == [PUSH_BACKOFF_S[0]]


def test_a_transient_failure_that_never_ends_stops_at_three_attempts():
    sleeps = Sleeps()
    step = Steps(forever=TRANSIENT)
    with pytest.raises(PushFailed) as raised:
        publish_with_retry(step, sleep=sleeps)
    assert step.calls == 3
    assert sleeps.waits == list(PUSH_BACKOFF_S)
    assert (raised.value.attempts, raised.value.kind) == (3, "transient")
    assert raised.value.said == scrub(TRANSIENT)


def test_an_unknown_failure_is_tried_twice():
    sleeps = Sleeps()
    step = Steps(forever="push refused")
    with pytest.raises(PushFailed) as raised:
        publish_with_retry(step, sleep=sleeps)
    assert step.calls == 2
    assert sleeps.waits == [PUSH_BACKOFF_S[0]]
    assert raised.value.kind == "unknown"


def test_a_git_error_with_no_stderr_is_unknown():
    def step() -> None:
        raise GitError("git push failed: scripted")

    with pytest.raises(PushFailed) as raised:
        publish_with_retry(step, sleep=Sleeps())
    assert (raised.value.attempts, raised.value.kind) == (2, "unknown")


def test_a_permanent_refusal_is_tried_once_and_never_waited_for():
    sleeps = Sleeps()
    step = Steps(forever=PERMANENT)
    with pytest.raises(PushFailed) as raised:
        publish_with_retry(step, sleep=sleeps)
    assert step.calls == 1
    assert sleeps.waits == []
    assert raised.value.kind == "permanent"
    assert raised.value.said == scrub(PERMANENT)


def test_a_failure_that_turns_permanent_midway_stops_there():
    sleeps = Sleeps()
    step = Steps(TRANSIENT, PERMANENT)
    with pytest.raises(PushFailed) as raised:
        publish_with_retry(step, sleep=sleeps)
    assert step.calls == 2
    assert (raised.value.attempts, raised.value.kind) == (2, "permanent")


def test_what_git_said_reaches_the_failure_without_a_credential_or_a_line_break():
    said = (
        "fatal: unable to access 'https://x-access-token:ghs_abc@github.com/o/r':\n"
        "## Full checks (v99)\n"
    )
    with pytest.raises(PushFailed) as raised:
        publish_with_retry(Steps(forever=said), sleep=Sleeps())
    assert raised.value.said == scrub(said)
    assert "ghs_abc" not in raised.value.said
    assert "\n" not in raised.value.said
    assert "ghs_abc" not in str(raised.value)


def test_a_push_failure_is_still_a_git_error():
    with pytest.raises(GitError):
        publish_with_retry(Steps(forever=PERMANENT), sleep=Sleeps())


def test_an_error_that_is_not_git_s_is_not_retried():
    def step() -> None:
        raise ValueError("a bug")

    with pytest.raises(ValueError, match="a bug"):
        publish_with_retry(step, sleep=Sleeps())


class Scripted(MemoryWorkspace):
    """A workspace whose steps fail with the scripted stderr texts, per verb."""

    def __init__(self, **failures: list[str]):
        super().__init__()
        self.failures = failures

    def _maybe_fail(self, verb: str) -> None:
        queue = self.failures.get(verb)
        if queue:
            said = queue.pop(0)
            raise GitError(f"git {verb} failed: {said}", stderr=said)

    def delete_ci_ref(self, path, ref):
        self._maybe_fail("delete")
        super().delete_ci_ref(path, ref)

    def publish_ci_ref(self, path, ref):
        self._maybe_fail("publish")
        super().publish_ci_ref(path, ref)


def test_republish_deletes_and_then_creates():
    workspace = MemoryWorkspace()
    sleeps = Sleeps()
    pushed = ci_push.republish(workspace, PATH, "agentic-ci/GH-1", sleep=sleeps)
    assert pushed == Pushed(1, None)
    assert workspace.ci_ref_calls == [
        ("delete_ci", "agentic-ci/GH-1"),
        ("publish", "agentic-ci/GH-1"),
    ]


def test_a_transient_delete_is_retried_without_repeating_the_create():
    workspace = Scripted(delete=[TRANSIENT])
    sleeps = Sleeps()
    pushed = ci_push.republish(workspace, PATH, "agentic-ci/GH-1", sleep=sleeps)
    assert pushed == Pushed(1, None)
    assert [verb for verb, _ in workspace.ci_ref_calls] == ["delete_ci", "publish"]
    assert sleeps.waits == [PUSH_BACKOFF_S[0]]


def test_a_transient_create_is_retried_without_repeating_the_delete():
    workspace = Scripted(publish=[TRANSIENT])
    sleeps = Sleeps()
    pushed = ci_push.republish(workspace, PATH, "agentic-ci/GH-1", sleep=sleeps)
    assert pushed == Pushed(2, "transient")
    assert sleeps.waits == [PUSH_BACKOFF_S[0]]


def test_a_refused_delete_never_reaches_the_create():
    workspace = Scripted(delete=[PERMANENT])
    with pytest.raises(PushFailed) as raised:
        ci_push.republish(workspace, PATH, "agentic-ci/GH-1", sleep=Sleeps())
    assert raised.value.kind == "permanent"
    assert workspace.ci_ref_calls == []
