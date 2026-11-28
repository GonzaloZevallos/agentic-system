"""The mail text of the email notifier (GH-1056): what a message says, never how it is sent.

Pure and inert: no production module imports it yet (the poll use case of a later part does).
Every string that comes from an issue or a task goes through `clean` and, in the HTML part, then
through `html.escape`, field by field. Links are built from a validated `repo_url` and integers,
never from issue text. Every message is one `_compose`: the actionable header first and never cut,
then sections, cut at the same count in the text and the HTML part so both show the same thing.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Final

from . import drawing_answer, escalation_prompts
from .notify import PartFact, ReleaseEvent
from .schemas import SLUG
from .task import DepartureRecord, EscalationClass

__all__ = [
    "BODY_MAX_BYTES",
    "FIELD_MAX",
    "SUBJECT_MAX",
    "AcceptedDeparture",
    "DigestRow",
    "DigestView",
    "Message",
    "WindowedSection",
    "clean",
    "digest_message",
    "github_repo_url",
    "is_repo_url",
    "release_message",
    "repo_url",
    "waiting_message",
]

FIELD_MAX: Final = 500
SUBJECT_MAX: Final = 200
BODY_MAX_BYTES: Final = 64 * 1024
TRUNCATED: Final = "(truncated)"
SEE_COMMENT: Final = "See the escalation comment on the issue."
NO_USD: Final = "Set the amount from the escalation comment on the issue."
NO_TITLE: Final = "(no title)"
NO_PR: Final = "no pull request"
NOTHING: Final = "Nothing to report."

_REMOVED: Final = ("Cc", "Zl", "Zp")
_DIRECTION: Final = frozenset([0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)])
_REPO: Final = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.ASCII)
_REMOTES: Final = (
    re.compile(r"https://github\.com/([^/\s@:]+)/([^/\s@:]+)", re.ASCII),
    re.compile(r"git@github\.com:([^/\s@:]+)/([^/\s@:]+)", re.ASCII),
    re.compile(r"ssh://git@github\.com/([^/\s@:]+)/([^/\s@:]+)", re.ASCII),
)
_UNFILLED: Final = ("{", "}", "<usd>", "<n>")

_Line = tuple[str, str]
"""One line of a message as (text part, HTML part)."""


def clean(value: str, limit: int = FIELD_MAX) -> str:
    """`value` without control, line-separator and direction-mark characters, cut to `limit`
    characters with the `…` counted inside them. Never normalizes Unicode (D-03fa4682)."""
    kept = "".join(
        c for c in value if unicodedata.category(c) not in _REMOVED and ord(c) not in _DIRECTION
    )
    return kept if len(kept) <= limit else kept[: limit - 1] + "…"


def is_repo_url(value: str) -> bool:
    """Whether `value` is `https://github.com/<owner>/<repo>` in ASCII."""
    match = _REPO.fullmatch(value)
    return match is not None and not {".", ".."} & set(match.groups())


def repo_url(value: str) -> str:
    """`value` when `is_repo_url`, else `ValueError`."""
    if not is_repo_url(value):
        raise ValueError(f"repo is not a GitHub repository URL: {value!r}")
    return value


def github_repo_url(remote: str) -> str | None:
    """The `https://github.com/<owner>/<repo>` URL of a git remote on github.com (https, scp-like
    or ssh form, an optional `.git` suffix, whitespace trimmed), else `None`: another host, a
    remote with credentials (an https userinfo), or text that is not a remote. The only place
    that converts a remote, and it never raises or returns any of its text but a validated URL."""
    text = remote.strip()
    for pattern in _REMOTES:
        match = pattern.fullmatch(text)
        if match is None:
            continue
        owner, name = match.groups()
        name = name.removesuffix(".git")
        url = f"https://github.com/{owner}/{name}"
        return url if is_repo_url(url) else None
    return None


@dataclass(frozen=True)
class Message:
    """A mail: a one-line `subject` of at most `SUBJECT_MAX` characters, the `text` part and the
    `html` part. Rejects a bad subject at construction, never repairs it."""

    subject: str
    text: str
    html: str

    def __post_init__(self) -> None:
        if len(self.subject) > SUBJECT_MAX or any(
            unicodedata.category(c) in _REMOVED for c in self.subject
        ):
            raise ValueError(
                f"subject is not a single line of at most {SUBJECT_MAX}: {self.subject!r}"
            )


@dataclass(frozen=True)
class AcceptedDeparture:
    """A departure from the approved drawing that the owner accepted: the `issue` it was raised
    on, its `number` and its `text`."""

    issue: int
    number: int
    text: str

    def __post_init__(self) -> None:
        _positive("issue", self.issue)
        _positive("number", self.number)


def _positive(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} is not a positive integer: {value!r}")


def _join(*parts: str | _Line) -> _Line:
    pairs = [(p, escape(p)) if isinstance(p, str) else p for p in parts]
    return "".join(p[0] for p in pairs), "".join(p[1] for p in pairs)


def _ref(repo: str, kind: str, number: int) -> _Line:
    _positive("number", number)
    url = f"{repo}/{kind}/{number}"
    return f"#{number} ({url})", f'<a href="{url}">#{number}</a>'


def _compose(subject: str, header: Sequence[_Line], sections: Sequence[Sequence[_Line]]) -> Message:
    """The message: `header` always, then the most `sections` such that the text and the HTML part
    each fit in `BODY_MAX_BYTES` UTF-8 bytes, with a `(truncated)` line when some were left out."""

    def build(n: int) -> tuple[str, str]:
        blocks = [header, *sections[:n]]
        if n < len(sections):
            blocks.append([_join(TRUNCATED)])
        text = "\n\n".join("\n".join(t for t, _ in block) for block in blocks)
        body = "".join(f"<p>{h}</p>" for block in blocks for _, h in block)
        return text, f"<html><body>{body}</body></html>"

    def fits(n: int) -> bool:
        return all(len(part.encode()) <= BODY_MAX_BYTES for part in build(n))

    total = len(sections)
    low, high = (total, total) if fits(total) else (0, total - 1)
    while low < high:
        mid = (low + high + 1) // 2
        low, high = (mid, high) if fits(mid) else (low, mid - 1)
    text, html = build(low)
    return Message(clean(subject, SUBJECT_MAX), text, html)


def _title(titles: Mapping[int, str], issue: int) -> str:
    return clean(titles.get(issue, "")) or NO_TITLE


def waiting_message(
    *,
    product: str,
    issue: int,
    title: str,
    cls: EscalationClass,
    cause: escalation_prompts.PromptCause | None,
    reason: str,
    open_rows: Sequence[DepartureRecord],
    usd: float | None,
    repo: str,
) -> Message:
    """The notice of a task waiting for a human: the question, the recommended answer and the
    options of `escalation_prompts.prompt_for`, then the reason and the open departures. A line
    that would keep a placeholder other than `{issue}` and `{usd}` is a fixed one instead."""
    repo = repo_url(repo)
    link = _ref(repo, "issues", issue)
    if usd is not None and not (math.isfinite(usd) and usd > 0):
        raise ValueError(f"usd is not a positive amount: {usd!r}")

    def fill(template: str) -> _Line:
        if "{usd}" in template and usd is None:
            return _join(NO_USD, " ", link)
        filled = template.replace("{issue}", str(issue))
        filled = filled if usd is None else filled.replace("{usd}", str(usd))
        if any(mark in filled for mark in _UNFILLED):
            return _join(SEE_COMMENT, " ", link)
        return _join(filled)

    question, recommended, options = escalation_prompts.prompt_for(cls, cause)
    asked = fill(recommended)
    header = [
        _join(link, " ", clean(title)),
        _join("Question: ", fill(question)),
        _join("Recommended: ", asked),
    ]
    header.append(_join("Options:"))
    shown = {asked[0]}
    for option in options:
        line = fill(option)
        if line[0] not in shown:
            shown.add(line[0])
            header.append(_join("- ", line))
    rows = [row for row in open_rows if row["answer"] is None]
    if cause in ("drawing_departure", "drawing_cap"):
        header.extend(_join(c) for c in drawing_answer.prompt_lines(rows, issue))
    sections = [[_join("Reason: ", clean(reason))]] if clean(reason) else []
    if cause in ("drawing_departure", "drawing_cap") and rows:
        lines = drawing_answer.section_lines(rows)
        sections.append([_join(clean(line)) for line in lines])
    return _compose(f"[{product}] #{issue} needs you: {clean(title)}", header, sections)


def release_message(
    *,
    product: str,
    event: ReleaseEvent,
    titles: Mapping[int, str],
    departures: Sequence[AcceptedDeparture],
    repo: str,
) -> Message:
    """The notice of a landing: the merged leaves grouped by the intermediate parent each is a
    part of, each with its title and pull request, then the accepted drawing departures. A task
    with no parts lists itself. Only a `send` event has a notice."""
    if event.action != "send":
        raise ValueError(f"a {event.action} event has no notice")
    repo = repo_url(repo)
    root = event.key.issue

    def item(issue: int, tail: _Line | str) -> _Line:
        return _join(_ref(repo, "issues", issue), " ", _title(titles, issue), tail)

    sections: list[list[_Line]] = []
    if not event.leaves:
        sections.append([item(root, "")])
    groups: dict[int, list[PartFact]] = {}
    for leaf in sorted(event.leaves, key=lambda part: part.issue):
        groups.setdefault(leaf.parent_of, []).append(leaf)
    for parent in sorted(groups):
        lines = [_join("Parts of ", _ref(repo, "issues", parent), ":")]
        for leaf in groups[parent]:
            pull = NO_PR if leaf.pr is None else _ref(repo, "pull", leaf.pr)
            lines.append(item(leaf.issue, _join(" - ", pull)))
        sections.append(lines)
    if departures:
        lines = [_join("Accepted drawing departures:")]
        for d in departures:
            lines.append(
                _join(_ref(repo, "issues", d.issue), f" departure {d.number}: ", clean(d.text))
            )
        sections.append(lines)
    header = [_join("Released: ", _ref(repo, "issues", root), " ", _title(titles, root))]
    return _compose(f"[{product}] Released #{root}: {_title(titles, root)}", header, sections)


@dataclass(frozen=True)
class DigestRow:
    """One line of the digest: an issue or pull request `number`, its `title` and, for a paused
    task, the `reason` it is paused."""

    number: int
    title: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _positive("number", self.number)


@dataclass(frozen=True)
class WindowedSection:
    """The `rows` of an activity window; `cut` says more existed than the limit, `single_group`
    that the cut fell inside one instant so the rest could not be told apart."""

    rows: tuple[DigestRow, ...]
    cut: bool
    single_group: bool

    def __post_init__(self) -> None:
        if self.single_group and not self.cut:
            raise ValueError("single_group needs cut")


@dataclass(frozen=True)
class DigestView:
    """What the daily digest of `product` on `local_date` shows: the snapshot sections (waiting,
    paused, running), the windowed ones (merged, closed) and the `limit` that cut the latter."""

    product: str
    local_date: date
    limit: int
    waiting: tuple[DigestRow, ...]
    paused: tuple[DigestRow, ...]
    running: tuple[DigestRow, ...]
    merged: WindowedSection
    closed: WindowedSection

    def __post_init__(self) -> None:
        _positive("limit", self.limit)
        if SLUG.fullmatch(self.product) is None:
            raise ValueError(f"product is not a slug: {self.product!r}")
        if type(self.local_date) is not date:
            raise ValueError(f"local_date is not a plain date: {self.local_date!r}")


def digest_message(view: DigestView, repo: str) -> Message:
    """The daily digest: the snapshot sections first, then the windowed ones, empty ones omitted.
    A cut windowed section says `{limit}+` in its heading."""
    repo = repo_url(repo)

    def block(
        head: str, rows: Sequence[DigestRow], kind: str, extra: Sequence[_Line] = ()
    ) -> list[_Line]:
        lines = [_join(head), *extra]
        for row in rows:
            why = _join(" - paused: ", clean(row.reason)) if row.reason is not None else ""
            lines.append(
                _join(_ref(repo, kind, row.number), " ", clean(row.title) or NO_TITLE, why)
            )
        return lines

    def windowed(name: str, section: WindowedSection, kind: str) -> list[_Line]:
        count = f"{view.limit}+" if section.cut else str(len(section.rows))
        note = [_join(f"More than {view.limit} items share one instant; some are not listed.")]
        return block(f"{name} ({count})", section.rows, kind, note if section.single_group else ())

    snapshots = (
        ("Waiting for you", view.waiting),
        ("Paused", view.paused),
        ("Running", view.running),
    )
    sections = [block(f"{name} ({len(rows)})", rows, "issues") for name, rows in snapshots if rows]
    for name, section, kind in (
        ("Merged pull requests", view.merged, "pull"),
        ("Closed issues", view.closed, "issues"),
    ):
        if section.rows or section.cut:
            sections.append(windowed(name, section, kind))
    day = view.local_date.isoformat()
    header = (
        [_join(f"Daily digest {day}")]
        if sections
        else [_join(f"Daily digest {day}"), _join(NOTHING)]
    )
    return _compose(f"[{view.product}] Daily digest {day}", header, sections)
