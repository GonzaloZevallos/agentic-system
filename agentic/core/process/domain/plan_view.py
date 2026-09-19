"""Projecting the `Tech Lead` section for a reader with a subtask of its own
(issue #71, `plan-trim`).

The document keeps one shape on disk, every section stored whole (ADR-0001); this
module is the prompt's own view model over it. `for_reader` takes the `Tech Lead`
section's Markdown plus the plan's subtasks and returns the section with the
`### Subtasks` block replaced by the reader's own brief — rendered from the plan's
structured record (`title`, `brief`, `seams`, `depends_on`), never by matching a
`#### <id>` heading, whose text `tech_lead.md` never fixes the shape of (invariant 3:
the only structural cut this module makes is at the `###` level, which the prompt
does fix) — followed by a one-line index of the sister subtasks, id and title only,
so nobody duplicates another's work. Everything outside that one `###`-level block
survives byte-for-byte, in order.

Degrades to the full section, never to an empty one (invariant 4): no `### Subtasks`
heading to cut at, no reader id, an id absent from the plan, or `integration`, whose
job is the seams between subtasks, not one of them (ADR-0015)."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .task import INTEGRATION, Subtask

# `### Subtasks` at the top level only: `#### <id>` briefs inside it start with a
# fourth `#`, so this never matches one of those.
_SUBTASKS_HEADING = re.compile(r"^### Subtasks[ \t]*$", re.M)
_NEXT_TOP_HEADING = re.compile(r"^### ", re.M)


def for_reader(section: str, subtask_id: str | None, subtasks: Mapping[str, Subtask]) -> str:
    """The `Tech Lead` section as `subtask_id` should read it: its own brief instead of
    every subtask's, everything else byte-for-byte. `subtasks` is `state["subtasks"]`."""
    if subtask_id is None or subtask_id == INTEGRATION or subtask_id not in subtasks:
        return section
    heading = _SUBTASKS_HEADING.search(section)
    if heading is None:
        return section
    tail = section[heading.end() :]
    next_heading = _NEXT_TOP_HEADING.search(tail)
    block_end = heading.end() + (next_heading.start() if next_heading else len(tail))
    before, after = section[: heading.start()], section[block_end:]
    block = f"### Subtasks\n\n{_brief(subtasks[subtask_id])}\n\n{_index(subtask_id, subtasks)}"
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
