"""Typed tracker items (GH-620): what the plan asks of the issue tracker, as data and not as
free prose. A leaf at module level: the standard library and `typing` only, so `schemas`,
`task`, `rules` and `document` can all import it without a cycle. `escalate_human` imports
`rules` inside its body, so importing this module never imports `rules`.

The one place that decides what kind an item is, and the one table that says what each kind
holds (GH-625). Only `human` is performed so far: a question for the owner with the author's
recommendation, which reaches a human as an escalation. A kind is added to `KINDS` only in the
diff that adds the code that performs it; its row and its type may exist before."""

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, NamedTuple, TypedDict, assert_never


class HumanItem(TypedDict):
    """A decision only the owner can take: one line to ask, one line to recommend."""

    kind: Literal["human"]
    question: str
    recommendation: str


class CommentItem(TypedDict):
    """A comment the system posts on the issue."""

    kind: Literal["comment"]
    text: str


class OpenIssueItem(TypedDict):
    """An issue the system opens: one line for the title, several for the body."""

    kind: Literal["open_issue"]
    title: str
    body: str


TrackerItem = HumanItem | CommentItem | OpenIssueItem
Kind = Literal["human", "comment", "open_issue"]

KINDS: frozenset[str] = frozenset({"human"})

# The one definition of the limits: no other module or prompt states them as its own literal.
MAX_ITEMS = 5
MAX_FIELD_CHARS = 240
MAX_BODY_CHARS = 2000

_MARKERS = ("<!--", "-->", "{{")
# An `@` that starts a word: `actions/checkout@v4`, `pkg@1.2` and `a@b.com` have a word
# character, a `/` or a `.` before it and pass.
_MENTION = re.compile(r"(?<![\w/.])@[A-Za-z]")


class _Field(NamedTuple):
    name: str
    several_lines: bool = False
    cap: int = MAX_FIELD_CHARS


class _Row(NamedTuple):
    fields: tuple[_Field, ...]
    label: str  # the field `summary_line` shows
    does: str  # one sentence: what the kind is for, as the prompt says it


# Every kind, in the order the prompt names them. A loop over kinds walks this table, never
# `KINDS`, which is a set and has no order.
_TABLE: Mapping[Kind, _Row] = {
    "human": _Row(
        (_Field("question"), _Field("recommendation")),
        "question",
        "Its `question` asks the owner to do or approve the work (for\n"
        '   example "Close #12 as superseded?") and its `recommendation` says what to do.',
    ),
    "comment": _Row(
        (_Field("text", several_lines=True),),
        "text",
        "Its `text` is a comment the system posts on the issue.",
    ),
    "open_issue": _Row(
        (_Field("title"), _Field("body", several_lines=True, cap=MAX_BODY_CHARS)),
        "title",
        "Its `title` and `body` are the issue the system opens.",
    ),
}


def _shape(kind: Kind) -> str:
    return f"{{kind: {kind}, {', '.join(field.name for field in _TABLE[kind].fields)}}}"


_WRITE_AS_HUMAN = f"write the item as {_shape('human')}"


def _one_line(text: str, what: str) -> None:
    # The same rule and text as `schemas._one_line`, which cannot be imported from here.
    if not text.strip() or "\n" in text.strip():
        raise ValueError(f"{what} must be one non-empty line")


def digest(lines: Sequence[str]) -> str:
    """The first twelve hex digits of the sha256 of the stripped lines joined by newlines:
    the one hashing both `rules.tracker_hash` (a checklist) and `item_hash` use."""
    text = "\n".join(line.strip() for line in lines)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _texts(item: TrackerItem) -> dict[str, str]:
    """The fields of `item` in the order of its row: the one place item fields are read."""
    if item["kind"] == "human":
        return {"question": item["question"], "recommendation": item["recommendation"]}
    if item["kind"] == "comment":
        return {"text": item["text"]}
    if item["kind"] == "open_issue":
        return {"title": item["title"], "body": item["body"]}
    assert_never(item)


def item_hash(item: TrackerItem) -> str:
    """What tells one item from another. It covers the kind and the fields in their order, and
    it is a stored key (`tracker_actions_done`, `tracker_human`), so its input is fixed: for a
    `human` item `[kind, question, recommendation]`."""
    return digest([item["kind"], *_texts(item).values()])


def human_items(items: Sequence[TrackerItem]) -> list[HumanItem]:
    """The items for the owner, in order: one of the two filters, and with `system_items` the
    only place that tells a human item from a system one."""
    return [item for item in items if item["kind"] == "human"]


def system_items(items: Sequence[TrackerItem]) -> list[CommentItem | OpenIssueItem]:
    """The items the system performs itself, in order: what `human_items` leaves."""
    return [item for item in items if item["kind"] != "human"]


def summary_line(item: TrackerItem) -> str:
    """The kind and what names the item: its title, or the first 80 characters of its text
    (newlines read as spaces, an ellipsis only when something was cut)."""
    row = _TABLE[item["kind"]]
    label = _texts(item)[row.label]
    if any(field.name == row.label and field.several_lines for field in row.fields):
        flat = label.replace("\n", " ")
        label = f"{flat[:80]}…" if len(flat) > 80 else flat
    return f"{item['kind']}: {label}"


def parse(raw: object) -> list[TrackerItem]:
    """The items of `raw`, a list of mappings, as new dicts holding exactly the keys of their
    kind, with stripped strings (so the stored value, its hash and the posted line are one
    string). Raises `ValueError` on anything else, naming the way out: nothing is dropped
    silently.

    The checks run in a fixed order: a list, at most `MAX_ITEMS`, each item in turn, and last
    that no `item_hash` repeats. This is the only validator of item text: it reaches a comment
    or a question for the owner only after passing here (GH-663)."""
    if not isinstance(raw, list):
        raise ValueError(f"tracker_items must be a list of items; {_WRITE_AS_HUMAN}")
    if len(raw) > MAX_ITEMS:
        raise ValueError(
            f"tracker_items holds {len(raw)} items, at most {MAX_ITEMS} are allowed: keep the "
            f"decisions only the owner can take; {_WRITE_AS_HUMAN}"
        )
    items = [_parse_item(entry) for entry in raw]
    seen: set[str] = set()
    for item in items:
        if (key := item_hash(item)) in seen:
            raise ValueError(
                f"tracker_items names the item {summary_line(item)!r} twice: drop the repeat; "
                f"{_WRITE_AS_HUMAN}"
            )
        seen.add(key)
    return items


def _performed(kind: object) -> Kind | None:
    """`kind` when it names a kind of the table that is in `KINDS`, else None."""
    return next((known for known in _TABLE if known == kind and known in KINDS), None)


def _parse_item(entry: object) -> TrackerItem:
    if not isinstance(entry, Mapping):
        raise ValueError(f"a tracker item must be a mapping, not {entry!r}; {_WRITE_AS_HUMAN}")
    kind = _performed(entry.get("kind"))
    # A kind that is not performed has no keys of its own to judge: only a key no kind holds
    # is named, before the kind is refused.
    judged = (kind,) if kind else tuple(_TABLE)
    keys = {field.name for row in judged for field in _TABLE[row].fields}
    if extra := sorted(str(key) for key in entry if key != "kind" and key not in keys):
        raise ValueError(f"a tracker item has no key {', '.join(extra)}; {_WRITE_AS_HUMAN}")
    if kind is None:
        raise ValueError(f"unknown tracker item kind {entry.get('kind')!r}; {_WRITE_AS_HUMAN}")
    texts: dict[str, str] = {}
    for field in _TABLE[kind].fields:
        value = entry.get(field.name)
        if not isinstance(value, str):
            raise ValueError(f"a tracker item is missing its {field.name}; {_WRITE_AS_HUMAN}")
        what = f"a tracker item's {field.name}"
        if field.several_lines:
            if not value.strip():
                raise ValueError(f"{what} must not be empty")
        else:
            _one_line(value, what)
        texts[field.name] = _field_text(value.strip(), field)
    return _build(kind, texts)


def _build(kind: Kind, texts: Mapping[str, str]) -> TrackerItem:
    match kind:
        case "human":
            return {
                "kind": "human",
                "question": texts["question"],
                "recommendation": texts["recommendation"],
            }
        case "comment":
            return {"kind": "comment", "text": texts["text"]}
        case "open_issue":
            return {"kind": "open_issue", "title": texts["title"], "body": texts["body"]}
        case _:
            assert_never(kind)


def _field_text(text: str, field: _Field) -> str:
    if len(text) > field.cap:
        raise ValueError(
            f"a tracker item's {field.name} is {len(text)} characters, at most {field.cap} "
            f"are allowed: say it shorter; {_WRITE_AS_HUMAN}"
        )
    if marker := next((m for m in _MARKERS if m in text), None):
        raise ValueError(
            f"a tracker item's {field.name} holds the marker {marker!r}, which the issue "
            f"tracker would read as markup: say it in plain words; {_WRITE_AS_HUMAN}"
        )
    if _MENTION.search(text):
        raise ValueError(
            f"a tracker item's {field.name} holds a mention (@name), which would notify a "
            f"person: write the name without the @; {_WRITE_AS_HUMAN}"
        )
    return text


def kinds_text() -> str:
    """What the Tech Lead prompts say about tracker items, from the table and the limits: the
    kinds in `KINDS`, in the table's order. With `human` alone it is, byte for byte, the
    paragraph the prompts carried before the table (the continuation lines are indented three
    spaces, as the prompts' list item is)."""
    kinds = [kind for kind in _TABLE if kind in KINDS]
    shapes = " or ".join(f"a `{kind}` item, `{_shape(kind)}`" for kind in kinds)
    closing = ",\n   and no other kind exists." if len(kinds) == 1 else "."
    does = " ".join(_TABLE[kind].does for kind in kinds)
    wide = "".join(
        f", except the `{kind}` `{field.name}`, which may run to {field.cap} characters"
        f"{' over several lines' if field.several_lines else ''}"
        for kind in kinds
        for field in _TABLE[kind].fields
        if field.several_lines or field.cap != MAX_FIELD_CHARS
    )
    return (
        f"`tracker_items`: tracker work is {shapes}{closing} {does} Each field\n"
        f"   is one line of at most {MAX_FIELD_CHARS} characters with no @-mention{wide}, and a "
        f"plan\n   carries at most {MAX_ITEMS} items."
    )


def unanswered(
    items: Sequence[TrackerItem], done: Mapping[str, Mapping[str, str]]
) -> list[TrackerItem]:
    """The items whose hash has no record in `done` carrying `answered`: the one place that
    decides whether an item is unanswered. A record of only `at` (raised, not answered) counts."""
    return [item for item in items if "answered" not in done.get(item_hash(item), {})]


def question_line(item: HumanItem) -> str:
    """The one line an item reaches a person as: the only formatter of item text."""
    return f"{item['question']} — Recommended: {item['recommendation']}"


def escalate_human(state: Mapping[str, Any], at: str) -> dict[str, Any]:
    """The patch that raises the unanswered `human` items as a `questions` escalation, `{}` when
    there is nothing new to raise. Pure: `at` is the instant the caller stamped. An item whose
    hash is already in `tracker_human` is not raised twice; the pending hashes stay pending.

    The second fence of GH-594: it passes `spec=state.get("spec")` and no `spec_cause`, so under
    a spec `rules.escalation` raises `ValueError`. That is deliberate: a spec delivery never asks
    the owner through a `human` item, `schemas.plan_check_for` refuses such a plan first, and a
    document that reached this point under a spec has a bug to see, not a question to ask."""
    from . import rules

    pending: list[str] = list(state.get("tracker_human", []))
    fresh = [
        item
        for item in human_items(
            unanswered(parse(state.get("tracker_items", [])), state.get("tracker_actions_done", {}))
        )
        if item_hash(item) not in pending
    ]
    if not fresh:
        return {}
    hashes = [item_hash(item) for item in fresh]
    reason = f"the plan leaves {len(pending) + len(fresh)} decision(s) to the owner"
    return rules.logged(
        {
            "status": "waiting_on_human",
            **rules.escalation(
                "questions",
                reason,
                tracker_human=[*pending, *hashes],
                spec=state.get("spec"),
            ),
            "questions_for_human": [question_line(item) for item in fresh],
            "tracker_actions_done": {h: {"at": at} for h in hashes},
        },
        at=at,
    )
