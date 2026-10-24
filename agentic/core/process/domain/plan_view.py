"""Projecting the `Tech Lead` section for a reader with a subtask of its own
(issue #71, `plan-trim`), and completing the plan's own `### Subtasks` block from its
structured record when the model's markdown falls short of it (issue #139,
`brief-always-reaches`; issue #152, `plan-block-fill`): the Developer and the reviewer
always get their brief, the plan always gives them a `### Subtasks` block to find it
from, never both missing at once.

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
skipped: this is what every document resumed from before this module existed still
needs, and also what a plan `with_subtasks_block` has already repaired, when the
Tech Lead's own prose also left a near-miss heading next to the system's block,
still projects.

Degrades to the full section, never to an empty one (invariant 4): no reader id, an
id absent from the plan, or `integration`, whose job is the seams between subtasks,
not one of them (ADR-0015).

`subtasks_block` is the one place that knows what `### Subtasks` means: the exact
heading line and the span of the block it opens, skipping anything inside a fenced
code block. `with_subtasks_block` is the plan stage's own repair over a plan's
markdown and its structured subtasks, in plan order: when no exact `### Subtasks`
line exists outside a fence, it appends one, built entirely from the structured
record, with a note saying so; when the block exists but has fewer `####` than
subtasks, it keeps the model's prose byte for byte and inserts the same kind of note
naming the gap; otherwise the section is returned unchanged. Never a role failure
(ADR-0023): a plan whose structured `subtasks` passed `schemas.plan_check` no longer
escalates only because its markdown fell short of the heading. Both this function and
`subtasks_block` read fences the same bounded way: a fence opens with up to 3 spaces
of indentation and 3 or more equal backticks or tildes, with an optional info
string; it closes with a line of the same character, at least as many, and no info
string; a fence that never closes runs to the end of the text."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .task import INTEGRATION, PartRecord, Subtask

# `### Subtasks` at the top level only, exactly that line (`### Subtasks:` and
# `#### Subtasks` both miss): the `#### <id>` briefs inside it start with a fourth
# `#`, so this never matches one of those.
_SUBTASKS_HEADING = re.compile(r"^### Subtasks[ \t]*$", re.M)
_NEXT_TOP_HEADING = re.compile(r"^### ", re.M)
_H4_HEADING = re.compile(r"^#### ", re.M)
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$", re.M)

# One prefix both notes share, so a single grep over a document finds either kind of
# repair (issue #152).
_NOTE_PREFIX = "`### Subtasks` written by the system from the structured plan: "
NOTE_WRITTEN = (
    _NOTE_PREFIX + "no exact `### Subtasks` line (outside code fences) was found in this "
    "section, so the block below was built from the structured plan. Any list the Tech "
    "Lead wrote under another heading is not the one the Developers read."
)
NOTE_SHORT = (
    _NOTE_PREFIX + "the block below lists fewer `####` than the plan's {n} subtasks. Each "
    "Developer's own brief still comes from the structured plan, not from this prose."
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


def with_subtasks_block(section: str, subtasks: Sequence[Subtask]) -> str:
    """`section` with a complete `### Subtasks` block guaranteed, repaired from
    `subtasks` (in plan order) when the model's own markdown falls short (issue #152):
    the Developer and the reviewer always have a block to read their brief from
    (`for_reader`), so a plan never escalates only because its markdown missed the
    heading or wrote too few `####`.

    No exact `### Subtasks` line outside a fence (`subtasks_block(section) is None`):
    the block is appended, built entirely from `subtasks`, with `NOTE_WRITTEN`. The
    block exists but has fewer unfenced `####` than `len(subtasks)`: the model's text
    is kept byte for byte, with `NOTE_SHORT` inserted on its own paragraph right after
    the heading line. Otherwise `section` is returned unchanged. Never compares a
    `####` heading's text to an id or a title (invariant 3): it only counts them,
    because the reader's brief comes from the structured output, not from the
    prose."""
    span = subtasks_block(section)
    if span is None:
        briefs = "\n\n".join(_brief(s) for s in subtasks)
        return f"{section.rstrip()}\n\n### Subtasks\n\n{NOTE_WRITTEN}\n\n{briefs}"
    start, end = span
    block = section[start:end]
    fenced = _fenced_ranges(block)
    have = sum(1 for m in _H4_HEADING.finditer(block) if not _in_fenced(m.start(), fenced))
    if have >= len(subtasks):
        return section
    heading = _SUBTASKS_HEADING.search(block)
    assert heading is not None  # `span` came from matching this same heading
    note = NOTE_SHORT.format(n=len(subtasks))
    repaired = block[: heading.end()] + f"\n\n{note}" + block[heading.end() :]
    return section[:start] + repaired + section[end:]


_PARTS_HEADING = re.compile(r"^### Parts[ \t]*$", re.M)
NOTE_PARTS = (
    "`### Parts` written by the system from the structured plan: no exact `### Parts` line "
    "(outside code fences) was found in this section, so the block below was built from the "
    "structured plan. Each issue the split opens is written from the structured parts."
)


def with_parts_block(section: str, parts: Sequence[PartRecord]) -> str:
    """`section` with a `### Parts` block guaranteed, the sibling of `with_subtasks_block`
    for a plan that does not fit (ADR-0054): when the model's markdown has no exact
    `### Parts` line outside a code fence, one is appended, built entirely from `parts`
    in plan order, with a note saying so; otherwise `section` is returned unchanged. Never
    a role failure: the issues are written from the structured parts, not from this prose."""
    fenced = _fenced_ranges(section)
    if any(not _in_fenced(m.start(), fenced) for m in _PARTS_HEADING.finditer(section)):
        return section
    blocks = "\n\n".join(_part_brief(n, p) for n, p in enumerate(parts, 1))
    return f"{section.rstrip()}\n\n### Parts\n\n{NOTE_PARTS}\n\n{blocks}"


_TRACKER_HEADING = re.compile(r"^### Tracker work for the human[ \t]*$", re.M)
TRACKER_TITLE = "Tracker work for the human"


def with_tracker_block(section: str, checklist: Sequence[str]) -> str:
    """`section` with a `### Tracker work for the human` list built from the structured
    `checklist` (GH-336), so the human reading the plan finds what is theirs to do on the
    tracker. Nothing when the checklist is empty, and unchanged when the model's own markdown
    already has that heading outside a code fence: the comment on the issue is written from
    the structured list, not from this prose."""
    if not checklist:
        return section
    fenced = _fenced_ranges(section)
    if any(not _in_fenced(m.start(), fenced) for m in _TRACKER_HEADING.finditer(section)):
        return section
    items = "\n".join(f"- {item}" for item in checklist)
    return f"{section.rstrip()}\n\n### {TRACKER_TITLE}\n\n{items}"


def _part_brief(number: int, part: PartRecord) -> str:
    depends_on = ", ".join(f"part {d}" for d in part["depends_on"]) or "nothing"
    return f"#### Part {number}: {part['title']}\n\n{part['body']}\n\nDepends on: {depends_on}."


def for_reader(section: str, subtask_id: str | None, subtasks: Mapping[str, Subtask]) -> str:
    """The `Tech Lead` section as `subtask_id` should read it: its own brief instead of
    every subtask's, everything else byte-for-byte. `subtasks` is `state["subtasks"]`.

    With a `### Subtasks` block, the replacement is exact, byte for byte the same as
    before this function read the block's span from `subtasks_block` instead of
    finding it inline. Without one, the block is appended after the section instead
    of being skipped: this append branch now serves documents written before this
    module's repair existed, and a repaired plan whose Tech Lead also left a
    near-miss heading (`### Subtasks:`, say) next to the system's own block -- both
    still need their reader fed, not skipped."""
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
    screens = subtask.get("screens", [])
    # Only when non-empty (ADR-0040, invariant: "a task with no `observation` design
    # items renders a pull request body byte-identical to today's" -- the same holds
    # here, for a subtask with no screen of its own): a plan with no UI subtask moves
    # this projection by not one byte.
    screens_line = f"\n\nScreens: {', '.join(f'`{s}`' for s in screens)}." if screens else ""
    return f"#### {title}\n\n{brief}\n\nSeams: {seams}.\n\nDepends on: {depends_on}.{screens_line}"


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
