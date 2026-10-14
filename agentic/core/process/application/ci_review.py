"""The CI evidence a review reads (GH-216): what the declared checks say on the head under
review, written by the system as a section, and the report the findings are derived from.

Reading never fails the review: an unreadable report, another head or a task with no pull
request each turn into one line saying CI was not read and why, and no finding."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import ci
from ..domain.rules import full_commit_id, same_commit
from ..domain.task import TaskState
from .ports import Ports

CI_SECTION = "CI"


@dataclass(frozen=True)
class CiEvidence:
    """`section` is the text the reviewer reads. `head` is the report the system's own
    findings come from: set only when it was read and is the head under review's."""

    section: str
    head: ci.CiReport | None = None
    master: ci.CiReport | ci.CiUnreadable | None = None


def _line(report: ci.CiReport, name: str) -> str:
    verdict = ci.ci_state(report, (name,))
    runs = [check for check in report.checks if check.name == name]
    link = (verdict.failing or tuple(runs))[0].link if runs else None
    return f"{verdict.state}" + (f" {link}" if link else "")


def _master_line(master: ci.CiReport | ci.CiUnreadable | None, name: str) -> str:
    if master is None:
        return ""
    if isinstance(master, ci.CiUnreadable):
        return f"; base branch: not read ({master.cause})"
    return f"; base branch `{master.base_ref or 'latest'}`: {_line(master, name)}"


def _unread(why: str) -> CiEvidence:
    return CiEvidence(f"CI was not read: {why}.")


def read_ci(state: TaskState, declared: tuple[str, ...], head: str, ports: Ports) -> CiEvidence:
    reference = state.get("pull_request")
    if not reference:
        return _unread("the task has no pull request yet")
    report = ports.tracker.ci_report(reference)
    if isinstance(report, ci.CiUnreadable):
        return _unread(f"`gh` could not be read ({report.cause})")
    if not full_commit_id(report.head_sha):
        return _unread("the report's commit is not a full id")
    if not same_commit(report.head_sha, head):
        return _unread("the pull request's head is not the head under review")
    master = ports.tracker.branch_ci(report.base_ref) if report.base_ref else None
    lines = [f"- `{name}`: {_line(report, name)}{_master_line(master, name)}" for name in declared]
    return CiEvidence("\n".join(lines), report, master)
