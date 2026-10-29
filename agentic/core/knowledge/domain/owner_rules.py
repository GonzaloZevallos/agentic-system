"""The owner's decisions file: which entries are in force.

An entry is a list line `- D-<8 hex>: <rule> ...`; a line indented under it continues it. An
entry is out of force when a later one cites its id in a `Reemplaza:` line, or when it was
struck (`- ~~D-...~~`). A role is given only the entries in force (`live`).
"""

from __future__ import annotations

import re

_ENTRY = re.compile(r"^- (?P<struck>~~)?(?P<id>D-[0-9a-f]{8})\b")
_REPLACES = re.compile(r"Reemplaza:\s*(D-[0-9a-f]{8})")


def live(text: str) -> str:
    """The text of a decisions file with only the entries in force.

    Lines that are not part of an entry (the title, the preamble) stay. An entry replaced
    by another, a struck one, and every repeat of an id after its first are dropped."""
    blocks = _blocks(text)
    replaced = {cited for block in blocks for cited in _REPLACES.findall(block)}
    seen: set[str] = set()
    kept: list[str] = []
    for block in blocks:
        found = _ENTRY.match(block)
        if found is None:
            kept.append(block)
            continue
        entry_id = found.group("id")
        if found.group("struck") or entry_id in replaced or entry_id in seen:
            continue
        seen.add(entry_id)
        kept.append(block)
    return "".join(kept)


def _blocks(text: str) -> list[str]:
    """The text as consecutive blocks, each an entry with its indented continuation or a
    run of lines that is no entry; the blocks joined give the text back."""
    blocks: list[list[str]] = []
    in_entry = False
    for line in text.splitlines(keepends=True):
        if _ENTRY.match(line):
            blocks.append([line])
            in_entry = True
        elif in_entry and line[:1] in (" ", "\t"):
            blocks[-1].append(line)
        else:
            if in_entry or not blocks:
                blocks.append([])
            blocks[-1].append(line)
            in_entry = False
    return ["".join(block) for block in blocks]
