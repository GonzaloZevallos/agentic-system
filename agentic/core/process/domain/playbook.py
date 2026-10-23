"""A role's playbook: the lessons its reviewed mistakes taught, as data (ADR-0043).

Pure: no I/O, and nothing imported outside the process domain. A playbook is a value
object. The curator (`apply`) is the only thing that changes one, and it only ever adds,
merges, bumps or retires one item at a time, never rewrites the whole: no code path takes
model text and writes a playbook from it. Every add must cite a closed task with a
`Backing` (a return, a finding, a design remark or a human decision, built by code from
the closed document) of this lesson's category for this very role, so a lesson always
traces to a recorded fact.

`ACCOUNTABLE` is the one table that says which role a recorded fact is attributable to;
`accountable`, `attribute` and `PLAYBOOK_ROLES` all read it, so lists cannot drift apart.

`keep` decides whether a rated lesson enters the playbook or waits in `candidates.yaml`;
`archive_due` decides whether one that stopped applying leaves the prompt for
`archived.yaml`. Both read named thresholds and nothing else repeats their comparisons.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, get_args

import yaml

from .copy import copy_location_parts
from .schemas import TaskType
from .task import INTEGRATION, CopyCategory, FindingCategory

# At most this many items per role, and at most this many characters in one lesson: the
# section rides in the role's system prompt, so both are hard.
PLAYBOOK_MAX_ITEMS = 12
LESSON_MAX_CHARS = 240

CATEGORIES: tuple[str, ...] = get_args(FindingCategory)
# What a lesson can be about: a finding category, or what a finding category cannot say
# (ADR-0056). `design` is a lead's remark, `human-decision` what a person decided on an
# escalation, `definition` a return of the PRD. No `FindingCategory` is added: this is a
# superset of it, and `ChangeCategory` already pairs `definition` with it the same way.
LessonCategory = FindingCategory | CopyCategory | Literal["design", "human-decision", "definition"]
COPY_CATEGORIES: tuple[str, ...] = get_args(CopyCategory)
LESSON_CATEGORIES: tuple[str, ...] = (
    *CATEGORIES,
    *COPY_CATEGORIES,
    "design",
    "human-decision",
    "definition",
)
# The pair a review writes into `return_history` (`roles.pair_for`, a test pins the two
# together): the returns the table attributes to a Developer or to Tech Lead come from it.
REVIEW_PAIR = "Tech Lead→Developer"
# The other pairs whose returns the table attributes (a test pins each to `roles`).
DEFINITION_PAIR = "PM→Product Analyst"
DESIGN_PAIRS = {"UX Lead→UX Designer": "ux", "UI Lead→UI Designer": "ui"}
# The categories that are the plan's, not the branch's: Tech Lead's own mistake.
_PLANNER = ("plan-calibration", "integration-crosscut")
SECTION_TITLE = "### Playbook"
# The other files `.agentic/playbooks/` holds at its top level: the delivery's own
# records, not a role's playbook. `summaries/` is a subdirectory and never listed.
RECORD_FILES = ("reflected", "rejected", "candidates", "archived")

# Where a fact comes from: a return in `return_history`, a finding row, a design remark,
# or a human decision in the HUMAN section.
Source = Literal["return", "finding", "design", "human_decision"]
# What the fact is, for attribution: a Tech Lead review (`review`), PM's early final
# validation (`final_validation`, to the Developer of the subtask), PM's return of the
# PRD (`definition`), a lead's remark (`design`) or a finding of Tech Writer's review of the
# texts (`copy_review`).
Origin = Literal["review", "final_validation", "definition", "design", "copy_review"]
INTEGRATION_QUALIFIER = "integration"


def _owner(origin: str, task_type: str, category: str, qualifier: str) -> str:
    if origin == "definition":
        return "product_analyst"
    if origin == "design":
        return "ux_designer" if qualifier == "ux" else "ui_designer"
    if origin == "copy_review" and category == "copy-criteria-written":
        return "tech_writer"
    developer = "developer_integrator" if qualifier == INTEGRATION_QUALIFIER else "developer"
    if origin == "review" and category in _PLANNER:
        return "tech_lead_platform" if task_type == "platform_enhancement" else "tech_lead"
    return developer


def _rows() -> dict[tuple[str, str, str, str], str]:
    rows: dict[tuple[str, str, str, str], str] = {}
    for task_type in get_args(TaskType):
        for origin, categories, qualifiers in (
            ("review", CATEGORIES, ("", INTEGRATION_QUALIFIER)),
            ("final_validation", CATEGORIES, ("", INTEGRATION_QUALIFIER)),
            ("definition", ("definition",), ("",)),
            ("design", ("design",), ("ux", "ui")),
            ("copy_review", COPY_CATEGORIES, ("", INTEGRATION_QUALIFIER)),
        ):
            for category in categories:
                for qualifier in qualifiers:
                    key = (origin, task_type, category, qualifier)
                    rows[key] = _owner(origin, task_type, category, qualifier)
    return rows


# (origin, task type, category, qualifier) -> the slug a fact is attributable to. The
# qualifier is `integration` for the integrator's subtask, `ux`/`ui` for a lead's side.
ACCOUNTABLE: dict[tuple[str, str, str, str], str] = _rows()
PLAYBOOK_ROLES = frozenset(ACCOUNTABLE.values())


def finding_origin(location: str) -> Origin:
    """The origin of a findings row that is not PM's final validation, by its `location`: a
    text finding of Tech Writer's review, or a Tech Lead review's."""
    return "copy_review" if copy_location_parts(location) is not None else "review"


def attribute(origin: Origin, task_type: str, category: str, qualifier: str = "") -> str | None:
    """The slug a fact of `origin` and `category` is attributable to, or `None`."""
    return ACCOUNTABLE.get((origin, task_type, category, qualifier))


@dataclass(frozen=True)
class Backing:
    """One recorded fact a lesson can cite, built by code from a closed document, never
    from prose: `slug` is the role it is attributable to, `category` the lesson category
    it backs, `ref` the decision number for a `human_decision` (else empty)."""

    source: Source
    slug: str
    category: str
    ref: str = ""


def return_backing(entry: Mapping[str, Any], task_type: str) -> Backing | None:
    """The `Backing` a `return_history` entry makes, or `None`: a landing return, a pair
    the table does not name (PM's return to Tech Lead has no subtask to attribute to)."""
    if entry.get("by") == "landing":
        return None  # the landing reopened the stage; no role returned it
    pair = entry.get("pair")
    category = entry.get("category")
    if not isinstance(pair, str) or not isinstance(category, str):
        return None
    base, _, subtask = pair.partition(":")
    origin: Origin
    if base == REVIEW_PAIR:
        qualifier = INTEGRATION_QUALIFIER if subtask == INTEGRATION else ""
        origin, kind = "review", category
    elif base == DEFINITION_PAIR:
        origin, kind, qualifier = "definition", "definition", ""
    elif base in DESIGN_PAIRS:
        origin, kind, qualifier = "design", "design", DESIGN_PAIRS[base]
    else:
        return None
    slug = attribute(origin, task_type, kind, qualifier)
    return Backing("return", slug, kind) if slug else None


def accountable(entry: Mapping[str, Any], task_type: str) -> str | None:
    """The slug of the role a Tech Lead review return (`REVIEW_PAIR`) is attributable to,
    or `None`: a landing return, any other pair, a task type the table does not name.
    The other pairs' returns are `return_backing`'s."""
    backing = return_backing(entry, task_type)
    if backing is None or not str(entry.get("pair", "")).startswith(REVIEW_PAIR):
        return None
    return backing.slug


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

# What keeps a lesson (ADR-0056): `keep` is the only place these are compared.
KEEP_CONFIDENCE = "high"
KEEP_MIN_TASKS = 2
# A lesson in the prompt that no task of its role backed or cited in this many of the
# role's tasks leaves the prompt for `archived.yaml`: `archive_due` is the only comparison.
ARCHIVE_AFTER = 20

Confidence = Literal["low", "medium", "high"]
CONFIDENCES: tuple[str, ...] = get_args(Confidence)


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
    # Optional on read (an item written before ADR-0056 has neither): the day a task last
    # backed or cited it, and the day the item reached a prompt, from the merge of the
    # candidate PR that carried it (`playbook_candidates.in_prompt_since`), never from
    # `added_at`. An item with no `in_prompt_since` has not been in a prompt, so it never ages.
    last_applied: str = ""
    in_prompt_since: str = ""

    @property
    def seen(self) -> int:
        return len(self.seen_in)


@dataclass(frozen=True)
class Playbook:
    slug: str
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Pending:
    """A rated lesson that did not pass `keep` yet: it waits in `candidates.yaml`, outside
    every prompt, collecting the tasks that cite it. `sources` is every kind of fact that
    backed it, so `keep` can tell a lesson only people taught from one the reviews did."""

    slug: str
    category: str
    lesson: str
    confidence: Confidence
    seen_in: tuple[str, ...]
    sources: tuple[str, ...]
    added_at: str

    @property
    def seen(self) -> int:
        return len(self.seen_in)


@dataclass(frozen=True)
class Archived:
    """An item that left the prompt without being deleted: restored by an `add` that
    matches it, keeping its id and its `seen_in`."""

    item: Item
    reason: str
    archived_at: str


def keep(confidence: Confidence, seen: int, sources: Collection[str]) -> bool:
    """Whether a rated lesson enters the playbook: confidence is `KEEP_CONFIDENCE`, or it
    appeared in `KEEP_MIN_TASKS` tasks. A lesson only human decisions back needs the tasks
    whatever its confidence: one escalation can be about that task alone."""
    if sources and all(source == "human_decision" for source in sources):
        return seen >= KEEP_MIN_TASKS
    return confidence == KEEP_CONFIDENCE or seen >= KEEP_MIN_TASKS


def archive_due(item: Item, role_tasks_since: int, backed: bool) -> bool:
    """Whether `item` leaves the prompt: it has been in one (`in_prompt_since`), the role
    ran `ARCHIVE_AFTER` tasks since, and none of them backed or cited it (`backed`)."""
    if not item.in_prompt_since or backed:
        return False
    return role_tasks_since >= ARCHIVE_AFTER


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
_ITEM_OPTIONAL = ("last_applied", "in_prompt_since")


def item_data(item: Item) -> dict[str, Any]:
    """An item as the file carries it: the optional keys only when set, so a file with
    none of them is byte-identical to one written before."""
    data: dict[str, Any] = {
        "id": item.id,
        "lesson": item.lesson,
        "category": item.category,
        "since": item.since,
        "seen_in": list(item.seen_in),
        "added_at": item.added_at,
    }
    if item.last_applied:
        data["last_applied"] = item.last_applied
    if item.in_prompt_since:
        data["in_prompt_since"] = item.in_prompt_since
    return data


def render_file(playbook: Playbook) -> str:
    """The playbook file's text: deterministic, so a diff shows only the delta."""
    text: str = yaml.safe_dump(
        {"items": [item_data(i) for i in playbook.items]},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10**6,
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
        item = parse_item(entry, slug)
        if any(item.id == other.id for other in items):
            raise ValueError(f"playbook {slug}: duplicate id {item.id}")
        items.append(item)
    return Playbook(slug=slug, items=tuple(items))


def parse_item(entry: Any, slug: str) -> Item:
    """One item of a playbook or of `archived.yaml`; `ValueError` names what is wrong."""
    if not isinstance(entry, dict):
        raise ValueError(f"playbook {slug}: an item is not a mapping")
    label = entry.get("id", "?")
    unknown = sorted(str(k) for k in entry if k not in (*_ITEM_KEYS, *_ITEM_OPTIONAL))
    if unknown:
        raise ValueError(f"playbook {slug}: item {label} has unknown key {unknown[0]}")
    missing = [k for k in _ITEM_KEYS if k not in entry]
    if missing:
        raise ValueError(f"playbook {slug}: item {label} is missing {missing[0]}")
    for key in (*_ITEM_KEYS, *(k for k in _ITEM_OPTIONAL if k in entry)):
        if key != "seen_in" and not isinstance(entry[key], str):
            raise ValueError(f"playbook {slug}: item {label}: {key} is not text")
    seen = entry["seen_in"]
    if not isinstance(seen, list) or not all(isinstance(t, str) for t in seen):
        raise ValueError(f"playbook {slug}: item {label}: seen_in is not a list of task ids")
    if refusal := lesson_refusal(entry["lesson"]):
        raise ValueError(f"playbook {slug}: item {label}: {refusal}")
    if entry["category"] not in LESSON_CATEGORIES:
        raise ValueError(f"playbook {slug}: item {label}: category {entry['category']} is unknown")
    return Item(
        id=entry["id"],
        lesson=entry["lesson"].strip(),
        category=entry["category"],
        since=entry["since"],
        seen_in=tuple(dict.fromkeys(seen)),
        added_at=entry["added_at"],
        last_applied=entry.get("last_applied", ""),
        in_prompt_since=entry.get("in_prompt_since", ""),
    )


# ---- the curator ---------------------------------------------------------------------


@dataclass(frozen=True)
class Delta:
    """One change the reflector proposes. `lesson` is for add and merge, `item_id` for
    merge, bump and retire, `tasks` the task ids it cites. `confidence` is the model's
    own rating that the lesson is general (a delta that states none is read as it was
    before ADR-0056: kept), `scope` where it goes (`system` becomes an issue, never a
    playbook line), `decision` the `Decision N` a human-decision lesson cites."""

    op: Literal["add", "merge", "bump", "retire"]
    slug: str
    category: str = ""
    lesson: str = ""
    item_id: str = ""
    tasks: tuple[str, ...] = ()
    confidence: Confidence = "high"
    scope: Literal["product", "system"] = "product"
    decision: int = 0


@dataclass(frozen=True)
class Evidence:
    """What `apply` knows of one cited task, read by code from the closed document:
    whether it closed, its type, its `backings`, its human `decisions` by number (text)
    and the slugs that `ran` in it. `return_history` is the entries as the document keeps
    them; each one that `return_backing` names counts as a `return` backing, so evidence
    written before ADR-0056 reads the same."""

    closed: bool
    task_type: str
    return_history: Sequence[Mapping[str, Any]] = ()
    backings: Sequence[Backing] = ()
    decisions: Mapping[int, str] = field(default_factory=dict)
    ran: frozenset[str] = frozenset()


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
    recorded as the bump it became, so a summary counts what really happened. An add
    that promoted a candidate or restored an archived item stays an `add` and keeps the
    `seen_in` it had. `held` are the adds `keep` sent to `candidates`; `candidates` and
    `archived` are the whole lists after the run."""

    playbook: Playbook
    applied: tuple[Delta, ...] = ()
    refused: tuple[Refusal, ...] = ()
    held: tuple[Delta, ...] = ()
    candidates: tuple[Pending, ...] = ()
    archived: tuple[Archived, ...] = ()


@dataclass(frozen=True)
class _Shelves:
    """What one run of the curator carries from delta to delta."""

    playbook: Playbook
    candidates: tuple[Pending, ...]
    archived: tuple[Archived, ...]


def apply(
    playbook: Playbook,
    deltas: Sequence[Delta],
    evidence: Mapping[str, Evidence],
    rejected: Sequence[Rejected],
    *,
    section_hash: str,
    now: str,
    candidates: Sequence[Pending] = (),
    archived: Sequence[Archived] = (),
) -> Applied:
    """Fold `deltas` into `playbook`, one at a time, each against the playbook the
    earlier ones left. Never mutates, never touches an item a delta does not name.
    `section_hash` is what a new item records as `since`; `now` is stored as `added_at`
    and `last_applied`. An add `keep` does not pass goes to `candidates`."""
    current = _Shelves(playbook, tuple(candidates), tuple(archived))
    applied: list[Delta] = []
    refused: list[Refusal] = []
    held: list[Delta] = []
    for delta in deltas:
        outcome = _apply_one(current, delta, evidence, rejected, section_hash, now)
        if isinstance(outcome, str):
            refused.append(Refusal(delta, outcome))
            continue
        current, effective, waits = outcome
        (held if waits else applied).append(effective)
    return Applied(
        current.playbook,
        tuple(applied),
        tuple(refused),
        tuple(held),
        current.candidates,
        current.archived,
    )


def _apply_one(
    shelves: _Shelves,
    delta: Delta,
    evidence: Mapping[str, Evidence],
    rejected: Sequence[Rejected],
    section_hash: str,
    now: str,
) -> str | tuple[_Shelves, Delta, bool]:
    playbook = shelves.playbook
    if delta.slug != playbook.slug:
        return f"the delta is for slug {delta.slug}, this is {playbook.slug}"
    if delta.op == "add":
        return _add(shelves, delta, evidence, rejected, section_hash, now)
    target = next((i for i in playbook.items if i.id == delta.item_id), None)
    if target is None:
        return f"there is no item {delta.item_id!r}"
    if delta.op == "retire":
        return replace(shelves, playbook=_replace_items(playbook, target, None)), delta, False
    if delta.category and delta.category != target.category:
        return f"the delta's category is not the item's ({target.category})"
    new_ids = tuple(t for t in dict.fromkeys(delta.tasks) if t not in target.seen_in)
    lesson = target.lesson
    if delta.op == "merge":
        lesson = delta.lesson.strip()
    if new_ids:
        checked = _evidence_check(
            delta.slug, target.category, new_ids, evidence, lesson, delta.decision
        )
        if checked.why:
            return checked.why
    if delta.op == "merge":
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
    merged = replace(
        target,
        lesson=lesson,
        seen_in=target.seen_in + new_ids,
        last_applied=now if new_ids else target.last_applied,
    )
    return replace(shelves, playbook=_replace_items(playbook, target, merged)), delta, False


def _add(
    shelves: _Shelves,
    delta: Delta,
    evidence: Mapping[str, Evidence],
    rejected: Sequence[Rejected],
    section_hash: str,
    now: str,
) -> str | tuple[_Shelves, Delta, bool]:
    playbook = shelves.playbook
    if delta.category not in LESSON_CATEGORIES:
        return f"the category {delta.category!r} is not a lesson category"
    if why := lesson_refusal(delta.lesson):
        return why
    lesson = delta.lesson.strip()
    if _is_rejected(rejected, delta.slug, delta.category, lesson):
        return "the lesson was rejected before"
    cited = tuple(dict.fromkeys(delta.tasks))
    checked = _evidence_check(delta.slug, delta.category, cited, evidence, lesson, delta.decision)
    if checked.why:
        return checked.why
    # Only a task that carries a matching backing counts toward `seen`: padding a citation
    # with an unbacked closed task cannot satisfy `KEEP_MIN_TASKS`.
    tasks, sources = checked.backed, checked.sources
    text = normalise(lesson)
    same = next((i for i in playbook.items if normalise(i.lesson) == text), None)
    if same is not None:
        new_ids = tuple(t for t in tasks if t not in same.seen_in)
        if not new_ids:
            return f"nothing to bump on {same.id}: every cited task is already counted"
        bumped = replace(same, seen_in=same.seen_in + new_ids, last_applied=now)
        effective = replace(delta, op="bump", item_id=same.id)
        return replace(shelves, playbook=_replace_items(playbook, same, bumped)), effective, False
    shelf = next(
        (
            a
            for a in shelves.archived
            if normalise(a.item.lesson) == text
            and a.item.category == delta.category
            and a.item.id.rpartition("-")[0] == delta.slug
        ),
        None,
    )
    if shelf is not None:
        # A restore keeps the id and the `seen_in` it had, plus the tasks this add cites.
        if len(playbook.items) >= PLAYBOOK_MAX_ITEMS:
            return f"the playbook has {PLAYBOOK_MAX_ITEMS} items: merge or retire first"
        seen_in = tuple(dict.fromkeys((*shelf.item.seen_in, *tasks)))
        restored = replace(shelf.item, seen_in=seen_in, last_applied=now, in_prompt_since="")
        out = _Shelves(
            Playbook(playbook.slug, playbook.items + (restored,)),
            shelves.candidates,
            tuple(a for a in shelves.archived if a is not shelf),
        )
        return out, delta, False
    waiting = next(
        (
            p
            for p in shelves.candidates
            if p.slug == delta.slug and normalise(p.lesson) == text and p.category == delta.category
        ),
        None,
    )
    seen_in = tuple(dict.fromkeys((*(waiting.seen_in if waiting else ()), *tasks)))
    all_sources = tuple(sorted({*(waiting.sources if waiting else ()), *sources}))
    confidence = _top(delta.confidence, waiting.confidence if waiting else "low")
    others = tuple(p for p in shelves.candidates if p is not waiting)
    if not keep(confidence, len(seen_in), all_sources):
        if waiting is None:
            fresh = Pending(
                delta.slug, delta.category, lesson, confidence, seen_in, all_sources, now
            )
            return replace(shelves, candidates=(*shelves.candidates, fresh)), delta, True
        if all(t in waiting.seen_in for t in tasks):
            return "nothing to add: the candidate already counts every cited task"
        grown = replace(waiting, confidence=confidence, seen_in=seen_in, sources=all_sources)
        grown_all = tuple(grown if p is waiting else p for p in shelves.candidates)
        return replace(shelves, candidates=grown_all), delta, True
    if len(playbook.items) >= PLAYBOOK_MAX_ITEMS:
        return f"the playbook has {PLAYBOOK_MAX_ITEMS} items: merge or retire first"
    numbers = [_number(i.id)[0] for i in playbook.items]
    numbers += [_number(a.item.id)[0] for a in shelves.archived]
    number = max((n for n in numbers if n < 10**9), default=0)
    item = Item(
        id=f"{playbook.slug}-{number + 1}",
        lesson=lesson,
        category=delta.category,
        since=section_hash,
        seen_in=seen_in,
        added_at=now,
        last_applied=now,
    )
    out = _Shelves(Playbook(playbook.slug, playbook.items + (item,)), others, shelves.archived)
    return out, delta, False


def _top(first: Confidence, second: Confidence) -> Confidence:
    return first if CONFIDENCES.index(first) >= CONFIDENCES.index(second) else second


def archive_stale(
    playbook: Playbook,
    archived: Sequence[Archived],
    aging: Mapping[str, tuple[int, bool]],
    *,
    now: str,
) -> tuple[Playbook, tuple[Archived, ...]]:
    """Move every item `archive_due` says is due out of the playbook, into the archive
    with the reason `stale`; nothing is deleted. `aging` maps an item id to
    `(role_tasks_since, backed)`; an item it does not name does not age."""
    due = [i for i in playbook.items if i.id in aging and archive_due(i, *aging[i.id])]
    if not due:
        return playbook, tuple(archived)
    gone = {i.id for i in due}
    kept = Playbook(playbook.slug, tuple(i for i in playbook.items if i.id not in gone))
    return kept, (*archived, *(Archived(i, "stale", now) for i in due))


def screen_system(
    deltas: Sequence[Delta], evidence: Mapping[str, Evidence]
) -> tuple[tuple[Delta, ...], tuple[Refusal, ...]]:
    """The `scope: system` deltas that may become an issue in the system's repo, and the
    refusals of the rest. A lesson for the system is only ever an `add`: its text passes
    `lesson_refusal` (and carries no comment marker, which would break the issue's block),
    its cited tasks pass the same evidence check a playbook add does, and `keep` rates it.
    Nothing here touches a playbook: the caller files what passes as an issue (ADR-0002)."""
    passed: list[Delta] = []
    refused: list[Refusal] = []
    for delta in deltas:
        why = _system_refusal(delta, evidence)
        if why:
            refused.append(Refusal(delta, why))
        else:
            passed.append(delta)
    return tuple(passed), tuple(refused)


def _system_refusal(delta: Delta, evidence: Mapping[str, Evidence]) -> str | None:
    if delta.op != "add":
        return "a lesson for the system is only proposed as an add"
    if delta.category not in LESSON_CATEGORIES:
        return f"the category {delta.category!r} is not a lesson category"
    if why := lesson_refusal(delta.lesson):
        return why
    if "<!--" in delta.lesson or "-->" in delta.lesson:
        return "the lesson contains a comment marker"
    cited = tuple(dict.fromkeys(delta.tasks))
    checked = _evidence_check(
        delta.slug, delta.category, cited, evidence, delta.lesson, delta.decision
    )
    if checked.why:
        return checked.why
    if not keep(delta.confidence, len(checked.backed), checked.sources):
        return "the lesson is not rated high and appeared in too few tasks to propose"
    return None


def _is_rejected(rejected: Sequence[Rejected], slug: str, category: str, lesson: str) -> bool:
    text = normalise(lesson)
    return any(
        r.slug == slug and r.category == category and normalise(r.lesson) == text for r in rejected
    )


# Words of five characters or more that say nothing about a decision: a lesson that shares
# only these with a decision does not come from it. A closed list, Spanish and English.
_TRIVIAL = frozenset(
    """
    about after again being before could decision decisions every other should still their
    there these those under using where which while would always never lesson task tasks
    antes cuando donde entre estas estos hasta luego nunca otros porque siempre sobre tambien
    tiene tienen todas todos puede pueden tarea tareas leccion decision debe cada
    """.split()
)
_WORD = re.compile(r"[a-z0-9]{5,}")


def shared_term(lesson: str, decision: str) -> bool:
    """Whether `lesson` and `decision` share a non-trivial term: five characters or more,
    accents and case folded, not in the closed `_TRIVIAL` list."""

    def terms(text: str) -> set[str]:
        flat = unicodedata.normalize("NFKD", normalise(text))
        plain = "".join(c for c in flat if not unicodedata.combining(c))
        return {w for w in _WORD.findall(plain) if w not in _TRIVIAL}

    return bool(terms(lesson) & terms(decision))


def _backings(known: Evidence) -> list[Backing]:
    derived = (return_backing(entry, known.task_type) for entry in known.return_history)
    return [*known.backings, *(b for b in derived if b is not None)]


def _decision_refusal(backing: Backing, known: Evidence, lesson: str, cited: int) -> str | None:
    """Why a `human_decision` backing cannot back `lesson`, or `None`: the lesson cites
    that decision, the decision block exists, the role ran in the task and the lesson
    shares a non-trivial term with what was decided."""
    number = int(backing.ref) if backing.ref.isdigit() else 0
    if not cited or cited != number:
        return f"the lesson does not cite Decision {number or '?'}"
    text = known.decisions.get(number)
    if text is None:
        return f"Decision {number} does not exist in the task"
    if backing.slug not in known.ran:
        return f"{backing.slug} did not run in the task"
    if not shared_term(lesson, text):
        return f"the lesson shares no term with Decision {number}"
    return None


@dataclass(frozen=True)
class _Check:
    """The outcome of `_evidence_check`: `why` it refuses (or `None`), the `sources` that
    back the lesson and the cited `backed` tasks, the only ones `keep` may count."""

    why: str | None = None
    sources: frozenset[str] = frozenset()
    backed: tuple[str, ...] = ()


def _evidence_check(
    slug: str,
    category: str,
    tasks: Sequence[str],
    evidence: Mapping[str, Evidence],
    lesson: str = "",
    decision: int = 0,
) -> _Check:
    """Why `tasks` cannot back a lesson of `category` for `slug`, the sources that do back
    it and the tasks that carry one: every task is known and closed, and at least one has a
    `Backing` of that slug and category (a `human_decision` one also needs its decision
    to exist, the role to have run and a term in common with the lesson)."""
    if not tasks:
        return _Check("the delta cites no task")
    for task in tasks:
        known = evidence.get(task)
        if known is None:
            return _Check(f"task {task} has no evidence")
        if not known.closed:
            return _Check(f"task {task} is not closed")
    sources: set[str] = set()
    backed: list[str] = []
    reasons: list[str] = []
    for task in tasks:
        for backing in _backings(evidence[task]):
            if backing.slug != slug or backing.category != category:
                continue
            if backing.source == "human_decision":
                why = _decision_refusal(backing, evidence[task], lesson, decision)
                if why:
                    reasons.append(why)
                    continue
            sources.add(backing.source)
            if task not in backed:
                backed.append(task)
    if not sources:
        return _Check(
            reasons[0]
            if reasons
            else f"no cited task has a return_history entry or a backing of {category} for {slug}"
        )
    return _Check(None, frozenset(sources), tuple(backed))


def _evidence_refusal(
    slug: str, category: str, tasks: Sequence[str], evidence: Mapping[str, Evidence]
) -> str | None:
    """Why `tasks` cannot back a lesson of `category` for `slug`, or `None`."""
    return _evidence_check(slug, category, tasks, evidence).why


def _replace_items(playbook: Playbook, old: Item, new: Item | None) -> Playbook:
    items = tuple(
        new if i.id == old.id and new is not None else i
        for i in playbook.items
        if not (i.id == old.id and new is None)
    )
    return Playbook(playbook.slug, items)
