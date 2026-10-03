"""Projecting the `Tech Lead` section for a reader with a subtask of its own
(issue #71, `plan-trim`), and the plan's own shape check (issue #139, `brief-always-
reaches`): the Developer and the reviewer always get their brief, the plan always
gives them a `### Subtasks` block to find it from, never both missing at once.

The document keeps one shape on disk, every section stored whole (ADR-0001); this
module is the prompt's own view model over it. `for_reader` takes the `Tech Lead`
section's Markdown plus the plan's subtasks and returns the section with the
`### Subtasks` block replaced by the reader's own brief — rendered from the plan's
structured record (`title`, `brief`, `seams`, `depends_on`), never by matching a
`#### <id>` heading, whose text `tech_lead.md` never fixes the shape of (invariant 3:
the only structural cut this module makes is at the `###` level, which the prompt
does fix) — followed by a one-line index of the sister subtasks, id and title only,
so nobody duplicates another's work. Everything outside that one `###`-level block
survives byte-for-byte, in order. When the section has no such block (a Tech Lead
model that used its own headings instead), the block is appended rather than
skipped: `plan_refusal` is what keeps a *new* plan from reaching that state, but a
plan written before this module existed must still project something, not nothing,
when it is resumed.

Degrades to the full section, never to an empty one (invariant 4): no reader id, an
id absent from the plan, or `integration`, whose job is the seams between subtasks,
not one of them (ADR-0015).

`subtasks_block` is the one place that knows what `### Subtasks` means: the exact
heading line and the span of the block it opens, skipping anything inside a fenced
code block. `plan_refusal` is the pure verdict over a plan's markdown and the ids its
structured output declares: `None` when the block exists with one `####` per
subtask, or else the exact reason a plan fails the check, as a model-facing string
(ADR-0023). Both read fences the same bounded way: a fence opens with up to 3 spaces
of indentation and 3 or more equal backticks or tildes, with an optional info
string; it closes with a line of the same character, at least as many, and no info
string; a fence that never closes runs to the end of the text."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .task import INTEGRATION, Subtask

# `### Subtasks` at the top level only, exactly that line (`### Subtasks:` and
# `#### Subtasks` both miss): the `#### <id>` briefs inside it start with a fourth
# `#`, so this never matches one of those.
_SUBTASKS_HEADING = re.compile(r"^### Subtasks[ \t]*$", re.M)
_NEXT_TOP_HEADING = re.compile(r"^### ", re.M)
_H4_HEADING = re.compile(r"^#### ", re.M)
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$", re.M)

_NO_BLOCK = (
    "the plan's markdown has no `### Subtasks` heading (exactly that line, outside code "
    "fences); add it with one `####` per subtask: {ids}."
)
_TOO_FEW = (
    "`### Subtasks` has {have} `####` headings but the structured output has {need} "
    "subtasks ({ids}); give each subtask its own `####`."
)


def subtasks_block(section: str) -> tuple[int, int] | None:
    """The line `### Subtasks` (matching exactly `^### Subtasks[ \t]*$`, outside any
    fenced code block) and the end of the block it opens: the start of the next
    top-level `### ` heading, also outside a fence, or the end of the text. `None`
    when no such heading exists. The only definition of the block's boundaries;
    every other module and test reads them from here."""
    fenced = _fenced_ranges(section)
    heading = next(
        (m for m in _SUBTASKS_HEADING.finditer(section) if not _in_fenced(m.start(), fenced)),
        None,
    )
    if heading is None:
        return None
    next_heading = next(
        (
            m
            for m in _NEXT_TOP_HEADING.finditer(section)
            if m.start() >= heading.end() and not _in_fenced(m.start(), fenced)
        ),
        None,
    )
    end = next_heading.start() if next_heading else len(section)
    return heading.start(), end


def plan_refusal(markdown: str, ids: Sequence[str]) -> str | None:
    """The plan's own shape check (issue #139): `None` when `markdown` has a
    `### Subtasks` block with at least one `####` heading per id in `ids`, otherwise
    the exact reason, worded for the model that wrote it. Never compares a `####`
    heading's text to an id or a title (invariant 3): it only counts them, because the
    reader's brief comes from the structured output, not from the prose."""
    listed = ", ".join(f"`{i}`" for i in ids)
    span = subtasks_block(markdown)
    if span is None:
        return _NO_BLOCK.format(ids=listed)
    start, end = span
    block = markdown[start:end]
    fenced = _fenced_ranges(block)
    have = sum(1 for m in _H4_HEADING.finditer(block) if not _in_fenced(m.start(), fenced))
    if have < len(ids):
        return _TOO_FEW.format(have=have, need=len(ids), ids=listed)
    return None


def for_reader(section: str, subtask_id: str | None, subtasks: Mapping[str, Subtask]) -> str:
    """The `Tech Lead` section as `subtask_id` should read it: its own brief instead of
    every subtask's, everything else byte-for-byte. `subtasks` is `state["subtasks"]`.

    With a `### Subtasks` block, the replacement is exact, byte for byte the same as
    before this function read the block's span from `subtasks_block` instead of
    finding it inline. Without one, the block is appended after the section instead
    of being skipped: a document a new plan can no longer produce (`plan_refusal`
    rejects it first), but an old one, resumed, still needs its reader fed."""
    if subtask_id is None or subtask_id == INTEGRATION or subtask_id not in subtasks:
        return section
    block = f"### Subtasks\n\n{_brief(subtasks[subtask_id])}\n\n{_index(subtask_id, subtasks)}"
    span = subtasks_block(section)
    if span is None:
        return f"{section.rstrip()}\n\n{block}"
    start, end = span
    before, after = section[:start], section[end:]
    return f"{before}{block}\n\n{after}" if after else f"{before}{block}"


def _brief(subtask: Subtask) -> str:
    seams = ", ".join(f"`{s}`" for s in subtask.get("seams", [])) or "none declared"
    depends_on = ", ".join(f"`{d}`" for d in subtask.get("depends_on", [])) or "nothing"
    title = subtask.get("title", subtask.get("id", ""))
    brief = subtask.get("brief", "")
    return f"#### {title}\n\n{brief}\n\nSeams: {seams}.\n\nDepends on: {depends_on}."


def _index(subtask_id: str, subtasks: Mapping[str, Subtask]) -> str:
    others = [(sid, sub.get("title", sid)) for sid, sub in subtasks.items() if sid != subtask_id]
    if not others:
        return "No other subtasks."
    lines = "\n".join(f"- `{sid}` — {title}" for sid, title in others)
    return f"The other subtasks, for context only, not yours to build:\n\n{lines}"


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    """Bounded CommonMark fences: each returned span runs from the opening fence
    line's start to the closing fence line's end (or the text's end, unclosed)."""
    ranges: list[tuple[int, int]] = []
    open_char: str | None = None
    open_len = 0
    start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        bare = line[:-1] if line.endswith("\n") else line
        match = _FENCE_LINE.match(bare)
        if open_char is None:
            if match is not None:
                open_char, open_len, start = match.group(1)[0], len(match.group(1)), offset
        elif (
            match is not None
            and match.group(1)[0] == open_char
            and len(match.group(1)) >= open_len
            and match.group(2).strip() == ""
        ):
            ranges.append((start, offset + len(line)))
            open_char = None
        offset += len(line)
    if open_char is not None:
        ranges.append((start, len(text)))
    return ranges


def _in_fenced(offset: int, fenced: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in fenced)
