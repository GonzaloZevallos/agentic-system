"""The candidate pull request of the playbooks and what it remembers (ADR-0043).

Pure: no I/O. The `reflect` verb proposes lessons on the branch `agentic/playbook`, in one
open pull request per product. Everything the system must remember across runs lives in
two places a person can see and the system can read back without a database:

- the files under `.agentic/playbooks/` on the branch (`reflected.yaml`, `rejected.yaml`,
  the playbooks, `summaries/`), which become the product's the day the PR merges;
- the marker block at the end of the PR body, which outlives the branch: a PR closed
  unmerged leaves no file on the default branch, but its block says which tasks were
  reflected and which lessons it proposed, so a person's refusal is remembered.

The block is matched locally, as a substring of the body, never through the search index.
Nothing here decides what a lesson says: `playbook.apply` does that.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from . import playbook as pb
from .document import HUMAN

MARKER = "<!-- agentic:playbook -->"
# Closed task documents whose delivery a person kept: a closed pull request taught
# nothing about the code that merged. `auto_merged` is left out on purpose: no human
# endorsed that delivery (ADR-0045). Declared here, not imported from measurement
# (ADR-0010); a test keeps it equal to `metrics.OUTCOME_CLASS`'s `human_kept`.
KEPT = ("merged", "merged_with_changes")
# Reflected tasks that make a summary due, and the files this module owns beside the
# playbooks (`playbook.RECORD_FILES` names the same four).
SUMMARY_EVERY = 10
# A reflection that failed for a transient reason is tried again, this many attempts in all,
# before the key is marked `failed` for good.
REFLECT_ATTEMPTS = 3
PLAYBOOKS_DIR = ".agentic/playbooks"
LEDGER_FILE = f"{PLAYBOOKS_DIR}/reflected.yaml"
REJECTED_FILE = f"{PLAYBOOKS_DIR}/rejected.yaml"
# Rated lessons that did not pass `playbook.keep` (outside every prompt) and items
# `playbook.archive_due` took out of one (kept, never deleted).
CANDIDATES_FILE = f"{PLAYBOOKS_DIR}/candidates.yaml"
ARCHIVED_FILE = f"{PLAYBOOKS_DIR}/archived.yaml"
SUMMARIES_DIR = f"{PLAYBOOKS_DIR}/summaries"

_FENCE = re.compile(r"```yaml\n(?P<yaml>.*?)```", re.DOTALL)


def playbook_file(slug: str) -> str:
    return f"{PLAYBOOKS_DIR}/{slug}.yaml"


def summary_file(number: int) -> str:
    return f"{SUMMARIES_DIR}/{number}.md"


# ---- the PR's block ------------------------------------------------------------------


@dataclass(frozen=True)
class Add:
    """A lesson the curator added, as the PR proposed it: `lesson` is the normalised text,
    the form two lessons are compared in."""

    slug: str
    category: str
    lesson: str


@dataclass(frozen=True)
class Record:
    """What one candidate PR carries: the tasks it reflected with the cost each paid, the
    adds it proposed, when the reflection started and how far the summaries had got."""

    tasks: Mapping[str, float] = field(default_factory=dict)
    adds: tuple[Add, ...] = ()
    started_at: str = ""
    summaries: int = 0
    summarised: int = 0


def block(record: Record) -> str:
    """The marker and a fenced YAML block, to end the PR body with."""
    data: dict[str, Any] = {
        "started_at": record.started_at,
        "summaries": record.summaries,
        "summarised": record.summarised,
        "tasks": {task: round(cost, 4) for task, cost in record.tasks.items()},
        "adds": [{"slug": a.slug, "category": a.category, "lesson": a.lesson} for a in record.adds],
    }
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10**6)
    return f"{MARKER}\n```yaml\n{text}```\n"


def parse_block(body: str) -> Record | None:
    """The record under the marker, or `None` when the body has no marker or no readable
    block after it. A body someone edited by hand is never an error: it just stops being
    a candidate, and its tasks are reflected again."""
    at = body.find(MARKER)
    if at < 0:
        return None
    found = _FENCE.search(body, at + len(MARKER))
    if found is None:
        return None
    try:
        data = yaml.safe_load(found["yaml"])
        return _record_of(data)
    except (yaml.YAMLError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _record_of(data: Any) -> Record:
    if not isinstance(data, dict):
        raise ValueError("the block is not a mapping")
    tasks = data.get("tasks") or {}
    adds = data.get("adds") or []
    if not isinstance(tasks, dict) or not isinstance(adds, list):
        raise ValueError("tasks or adds have the wrong shape")
    return Record(
        tasks={str(task): float(cost) for task, cost in tasks.items()},
        adds=tuple(
            Add(slug=str(a["slug"]), category=str(a["category"]), lesson=str(a["lesson"]))
            for a in adds
        ),
        started_at=str(data.get("started_at") or ""),
        summaries=int(data.get("summaries") or 0),
        summarised=int(data.get("summarised") or 0),
    )


# ---- what a person refused -----------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A candidate pull request as `rejected_from` reads it: `state` is `OPEN`, `MERGED` or
    `CLOSED`, `merge_commit` the commit a merge made (empty for the others)."""

    state: str
    merge_commit: str
    record: Record


def reflected_from(candidates: Sequence[Candidate]) -> dict[str, float]:
    """Every task a candidate reflected, whatever became of the PR: reflecting costs
    money, so a task is never asked twice because its PR was closed."""
    out: dict[str, float] = {}
    for candidate in candidates:
        out.update(candidate.record.tasks)
    return out


def rejected_from(
    candidates: Sequence[Candidate], playbook_at_merge: Callable[[str, str], pb.Playbook]
) -> list[pb.Rejected]:
    """The lessons people refused. A PR closed unmerged refused every add it carried. A
    merged one refused an add only when the add is absent from that slug's playbook file
    *at the PR's merge commit* (`playbook_at_merge(commit, slug)`): at `HEAD` it could be
    missing because a later PR retired it, which is not a refusal. An open PR refused
    nothing yet."""
    out: list[pb.Rejected] = []
    for candidate in candidates:
        if candidate.state == "CLOSED":
            refused = candidate.record.adds
        elif candidate.state == "MERGED" and candidate.merge_commit:
            refused = tuple(
                add
                for add in candidate.record.adds
                if not _present(playbook_at_merge(candidate.merge_commit, add.slug), add.lesson)
            )
        else:
            continue
        out.extend(pb.Rejected(a.slug, a.category, pb.normalise(a.lesson)) for a in refused)
    return out


def _present(playbook: pb.Playbook, lesson: str) -> bool:
    text = pb.normalise(lesson)
    return any(pb.normalise(item.lesson) == text for item in playbook.items)


def merge_rejected(*groups: Sequence[pb.Rejected]) -> list[pb.Rejected]:
    """The union of several rejected lists, once each, first-seen order."""
    seen: dict[tuple[str, str, str], pb.Rejected] = {}
    for group in groups:
        for item in group:
            seen.setdefault((item.slug, item.category, pb.normalise(item.lesson)), item)
    return list(seen.values())


def render_rejected(rejected: Sequence[pb.Rejected]) -> str:
    data = [{"slug": r.slug, "category": r.category, "lesson": r.lesson} for r in rejected]
    text: str = yaml.safe_dump({"rejected": data}, sort_keys=False, allow_unicode=True, width=10**6)
    return text


def parse_rejected(text: str) -> list[pb.Rejected]:
    """Read `rejected.yaml`; empty text is an empty list, anything malformed raises
    `ValueError`."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"rejected.yaml: not valid YAML: {error}") from error
    if data is None:
        return []
    items = data.get("rejected") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("rejected.yaml: no `rejected` list")
    try:
        return [pb.Rejected(str(i["slug"]), str(i["category"]), str(i["lesson"])) for i in items]
    except (KeyError, TypeError) as error:
        raise ValueError(f"rejected.yaml: a malformed entry ({error})") from error


# ---- the ledger: reflected.yaml ------------------------------------------------------

Gate = Literal["review", "decision"]
# The two doors a task enters through (ADR-0056): `review` reads every source once the
# delivery is kept, `decision` only the human's decisions of a task that ended without one.
GATES: tuple[Gate, ...] = ("review", "decision")


def ledger_key(task_id: str, gate: Gate = "review") -> str:
    return f"{task_id}@{gate}"


def split_key(key: str) -> tuple[str, str]:
    """`(task id, gate)` of a ledger key; a key without `@` (every one written before
    ADR-0056) is read as `@review`."""
    task, _, gate = key.partition("@")
    return task, gate or "review"


def normal_key(key: str) -> str:
    task, gate = split_key(key)
    return f"{task}@{gate}"


@dataclass(frozen=True)
class Entry:
    """One reflected key: what it cost, why the reflection failed (empty when it did
    not), the effective deltas the curator applied as plain dicts, how many of the task's
    `### Decision` blocks it had read and how many attempts a transient failure has used."""

    cost_usd: float = 0.0
    failed: str = ""
    applied: tuple[Mapping[str, Any], ...] = ()
    decisions: int = 0
    attempts: int = 0

    @property
    def waits_retry(self) -> bool:
        """A transient failure with attempts left: not reflected yet, not failed for good."""
        return not self.failed and 0 < self.attempts < REFLECT_ATTEMPTS


def reflected_tasks(ledger: Ledger) -> int:
    """How many tasks the ledger has reflected: distinct ids over both gates, not counting
    a key that waits for a retry. The one place this is counted; the summary and the
    archive both call it."""
    return len({split_key(k)[0] for k, entry in ledger.tasks.items() if not entry.waits_retry})


def entries_since(ledger: Ledger, reflected: int) -> list[Entry]:
    """The entries of the tasks reflected after the first `reflected` distinct ones (the
    summary's window), in order. A key waiting for a retry belongs to no task yet. Counts
    the way `reflected_tasks` does: by distinct task id."""
    order: dict[str, int] = {}
    out: list[Entry] = []
    for key, entry in ledger.tasks.items():
        if entry.waits_retry:
            continue
        task = split_key(key)[0]
        if order.setdefault(task, len(order)) >= reflected:
            out.append(entry)
    return out


@dataclass(frozen=True)
class Ledger:
    """`reflected.yaml`. `tasks` keeps the order keys were reflected in (a key is
    `<id>@review` or `<id>@decision`); `summarised` is how many tasks the last summary
    covered, `summaries` how many were written."""

    started_at: str = ""
    tasks: Mapping[str, Entry] = field(default_factory=dict)
    summaries: int = 0
    summarised: int = 0

    @property
    def since_summary(self) -> int:
        return reflected_tasks(self) - self.summarised

    @property
    def done_keys(self) -> set[str]:
        """The keys not to reflect again: every one but those waiting for a retry."""
        return {normal_key(k) for k, entry in self.tasks.items() if not entry.waits_retry}

    @property
    def decisions_read(self) -> dict[str, int]:
        return {normal_key(k): entry.decisions for k, entry in self.tasks.items()}


def render_ledger(ledger: Ledger) -> str:
    tasks: dict[str, Any] = {}
    for key, entry in ledger.tasks.items():
        item: dict[str, Any] = {"cost_usd": round(entry.cost_usd, 4)}
        if entry.failed:
            item["failed"] = entry.failed
        if entry.applied:
            item["applied"] = [dict(delta) for delta in entry.applied]
        if entry.decisions:
            item["decisions"] = entry.decisions
        if entry.attempts:
            item["attempts"] = entry.attempts
        tasks[normal_key(key)] = item
    data = {
        "started_at": ledger.started_at,
        "summaries": ledger.summaries,
        "summarised": ledger.summarised,
        "tasks": tasks,
    }
    text: str = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10**6)
    return text


def parse_ledger(text: str) -> Ledger:
    """Read `reflected.yaml`; empty text is an empty ledger, anything malformed raises
    `ValueError` (the verb refuses to guess what was already paid for). A key without
    `@` is read as `@review`; `decisions` and `attempts` are optional."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"reflected.yaml: not valid YAML: {error}") from error
    if data is None:
        return Ledger()
    if not isinstance(data, dict) or not isinstance(data.get("tasks") or {}, dict):
        raise ValueError("reflected.yaml: not a mapping with a `tasks` mapping")
    try:
        tasks = {
            normal_key(str(task)): Entry(
                cost_usd=float(raw.get("cost_usd") or 0.0),
                failed=str(raw.get("failed") or ""),
                applied=tuple(dict(d) for d in raw.get("applied") or []),
                decisions=int(raw.get("decisions") or 0),
                attempts=int(raw.get("attempts") or 0),
            )
            for task, raw in (data.get("tasks") or {}).items()
        }
        return Ledger(
            started_at=str(data.get("started_at") or ""),
            tasks=tasks,
            summaries=int(data.get("summaries") or 0),
            summarised=int(data.get("summarised") or 0),
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"reflected.yaml: a malformed task ({error})") from error


def merge_ledgers(ledgers: Sequence[Ledger], candidates: Sequence[Candidate] = ()) -> Ledger:
    """What is known to be reflected, from every place that says so: ledger files first
    (they carry the entries), then candidate blocks (only task ids and costs, always
    `@review`: a block says nothing of the gate). The earliest `started_at` wins, the
    counters are the furthest any source reached."""
    tasks: dict[str, Entry] = {}
    for ledger in ledgers:
        for task, entry in ledger.tasks.items():
            tasks.setdefault(normal_key(task), entry)
    for candidate in candidates:
        for task, cost in candidate.record.tasks.items():
            tasks.setdefault(normal_key(task), Entry(cost_usd=cost))
    starts = [
        x
        for x in (*(g.started_at for g in ledgers), *(c.record.started_at for c in candidates))
        if x
    ]
    summaries = (*(g.summaries for g in ledgers), *(c.record.summaries for c in candidates))
    summarised = (*(g.summarised for g in ledgers), *(c.record.summarised for c in candidates))
    return Ledger(
        started_at=min(starts) if starts else "",
        tasks=tasks,
        summaries=max(summaries, default=0),
        summarised=max(summarised, default=0),
    )


# ---- which tasks to reflect ----------------------------------------------------------

_DECISION_HEADING = re.compile(r"^### Decision \d+", re.M)


def decision_count(document: Mapping[str, Any]) -> int:
    """How many `### Decision N` blocks the document's HUMAN section carries."""
    sections = document.get("sections")
    human = sections.get(HUMAN, "") if isinstance(sections, Mapping) else ""
    return len(_DECISION_HEADING.findall(str(human or "")))


def _decision_clock(document: Mapping[str, Any], closed_escalations: Collection[str]) -> str:
    """When a task ended without a kept delivery, or `""` when it did not (yet):
    `discarded`, `split` and `tracker_only` by `finished_at`, a closed pull request by
    `closed_at`, an escalation whose issue is closed by the end of its last run."""
    status = document.get("status")
    if status in ("discarded", "split", "tracker_only"):
        return str(document.get("finished_at") or "")
    if document.get("closed_at") and document.get("delivery_outcome") == "closed":
        return str(document["closed_at"])
    if status == "waiting_on_human" and str(document.get("id") or "") in closed_escalations:
        runs = document.get("runs")
        ended = [
            str(r.get("ended_at"))
            for r in runs or []
            if isinstance(r, Mapping) and r.get("ended_at")
        ]
        return max(ended, default="")
    return ""


def eligible(
    documents: Sequence[Mapping[str, Any]],
    reflected_ids: Collection[str],
    started_at: str,
    since: str | None,
    *,
    gate: Gate = "review",
    closed_escalations: Collection[str] = frozenset(),
    decisions_read: Mapping[str, int] | None = None,
) -> list[Mapping[str, Any]]:
    """The documents worth a reflection through `gate`, oldest first, closed on or after
    `since` (else after `started_at`) and not reflected yet. With neither `since` nor
    `started_at` nothing is eligible: the first run only starts the clock, it never reaches
    back (backfill is explicit, `--since`). `reflected_ids` are ledger keys (a bare id is
    `@review`); no I/O: whether an escalation's issue is closed is `closed_escalations`.

    - `review`: `closed_at` and a delivery a person kept (`KEPT`).
    - `decision`: it has a `### Decision`, ended without a kept delivery (`_decision_clock`),
      and is new or has more decisions than the key had read (`decisions_read`)."""
    threshold = since or started_at
    if not threshold:
        return []
    done = {normal_key(k) for k in reflected_ids}
    read = decisions_read or {}
    clocked: list[tuple[str, Mapping[str, Any]]] = []
    for d in documents:
        key = ledger_key(str(d.get("id") or ""), gate)
        if gate == "review":
            clock = (
                str(d["closed_at"])
                if d.get("closed_at") and d.get("delivery_outcome") in KEPT
                else ""
            )
            fresh = key not in done
        else:
            count = decision_count(d)
            clock = _decision_clock(d, closed_escalations) if count else ""
            fresh = key not in done or count > read.get(key, 0)
        if clock and clock >= threshold and fresh:
            clocked.append((clock, d))
    return [d for _, d in sorted(clocked, key=lambda pair: pair[0])]


def in_prompt_since(
    item_id: str,
    merged_candidates: Sequence[Candidate],
    playbook_at_merge: Callable[[str, str], pb.Playbook],
    commit_date: Callable[[str], str],
    *,
    not_before: str = "",
) -> str | None:
    """The date `item_id` reached a prompt: the merge date of the first merged candidate
    PR whose playbook, read at its merge commit (`playbook_at_merge(commit, slug)`),
    contains the item's **id**. Never `added_at`, never the block's text: an item whose PR
    never merged has not been in a prompt and does not age. `not_before` skips merges
    before it (a restored item counts from the merge after its restore)."""
    slug = item_id.rpartition("-")[0]
    dates = [
        date
        for candidate in merged_candidates
        if candidate.state == "MERGED" and candidate.merge_commit
        if any(i.id == item_id for i in playbook_at_merge(candidate.merge_commit, slug).items)
        if (date := commit_date(candidate.merge_commit)) and date >= not_before
    ]
    return min(dates) if dates else None


# ---- candidates.yaml and archived.yaml -----------------------------------------------


def render_candidates(pending: Sequence[pb.Pending]) -> str:
    data = [
        {
            "slug": p.slug,
            "category": p.category,
            "lesson": p.lesson,
            "confidence": p.confidence,
            "seen_in": list(p.seen_in),
            "sources": list(p.sources),
            "added_at": p.added_at,
        }
        for p in pending
    ]
    text: str = yaml.safe_dump(
        {"candidates": data}, sort_keys=False, allow_unicode=True, width=10**6
    )
    return text


def parse_candidates(text: str) -> list[pb.Pending]:
    """Read `candidates.yaml`; empty text is an empty list, anything malformed raises
    `ValueError`. The lesson goes through `lesson_refusal` like any other."""
    rows = _rows_of(text, "candidates")
    out: list[pb.Pending] = []
    for row in rows:
        try:
            confidence = str(row["confidence"])
            if confidence not in pb.CONFIDENCES:
                raise ValueError(f"confidence {confidence} is unknown")
            lesson = str(row["lesson"])
            if refusal := pb.lesson_refusal(lesson):
                raise ValueError(refusal)
            if str(row["category"]) not in pb.LESSON_CATEGORIES:
                raise ValueError(f"category {row['category']} is unknown")
            out.append(
                pb.Pending(
                    slug=str(row["slug"]),
                    category=str(row["category"]),
                    lesson=lesson.strip(),
                    confidence=_confidence(confidence),
                    seen_in=tuple(dict.fromkeys(str(t) for t in row.get("seen_in") or [])),
                    sources=tuple(str(x) for x in row.get("sources") or []),
                    added_at=str(row.get("added_at") or ""),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"candidates.yaml: a malformed entry ({error})") from error
    return out


def _confidence(text: str) -> pb.Confidence:
    if text == "low":
        return "low"
    if text == "medium":
        return "medium"
    if text == "high":
        return "high"
    raise ValueError(f"confidence {text} is unknown")


def render_archived(archived: Sequence[pb.Archived]) -> str:
    data = [
        {"reason": a.reason, "archived_at": a.archived_at, **pb.item_data(a.item)} for a in archived
    ]
    text: str = yaml.safe_dump({"archived": data}, sort_keys=False, allow_unicode=True, width=10**6)
    return text


def parse_archived(text: str) -> list[pb.Archived]:
    """Read `archived.yaml`: each row an item plus `reason` and `archived_at`."""
    out: list[pb.Archived] = []
    for row in _rows_of(text, "archived"):
        if not isinstance(row, dict):
            raise ValueError("archived.yaml: an entry is not a mapping")
        rest = {k: v for k, v in row.items() if k not in ("reason", "archived_at")}
        slug = str(row.get("id", "")).rpartition("-")[0]
        out.append(
            pb.Archived(
                pb.parse_item(rest, slug),
                str(row.get("reason") or ""),
                str(row.get("archived_at") or ""),
            )
        )
    return out


def _rows_of(text: str, key: str) -> list[Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"{key}.yaml: not valid YAML: {error}") from error
    if data is None:
        return []
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{key}.yaml: no `{key}` list")
    return rows


def summary_due(count_since_last: int) -> bool:
    """A summary every `SUMMARY_EVERY` reflected closed tasks."""
    return count_since_last >= SUMMARY_EVERY
