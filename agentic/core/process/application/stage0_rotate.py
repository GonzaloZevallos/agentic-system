"""The service that rotates stage0 (GH-632, part 2a): holds the rotation lock, reads the
window of origin/master, builds and smokes the candidate `target` picks, and switches
`current` to it. Pure application: the filesystem, git, the clock and the environment arrive
through ports and parameters (`pin`, `now`, `own`), never read here. `agentic stage0 rotate`
(part 2d) is what calls `rotate`; nothing runs that command automatically yet."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..domain.rules import same_commit
from ..domain.stage0_rotation import (
    Green,
    Keep,
    Proceed,
    Red,
    Stop,
    Unavailable,
    Unreadable,
    partial_name,
    target,
)
from .ports import (
    FailurePolicy,
    MasterWindow,
    Stage0Candidate,
    Stage0Escalation,
    Stage0Store,
    Stage0Users,
)

Out = Callable[[str], None]
Prune = Callable[[Stage0Store, Stage0Users, Path, Out], None]

_SHA40 = re.compile(r"[0-9a-f]{40}")


def no_prune(store: Stage0Store, users: Stage0Users, own: Path, out: Out) -> None:
    """The prune of a rotation that prunes nothing (a later part plugs the real one in)."""


class NoEscalation:
    """The escalation of a rotation that escalates nothing (a later part plugs the real one in)."""

    def run(self) -> None:
        return None


class DefaultFailurePolicy:
    """Keeps `current` on any failure: removes the partial and says why, and never writes
    `rejected` (a red smoke on a machine problem must not blacklist a commit)."""

    def __init__(self, store: Stage0Store) -> None:
        self._store = store

    def prepare_failed(self, sha: str, outcome: Unavailable) -> Stop:
        self._store.remove_partial(sha)
        return Stop("keep: unavailable")

    def smoke_failed(self, sha: str, stage: str, outcome: Red | Unavailable) -> Proceed | Stop:
        self._store.remove_partial(sha)
        if isinstance(outcome, Red):
            return Stop(f"smoke_failed: {sha}")
        return Stop("keep: unavailable")

    def succeeded(self, sha: str) -> None:
        return None


def rotate(
    store: Stage0Store,
    users: Stage0Users,
    window: MasterWindow,
    candidate: Stage0Candidate,
    escalation: Stage0Escalation,
    *,
    pin: str | None,
    now: datetime,
    own: Path,
    failure_policy: FailurePolicy | None = None,
    prune: Prune = no_prune,
    out: Out,
) -> None:
    """One rotation. Everything but the lock runs inside it; a lock held elsewhere prints
    `keep: busy` and touches nothing. `users` exists only to be handed to `prune`. Catches
    nothing: a port that raises propagates, and the lock is released on the way out.

    `current` changes only in `switch_current`, after `promote` of a partial smoked green
    and `write_switch` (an intent record, confirmed against `current()` by its readers)."""
    policy = failure_policy if failure_policy is not None else DefaultFailurePolicy(store)
    with store.lock() as held:
        if not held:
            out("keep: busy")
            return
        store.clear_partials()
        escalation.run()
        _adopt(store, users, window, candidate, policy, pin=pin, now=now, own=own, out=out)
        prune(store, users, own, out)


def _leftover_in_use(
    store: Stage0Store, users: Stage0Users, wanted: str, current: str | None
) -> bool:
    """Removes every non-current stage already named for the target (a `promote` that a
    later step left behind, which would make the next `promote` of the same sha fail), unless
    something runs from it or `users` cannot say: then True, and nothing is removed."""
    for name, _ in store.stages():
        if _SHA40.fullmatch(name) is None or not same_commit(name, wanted) or name == current:
            continue
        in_use = users.users()
        if isinstance(in_use, Unreadable) or name in in_use:
            return True
        store.remove(name)
    return False


def _adopt(
    store: Stage0Store,
    users: Stage0Users,
    window: MasterWindow,
    candidate: Stage0Candidate,
    policy: FailurePolicy,
    *,
    pin: str | None,
    now: datetime,
    own: Path,
    out: Out,
) -> None:
    current = store.current()
    entries = window.entries(current)
    if isinstance(entries, Unreadable):
        out("keep: unreadable")
        return
    wanted = target(entries, current, pin, store.rejected())
    if isinstance(wanted, Keep):
        out(f"keep: {wanted.cause}")
        if wanted.cause == "up_to_date" and current is not None:
            policy.succeeded(current)
        return
    if _leftover_in_use(store, users, wanted.sha, current):
        out("keep: busy")
        return
    prepared = candidate.prepare(wanted.sha)
    if isinstance(prepared, Unavailable):
        out(policy.prepare_failed(wanted.sha, prepared).line)
        return
    sha = prepared.sha
    if not same_commit(sha, wanted.sha) or _SHA40.fullmatch(sha) is None:
        raise ValueError(f"candidate resolved {sha!r}, not the target {wanted.sha!r}")
    stage = partial_name(sha)
    first_env = store.copy_env(own, stage)
    if _smoke(candidate, policy, sha, stage, out) is None:
        return
    if (
        store.copy_env(own, stage) != first_env
        and _smoke(candidate, policy, sha, stage, out) is None
    ):
        return
    store.promote(sha)
    settled = candidate.settle(sha)
    if not isinstance(settled, Green):
        store.remove(sha)
        out(policy.prepare_failed(sha, settled).line)
        return
    # #738 computes `expected` from the heartbeats; until then no rotation expects a poller.
    store.write_switch(previous=current, new=sha, switched_at=now, expected=frozenset())
    store.switch_current(sha)
    policy.succeeded(sha)
    out(f"switched: {sha}")


def _smoke(
    candidate: Stage0Candidate, policy: FailurePolicy, sha: str, stage: str, out: Out
) -> Green | None:
    """The `Green` that lets the adoption go on, or None when the policy stopped it. The
    only value that reaches `promote` is one typed `Green`, the smoke's own or the one a
    `Proceed` carries."""
    outcome = candidate.smoke(stage)
    if isinstance(outcome, Green):
        return outcome
    verdict = policy.smoke_failed(sha, stage, outcome)
    out(verdict.line)
    if isinstance(verdict, Proceed):
        return verdict.smoke
    return None
