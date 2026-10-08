"""The product's GitHub CI as the merge gate's and the review's evidence (GH-216).

This module is the one place the CI verdict lives: a pure classification of structured
GraphQL enum values, never of stderr or prose. The adapters read; this decides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

PENDING_STATUSES = frozenset({"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"})
GREEN_CONCLUSIONS = frozenset({"SUCCESS", "NEUTRAL"})

CI_CATEGORY = "process-suite-red"


@dataclass(frozen=True)
class CiCheck:
    """One check run, or one commit status mapped onto the same shape: `status` and
    `conclusion` hold the raw enum values (`StatusContext`: `state` is the conclusion)."""

    name: str
    status: str
    conclusion: str | None
    link: str | None


@dataclass(frozen=True)
class CiReport:
    head_sha: str
    base_ref: str | None
    base_sha: str | None
    opened_at: float | None
    checks: tuple[CiCheck, ...]


@dataclass(frozen=True)
class CiUnreadable:
    """`gh` could not answer, or answered something this module cannot read."""

    cause: Literal["gh_unavailable", "gh_unreadable"]


@dataclass(frozen=True)
class CiVerdict:
    state: Literal["green", "red", "pending"]
    failing: tuple[CiCheck, ...]
    missing: tuple[str, ...]


def _is_pending(check: CiCheck) -> bool:
    return check.status in PENDING_STATUSES or check.conclusion is None


def _is_green(check: CiCheck) -> bool:
    return not _is_pending(check) and check.conclusion in GREEN_CONCLUSIONS


def _is_red(check: CiCheck) -> bool:
    # A pending run is pending whatever it reports; anything settled that is not green is
    # red (`SKIPPED` included: a conditional job must not be declared).
    return not _is_pending(check) and not _is_green(check)


def ci_state(report: CiReport, declared: tuple[str, ...]) -> CiVerdict:
    """Classify `report` against the declared names, matched exactly. A name with any red
    run is red, else with any pending run pending; a name with no run is pending and
    listed in `missing`. What is not declared is ignored."""
    failing: list[CiCheck] = []
    missing: list[str] = []
    pending = False
    for name in declared:
        runs = [check for check in report.checks if check.name == name]
        if not runs:
            missing.append(name)
            pending = True
            continue
        red = [check for check in runs if _is_red(check)]
        if red:
            failing.extend(red)
        elif any(_is_pending(check) for check in runs):
            pending = True
    state: Literal["green", "red", "pending"] = (
        "red" if failing else "pending" if pending else "green"
    )
    return CiVerdict(state, tuple(failing), tuple(missing))


def _first_red(report: CiReport, name: str) -> CiCheck | None:
    for check in report.checks:
        if check.name == name and _is_red(check):
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
