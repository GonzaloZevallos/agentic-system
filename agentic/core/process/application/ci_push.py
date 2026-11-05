"""The retry of the CI path's pushes (GH-487): every push or delete of a ref of the CI path
goes through `publish_with_retry`, or through `republish`, which uses it step by step. No
other code loops over a push or names a number of attempts or seconds.

The retry wraps one git step, never a sequence: a delete that passed is not repeated
because the create after it failed. What git said decides how often a step is tried
(`domain/ci_push`); when the attempts run out, `PushFailed` carries what git said, already
through `scrub`. It is a `GitError`, so a caller that already ends on a failed push ends on
this one; the closed cause it escalates with stays `publish_failed` (ADR-0029).

The wait is synchronous: callers run the step in a worker thread, so a stop waits at most
the longest backoff.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..domain.ci_push import PUSH_BACKOFF_S, PushKind, attempts_for, classify, scrub
from .ports import GitError, Workspace


@dataclass(frozen=True)
class Pushed:
    """A step that went through. `attempts` is how many tries it took; `kind` is how the
    last failure read, `None` when the first try passed."""

    attempts: int
    kind: PushKind | None


class PushFailed(GitError):
    """A step that did not go through. `said` is git's stderr after `scrub`: safe to write
    into an escalation, a section or a log. `kind` is how it read at the last attempt."""

    def __init__(self, attempts: int, kind: PushKind, said: str):
        super().__init__(f"push failed after {attempts} attempt(s) ({kind}): {said}", stderr=said)
        self.attempts = attempts
        self.kind = kind
        self.said = said


def publish_with_retry(
    op: Callable[[], None], *, sleep: Callable[[float], None] = time.sleep
) -> Pushed:
    """Run `op`, one git step, until it passes or its failure's kind runs out of attempts.
    Raises `PushFailed`. An error that is not a `GitError` is not a push failure and goes up."""
    attempt = 0
    last: PushKind | None = None
    while True:
        attempt += 1
        try:
            op()
        except GitError as exc:
            last = classify(exc.stderr)
            if attempt >= attempts_for(last):
                raise PushFailed(attempt, last, scrub(exc.stderr or str(exc))) from exc
            sleep(PUSH_BACKOFF_S[attempt - 1])
        else:
            return Pushed(attempt, last)


def republish(
    workspace: Workspace, path: Path, ref: str, *, sleep: Callable[[float], None] = time.sleep
) -> Pushed:
    """Delete `ref` on the remote and create it again at the head of `path`, so GitHub sees
    a branch create (GH-462). The two steps are retried apart: a delete that passed is
    not repeated because the create failed. Returns what the create took."""
    publish_with_retry(lambda: workspace.delete_ci_ref(path, ref), sleep=sleep)
    return publish_with_retry(lambda: workspace.publish_ci_ref(path, ref), sleep=sleep)
