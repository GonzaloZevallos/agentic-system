"""The CI verdict of a pull request's head (GH-216, ADR-0045 amendment): the one place
that classifies a CI report. It reads structured enum fields only -- the `status` and
`conclusion` of a check run, the `state` of a status context -- never a message or a log.
Names are matched exactly against what the product declares; a check that is not
declared is ignored."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules

# A declared value, not a measured one: how long a pending or unreadable report is waited
# for, from the push (or the pull request's opening), before the merge gate refuses.
CI_WAIT_MAX_S = 3600

GREEN = frozenset({"SUCCESS", "NEUTRAL"})
RED = frozenset(
    {
        "FAILURE",
        "CANCELLED",
        "TIMED_OUT",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "SKIPPED",
        "STALE",
        "ERROR",
    }
)
PENDING = frozenset({"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"})


@dataclass(frozen=True)
class CiCheck:
    """One check run, or one status context mapped onto the same shape (`context` ->
    `name`, `state` -> `conclusion`, `targetUrl` -> `link`). `status` and `conclusion`
    hold the raw GraphQL enum values; `conclusion` is None while the run is not done."""

    name: str
    status: str
    conclusion: str | None
    link: str


@dataclass(frozen=True)
class CiReport:
    """The checks of one commit, read in the same query as its oid: `head_sha` is that
    commit, `base_ref`/`base_sha` the branch the pull request merges into and the commit
    CI ran it merged with (None when unknown), `opened_at` when the pull request was
    created."""

    head_sha: str
    base_ref: str | None
    base_sha: str | None
    opened_at: str | None
    checks: tuple[CiCheck, ...]


@dataclass(frozen=True)
class CiUnreadable:
    """`gh` could not be run or its answer could not be read: a closed result, so the
    caller never parses prose to tell why."""

    cause: Literal["gh_unavailable", "gh_unreadable"]


@dataclass(frozen=True)
class CiVerdict:
    """`failing` are the red checks of declared names; `missing` the declared names no run
    reported (they also make the verdict pending)."""

    state: Literal["green", "red", "pending"]
    failing: tuple[CiCheck, ...] = ()
    missing: tuple[str, ...] = ()


def _check_state(check: CiCheck) -> Literal["green", "red", "pending"]:
    value = check.conclusion
    if value in GREEN:
        return "green"
    if value in RED:
        return "red"
    return "pending"


def ci_state(report: CiReport, declared: tuple[str, ...]) -> CiVerdict:
    """Every run named exactly like a declared name counts: any red makes the name red,
    else any pending makes it pending, else it is green. A declared name with no run is
    pending and listed in `missing`. The aggregate is red, else pending, else green."""
    failing: list[CiCheck] = []
    missing: list[str] = []
    pending = False
    for name in declared:
        runs = [check for check in report.checks if check.name == name]
        if not runs:
            missing.append(name)
            pending = True
            continue
        states = [_check_state(run) for run in runs]
        failing.extend(run for run, state in zip(runs, states, strict=True) if state == "red")
        pending = pending or ("red" not in states and "pending" in states)
    state: Literal["green", "red", "pending"] = (
        "red" if failing else "pending" if pending else "green"
    )
    return CiVerdict(state, tuple(failing), tuple(missing))


def ci_fresh(
    base_sha: str | None,
    current_master: str | None,
    contains: Literal["yes", "no", "unknown"] | None,
) -> bool | None:
    """Whether CI ran on a tree that holds the current master: its base equals master, or
    the head contains it. `no` and `unknown` are stale, never fresh; None when nothing is
    known yet (the poller never asks git)."""
    if base_sha is not None and current_master is not None and base_sha == current_master:
        return True
    if contains is None:
        return None
    return contains == "yes"


def ci_wait_anchor(
    pushed_head: str | None,
    pushed_at: str | None,
    pr_head: str | None,
    opened_at: str | None,
) -> str | None:
    """Where the CI wait is measured from: the push, when it is of the head now on the
    pull request, else the pull request's creation, else nowhere. Never a commit time."""
    if pushed_at and rules.same_commit(pushed_head, pr_head):
        return pushed_at
    return opened_at
