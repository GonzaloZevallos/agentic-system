"""The reflector's use case (ADR-0043): what a closed task's returns teach the role that
made each mistake, as deltas to that role's playbook.

The input is built here, in code, from the closed document's `return_history` and the
review sections those returns came from: no prompt asks the model which findings the
review lowered, because `rules.lower_unbacked_blocks` already took them out of the
returns before they were recorded. Only returns `playbook.accountable` can attribute
reach the model; with none, the model is never called. What the model proposes goes
through `playbook.apply`, the curator, which is code: it decides which deltas apply.

This function writes nothing: not to disk, not to the closed document, not to an issue or
a checkpoint. The caller (the `reflect` verb) owns every write. Any failure, the role's
or an unexpected exception, is returned as a failed result and never raised, so a
reflection can never fail, escalate or reopen a task.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from agentic.shared.configuration import Configuration

from ..domain import playbook as pb
from ..domain import playbook_candidates as pc
from ..domain import rules
from ..domain.document import HUMAN
from ..domain.roles import PM_FINAL, REFLECTOR, prompt_hash
from ..domain.task import INTEGRATION, TaskState
from .ports import Ports
from .run_role import Escalating, run_role

# A text longer than this is cut in the reflector's input, with a note: the lessons live in
# the head of a review, a claim or a decision, and the input has to stay inside the
# per-attempt cap however long it ran. At most this many entries per source reach the model
# too (`facts` keeps blocking before important before observation, then the most recent) and
# the input says how many it left out.
REFLECT_MAX_ENTRIES = 40
REFLECT_MAX_CHARS = 6000
_SUPERSEDED = re.compile(r"^(?P<name>.+) \(v(?P<n>\d+)\)$")
_DECISION_BLOCK = re.compile(r"^### Decision (?P<n>\d+)\s*$", re.M)
_SOURCES: tuple[pb.Source, ...] = ("return", "finding", "design", "human_decision")
_TITLES = {
    "return": "Returns",
    "finding": "Findings",
    "design": "Design remarks",
    "human_decision": "Human decisions",
}
_RANK = {"blocking": 0, "important": 1, "observation": 2}
# A finding teaches only while it is the branch's or the plan's own: `outside` is nobody's
# lesson, and a `dismissed` one was not a fault.
_LEARNABLE_SCOPES = ("subtask", "plan")
_LEARNABLE_STATUSES = ("open", "fixed")


@dataclass(frozen=True)
class Reflected:
    """What one reflection produced. `playbooks` holds only the roles whose playbook
    changed, by slug; `applied` carries effective deltas (`pb.Applied`'s own rule) and
    `refused` each refused one with its reason. `failed` is a role or harness failure
    after its retry; the attempts it paid are still in `cost_usd`."""

    task_id: str
    playbooks: dict[str, pb.Playbook] = field(default_factory=dict)
    applied: tuple[pb.Delta, ...] = ()
    refused: tuple[pb.Refusal, ...] = ()
    cost_usd: float = 0.0
    failed: bool = False
    error: str = ""
    # The lists after the run, or `None` when the reflection left them as they were.
    candidates: tuple[pb.Pending, ...] | None = None
    archived: tuple[pb.Archived, ...] | None = None
    # The lessons for the system that passed `screen_system`: the caller files them as an
    # issue; they are in no playbook (ADR-0002).
    system: tuple[pb.Delta, ...] = ()


@dataclass(frozen=True)
class Fact:
    """One recorded fact the model may learn from, built by code from the closed document:
    its `backing` (what a lesson may cite it by), a one-line `title`, its `text` and its
    `rank` (0 blocking, 1 important, 2 observation) and `order` in the document."""

    backing: pb.Backing
    title: str
    text: str
    rank: int
    order: int


def facts(
    document: Mapping[str, Any], decisions: int = 0, sources: Collection[str] = _SOURCES
) -> list[Fact]:
    """Every fact of the closed document `pb.ACCOUNTABLE` can attribute, from structured
    fields alone, never from prose: returns of any pair, findings (`open` or `fixed`, scope
    `subtask` or `plan`: a `dismissed` or `outside` one is neither here nor a `Backing`),
    a lead's design remarks, and the human's `### Decision N` blocks above `decisions`."""
    task_type = str(document.get("type") or "")
    found: list[Fact] = []
    if "return" in sources:
        found += _return_facts(document, task_type)
    if "finding" in sources:
        found += _finding_facts(document, task_type)
    if "design" in sources:
        found += _design_facts(document, task_type)
    if "human_decision" in sources:
        found += _decision_facts(document, decisions)
    return found


def backings(document: Mapping[str, Any], decisions: int = 0) -> tuple[pb.Backing, ...]:
    """The distinct `Backing`s of the document: what `apply` may let a lesson cite."""
    return backings_of(document, decisions, _SOURCES)


def backings_of(
    document: Mapping[str, Any], decisions: int, sources: Collection[str]
) -> tuple[pb.Backing, ...]:
    return tuple(dict.fromkeys(f.backing for f in facts(document, decisions, sources)))


def _closed(document: Mapping[str, Any], gate: pc.Gate) -> bool:
    """Whether the document is one of the two doors' (ADR-0056): `review`, a closed one
    with a delivery a person kept; `decision`, one that ended without it (discarded, split,
    a closed pull request or an escalation whose issue closed; the poller checked which)."""
    if gate == "review":
        return bool(document.get("closed_at")) and document.get("delivery_outcome") in pc.KEPT
    return bool(document.get("closed_at")) or document.get("status") in (
        "discarded",
        "split",
        "waiting_on_human",
    )


def _return_facts(document: Mapping[str, Any], task_type: str) -> list[Fact]:
    sections: Mapping[str, str] = document.get("sections") or {}
    seen: dict[str, int] = {}
    out: list[Fact] = []
    for order, entry in enumerate(document.get("change_history") or []):
        if not isinstance(entry, Mapping) or entry.get("by") == "landing":
            continue  # a landing return has no review delivery of its own
        pair = str(entry.get("pair") or "")
        seen[pair] = seen.get(pair, 0) + 1
        backing = pb.return_backing(entry, task_type)
        if backing is None:
            continue
        review = (
            _review_text(sections, pair.partition(":")[2], seen[pair])
            if pair.startswith(pb.REVIEW_PAIR)
            else None
        )
        body = f"Pair: {pair}\nReason: {entry.get('reason') or ''}\n\n"
        body += f"Review it came from:\n\n{review}" if review else "Review: not kept."
        title = f"role {backing.slug}, category {backing.category}"
        out.append(Fact(backing, title, body, 0, order))
    return out


def _finding_facts(document: Mapping[str, Any], task_type: str) -> list[Fact]:
    out: list[Fact] = []
    seen: set[tuple[str, str, str]] = set()
    rows = rules.findings_rows(cast(TaskState, document))
    for order, row in enumerate(rows):
        if row["status"] not in _LEARNABLE_STATUSES or row["scope"] not in _LEARNABLE_SCOPES:
            continue
        origin: pb.Origin = "final_validation" if row["location"] == PM_FINAL.name else "review"
        for subtask in row["subtask"].split(", "):
            qualifier = pb.INTEGRATION_QUALIFIER if subtask == INTEGRATION else ""
            slug = pb.attribute(origin, task_type, row["category"], qualifier)
            key = (subtask, row["location"], row["claim"])
            if slug is None or key in seen:
                continue
            seen.add(key)
            body = (
                f"Subtask: {subtask}\nLocation: {row['location']}\n"
                f"Severity: {row['severity']}, scope {row['scope']}, status {row['status']}\n\n"
                f"{row['claim']}"
            )
            title = f"role {slug}, category {row['category']}"
            backing = pb.Backing("finding", slug, row["category"])
            out.append(Fact(backing, title, body, _RANK[row["severity"]], order))
    return out


def _design_facts(document: Mapping[str, Any], task_type: str) -> list[Fact]:
    out: list[Fact] = []
    for order, item in enumerate(document.get("design_items") or []):
        role = str(item.get("role_slug") or "")
        qualifier = (
            "ux" if role.startswith("ux_lead") else "ui" if role.startswith("ui_lead") else ""
        )
        slug = pb.attribute("design", task_type, "design", qualifier) if qualifier else None
        if slug is None:
            continue
        body = f"Raised by: {role}\nStep: {item.get('step') or ''}\n\n{item.get('text') or ''}"
        rank = _RANK.get(str(item.get("kind")), 2)
        out.append(Fact(pb.Backing("design", slug, "design"), f"role {slug}", body, rank, order))
    return out


def decision_blocks(document: Mapping[str, Any]) -> dict[int, str]:
    """The human's `### Decision N` blocks by number, as the HUMAN section carries them
    (a carried-over section keeps its numbering, so the numbers are the task's, not a run's)."""
    sections = document.get("sections")
    human = str(sections.get(HUMAN) or "") if isinstance(sections, Mapping) else ""
    marks = list(_DECISION_BLOCK.finditer(human))
    return {
        int(m["n"]): human[
            m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(human)
        ].strip()
        for i, m in enumerate(marks)
    }


def _decision_facts(document: Mapping[str, Any], decisions: int) -> list[Fact]:
    ran = sorted(set(document.get("prompts") or {}) & pb.PLAYBOOK_ROLES)
    out: list[Fact] = []
    for number, text in decision_blocks(document).items():
        if number <= decisions:
            continue
        # A decision is nobody's mistake: any role that ran in the task may learn from it,
        # and the curator checks the slug ran and shares a term with the decision.
        for slug in ran:
            backing = pb.Backing("human_decision", slug, "human-decision", str(number))
            out.append(Fact(backing, f"Decision {number}, role {slug}", text, 0, number))
    return out


def _review_text(sections: Mapping[str, str], subtask: str, delivery: int) -> str | None:
    name = rules.review_section(subtask)
    versions = {
        int(m["n"]): text
        for key, text in sections.items()
        if (m := _SUPERSEDED.match(key)) and m["name"] == name
    }
    text = versions.get(delivery) if delivery in versions else sections.get(name)
    return text


def _cut(text: str) -> str:
    if len(text) > REFLECT_MAX_CHARS:
        return text[:REFLECT_MAX_CHARS].rstrip() + "\n\n[the text is cut here]"
    return text


def reflection_input(
    task_id: str,
    found: Sequence[Fact],
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
) -> str:
    """The reflector's whole message: each source's facts (at most `REFLECT_MAX_ENTRIES`,
    blocking first, then the most recent, saying how many it left out), then each
    accountable role's playbook with item ids and its rejected lessons."""
    parts: list[str] = []
    for source in _SOURCES:
        mine = [f for f in found if f.backing.source == source]
        if not mine:
            continue
        chosen = sorted(mine, key=lambda f: (f.rank, -f.order))[:REFLECT_MAX_ENTRIES]
        parts.append(f"## {_TITLES[source]}")
        for number, fact in enumerate(sorted(chosen, key=lambda f: f.order), 1):
            parts.append(
                f"### {source.replace('_', ' ').capitalize()} {number}: task {task_id}, "
                f"{fact.title}\n\n{_cut(fact.text)}"
            )
        if len(mine) > len(chosen):
            parts.append(f"[{len(mine) - len(chosen)} entries omitted]")
    parts.append("## Playbooks")
    for slug in dict.fromkeys(f.backing.slug for f in found):
        current = playbooks.get(slug, pb.Playbook(slug))
        lines = [
            f"- {i.id} ({i.category}, cited by {i.seen} task(s)): {i.lesson}"
            for i in sorted(current.items, key=lambda i: _id_order(i.id))
        ]
        refused = [f"- ({r.category}) {r.lesson}" for r in rejected if r.slug == slug]
        parts.append(
            f"### {slug}\n\nItems ({len(current.items)} of {pb.PLAYBOOK_MAX_ITEMS}):\n"
            + ("\n".join(lines) if lines else "- none")
            + "\n\nRefused by a person:\n"
            + ("\n".join(refused) if refused else "- none")
        )
    return "\n\n".join(parts)


def _id_order(item_id: str) -> tuple[int, str]:
    tail = item_id.rpartition("-")[2]
    return (int(tail), item_id) if tail.isdigit() else (10**9, item_id)


def _stamp_since(before: pb.Playbook, after: pb.Playbook) -> pb.Playbook:
    """Give every item `after` introduced the hash of the section that contains it.

    `playbook_window.compare` judges an item at the boundary where the segment key (the
    hash of the section a task ran with, `Prompt.playbook`) equals the item's `since`. A
    task runs with the section *after* the add, so that is the hash a new item must
    record; `apply` only knows the section before it, so it is stamped here, once the
    whole batch for the slug is folded in."""
    known = {i.id for i in before.items}
    final = pb.section(after)
    since = prompt_hash(final) if final else "empty"
    return replace(
        after,
        items=tuple(i if i.id in known else replace(i, since=since) for i in after.items),
    )


async def reflect_task(
    cfg: Configuration,
    ports: Ports,
    document: Mapping[str, Any],
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
    *,
    now: str,
    candidates: Sequence[pb.Pending] = (),
    archived: Sequence[pb.Archived] = (),
    gate: pc.Gate = "review",
    decisions: int = 0,
) -> Reflected:
    """Reflect on one closed task. `playbooks` is each role's current playbook by slug
    (a role missing from it has an empty one), `rejected` what people refused before, and
    `now` what a new item records as `added_at`. `candidates` and `archived` are the lists
    `playbook.apply` promotes from and restores out of; the result carries them back. `gate`
    is the door the task came through (the `review` gate reads the four sources; `decision`,
    only the human's decisions) and `decisions` how many `### Decision` blocks an earlier
    reflection of it already read."""
    task_id = str(document.get("id") or "")
    shelves = (tuple(candidates), tuple(archived))
    try:
        return await _reflect(
            cfg, ports, document, (gate, decisions), playbooks, rejected, now, shelves
        )
    except Exception as error:  # by contract: a reflection never raises into a task
        return Reflected(task_id, failed=True, error=f"{type(error).__name__}: {error}")


async def _reflect(
    cfg: Configuration,
    ports: Ports,
    document: Mapping[str, Any],
    door: tuple[pc.Gate, int],
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
    now: str,
    shelves: tuple[tuple[pb.Pending, ...], tuple[pb.Archived, ...]],
) -> Reflected:
    task_id = str(document.get("id") or "")
    gate, decisions = door
    found = facts(document, decisions, ("human_decision",) if gate == "decision" else _SOURCES)
    if not found:
        return Reflected(task_id)
    state: TaskState = {
        "id": f"reflect-{task_id}",
        "title": f"Reflection on {task_id}",
        "description": f"What the returns of {task_id} teach the role that made each mistake.",
    }
    capped = cfg.model_copy(update={"max_budget_usd": cfg.reflect_max_budget_usd})
    message = reflection_input(task_id, found, playbooks, rejected)
    try:
        output, patch = await run_role(state, REFLECTOR, capped, ports, prompt=message)
    except Escalating as exc:  # pragma: no cover -- `run_role` converts these itself
        return Reflected(task_id, cost_usd=exc.cost_usd, failed=True, error=str(exc))
    if output is None:
        return Reflected(
            task_id,
            cost_usd=float(patch.get("cost_usd", 0.0)),
            failed=True,
            error=str(patch.get("escalated") or "the reflector failed"),
        )
    return _fold(
        output.data["deltas"], output.cost_usd, document, door, playbooks, rejected, now, shelves
    )


def _fold(
    raw: Sequence[Mapping[str, Any]],
    cost: float,
    document: Mapping[str, Any],
    door: tuple[pc.Gate, int],
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
    now: str,
    shelves: tuple[tuple[pb.Pending, ...], tuple[pb.Archived, ...]],
) -> Reflected:
    task_id = str(document.get("id") or "")
    gate, decisions = door
    candidates, archived = shelves
    deltas = [
        pb.Delta(
            op=d["op"],
            slug=d["slug"],
            category=d["category"],
            lesson=d["lesson"],
            item_id=d["item_id"],
            tasks=tuple(d["tasks"]),
            confidence=d.get("confidence") or "high",
            scope=d.get("scope", "product"),
            decision=int(d.get("decision") or 0),
        )
        for d in raw
    ]
    sources = ("human_decision",) if gate == "decision" else _SOURCES
    evidence = {
        task_id: pb.Evidence(
            closed=_closed(document, gate),
            task_type=str(document.get("type") or ""),
            backings=backings_of(document, decisions, sources),
            decisions=decision_blocks(document),
            ran=frozenset(document.get("prompts") or {}),
        )
    }
    system, unsuitable = pb.screen_system([d for d in deltas if d.scope == "system"], evidence)
    deltas = [d for d in deltas if d.scope != "system"]
    current = dict(playbooks)
    applied: list[pb.Delta] = []
    refused: list[pb.Refusal] = list(unsuitable)
    changed: dict[str, pb.Playbook] = {}
    for slug in dict.fromkeys(d.slug for d in deltas):
        if slug not in pb.PLAYBOOK_ROLES:
            refused.extend(
                pb.Refusal(d, f"no playbook for slug {d.slug!r}") for d in deltas if d.slug == slug
            )
            continue
        before = current.get(slug, pb.Playbook(slug))
        rendered = pb.section(before)
        outcome = pb.apply(
            before,
            [d for d in deltas if d.slug == slug],
            evidence,
            rejected,
            section_hash=prompt_hash(rendered) if rendered else "empty",
            now=now,
            candidates=candidates,
            archived=archived,
        )
        candidates, archived = outcome.candidates, outcome.archived
        applied.extend(outcome.applied)
        refused.extend(outcome.refused)
        if outcome.playbook != before:
            changed[slug] = _stamp_since(before, outcome.playbook)
    return replace(
        Reflected(task_id),
        playbooks=changed,
        applied=tuple(applied),
        refused=tuple(refused),
        cost_usd=cost,
        candidates=candidates,
        archived=archived,
        system=system,
    )
