"""The Developer's coverage map (GH-174): where each plan invariant and each item of the
subtask brief landed in the diff, as lines the delivery carries in its `coverage` field.

This module owns the format and nothing else re-parses a line. Pure: it decides only on
the delivery's `coverage` field and the number of plan invariants, never on `markdown`,
the working tree or the invariant's own text. The check is of format, not of truth: the
map is the reviewer's starting list, a claim the review still verifies on the diff.

Accepted lines:

- `invariant N: <path>:<line>[, <path>:<line>...]`
- `invariant N: unsatisfiable, see Decisions`
- `invariant N: not touched by this subtask: <one phrase>`
- `brief: <item> -> <path>:<line>[, ...]` (rendered, never checked)

Parsing is lenient in two ways: surrounding whitespace is stripped and the word
`invariant` matches in any case. An out-of-range or duplicate number is ignored."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

UNSATISFIABLE = "unsatisfiable, see Decisions"
NOT_TOUCHED = "not touched by this subtask"
# How many times one run may be refused for a map that leaves an invariant out; the
# counter lives in `application/coverage_guard.py`, the cap here with the format.
REFUSALS = 1

MAPPED_FORM = "invariant N: <path>:<line>[, <path>:<line>...]"
UNSATISFIABLE_FORM = f"invariant N: {UNSATISFIABLE}"
NOT_TOUCHED_FORM = f"invariant N: {NOT_TOUCHED}: <one phrase>"

_INVARIANT = re.compile(r"invariant\s+(\d+)\s*:\s*(.*)", re.IGNORECASE)
_LOCATION = re.compile(r"\S+:\d+")
_BRIEF = re.compile(r"brief:\s*\S.*->\s*\S.*")

Kind = Literal["mapped", "unsatisfiable", "not_touched", "brief"]


@dataclass(frozen=True)
class Entry:
    """One accepted line, verbatim (whitespace stripped), and the invariant it covers."""

    kind: Kind
    number: int | None
    text: str


@dataclass(frozen=True)
class Parsed:
    """`entries` are the accepted lines in emitted order; `missing` the numbers in
    `1..count` with no accepted line, and `malformed` those among them that had a line
    which was not one of the accepted forms."""

    entries: tuple[Entry, ...]
    missing: tuple[int, ...]
    malformed: tuple[int, ...]


def parse(lines: object, count: int) -> Parsed:
    """The one parser. `lines` is what the delivery's `coverage` field held: a string of
    lines or a list of them. Anything else, or a non-string item, reads as no line."""
    entries: list[Entry] = []
    covered: set[int] = set()
    seen: set[int] = set()
    for line in _split(lines):
        invariant = _INVARIANT.fullmatch(line)
        if invariant is None:
            if _BRIEF.fullmatch(line):
                entries.append(Entry("brief", None, line))
            continue
        number = int(invariant.group(1))
        if not 1 <= number <= count:
            continue
        seen.add(number)
        kind = _kind(invariant.group(2).strip())
        if kind is not None and number not in covered:
            covered.add(number)
            entries.append(Entry(kind, number, line))
    missing = tuple(n for n in range(1, count + 1) if n not in covered)
    return Parsed(tuple(entries), missing, tuple(n for n in missing if n in seen))


def missing_invariants(tool_input: object, count: int) -> str | None:
    """The refusal for a delivery whose map leaves out one of the `count` plan
    invariants, or `None` when every one has an accepted line. Reads only the
    `coverage` field of the payload; a payload that is not a dict reads as all missing."""
    if count <= 0:
        return None
    field = tool_input.get("coverage") if isinstance(tool_input, dict) else None
    parsed = parse(field, count)
    if not parsed.missing:
        return None
    numbers = ", ".join(str(n) for n in parsed.missing)
    reason = f"The coverage map has no accepted line for invariant(s) {numbers}."
    if parsed.malformed:
        bad = ", ".join(str(n) for n in parsed.malformed)
        reason += f" The line for {bad} is malformed."
    return (
        f"{reason} Map every plan invariant, one line each, in one of these forms: "
        f"`{MAPPED_FORM}`, `{UNSATISFIABLE_FORM}`, `{NOT_TOUCHED_FORM}`. "
        "An invariant with no location is work still to do."
    )


def render(lines: object, count: int) -> str | None:
    """The `### Coverage` block of the Developer's section, or `None` when the plan has
    no invariants. The accepted lines verbatim in emitted order, then the numbers of
    the invariants not touched, claimed unsatisfiable and not mapped; an empty group is
    omitted."""
    if count <= 0:
        return None
    parsed = parse(lines, count)
    groups = [
        ("Not touched", _numbers(parsed, "not_touched")),
        ("Claimed unsatisfiable", _numbers(parsed, "unsatisfiable")),
        ("Not mapped", list(parsed.missing)),
    ]
    summary = [f"{name}: {', '.join(map(str, nums))}" for name, nums in groups if nums]
    parts = ["\n".join(["### Coverage", *(e.text for e in parsed.entries)])]
    if summary:
        parts.append("\n".join(summary))
    return "\n\n".join(parts)


def _numbers(parsed: Parsed, kind: Kind) -> list[int]:
    return sorted(e.number for e in parsed.entries if e.kind == kind and e.number is not None)


def _kind(body: str) -> Kind | None:
    if body == UNSATISFIABLE:
        return "unsatisfiable"
    if body.startswith(f"{NOT_TOUCHED}:"):
        return "not_touched" if body[len(NOT_TOUCHED) + 1 :].strip() else None
    tokens = [token.strip() for token in body.split(",")]
    return "mapped" if all(_LOCATION.fullmatch(token) for token in tokens) else None


def _split(lines: object) -> list[str]:
    if isinstance(lines, str):
        raw = [lines]
    elif isinstance(lines, list):
        raw = [item for item in lines if isinstance(item, str)]
    else:
        return []
    return [line.strip() for item in raw for line in item.splitlines() if line.strip()]
