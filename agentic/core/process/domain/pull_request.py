"""The body of a task's pull request, bounded to what GitHub accepts (ADR-0011).

GitHub refuses a body over 65,536 characters, counted in UTF-16 code units. The body
`deliver` sends carries the definition, the plan and the design's observations, which
a large task makes longer than that, so what does not fit is left out and a pointer to
the task document, which holds every section whole, takes its place. Pure: the pointer's
text is the caller's, because only the caller knows where the document is."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeIs, get_args

from .task import assumption_text

# Under GitHub's 65,536 with room for `Closes #N\n\n` (the tracker's own prefix).
PR_BODY_MAX = 60_000

TRUNCATED = "(truncated; full text in the task document)"
POINTER_HEADING = "Task document"
ASSUMPTIONS_HEADING = "Assumptions"
# The most delivery-assumption rows the body writes; the rest are counted, not shown.
ASSUMPTIONS_SHOWN = 20
_SEPARATOR = "\n\n"


RejectReason = Literal["missing", "multiline", "too_long", "forbidden_char"]
"""Why a recorded API break is refused (`api_break_reason`): closed, so a comment can name it."""

API_BREAK_REASON_MAX = 240
_REASON_FORBIDDEN = ("`", "{{", "](", "://", "http")


def api_break_reason(text: str) -> str | RejectReason:
    """The reason of an approved API break as the pull request body may carry it, or the closed
    reason it is refused: one non-empty line (`missing`, `multiline`), at most
    `API_BREAK_REASON_MAX` characters (`too_long`), with no backtick, `{{` or link
    (`forbidden_char`). Surrounding blanks are trimmed. Pure."""
    reason = text.strip()
    if not reason:
        return "missing"
    if "\n" in reason or "\r" in reason:
        return "multiline"
    if len(reason) > API_BREAK_REASON_MAX:
        return "too_long"
    if any(bad in reason.lower() for bad in _REASON_FORBIDDEN):
        return "forbidden_char"
    return reason


def is_reject_reason(value: str) -> TypeIs[RejectReason]:
    """Whether an `api_break_reason` answer is one of its refusals (a reason that is itself the
    word `missing` is a refusal too: the caller never needs to tell them apart)."""
    return value in get_args(RejectReason)


def declared_reason(body: str, prefix: str) -> str | None:
    """The reason of the first line of `body` that starts with `prefix`, optional blanks and a
    non-empty reason, else `None`: `\r\n` read as `\n`, the rule of apostrophe's
    `scripts/docs/api_breaks.py` (ADR-0066), copied here. The only place a body is read for it."""
    pattern = rf"^{re.escape(prefix)}[ \t]*(\S.*?)[ \t]*$"
    found = re.search(pattern, body.replace("\r\n", "\n"), re.MULTILINE)
    return found.group(1) if found else None


def declares(body: str, prefix: str) -> bool:
    """Whether `body` carries the required line (`declared_reason`)."""
    return declared_reason(body, prefix) is not None


def with_required_lines(body: str, prefix: str, reason: str) -> str:
    """`body` with the line `<prefix> <reason>` first when it does not declare one already;
    a body that declares it is returned as it is. The only place the line is concatenated."""
    if declares(body, prefix):
        return body
    return f"{prefix} {reason}{_SEPARATOR}{body}"


@dataclass(frozen=True)
class BoundedBody:
    text: str
    # True when the text carries a pointer to the task document: the caller commits the
    # document only then.
    bounded: bool
    # The headings left out or trimmed, in order.
    omitted: tuple[str, ...]


# The marker the system writes in the body of the one terminal child of a part that split
# (GH-592): that child's pull request closes the parent part's issue too, which releases the
# issues blocked by it. An HTML comment, invisible once rendered.
_CLOSES_MARKER = re.compile(r"<!-- agentic-closes: #(\d+) -->")


def closes_marker(issue: int) -> str:
    """The marker that tells a child's delivery to close issue `issue` as well."""
    return f"<!-- agentic-closes: #{issue} -->"


def closes_line(description: str) -> str:
    """The `Closes #N` line a task's pull request carries for the parent part its issue body
    names, or `""` for a task that names none."""
    match = _CLOSES_MARKER.search(description)
    return f"Closes #{match.group(1)}" if match else ""


def github_length(text: str) -> int:
    """The unit GitHub's limit is in: UTF-16 code units, so an emoji counts two."""
    return len(text.encode("utf-16-le")) // 2


def _toggles_fence(line: str) -> bool:
    stripped = line.lstrip(" ")
    return stripped.startswith("```") or stripped.startswith("~~~")


def fence_open(lines: Sequence[str]) -> bool:
    """Whether `lines` end inside a fenced block: a line that starts with a fence
    toggles it."""
    inside = False
    for line in lines:
        if _toggles_fence(line):
            inside = not inside
    return inside


def bounded_body(
    parts: Sequence[tuple[str, str]],
    fallback: str,
    pointer: Callable[[tuple[str, ...]], str],
    limit: int = PR_BODY_MAX,
    *,
    preface: str = "",
    required: str = "",
    assumptions: Sequence[str] = (),
) -> BoundedBody:
    """`parts` are `(heading, text)` pairs rendered as `## heading` sections. A body
    within `limit` is exactly their join, or `fallback` when there are none. A longer one
    keeps whole sections from the front while they fit, trims the first section (the
    PM's; on a platform task, which has none, whichever comes first) and only it, at a
    blank line outside a fence, drops every other whole, and ends with the pointer, which
    names what was left out.

    Room for the pointer is reserved for the longest list it can be given (every
    heading), so the real one, shorter or equal, always fits.

    A `preface` (what a replace of the remote branch overwrote, GH-204) leads the body as
    its own block: it counts against `limit` and is never trimmed -- the sections give
    way to it. With `""` the result is exactly what it was before it existed. A `required`
    line (the product's `Breaking-API:` line) is reserved the same way, but it is not added:
    the caller puts it in front (`with_required_lines`) and the sections give way to its room.

    The `assumptions` (a spec delivery's, GH-594) are the texts of its rows: after the preface
    they lead the sections as an `## Assumptions` block of `Assumption: <text>` lines
    (`_assumptions_block`), reserved like the preface so the sections give way and the block is
    never dropped. With `()`, or only rows `assumption_text` refuses, the result is exactly what
    it was before they existed."""
    if required:
        room = limit - github_length(required) - len(_SEPARATOR)
        return bounded_body(
            parts, fallback, pointer, room, preface=preface, assumptions=assumptions
        )
    if preface:
        room = limit - github_length(preface) - len(_SEPARATOR)
        inner = bounded_body(parts, fallback, pointer, room, assumptions=assumptions)
        return BoundedBody(f"{preface}{_SEPARATOR}{inner.text}", inner.bounded, inner.omitted)
    if assumptions:
        headings = tuple(heading for heading, _ in parts)
        reserved = github_length(_pointer_block(pointer, headings)) + len(_SEPARATOR)
        block = _assumptions_block(assumptions, limit - reserved - len(_SEPARATOR))
        if block:
            room = limit - github_length(block) - len(_SEPARATOR)
            inner = bounded_body(parts, fallback, pointer, room)
            return BoundedBody(f"{block}{_SEPARATOR}{inner.text}", inner.bounded, inner.omitted)
    rendered = [f"## {heading}\n\n{text}" for heading, text in parts]
    full = _SEPARATOR.join(rendered) or fallback
    if github_length(full) <= limit:
        return BoundedBody(full, False, ())

    headings = tuple(heading for heading, _ in parts)
    budget = limit - github_length(_pointer_block(pointer, headings)) - len(_SEPARATOR)
    kept: list[str] = []
    used = 0
    for section in rendered:
        cost = github_length(section) + (len(_SEPARATOR) if kept else 0)
        if used + cost > budget:
            break
        kept.append(section)
        used += cost

    omitted = headings[len(kept) :]
    if not kept and rendered:
        trimmed = _trimmed(rendered[0], budget)
        if trimmed is not None:
            kept.append(trimmed)
    blocks = [*kept, _pointer_block(pointer, omitted)]
    return BoundedBody(_SEPARATOR.join(blocks), True, omitted)


def _assumptions_block(texts: Sequence[str], room: int) -> str:
    """The `## Assumptions` block for the rows `assumption_text` accepts (first of each canonical
    text), `""` when there are none: at most `ASSUMPTIONS_SHOWN` whole rows, as many as fit in
    `room` together with the heading and the closing line `…and N more in the task document`,
    which counts the rows left out and is written whenever there are any. When the heading and
    the closing line do not fit together, only the closing line is."""
    rows = list(dict.fromkeys(c for c in map(assumption_text, texts) if c is not None))
    lines = [f"Assumption: {row}" for row in rows]
    if not lines:
        return ""
    for shown in range(min(len(lines), ASSUMPTIONS_SHOWN), -1, -1):
        closing = (
            [f"…and {len(lines) - shown} more in the task document"] if shown < len(lines) else []
        )
        block = f"## {ASSUMPTIONS_HEADING}\n\n" + "\n".join([*lines[:shown], *closing])
        if github_length(block) <= room:
            return block
    return f"…and {len(lines)} more in the task document"


def _pointer_block(pointer: Callable[[tuple[str, ...]], str], omitted: tuple[str, ...]) -> str:
    return f"## {POINTER_HEADING}\n\n{pointer(omitted)}"


def _trimmed(section: str, budget: int) -> str | None:
    """`section` (heading, blank line, text) cut at the last blank line that is outside
    a fence and leaves room for the closing line within `budget`; `None` when there is
    none, so the section is dropped whole instead."""
    closing = _SEPARATOR + TRUNCATED
    room = budget - github_length(closing)
    lines = section.split("\n")
    best: int | None = None
    inside = False
    has_text = False
    used = 0  # the length of `lines[:number]` joined
    for number, line in enumerate(lines):
        if used > room:
            break
        if number >= 2 and not line.strip() and not inside and has_text:
            best = number
        if number >= 2 and line.strip():
            has_text = True
        if _toggles_fence(line):
            inside = not inside
        used += github_length(line) + (1 if number else 0)
    if best is None:
        return None
    return "\n".join(lines[:best]) + closing
