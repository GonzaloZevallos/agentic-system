"""A role's playbook: the lessons its reviewed mistakes taught, as data (ADR-0043).

Pure: no I/O, and nothing imported outside the process domain. A playbook is a value
object. The curator (`apply`) is the only thing that changes one, and it only ever adds,
merges, bumps or retires one item at a time, never rewrites the whole: no code path takes
model text and writes a playbook from it. Every add must cite a closed task whose
`return_history` shows the category for this very role (`accountable`), so a lesson
always traces to a real return.

`ACCOUNTABLE` is the one table that says which role a return is attributable to;
`accountable` and `PLAYBOOK_ROLES` both read it, so two lists cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, get_args

import yaml

from .schemas import TaskType
from .task import INTEGRATION, FindingCategory

# At most this many items per role, and at most this many characters in one lesson: the
# section rides in the role's system prompt, so both are hard.
PLAYBOOK_MAX_ITEMS = 12
LESSON_MAX_CHARS = 240

CATEGORIES: tuple[str, ...] = get_args(FindingCategory)
# The pair a review writes into `return_history` (`roles.pair_for`, a test pins the two
# together): every return the table can attribute comes from it. Anything else (PM's
# return of the PRD, a lead's return, PM's final validation) is not a lesson for a role
# this table names.
REVIEW_PAIR = "Tech Lead→Developer"
# The categories that are the plan's, not the branch's: Tech Lead's own mistake.
_PLANNER = ("plan-calibration", "integration-crosscut")
SECTION_TITLE = "### Playbook"
# The other files `.agentic/playbooks/` holds at its top level: the delivery's own
# records, not a role's playbook. `summaries/` is a subdirectory and never listed.
RECORD_FILES = ("reflected", "rejected")

ACCOUNTABLE: dict[tuple[str, str, bool], str] = {
    (task_type, category, integration): (
        ("tech_lead_platform" if task_type == "platform_enhancement" else "tech_lead")
        if category in _PLANNER
        else ("developer_integrator" if integration else "developer")
    )
    for task_type in get_args(TaskType)
    for category in CATEGORIES
    for integration in (False, True)
}
PLAYBOOK_ROLES = frozenset(ACCOUNTABLE.values())


def accountable(entry: Mapping[str, Any], task_type: str) -> str | None:
    """The slug of the role a `return_history` entry is attributable to, or `None`: a
    `definition` return, a task type or a pair the table does not name."""
    if entry.get("by") == "landing":
        return None  # the landing reopened the stage; no role returned it
    pair = entry.get("pair")
    category = entry.get("category")
    if not isinstance(pair, str) or not isinstance(category, str):
        return None
    base, _, subtask = pair.partition(":")
    if base != REVIEW_PAIR:
        return None
    return ACCOUNTABLE.get((task_type, category, subtask == INTEGRATION))


# ---- text ----------------------------------------------------------------------------

_FORBIDDEN = (
    ("\n", "a newline"),
    ("\r", "a newline"),
    ("`", "a backtick"),
    ("{{", "a `{{` placeholder"),
    ("http://", "a url"),
    ("https://", "a url"),
)


def lesson_refusal(text: str) -> str | None:
    """Why `text` cannot be a lesson, or `None`: one line, at most `LESSON_MAX_CHARS`,
    with nothing that would be read as markup, a placeholder or a link in a prompt."""
    if not text.strip():
        return "the lesson is empty"
    if len(text.strip()) > LESSON_MAX_CHARS:
        return f"the lesson is longer than {LESSON_MAX_CHARS} characters"
    for needle, name in _FORBIDDEN:
        if needle in text:
            return f"the lesson contains {name}"
    return None


def normalise(text: str) -> str:
    """The form two lessons are compared in: lowercase, whitespace collapsed."""
    return " ".join(text.lower().split())


# ---- the playbook --------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    id: str
    lesson: str
    category: str
    # The section hash the item first appeared under, or "empty" when the playbook was.
    since: str
    # The tasks that cited it, once each; `seen` is never stored.
    seen_in: tuple[str, ...]
    added_at: str

    @property
    def seen(self) -> int:
        return len(self.seen_in)


@dataclass(frozen=True)
class Playbook:
    slug: str
    items: tuple[Item, ...] = ()


def _number(item_id: str) -> tuple[int, str]:
    tail = item_id.rpartition("-")[2]
    return (int(tail), item_id) if tail.isdigit() else (10**9, item_id)


def section(playbook: Playbook) -> str:
    """What the role's prompt carries: nothing for an empty playbook, otherwise a titled
    list, one `- lesson (category)` line per item in id order."""
    if not playbook.items:
        return ""
    lines = [
        f"- {i.lesson} ({i.category})" for i in sorted(playbook.items, key=lambda i: _number(i.id))
    ]
    return SECTION_TITLE + "\n" + "\n".join(lines)


_ITEM_KEYS = ("id", "lesson", "category", "since", "seen_in", "added_at")


def render_file(playbook: Playbook) -> str:
    """The playbook file's text: deterministic, so a diff shows only the delta."""
    items = [
        {
            "id": i.id,
            "lesson": i.lesson,
            "category": i.category,
            "since": i.since,
            "seen_in": list(i.seen_in),
            "added_at": i.added_at,
        }
        for i in playbook.items
    ]
    text: str = yaml.safe_dump(
        {"items": items}, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10**6
    )
    return text


def parse(text: str, slug: str) -> Playbook:
    """Read a playbook file. Empty text is an empty playbook; a file that is not one
    raises `ValueError` naming the problem, never a silent partial read."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"playbook {slug}: not valid YAML: {error}") from error
    if data is None:
        return Playbook(slug=slug)
    if not isinstance(data, dict):
        raise ValueError(f"playbook {slug}: the file is not a mapping")
    unknown = sorted(str(k) for k in data if k != "items")
    if unknown:
        raise ValueError(f"playbook {slug}: unknown key {unknown[0]}")
    raw = data.get("items") or []
    if not isinstance(raw, list):
        raise ValueError(f"playbook {slug}: items is not a list")
    items: list[Item] = []
    for entry in raw:
        item = _parse_item(entry, slug)
        if any(item.id == other.id for other in items):
            raise ValueError(f"playbook {slug}: duplicate id {item.id}")
        items.append(item)
    return Playbook(slug=slug, items=tuple(items))


def _parse_item(entry: Any, slug: str) -> Item:
    if not isinstance(entry, dict):
        raise ValueError(f"playbook {slug}: an item is not a mapping")
    label = entry.get("id", "?")
    unknown = sorted(str(k) for k in entry if k not in _ITEM_KEYS)
    if unknown:
        raise ValueError(f"playbook {slug}: item {label} has unknown key {unknown[0]}")
    missing = [k for k in _ITEM_KEYS if k not in entry]
    if missing:
        raise ValueError(f"playbook {slug}: item {label} is missing {missing[0]}")
    for key in _ITEM_KEYS:
        if key != "seen_in" and not isinstance(entry[key], str):
            raise ValueError(f"playbook {slug}: item {label}: {key} is not text")
    seen = entry["seen_in"]
    if not isinstance(seen, list) or not all(isinstance(t, str) for t in seen):
        raise ValueError(f"playbook {slug}: item {label}: seen_in is not a list of task ids")
    if refusal := lesson_refusal(entry["lesson"]):
        raise ValueError(f"playbook {slug}: item {label}: {refusal}")
    if entry["category"] not in CATEGORIES:
        raise ValueError(f"playbook {slug}: item {label}: category {entry['category']} is unknown")
    return Item(
        id=entry["id"],
        lesson=entry["lesson"].strip(),
        category=entry["category"],
        since=entry["since"],
        seen_in=tuple(dict.fromkeys(seen)),
        added_at=entry["added_at"],
    )


# ---- the curator ---------------------------------------------------------------------


@dataclass(frozen=True)
class Delta:
    """One change the reflector proposes. `lesson` is for add and merge, `item_id` for
    merge, bump and retire, `tasks` the task ids it cites."""

    op: Literal["add", "merge", "bump", "retire"]
    slug: str
    category: str = ""
    lesson: str = ""
    item_id: str = ""
    tasks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    """What `apply` knows of one cited task: whether it closed, its type, and its
    `return_history` entries, read from the closed document by the caller."""

    closed: bool
    task_type: str
    return_history: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class Rejected:
    """A lesson a human refused (closing the candidate PR, or taking it out of the file
    before merging): `lesson` is the normalised text."""

    slug: str
    category: str
    lesson: str


@dataclass(frozen=True)
class Refusal:
    delta: Delta
    reason: str


@dataclass(frozen=True)
class Applied:
    """`applied` carries the effective deltas: an add that matched an existing item is
    recorded as the bump it became, so a summary counts what really happened."""

    playbook: Playbook
    applied: tuple[Delta, ...] = ()
    refused: tuple[Refusal, ...] = ()


def apply(
    playbook: Playbook,
    deltas: Sequence[Delta],
    evidence: Mapping[str, Evidence],
    rejected: Sequence[Rejected],
    *,
    section_hash: str,
    now: str,
) -> Applied:
    """Fold `deltas` into `playbook`, one at a time, each against the playbook the
    earlier ones left. Never mutates, never touches an item a delta does not name.
    `section_hash` is what a new item records as `since`; `now` is stored as `added_at`."""
    current = playbook
    applied: list[Delta] = []
    refused: list[Refusal] = []
    for delta in deltas:
        outcome = _apply_one(current, delta, evidence, rejected, section_hash, now)
        if isinstance(outcome, str):
            refused.append(Refusal(delta, outcome))
        else:
            current, effective = outcome
            applied.append(effective)
    return Applied(current, tuple(applied), tuple(refused))


def _apply_one(
    playbook: Playbook,
    delta: Delta,
    evidence: Mapping[str, Evidence],
    rejected: Sequence[Rejected],
    section_hash: str,
    now: str,
) -> str | tuple[Playbook, Delta]:
    if delta.slug != playbook.slug:
        return f"the delta is for slug {delta.slug}, this is {playbook.slug}"
    if delta.op == "add":
        return _add(playbook, delta, evidence, rejected, section_hash, now)
    target = next((i for i in playbook.items if i.id == delta.item_id), None)
    if target is None:
        return f"there is no item {delta.item_id!r}"
    if delta.op == "retire":
        return _replace_items(playbook, target, None), delta
    if delta.category and delta.category != target.category:
        return f"the delta's category is not the item's ({target.category})"
    new_ids = tuple(t for t in dict.fromkeys(delta.tasks) if t not in target.seen_in)
    if new_ids and (why := _evidence_refusal(delta.slug, target.category, new_ids, evidence)):
        return why
    lesson = target.lesson
    if delta.op == "merge":
        lesson = delta.lesson.strip()
        if why := lesson_refusal(delta.lesson):
            return why
        if _is_rejected(rejected, delta.slug, target.category, lesson):
            return "the lesson was rejected before"
        twin = next(
            (
                i
                for i in playbook.items
                if i.id != target.id and normalise(i.lesson) == normalise(lesson)
            ),
            None,
        )
        if twin:
            return f"the lesson duplicates {twin.id}"
    elif not new_ids:
        return "nothing to bump: every cited task is already counted"
    merged = replace(target, lesson=lesson, seen_in=target.seen_in + new_ids)
    return _replace_items(playbook, target, merged), delta


def _add(
    playbook: Playbook,
    delta: Delta,
    evidence: Mapping[str, Evidence],
    rejected: Sequence[Rejected],
    section_hash: str,
    now: str,
) -> str | tuple[Playbook, Delta]:
    if delta.category not in CATEGORIES:
        return f"the category {delta.category!r} is not a finding category"
    if why := lesson_refusal(delta.lesson):
        return why
    lesson = delta.lesson.strip()
    if _is_rejected(rejected, delta.slug, delta.category, lesson):
        return "the lesson was rejected before"
    tasks = tuple(dict.fromkeys(delta.tasks))
    if why := _evidence_refusal(delta.slug, delta.category, tasks, evidence):
        return why
    same = next((i for i in playbook.items if normalise(i.lesson) == normalise(lesson)), None)
    if same is not None:
        new_ids = tuple(t for t in tasks if t not in same.seen_in)
        if not new_ids:
            return f"nothing to bump on {same.id}: every cited task is already counted"
        bumped = replace(same, seen_in=same.seen_in + new_ids)
        effective = replace(delta, op="bump", item_id=same.id)
        return _replace_items(playbook, same, bumped), effective
    if len(playbook.items) >= PLAYBOOK_MAX_ITEMS:
        return f"the playbook has {PLAYBOOK_MAX_ITEMS} items: merge or retire first"
    number = max((_number(i.id)[0] for i in playbook.items if _number(i.id)[0] < 10**9), default=0)
    item = Item(
        id=f"{playbook.slug}-{number + 1}",
        lesson=lesson,
        category=delta.category,
        since=section_hash,
        seen_in=tasks,
        added_at=now,
    )
    return Playbook(playbook.slug, playbook.items + (item,)), delta


def _is_rejected(rejected: Sequence[Rejected], slug: str, category: str, lesson: str) -> bool:
    text = normalise(lesson)
    return any(
        r.slug == slug and r.category == category and normalise(r.lesson) == text for r in rejected
    )


def _evidence_refusal(
    slug: str, category: str, tasks: Sequence[str], evidence: Mapping[str, Evidence]
) -> str | None:
    """Why `tasks` cannot back a lesson of `category` for `slug`, or `None`: every one
    is known and closed, and at least one returned that category on this role's account."""
    if not tasks:
        return "the delta cites no task"
    for task in tasks:
        known = evidence.get(task)
        if known is None:
            return f"task {task} has no evidence"
        if not known.closed:
            return f"task {task} is not closed"
    backed = any(
        entry.get("category") == category and accountable(entry, evidence[task].task_type) == slug
        for task in tasks
        for entry in evidence[task].return_history
    )
    if not backed:
        return f"no cited task has a return_history entry of {category} for {slug}"
    return None


def _replace_items(playbook: Playbook, old: Item, new: Item | None) -> Playbook:
    items = tuple(
        new if i.id == old.id and new is not None else i
        for i in playbook.items
        if not (i.id == old.id and new is None)
    )
    return Playbook(playbook.slug, items)
