"""When the candidate playbook PR lands by itself (owner's rule of 2026-11-16: playbook lessons
land with no human approval). Pure: no I/O, no clock; the application reads, this decides.

`land_verdict` is the one function that says `Merge`: a decision table with a fixed order of
gates (paths, content, CI, base). `LandAttempt` is what the last attempt left behind, so a
refusal that repeats can be told apart from one that happened once."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from . import ci
from . import playbook as pb
from . import playbook_candidates as pc

RefuseCause = Literal[
    "paths_unreadable",
    "path_outside",
    "playbook_invalid",
    "foreign_head",
    "ci_red",
    "ci_unreported",
    "no_merge_commit",
    "stale_conflict",
]
WaitCause = Literal["ci_pending", "ci_unreadable", "stale_base", "master_unreadable"]
ContentVerdict = Literal["ok", "invalid", "outside"]

# How many refusals for one cause in a row halt the landing for it. Compared only in `land_halted`.
LAND_REFUSALS_MAX = 3
ATTEMPT_VERSION = 1

_PREFIX = pc.PLAYBOOKS_DIR + "/"
_SUMMARY = re.compile(re.escape(pc.SUMMARIES_DIR) + r"/\d+\.md")
# The files of the closed table that are not a role's playbook, each with its parser.
_RECORD_PARSERS = {
    pc.CANDIDATES_FILE: pc.parse_candidates,
    pc.ARCHIVED_FILE: pc.parse_archived,
    pc.REJECTED_FILE: pc.parse_rejected,
    pc.LEDGER_FILE: pc.parse_ledger,
}
_VERDICTS = frozenset({"merge", "wait", "refuse", "failed"})


@dataclass(frozen=True)
class Merge:
    """Merge the pull request at this exact `head`: the one the CI read judged."""

    head: str


@dataclass(frozen=True)
class Wait:
    cause: WaitCause


@dataclass(frozen=True)
class Refuse:
    cause: RefuseCause


@dataclass(frozen=True)
class Failed:
    """The merge call itself failed; `cause` is the class of the failure, never a prose."""

    cause: str


Verdict = Merge | Wait | Refuse | Failed


def foreign_refusal(cross_repository: bool | None) -> Refuse | None:
    """A pull request is acted on only when it is known to come from this repository: anything
    but `False` (a fork, or a read that could not say) is `foreign_head`."""
    return None if cross_repository is False else Refuse("foreign_head")


def _role_verdict(path: str, text: str | None) -> ContentVerdict | None:
    """The verdict of a role's playbook file, `None` when `path` is not one."""
    slug, dot, extension = path.removeprefix(_PREFIX).rpartition(".")
    if path.startswith(_PREFIX) and extension == "yaml" and dot and slug in pb.PLAYBOOK_ROLES:
        if text is None:
            return "ok"
        try:
            return "invalid" if pb.over_cap(pb.parse(text, slug)) else "ok"
        except ValueError:
            return "invalid"
    return None


def _file_verdict(path: str, text: str | None) -> ContentVerdict:
    role = _role_verdict(path, text)
    if role is not None:
        return role
    if text is None:
        return "outside"
    if _SUMMARY.fullmatch(path):
        return "ok"
    parser = _RECORD_PARSERS.get(path)
    if parser is None:
        return "outside"
    try:
        parser(text)
    except ValueError:
        return "invalid"
    return "ok"


def content_verdict(files: Mapping[str, str | None]) -> ContentVerdict:
    """What the batch of changed files, by path and text at the head (`None` = deleted), is
    worth: a closed table of file to parser. A role's playbook goes through `playbook.parse`
    and the role cap; the four record files through their own parser; `summaries/<n>.md` is
    checked by its path only. Any other path, and the deletion of anything but a role's
    playbook, is `outside`, which wins over `invalid`."""
    verdicts = {_file_verdict(path, text) for path, text in files.items()}
    if "outside" in verdicts:
        return "outside"
    return "invalid" if "invalid" in verdicts else "ok"


def _path_outside(path: str) -> bool:
    normal = posixpath.normpath(path)
    return (
        posixpath.isabs(path)
        or posixpath.isabs(normal)
        or ".." in path.split("/")
        or ".." in normal.split("/")
        or not normal.startswith(_PREFIX)
    )


def _ci_verdict(
    report: ci.CiReport | ci.CiUnreadable, names: tuple[str, ...], confirmed: bool
) -> Refuse | Wait | None:
    if isinstance(report, ci.CiUnreadable):
        return Wait("ci_unreadable")
    if not names:
        # Nothing declared gates this merge: that is a defect of the declaration, not a green.
        return Refuse("ci_unreported")
    verdict = ci.ci_state(report, names)
    if verdict.state == "red":
        return Refuse("ci_red")
    if ci.unreported(report, names, confirmed):
        return Refuse("ci_unreported")
    if verdict.state == "pending" or verdict.missing:
        return Wait("ci_pending")
    return None


def land_verdict(
    paths: Sequence[str],
    content: ContentVerdict,
    report: ci.CiReport | ci.CiUnreadable,
    names: tuple[str, ...],
    master_covered: bool,
    confirmed: bool,
) -> Merge | Wait | Refuse:
    """The only function that decides `Merge`. Gates in a fixed order: the changed `paths`
    (empty is `paths_unreadable`; absolute, `..` or outside the directory prefix, never a glob,
    is `path_outside`), the `content` verdict, the CI over `ci.ci_state` (red, then a name
    that never reported once `confirmed`, then pending or missing, then unreadable), and
    last whether master's gain is covered (`master_covered`)."""
    if not paths:
        return Refuse("paths_unreadable")
    if any(_path_outside(path) for path in paths) or content == "outside":
        return Refuse("path_outside")
    if content == "invalid":
        return Refuse("playbook_invalid")
    gate = _ci_verdict(report, names, confirmed)
    if gate is not None:
        return gate
    if not master_covered:
        return Wait("stale_base")
    assert isinstance(report, ci.CiReport)
    return Merge(report.head_sha)


@dataclass(frozen=True)
class LandAttempt:
    """What the last landing attempt did, as the record keeps it. `streak` counts the refusals
    in a row for `cause`; `escalated_at` is when that streak was last reported to a person."""

    head: str
    at: str
    verdict: Literal["merge", "wait", "refuse", "failed"]
    cause: str | None = None
    streak: int = 0
    master_sha: str | None = None
    escalated_at: str | None = None
    v: int = ATTEMPT_VERSION

    def to_data(self) -> dict[str, Any]:
        """The record's JSON object; the optional keys only when set, so a plain attempt carries
        just `v`, `head`, `at` and `verdict`."""
        data: dict[str, Any] = {
            "v": self.v,
            "head": self.head,
            "at": self.at,
            "verdict": self.verdict,
        }
        if self.cause is not None:
            data["cause"] = self.cause
        if self.streak:
            data["streak"] = self.streak
        if self.master_sha is not None:
            data["master_sha"] = self.master_sha
        if self.escalated_at is not None:
            data["escalated_at"] = self.escalated_at
        return data

    @staticmethod
    def from_data(data: object) -> LandAttempt | None:
        """The attempt `to_data` wrote, or `None` for another version or anything malformed."""
        if not isinstance(data, dict) or type(data.get("v")) is not int:
            return None
        if data["v"] != ATTEMPT_VERSION:
            return None
        head, at, verdict = data.get("head"), data.get("at"), data.get("verdict")
        cause, master_sha = data.get("cause"), data.get("master_sha")
        escalated_at, streak = data.get("escalated_at"), data.get("streak", 0)
        texts = (head, at)
        optional = (cause, master_sha, escalated_at)
        if not all(isinstance(item, str) for item in texts):
            return None
        if any(item is not None and not isinstance(item, str) for item in optional):
            return None
        if verdict not in _VERDICTS or type(streak) is not int or streak < 0:
            return None
        return LandAttempt(
            head=str(head),
            at=str(at),
            verdict=verdict,
            cause=cause,
            streak=streak,
            master_sha=master_sha,
            escalated_at=escalated_at,
        )


def next_attempt(
    prior: LandAttempt | None, verdict: Verdict, head: str, at: str, master_sha: str | None
) -> LandAttempt:
    """The record after `verdict`: a refusal for the cause the prior attempt refused for adds one
    to its streak, a refusal for another cause starts at 1, a merge resets to 0; a wait or a
    failed call leaves the streak as it was. `escalated_at` survives only while the streak does."""
    streak = 0 if prior is None else prior.streak
    escalated_at = None if prior is None else prior.escalated_at
    if isinstance(verdict, Refuse):
        same = prior is not None and prior.verdict == "refuse" and prior.cause == verdict.cause
        streak = streak + 1 if same else 1
        escalated_at = escalated_at if same else None
        return LandAttempt(head, at, "refuse", verdict.cause, streak, master_sha, escalated_at)
    if isinstance(verdict, Merge):
        return LandAttempt(head, at, "merge", None, 0, master_sha, None)
    name: Literal["wait", "failed"] = "wait" if isinstance(verdict, Wait) else "failed"
    return LandAttempt(head, at, name, verdict.cause, streak, master_sha, escalated_at)


def land_halted(attempt: LandAttempt | None) -> bool:
    """Whether the refusals in a row reached `LAND_REFUSALS_MAX`: the one comparison."""
    return (
        attempt is not None and attempt.verdict == "refuse" and attempt.streak >= LAND_REFUSALS_MAX
    )


def halt_marker(product: str, cause: str) -> str:
    """The hidden marker of the tracker comment that reports a halted landing."""
    return f"<!-- agentic-playbook-land-halt: {product} {cause} -->"
