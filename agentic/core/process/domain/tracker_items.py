"""Typed tracker items (GH-620): what the plan asks of the issue tracker, as data and not as
free prose. A leaf module: the standard library and `typing` only, so `schemas`, `task`,
`rules` and `document` can all import it without a cycle.

The one place that decides what kind an item is. Only `human` exists in this part: a question
for the owner with the author's recommendation, which reaches a human as an escalation. A kind
is added to `KINDS` only in the diff that adds the code that performs it."""

import hashlib
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict


class HumanItem(TypedDict):
    """A decision only the owner can take: one line to ask, one line to recommend."""

    kind: Literal["human"]
    question: str
    recommendation: str


# A union on `kind`; one member for now.
TrackerItem = HumanItem

KINDS: frozenset[str] = frozenset({"human"})

_FIELDS = ("question", "recommendation")
_WRITE_AS_HUMAN = "write the item as {kind: human, question, recommendation}"


def _one_line(text: str, what: str) -> None:
    # The same rule and text as `schemas._one_line`, which cannot be imported from here.
    if not text.strip() or "\n" in text.strip():
        raise ValueError(f"{what} must be one non-empty line")


def digest(lines: Sequence[str]) -> str:
    """The first twelve hex digits of the sha256 of the stripped lines joined by newlines:
    the one hashing both `rules.tracker_hash` (a checklist) and `item_hash` use."""
    text = "\n".join(line.strip() for line in lines)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def item_hash(item: TrackerItem) -> str:
    """What tells one item from another. It covers the kind, and it is a stored key
    (`tracker_actions_done`, `tracker_human`), so its input is fixed."""
    return digest([item["kind"], item["question"], item["recommendation"]])


def parse(raw: object) -> list[TrackerItem]:
    """The items of `raw`, a list of mappings, as new dicts holding exactly the three keys.
    Raises `ValueError` on anything else, naming the way out: nothing is dropped silently."""
    if not isinstance(raw, list):
        raise ValueError(f"tracker_items must be a list of items; {_WRITE_AS_HUMAN}")
    return [_parse_item(entry) for entry in raw]


def _parse_item(entry: object) -> TrackerItem:
    if not isinstance(entry, Mapping):
        raise ValueError(f"a tracker item must be a mapping, not {entry!r}; {_WRITE_AS_HUMAN}")
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError(f"unknown tracker item kind {kind!r}; {_WRITE_AS_HUMAN}")
    texts: dict[str, str] = {}
    for field in _FIELDS:
        value = entry.get(field)
        if not isinstance(value, str):
            raise ValueError(f"a tracker item is missing its {field}; {_WRITE_AS_HUMAN}")
        _one_line(value, f"a tracker item's {field}")
        texts[field] = value
    return {
        "kind": "human",
        "question": texts["question"],
        "recommendation": texts["recommendation"],
    }
