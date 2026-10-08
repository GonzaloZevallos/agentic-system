"""The product's GitHub CI as the merge gate (GH-216, ADR-0045 amendment): the one place
the CI verdict lives. Pure: it reads the structured enum fields `gh` reports and never a
stderr or a prose; the adapters fetch, the application decides what to do with the answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .rules import same_commit

# How long a pending, missing or unreadable CI is waited for before the merge gate refuses.
# A declared value, not a measured one: nobody has measured a product's CI duration yet.
CI_WAIT_MAX_S = 3600

Containment = Literal["yes", "no", "unknown"]

_GREEN = frozenset({"SUCCESS", "NEUTRAL"})
# A conclusion that says the check has not decided yet: the `StatusContext` states that
# are not final, whatever `status` says.
_UNDECIDED = frozenset({"PENDING", "EXPECTED"})


@dataclass(frozen=True)
class CiCheck:
    """One check run, or one commit status, as GraphQL reports it. `status` and
    `conclusion` hold the raw enum values. A `StatusContext` maps `context` to `name`,
    `state` to `conclusion` and `targetUrl` to `link`, with `status` `COMPLETED` or
    `PENDING`."""

    name: str
    status: str
    conclusion: str | None
    link: str


@dataclass(frozen=True)
class CiReport:
    """What one read of a commit's CI says. `head_sha` is the commit the checks belong
    to, read in the same query as `checks`. `base_ref`, `base_sha` and `opened_at` (an ISO
    UTC string) are None for a branch's report, which has no pull request."""

    head_sha: str
    base_ref: str | None
    base_sha: str | None
    opened_at: str | None
    checks: tuple[CiCheck, ...]


@dataclass(frozen=True)
class CiUnreadable:
    """The CI could not be read, which is not the same as a report that lacks a check:
    `gh` is missing or failed (`gh_unavailable`), or answered something this code cannot
    read (`gh_unreadable`)."""

    cause: Literal["gh_unavailable", "gh_unreadable"]


@dataclass(frozen=True)
class CiVerdict:
    """`failing` is the red runs of the declared checks, in declared order; `missing` the
    declared names the report never mentions."""

    state: Literal["green", "red", "pending"]
    failing: tuple[CiCheck, ...]
    missing: tuple[str, ...]


def _pending(check: CiCheck) -> bool:
    return check.status != "COMPLETED" or check.conclusion in _UNDECIDED


def _green(check: CiCheck) -> bool:
    return check.conclusion in _GREEN


def ci_state(report: CiReport, declared: tuple[str, ...]) -> CiVerdict:
    """The verdict of `report` over the `declared` check names, matched exactly: a matrix
    job `check (3.12)` is not `check`, and a check nobody declared is ignored. Every run
    of a declared name counts: any red makes the name red, else any pending makes it
    pending, else it is green. Red is every conclusion that is not green and not pending,
    an unknown one included, so a value GitHub adds later is never read as green. A
    declared name with no run is pending and listed in `missing`. The aggregate is red if
    any name is red, else pending if any is, else green."""
    failing: list[CiCheck] = []
    missing: list[str] = []
    waiting = False
    for name in dict.fromkeys(declared):
        runs = [check for check in report.checks if check.name == name]
        if not runs:
            missing.append(name)
            waiting = True
            continue
        red = [check for check in runs if not _pending(check) and not _green(check)]
        failing.extend(red)
        if not red and any(_pending(check) for check in runs):
            waiting = True
    if failing:
        state: Literal["green", "red", "pending"] = "red"
    elif waiting:
        state = "pending"
    else:
        state = "green"
    return CiVerdict(state, tuple(failing), tuple(missing))


def ci_fresh(
    base_sha: str | None, current_master: str | None, contains: Containment | None
) -> bool | None:
    """Whether the CI ran on a base that is still master's: True when both shas are known
    and name the same commit, or when `contains` says the head already has master. False
    when `contains` is `no` or `unknown`: a containment that cannot be read counts as
    stale, never fresh. None when nothing is known yet, the poller's case."""
    if same_commit(base_sha, current_master) or contains == "yes":
        return True
    if contains in ("no", "unknown"):
        return False
    return None


def ci_wait_anchor(
    pushed_head: str | None,
    pushed_at: str | None,
    pr_head: str | None,
    opened_at: str | None,
) -> str | None:
    """The instant the CI wait bound is counted from: the push time when the head pushed
    is the pull request's head, else the pull request's creation. Never a commit
    timestamp, which says when the code was written and not when CI could have started."""
    if pushed_at is not None and same_commit(pushed_head, pr_head):
        return pushed_at
    return opened_at
