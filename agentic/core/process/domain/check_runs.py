"""Which Bash calls of a Developer are a run of a declared check (GH-467, ADR-0008).

The decision is pure: the command text and the declared checks arrive as arguments, and the
caller (the Bash hooks, in the adapter) stamps the time, scrubs the segment and writes it.
No clock and no I/O here, so the decision is the same in every test and on every machine.

A command is split on `&&`, `;`, `||`, `|` and a lone `&` (quotes and `2>&1` / `&>` are not
separators). In each segment the leading `NAME=value` assignments are skipped (they may hold a
secret and are never part of what is recorded), then `nohup`/`setsid` and `uv run`. What is
left is matched against the declared checks by its argv prefix: the program plus its first
subcommand or module (`ruff check`, `python -m pytest`, `pytest`). `git`, `python`, `uv`,
`bash` and `sh` alone are never a prefix: a check whose only prefix would be one of them, or
a call that is only one of them, matches nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .presubmit import RerunForm

# Programs that stand for a thousand unrelated commands: never matched without a second token.
_GENERIC = frozenset({"git", "python", "python3", "uv", "bash", "sh"})
_PYTHONS = frozenset({"python", "python3"})
_DETACHERS = frozenset({"nohup", "setsid"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PLACEHOLDER = re.compile(r"^\{[a-z_]+\}$")
# A declared form of a check is passed as `<name>#<form>` (`tests#affected`); the name is what
# is recorded.
_FORM_SEPARATOR = "#"


@dataclass(frozen=True)
class Match:
    """The segment of a Bash command that ran a declared check. `segment` is the text as
    written minus its leading assignments, not yet scrubbed. `check` is the declared name on
    an exact match with a declared command or form, `None` on a prefix match only.
    `status_decided` is False when a pipe or a later chained segment decides the exit status
    of the command, so the segment's own code is unknown. `detached` is True when the segment
    is backgrounded or started under `nohup`/`setsid`, so its end is not the call's end."""

    segment: str
    check: str | None
    status_decided: bool
    detached: bool


@dataclass(frozen=True)
class _Segment:
    text: str
    backgrounded: bool


def match(command: str, declared: Mapping[str, str]) -> Match | None:
    """The first segment of `command` that runs one of the `declared` checks, or None.
    `declared` maps a check name to its command; a rerun or affected form of the same check
    is passed under `<name>#<form>`."""
    forms = [
        (name.partition(_FORM_SEPARATOR)[0], _normalised(cmd)) for name, cmd in declared.items()
    ]
    segments = _split(command)
    for index, segment in enumerate(segments):
        words, offset, detached = _words_of(segment.text)
        found = _against(words, forms)
        if found is not None:
            return Match(
                segment=segment.text[offset:].strip(),
                check=found[0],
                status_decided=index == len(segments) - 1,
                detached=detached or segment.backgrounded,
            )
    return None


def _against(words: list[str], forms: list[tuple[str, list[str]]]) -> tuple[str | None] | None:
    """`(name,)` for a call that equals a declared form, `(None,)` for one that only shares
    its prefix, None for no match."""
    prefix_hit = False
    for name, form in forms:
        prefix = _prefix(form)
        if prefix is None or words[: len(prefix)] != prefix:
            continue
        if _equal(words, form):
            return (name,)
        prefix_hit = True
    return (None,) if prefix_hit else None


def _equal(words: list[str], form: list[str]) -> bool:
    return len(words) == len(form) and all(
        _PLACEHOLDER.match(want) is not None or want == got for want, got in zip(form, words)
    )


def _prefix(form: list[str]) -> list[str] | None:
    """The program plus its first subcommand or module, None when that is a generic program
    alone."""
    if not form:
        return None
    rest = form[1:]
    if form[0] in _PYTHONS and len(rest) >= 2 and rest[0] == "-m":
        return form[:3]
    if rest and not rest[0].startswith("-") and _PLACEHOLDER.match(rest[0]) is None:
        return form[:2]
    return None if form[0] in _GENERIC else form[:1]


def _normalised(command: str) -> list[str]:
    return _words_of(command)[0]


def _words_of(text: str) -> tuple[list[str], int, bool]:
    """The words of one segment with assignments, detachers and `uv run` skipped, the offset
    in `text` where the first word after the assignments starts, and whether a detacher was
    skipped."""
    spans = _tokens(text)
    first = 0
    while first < len(spans) and _ASSIGNMENT.match(spans[first][0]):
        first += 1
    offset = spans[first][1] if first < len(spans) else len(text)
    words = [word for word, _ in spans[first:]]
    detached = False
    while words and words[0] in _DETACHERS:
        detached = True
        words = words[1:]
    if len(words) >= 2 and words[0] == "uv" and words[1] == "run":
        words = words[2:]
        while words and words[0].startswith("-"):
            words = words[1:]
    return words, offset, detached


def _tokens(text: str) -> list[tuple[str, int]]:
    """Shell words with their start offsets: quotes and backslashes removed from the word,
    never expanded."""
    out: list[tuple[str, int]] = []
    current: list[str] = []
    start = -1
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"' and index + 1 < len(text):
                index += 1
                current.append(text[index])
            else:
                current.append(char)
        elif char in "'\"":
            quote = char
            start = index if start < 0 else start
        elif char == "\\" and index + 1 < len(text):
            start = index if start < 0 else start
            index += 1
            current.append(text[index])
        elif char.isspace():
            if start >= 0:
                out.append(("".join(current), start))
                current, start = [], -1
        else:
            start = index if start < 0 else start
            current.append(char)
        index += 1
    if start >= 0:
        out.append(("".join(current), start))
    return out


def _split(command: str) -> list[_Segment]:
    """The non-empty segments of `command` between `&&`, `;`, `||`, `|`, a newline and a lone
    `&` (which backgrounds the segment before it)."""
    segments: list[_Segment] = []
    start = 0
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "\\":
            index += 1
        elif char in ";|&\n":
            width = 2 if command[index : index + 2] in ("&&", "||") else 1
            if char == "&" and width == 1 and _is_redirect(command, index):
                index += 1
                continue
            segments.append(_Segment(command[start:index], char == "&" and width == 1))
            index += width
            start = index
            continue
        index += 1
    segments.append(_Segment(command[start:], False))
    return [segment for segment in segments if segment.text.strip()]


def _is_redirect(command: str, index: int) -> bool:
    """`&` that is part of `2>&1` or `&>file`, not a separator."""
    before = command[index - 1] if index else ""
    after = command[index + 1] if index + 1 < len(command) else ""
    return before in "<>" or after == ">"


def declared_forms(
    checks: Mapping[str, str], affected: Mapping[str, str], rerun: Mapping[str, RerunForm]
) -> dict[str, str]:
    """The commands `match` is given: every declared check under its name, and its affected
    and failed-only forms under `<name>#affected` and `<name>#rerun`, so a call that equals a
    form is recorded as that check (a form of an undeclared check is left out)."""
    forms = dict(checks)
    for name, command in affected.items():
        if name in checks:
            forms[f"{name}{_FORM_SEPARATOR}affected"] = command
    for name, form in rerun.items():
        if name in checks:
            forms[f"{name}{_FORM_SEPARATOR}rerun"] = form.command
    return forms
