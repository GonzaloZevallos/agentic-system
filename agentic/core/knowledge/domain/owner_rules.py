"""The owner's rules as text (GH-431): what a marker in a tracker comment says, the id of an
entry and the append-only write of a decisions file.

A rule reaches a decisions file (`docs/decisiones-del-dueño.md`, `docs/product/...`) only
through `parse` and `append`, and only the text of a comment whose author is the configured
owner (invariant 3). The text that gets written is limited so that it cannot carry anything but
a rule (invariant 4); an entry is an append-only record with a content-addressed id
(invariant 5), so parallel branches that each add a rule need no shared counter (the same
problem ADR-0049 solves for ADR numbers); the only line of an existing entry that ever changes is
its rule, struck through when a later entry replaces it (invariant 6).

Pure: text in, text out. Which file a rule goes to, and reading the tracker, are not here.
`live`, the entries that are not struck, belongs to `knowledge`.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from agentic.core.process.domain.task import OwnerRuleRejection

Scope = Literal["product", "system"]

MAX_RULE_CHARS = 400

# The marker: `Owner's rule`, optionally one parenthesis, a colon. Anchored at the start of a
# line: a quoted line (`> Owner's rule: ...`) or the phrase in the middle of one is no marker.
_MARKER = re.compile(r"Owner's rule(?: \((?P<paren>[^)]*)\))?[ \t]*:(?P<text>.*)")
# A line that opens like a marker with a parenthesis the pattern above does not close or
# colon: still an attempt at one, so reported rather than dropped.
_ATTEMPT = re.compile(r"Owner's rule \((?P<paren>[^:]*)(?::(?P<text>.*))?")
_PAREN = re.compile(r"(?P<system>system)?(?:, )?(?:replaces (?P<id>D-[0-9a-f]{8}))?")
_LINK = re.compile(r"\]\(|https?://", re.IGNORECASE)
_SPACES = re.compile(r"\s+")

_ID = r"D-[0-9a-f]{8}"
_HEADING = re.compile(
    rf"### (?P<id>{_ID}) · (?P<date>\d{{4}}-\d{{2}}-\d{{2}})(?: \(reemplazada por (?P<by>{_ID})\))?"
)
_STRUCK = re.compile(r"~~(?P<text>.*)~~")
_ADR = re.compile(r"ADR-\d{4}")
_METADATA = ("Origen:", "Reemplaza:", "Rozan:")


@dataclass(frozen=True)
class OwnerRule:
    """A rule the owner marked, ready to be written: `text` has passed every limit, `scope`
    names the file (`system`: the system's own, `product`: the product's), `replaces` the id
    of the entry it supersedes, `created_at` the comment's time and `url` where it came from."""

    text: str
    scope: Scope
    replaces: str | None
    created_at: str
    url: str


@dataclass(frozen=True)
class Rejected:
    """A marked rule that is not written, with the closed class of why. `system` is set by
    the caller (a rule for the system in a repo that has no file of the system's); `parse`
    sets the other five."""

    text: str
    cls: OwnerRuleRejection


@dataclass(frozen=True)
class Entry:
    """One entry of a decisions file as `parse_file` reads it. `text` is the rule without the
    strike-through, `struck` whether a later entry replaced it, `replaces` the id its
    `Reemplaza:` line cites and `rozan` the ADRs its `Rozan:` lines cite."""

    id: str
    date: str
    text: str
    replaces: str | None
    rozan: tuple[str, ...]
    struck: bool


def entry_id(text: str) -> str:
    """`D-` and the first eight hex digits of the sha256 of the text, normalised: NFC,
    whitespace collapsed, case folded. The id of a rule is its content, never a counter."""
    return "D-" + hashlib.sha256(_normalised(text).encode("utf-8")).hexdigest()[:8]


def _normalised(text: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def parse(
    comments: Sequence[tuple[str, str, str, str]], owner: str
) -> tuple[list[OwnerRule], list[Rejected]]:
    """The rules the owner marked in `comments` (body, author, created_at, url), and the
    marked text that was refused. A comment whose author is not exactly `owner` is ignored
    whatever it says, and so is every comment when `owner` is empty (invariant 3). A rule is
    one line: a line right after the marker that is neither blank nor another marker is a
    continuation, and the rule is rejected as `newline`."""
    accepted: list[OwnerRule] = []
    rejected: list[Rejected] = []
    if not owner:
        return accepted, rejected
    for body, author, created_at, url in comments:
        if author != owner:
            continue
        lines = body.splitlines()
        for index, line in enumerate(lines):
            marked = _read_marker(line.strip())
            if marked is None:
                continue
            text, scope, replaces, bad_scope = marked
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            continued = bool(following) and not following.startswith("Owner's rule")
            cls = _refusal(text, bad_scope, continued)
            if cls is not None:
                rejected.append(Rejected(text, cls))
            elif text and scope is not None:
                accepted.append(OwnerRule(text, scope, replaces, created_at, url))
    return accepted, rejected


def _read_marker(line: str) -> tuple[str, Scope | None, str | None, bool] | None:
    """(text, scope, replaces, bad_scope) of a marker line, `None` when it is not one."""
    found = _MARKER.fullmatch(line)
    if found is None:
        attempt = _ATTEMPT.fullmatch(line)
        if attempt is None:
            return None
        return (attempt["text"] or "").strip(), None, None, True
    text = found["text"].strip()
    paren = found["paren"]
    if paren is None:
        return text, "product", None, False
    parts = _PAREN.fullmatch(paren)
    if parts is None or not paren:
        return text, None, None, True
    scope: Scope = "system" if parts["system"] else "product"
    return text, scope, parts["id"], False


def _refusal(text: str, bad_scope: bool, continued: bool) -> OwnerRuleRejection | None:
    """The first limit the rule breaks, `None` when none, or when there is no rule at all
    (an empty marker with nothing after it)."""
    if bad_scope:
        return "unknown_scope"
    if continued:
        return "newline"
    if not text:
        return None
    if "{{" in text or "}}" in text:
        return "template"
    if _LINK.search(text):
        return "link"
    if len(text) > MAX_RULE_CHARS:
        return "too_long"
    return None


def append(file_text: str, rules: Sequence[OwnerRule]) -> str:
    """`file_text` with each rule whose id is not in it yet appended at the end, a blank line
    before and the entry after it: heading (`### D-<hex> · <date>`), the rule, `Origen:` and,
    when it replaces one, `Reemplaza:`. The replaced entry's rule line is struck through and
    its heading says by which entry, in the same text. Idempotent by id; never deletes."""
    result = file_text
    for rule in rules:
        rid = entry_id(rule.text)
        if _has_entry(result, rid):
            continue
        if rule.replaces:
            result = _strike(result, rule.replaces, rid)
        result = _with_entry(result, rid, rule)
    return result


def _has_entry(text: str, rid: str) -> bool:
    return any(
        (found := _HEADING.fullmatch(line)) and found["id"] == rid for line in text.splitlines()
    )


def _with_entry(text: str, rid: str, rule: OwnerRule) -> str:
    lines = [
        f"### {rid} · {rule.created_at[:10]}",
        rule.text,
        f"Origen: {rule.url or 'mensaje del dueño'}",
        *([f"Reemplaza: {rule.replaces}"] if rule.replaces else []),
    ]
    closed = text if not text or text.endswith("\n") else text + "\n"
    separator = "\n" if closed else ""
    return closed + separator + "\n".join(lines) + "\n"


def _strike(text: str, old_id: str, new_id: str) -> str:
    """`text` with the entry `old_id` marked as replaced by `new_id`: its rule line between
    `~~` and its heading suffixed. An entry that is absent or already struck stays as it is."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        found = _HEADING.fullmatch(line.rstrip("\r\n"))
        if not found or found["id"] != old_id or found["by"]:
            continue
        rule_at = _rule_line(lines, i + 1)
        if rule_at is None or _STRUCK.fullmatch(lines[rule_at].strip()):
            return text
        lines[i] = f"{line.rstrip(chr(13) + chr(10))} (reemplazada por {new_id})\n"
        lines[rule_at] = f"~~{lines[rule_at].strip()}~~\n"
        return "".join(lines)
    return text


def _rule_line(lines: list[str], start: int) -> int | None:
    """The index of the rule line of the entry whose heading is right before `start`: the
    first line that is neither blank nor a metadata line, before the next heading."""
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            return None
        if stripped and not stripped.startswith(_METADATA):
            return i
    return None


def parse_file(text: str) -> list[Entry]:
    """The entries of a decisions file, in file order: every `### D-<hex> · <date>` heading
    with the lines under it up to the next heading. Text that is no entry is ignored."""
    entries: list[Entry] = []
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _HEADING.fullmatch(line.rstrip())]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        heading = _HEADING.fullmatch(lines[start].rstrip())
        assert heading is not None
        body = lines[start + 1 : end]
        rule_at = _rule_line(body, 0)
        rule = body[rule_at].strip() if rule_at is not None else ""
        struck = _STRUCK.fullmatch(rule)
        replaces = next(
            (line.split(":", 1)[1].strip() for line in body if line.startswith("Reemplaza:")),
            None,
        )
        rozan = tuple(
            adr for line in body if line.startswith("Rozan:") for adr in _ADR.findall(line)
        )
        entries.append(
            Entry(
                id=heading["id"],
                date=heading["date"],
                text=struck["text"] if struck else rule,
                replaces=replaces,
                rozan=rozan,
                struck=bool(struck),
            )
        )
    return entries
