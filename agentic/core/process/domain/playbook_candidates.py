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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import playbook as pb

MARKER = "<!-- agentic:playbook -->"
# Closed task documents whose delivery a person kept: a closed pull request taught
# nothing about the code that merged.
KEPT = ("merged", "merged_with_changes")
# Reflected tasks that make a summary due, and the files this module owns beside the
# playbooks (`playbook.RECORD_FILES` names the same two).
SUMMARY_EVERY = 10
PLAYBOOKS_DIR = ".agentic/playbooks"
LEDGER_FILE = f"{PLAYBOOKS_DIR}/reflected.yaml"
REJECTED_FILE = f"{PLAYBOOKS_DIR}/rejected.yaml"
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
    text: str = yaml.safe_dump(
        {"rejected": data}, sort_keys=False, allow_unicode=True, width=10**6
    )
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


@dataclass(frozen=True)
class Entry:
    """One reflected task: what it cost, why the reflection failed (empty when it did
    not) and the effective deltas the curator applied, as plain dicts."""

    cost_usd: float = 0.0
    failed: str = ""
    applied: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class Ledger:
    """`reflected.yaml`. `tasks` keeps the order tasks were reflected in; `summarised` is
    how many of them the last summary covered, `summaries` how many were written."""

    started_at: str = ""
    tasks: Mapping[str, Entry] = field(default_factory=dict)
    summaries: int = 0
    summarised: int = 0

    @property
    def since_summary(self) -> int:
        return len(self.tasks) - self.summarised


def render_ledger(ledger: Ledger) -> str:
    tasks: dict[str, Any] = {}
    for task, entry in ledger.tasks.items():
        item: dict[str, Any] = {"cost_usd": round(entry.cost_usd, 4)}
        if entry.failed:
            item["failed"] = entry.failed
        if entry.applied:
            item["applied"] = [dict(delta) for delta in entry.applied]
        tasks[task] = item
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
    `ValueError` (the verb refuses to guess what was already paid for)."""
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
            str(task): Entry(
                cost_usd=float(raw.get("cost_usd") or 0.0),
                failed=str(raw.get("failed") or ""),
                applied=tuple(dict(d) for d in raw.get("applied") or []),
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
    (they carry the entries), then candidate blocks (only task ids and costs). The
    earliest `started_at` wins, the counters are the furthest any source reached."""
    tasks: dict[str, Entry] = {}
    for ledger in ledgers:
        for task, entry in ledger.tasks.items():
            tasks.setdefault(task, entry)
    for candidate in candidates:
        for task, cost in candidate.record.tasks.items():
            tasks.setdefault(task, Entry(cost_usd=cost))
    starts = [x for x in (*(l.started_at for l in ledgers), *(c.record.started_at for c in candidates)) if x]
    return Ledger(
        started_at=min(starts) if starts else "",
        tasks=tasks,
        summaries=max((*(l.summaries for l in ledgers), *(c.record.summaries for c in candidates)), default=0),
        summarised=max((*(l.summarised for l in ledgers), *(c.record.summarised for c in candidates)), default=0),
    )


# ---- which tasks to reflect ----------------------------------------------------------


def eligible(
    documents: Sequence[Mapping[str, Any]],
    reflected_ids: Sequence[str] | set[str],
    started_at: str,
    since: str | None,
) -> list[Mapping[str, Any]]:
    """The closed documents worth a reflection, oldest first: closed (`closed_at`), with
    a delivery a person kept, closed on or after `since` (else after `started_at`) and not
    reflected yet. With neither `since` nor `started_at` nothing is eligible: the first
    run only starts the clock, it never reaches back (backfill is explicit, `--since`)."""
    threshold = since or started_at
    if not threshold:
        return []
    done = set(reflected_ids)
    kept = [
        d
        for d in documents
        if d.get("closed_at")
        and d.get("delivery_outcome") in KEPT
        and str(d["closed_at"]) >= threshold
        and str(d.get("id") or "") not in done
    ]
    return sorted(kept, key=lambda d: str(d["closed_at"]))


def summary_due(count_since_last: int) -> bool:
    """A summary every `SUMMARY_EVERY` reflected closed tasks."""
    return count_since_last >= SUMMARY_EVERY
