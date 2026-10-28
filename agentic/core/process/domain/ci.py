"""The product's GitHub CI as the merge gate (GH-216, ADR-0045 amendment): the one place
the CI verdict lives. Pure: it reads the structured enum fields `gh` reports and never a
stderr or a prose; the adapters fetch, the application decides what to do with the answer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules

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


def pending_names(report: CiReport, declared: tuple[str, ...]) -> tuple[str, ...]:
    """The declared names that have a run and are still waiting on it (a name the report
    never mentions is in `CiVerdict.missing`, not here), in declared order."""
    return tuple(
        name
        for name in dict.fromkeys(declared)
        if any(check.name == name and _pending(check) for check in report.checks)
    )


def reported_names(report: CiReport) -> tuple[str, ...]:
    """The distinct check names `report` carries, in report order: what a refusal for a
    required name that never reported shows next to the names that were missing (GH-371)."""
    return tuple(dict.fromkeys(check.name for check in report.checks))


def red_on_base(head: CiReport, base: CiReport | None, names: tuple[str, ...]) -> tuple[str, ...]:
    """The `names` that fail on `head` and also fail on `base`, the base branch's latest
    report, in `names` order (GH-371). `()` without a base report: a base that was not read
    proves nothing. Red on both says only that both are red, not that it is the same failure."""
    if base is None:
        return ()
    on_head = {check.name for check in ci_state(head, names).failing}
    on_base = {check.name for check in ci_state(base, names).failing}
    return tuple(name for name in dict.fromkeys(names) if name in on_head and name in on_base)


def run_link(report: CiReport, name: str) -> str | None:
    """The link of the first red run of `name` in `report` (its status line when GitHub gave
    no link), `None` when no run of it is red."""
    red = _first_red(report, name)
    return None if red is None else _reference(red)


def ci_fresh(
    base_sha: str | None, current_master: str | None, contains: Containment | None
) -> bool | None:
    """Whether the CI ran on a base that is still master's: True when both shas are known
    and name the same commit, or when `contains` says the head already has master. False
    when `contains` is `no` or `unknown`: a containment that cannot be read counts as
    stale, never fresh. None when nothing is known yet, the poller's case."""
    if rules.same_commit(base_sha, current_master) or contains == "yes":
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
    if pushed_at is not None and rules.same_commit(pushed_head, pr_head):
        return pushed_at
    return opened_at


CI_CATEGORY = "process-suite-red"


def _first_red(report: CiReport, name: str) -> CiCheck | None:
    for check in report.checks:
        if check.name == name and not _pending(check) and not _green(check):
            return check
    return None


def _reference(check: CiCheck) -> str:
    """What backs a finding about `check`: its run link, or, when GitHub gave none, what
    it reported, so a blocking finding never lacks evidence."""
    return check.link or f"{check.name}: {check.status}/{check.conclusion}"


def _quoted(link: str | None, findings: list[dict[str, Any]]) -> bool:
    """Whether a finding's evidence already holds `link` as a whole link: `.../job/100`
    does not quote `.../job/10`."""
    if not link:
        return False
    pattern = re.compile(re.escape(link) + r"(?![\w/-])")
    return any(pattern.search(item.get("evidence", "")) for item in findings)


def ci_findings(
    head: CiReport,
    master: CiReport | CiUnreadable | None,
    declared: tuple[str, ...],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """`findings` plus one finding per declared check that is red on the head under
    review, written by the system so an approval cannot sit over red CI. Blocking, unless
    the same name is red on the base branch's latest commit: then it is pre-existing, an
    observation with both links. A check whose run link a finding already quotes in its
    evidence adds nothing."""
    on_master = master if isinstance(master, CiReport) else None
    extended = list(findings)
    for name in dict.fromkeys(check.name for check in ci_state(head, declared).failing):
        red = _first_red(head, name)
        if red is None:
            continue
        evidence = _reference(red)
        if _quoted(red.link, extended):
            continue
        red_on_master = None if on_master is None else _first_red(on_master, name)
        if red_on_master is not None:
            claim = (
                f"the declared CI check `{name}` is also red on the base branch's latest "
                "commit, so it is pre-existing"
            )
            # Never a second finding that quotes a run link another one already holds.
            if not _quoted(red_on_master.link, extended):
                evidence = f"{evidence} (head), {_reference(red_on_master)} (base branch)"
            severity = "observation"
        else:
            claim = f"the declared CI check `{name}` is red on the head under review"
            severity = "blocking"
        extended.append(
            {
                "location": f"CI: {name}",
                "claim": claim,
                "evidence": evidence,
                "severity": severity,
                "scope": "subtask",
                "category": CI_CATEGORY,
            }
        )
    return extended
