"""The Developer's coverage map (GH-174): where each plan invariant and each item of the
subtask brief landed in the diff, as lines the delivery carries in its `coverage` field.

This module owns the format and nothing else re-parses a line. Pure: it decides only on
the delivery's `coverage` field and the number of plan invariants, never on `markdown`,
the working tree or the invariant's own text. The check is of format, not of truth: the
map is the reviewer's starting list, a claim the review still verifies on the diff.

Accepted lines:

- `invariant N: <path>:<line>[-<last>][ (<comment>)][, <path>:<line>…]`: a location is a
  line or a range of lines, each one may carry one trailing comment in parentheses, and
  the commas inside a comment do not separate locations
- `invariant N: unsatisfiable, see Decisions`
- `invariant N: not touched by this subtask: <one phrase>`
- `brief: <item> -> <path>:<line>[, …]` (rendered, never checked)

`classify` holds the whole grammar of the body of an `invariant N:` line, and the reason a
body was refused; nothing else reads the shape of a body. `EXAMPLE` is the map the prompts
quote to the Developer.

Parsing is lenient in two ways: surrounding whitespace is stripped and the word
`invariant` matches in any case. An out-of-range or duplicate number is ignored."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

UNSATISFIABLE = "unsatisfiable, see Decisions"
NOT_TOUCHED = "not touched by this subtask"
# How many times one run may be refused for a map that leaves an invariant out; the
# counter lives in `application/coverage_guard.py`, the cap here with the format.
REFUSALS = 1
# How many refused lines the refusal quotes; the rest are counted.
_QUOTED = 3

MAPPED_FORM = "invariant N: <path>:<line>[-<last>][ (<comment>)][, <path>:<line>…]"
BRIEF_FORM = "brief: <item> -> <path>:<line>[, …]"
UNSATISFIABLE_FORM = f"invariant N: {UNSATISFIABLE}"
NOT_TOUCHED_FORM = f"invariant N: {NOT_TOUCHED}: <one phrase>"
# The complete map the refusal, the schema and the Developer prompt quote; the prompt
# carries it as a literal fenced block that a test pins against this constant.
EXAMPLE = (
    "invariant 1: src/app/main:42\n"
    "invariant 2: tests/app/main_test:10-24 (new test, inserted before the helper)\n"
    f"invariant 3: {NOT_TOUCHED}: only the prompts change\n"
    "brief: add the parser -> src/app/main:30-60"
)

_INVARIANT = re.compile(r"invariant\s+(\d+)\s*:\s*(.*)", re.IGNORECASE)
# The body of an `invariant N:` line, used by `classify` alone. `_PLAIN` is the original
# location, kept so that no line accepted before the range and the comment is refused now.
_PLAIN = re.compile(r"\S+:\d+")
# A comment holds no parentheses of its own, except an empty `()` such as `parse()`.
_COMMENT = r"\((?:[^()]|\(\))*\)"
_TOKEN = re.compile(rf"(?P<path>\S+?):(?P<start>\d+)(?:-(?P<end>\d+))?(?:\s*{_COMMENT})?")
_AFTER_COMMENT = re.compile(rf"\S+?:\d+(?:-\d+)?\s*{_COMMENT}\s*\S.*")
# Slips with one mechanical fix: they are never accepted, only shown corrected.
_SLIPS = (
    (re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)"), "-"),
    (re.compile(r"(?<=\d)\.\.(?=\d)"), "-"),
    (re.compile(r"(?<=[:\-])L(?=\d)"), ""),
    (re.compile(r"#L(?=\d)"), ":"),
    (re.compile(r"(?<=\d)\.(?=\s*(?:,|$))"), ""),
)
_BRIEF = re.compile(r"brief:\s*\S.*->\s*\S.*")

Kind = Literal["mapped", "unsatisfiable", "not_touched", "brief"]


@dataclass(frozen=True)
class Entry:
    """One accepted line, verbatim (whitespace stripped), and the invariant it covers."""

    kind: Kind
    number: int | None
    text: str


@dataclass(frozen=True)
class Refusal:
    """Why `classify` refused a body. `corrected` is the body with the one mechanical slip
    fixed, built from the Developer's own text and itself accepted; `None` when the fix
    would be a guess at the location."""

    reason: str
    corrected: str | None = None


@dataclass(frozen=True)
class Rejected:
    """A line of a valid invariant number that `classify` refused, verbatim."""

    number: int
    text: str
    refusal: Refusal


@dataclass(frozen=True)
class Parsed:
    """`entries` are the accepted lines in emitted order; `missing` the numbers in
    `1..count` with no accepted line, and `malformed` those among them that had a line
    which was not one of the accepted forms. `refused` is every refused line of a number
    in `1..count`, in emitted order."""

    entries: tuple[Entry, ...]
    missing: tuple[int, ...]
    malformed: tuple[int, ...]
    refused: tuple[Rejected, ...] = ()


def parse(lines: object, count: int) -> Parsed:
    """The one parser. `lines` is what the delivery's `coverage` field held: a string of
    lines or a list of them. Anything else, or a non-string item, reads as no line."""
    entries: list[Entry] = []
    refused: list[Rejected] = []
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
        kind = classify(invariant.group(2))
        if isinstance(kind, Refusal):
            refused.append(Rejected(number, line, kind))
        elif number not in covered:
            covered.add(number)
            entries.append(Entry(kind, number, line))
    missing = tuple(n for n in range(1, count + 1) if n not in covered)
    malformed = tuple(n for n in missing if n in seen)
    return Parsed(tuple(entries), missing, malformed, tuple(refused))


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
    quoted = [r for r in parsed.refused if r.number in parsed.missing]
    if quoted:
        reason += _quote(quoted)
    return (
        f"{reason} Map every plan invariant, one line each, in one of these forms: "
        f"`{MAPPED_FORM}`, `{UNSATISFIABLE_FORM}`, `{NOT_TOUCHED_FORM}`. "
        "An invariant with no location is work still to do. For example:\n"
        f"{EXAMPLE}"
    )


def referenced_paths(lines: object) -> set[str]:
    """The paths the map's locations name, in `stale_locations`'s own spelling: what a
    caller must look up before asking it."""
    return {path for path, _, _ in _locations(lines)}


def stale_locations(
    lines: object, file_lengths: Mapping[str, int], diff_paths: set[str]
) -> list[str]:
    """A reason for each location of an `invariant N:` or `brief:` line that cannot be the
    Developer's own work: the path is not a file (absent from `file_lengths`, the length in
    lines of each file that exists), a line lies past the end of it, or the path is not in
    the diff. Unsatisfiable and not-touched lines name no location and are skipped, so are
    lines `classify` refused. A format check like the rest of this module: a location that
    passes is still a claim the review verifies. Each reason once, in emitted order."""
    reasons: list[str] = []
    for path, first, last in _locations(lines):
        where = f"{path}:{first}" + (f"-{last}" if last != first else "")
        length = file_lengths.get(path)
        if length is None:
            reason = f"`{where}`: {path} does not exist in your checkout"
        elif path not in diff_paths:
            reason = f"`{where}`: {path} is not in your diff"
        elif first < 1 or last > length:
            reason = f"`{where}`: {path} has {length} lines"
        else:
            continue
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _locations(lines: object) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    for line in _split(lines):
        invariant = _INVARIANT.fullmatch(line)
        if invariant is not None:
            body = invariant.group(2)
            if classify(body) != "mapped":
                continue
        elif _BRIEF.fullmatch(line):
            body = line.rpartition("->")[2]
        else:
            continue
        for token in _tokens(body):
            location = _TOKEN.fullmatch(token)
            if location is None:
                continue
            start = int(location["start"])
            end = int(location["end"]) if location["end"] else start
            found.append((location["path"].removeprefix("./"), start, max(start, end)))
    return found


def _tokens(body: str) -> list[str]:
    """The comma-separated pieces of `body`, commas inside parentheses not separating."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        depth = max(depth + (char == "(") - (char == ")"), 0)
        if char == "," and depth == 0:
            tokens.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tokens.append("".join(current).strip())
    return tokens


def _quote(refused: list[Rejected]) -> str:
    parts = [" Refused:"]
    for item in refused[:_QUOTED]:
        parts.append(f"\n- `{item.text}`: {item.refusal.reason}.")
        if item.refusal.corrected is not None:
            parts.append(f" Shape it as `invariant {item.number}: {item.refusal.corrected}`.")
    if len(refused) > _QUOTED:
        parts.append(f"\n- and {len(refused) - _QUOTED} more.")
    parts.append(
        "\nA corrected shape fixes the shape only: the location must be your own, "
        "the one in your diff."
    )
    return "".join(parts)


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


def classify(body: str) -> Kind | Refusal:
    """The grammar of the body of an `invariant N:` line, and the reason when it is not
    one. The unsatisfiable form and the not-touched form are exact; their phrase is never
    read. Anything else is a comma-separated list of locations, `<path>:<line>` or
    `<path>:<first>-<last>`, each with at most one trailing `( … )` comment; the commas
    and the locations inside a comment do not count, and commas separate locations
    only outside parentheses. An empty `()` inside a comment is text, not a nested one."""
    body = body.strip()
    if not body:
        return Refusal("nothing follows the colon; give a form below")
    if body == UNSATISFIABLE:
        return "unsatisfiable"
    if body.startswith(f"{NOT_TOUCHED}:"):
        if body[len(NOT_TOUCHED) + 1 :].strip():
            return "not_touched"
        return Refusal(f"`{NOT_TOUCHED}:` needs its phrase, why the diff does not touch it")
    if all(_PLAIN.fullmatch(token.strip()) for token in body.split(",")):
        return "mapped"
    tokens: list[str] = []
    current: list[str] = []
    flat: list[str] = []
    depth = 0
    nested = stray = False
    skip = False
    for at, char in enumerate(body):
        if skip:
            skip = False
            continue
        shown = char
        if char == "(" and body[at + 1 : at + 2] == ")":
            flat.append("()")
            current.append("()")
            skip = True
            continue
        if char == "(":
            nested = nested or depth > 0
            shown = "[" if depth else "("
            depth += 1
        elif char == ")":
            stray = stray or depth == 0
            depth = max(depth - 1, 0)
            shown = "]" if depth else ")"
        flat.append(shown)
        if char == "," and depth == 0:
            tokens.append("".join(current))
            current = []
        else:
            current.append(char)
    tokens.append("".join(current))
    unbalanced = stray or depth > 0
    reason: str | None = None
    if unbalanced:
        reason = "the parentheses are unbalanced; a comment opens with ( and closes with )"
    elif nested:
        reason = "parentheses inside a comment; a comment holds none of its own"
    else:
        for token in map(str.strip, tokens):
            location = _TOKEN.fullmatch(token)
            if location is None:
                reason = (
                    "an empty entry between commas"
                    if not token
                    else "a comment with no location in front of it"
                    if token.startswith("(")
                    else f"text after the closing parenthesis of `{token}`; one comment, last"
                    if _AFTER_COMMENT.fullmatch(token)
                    else f"`{token}` is not a `<path>:<line>` or `<path>:<first>-<last>` location"
                )
            elif location["end"] and int(location["end"]) < int(location["start"]):
                reason = f"the range in `{token}` ends before it starts"
            if reason is not None:
                break
    if reason is None:
        return "mapped"
    candidate = body if unbalanced else "".join(flat)
    for slip, fix in _SLIPS:
        candidate = slip.sub(fix, candidate)
    fixed = candidate != body and classify(candidate) == "mapped"
    return Refusal(reason, candidate if fixed else None)


def _split(lines: object) -> list[str]:
    if isinstance(lines, str):
        raw = [lines]
    elif isinstance(lines, list):
        raw = [item for item in lines if isinstance(item, str)]
    else:
        return []
    return [line.strip() for item in raw for line in item.splitlines() if line.strip()]
