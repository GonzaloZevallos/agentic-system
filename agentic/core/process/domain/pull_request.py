"""The body of a task's pull request, bounded to what GitHub accepts (ADR-0011).

GitHub refuses a body over 65,536 characters, counted in UTF-16 code units. The body
`deliver` sends carries the definition, the plan and the design's observations, which
a large task makes longer than that, so what does not fit is left out and a pointer to
the task document, which holds every section whole, takes its place. Pure: the pointer's
text is the caller's, because only the caller knows where the document is."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Under GitHub's 65,536 with room for `Closes #N\n\n` (the tracker's own prefix).
PR_BODY_MAX = 60_000

TRUNCATED = "(truncated; full text in the task document)"
POINTER_HEADING = "Task document"
_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class BoundedBody:
    text: str
    # True when the text carries a pointer to the task document: the caller commits the
    # document only then.
    bounded: bool
    # The headings left out or trimmed, in order.
    omitted: tuple[str, ...]


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
    way to it. With `""` the result is exactly what it was before it existed."""
    if preface:
        room = limit - github_length(preface) - len(_SEPARATOR)
        inner = bounded_body(parts, fallback, pointer, room)
        return BoundedBody(
            f"{preface}{_SEPARATOR}{inner.text}", inner.bounded, inner.omitted
        )
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
