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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import playbook as pb
from ..domain import rules
from ..domain.roles import REFLECTOR, prompt_hash
from ..domain.task import TaskState
from .ports import Ports
from .run_role import Escalating, run_role

# A review section longer than this is cut in the reflector's input, with a note: the
# lessons live in the head of a review (its verdict, its findings), and the input has to
# stay inside the per-attempt cap however long a review ran.
REVIEW_MAX_CHARS = 6000
_SUPERSEDED = re.compile(r"^(?P<name>.+) \(v(?P<n>\d+)\)$")


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


@dataclass(frozen=True)
class Attributed:
    """One return of the task, attributed: whose lesson it can be, in which category,
    and the review section it came from (`None` when the document no longer has it)."""

    slug: str
    pair: str
    category: str
    reason: str
    review: str | None


def attributable_returns(document: Mapping[str, Any]) -> list[Attributed]:
    """The task's returns `playbook.accountable` can attribute, in order. The rest never
    reach the model. The n-th return of a subtask's pair came from the n-th delivery of its
    review section: a superseded one is `<section> (vN)`, the latest has no suffix."""
    task_type = str(document.get("type") or "")
    sections: Mapping[str, str] = document.get("sections") or {}
    seen: dict[str, int] = {}
    out: list[Attributed] = []
    for entry in document.get("change_history") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("by") == "landing":
            continue  # not a Tech Lead return: it has no review delivery of its own
        pair = str(entry.get("pair") or "")
        seen[pair] = seen.get(pair, 0) + 1
        slug = pb.accountable(entry, task_type)
        if slug is None:
            continue
        subtask = pair.partition(":")[2]
        out.append(
            Attributed(
                slug=slug,
                pair=pair,
                category=str(entry["category"]),
                reason=str(entry.get("reason") or ""),
                review=_review_text(sections, subtask, seen[pair]),
            )
        )
    return out


def _review_text(sections: Mapping[str, str], subtask: str, delivery: int) -> str | None:
    name = rules.review_section(subtask)
    versions = {
        int(m["n"]): text
        for key, text in sections.items()
        if (m := _SUPERSEDED.match(key)) and m["name"] == name
    }
    text = versions.get(delivery) if delivery in versions else sections.get(name)
    if text is None:
        return None
    if len(text) > REVIEW_MAX_CHARS:
        return text[:REVIEW_MAX_CHARS].rstrip() + "\n\n[the review is cut here]"
    return text


def reflection_input(
    task_id: str,
    returns: Sequence[Attributed],
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
) -> str:
    """The reflector's whole message: the attributable returns with the review each came
    from, then each accountable role's playbook with item ids and its rejected lessons."""
    parts = ["## Returns"]
    for number, item in enumerate(returns, 1):
        parts.append(
            f"### Return {number}: task {task_id}, role {item.slug}, category {item.category}\n\n"
            f"Pair: {item.pair}\nReason: {item.reason}\n\n"
            + (f"Review it came from:\n\n{item.review}" if item.review else "Review: not kept.")
        )
    parts.append("## Playbooks")
    for slug in dict.fromkeys(item.slug for item in returns):
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
    gate: str = "review",
) -> Reflected:
    """Reflect on one closed task. `playbooks` is each role's current playbook by slug
    (a role missing from it has an empty one), `rejected` what people refused before, and
    `now` what a new item records as `added_at`. `candidates` and `archived` are the lists
    `playbook.apply` promotes from and restores out of; the result carries them back. The
    `decision` gate reads only the human's decisions, which are not an input here yet:
    it asks the model nothing."""
    task_id = str(document.get("id") or "")
    shelves = (tuple(candidates), tuple(archived))
    try:
        return await _reflect(
            cfg, ports, document, task_id, playbooks, rejected, now, shelves, gate
        )
    except Exception as error:  # by contract: a reflection never raises into a task
        return Reflected(task_id, failed=True, error=f"{type(error).__name__}: {error}")


async def _reflect(
    cfg: Configuration,
    ports: Ports,
    document: Mapping[str, Any],
    task_id: str,
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
    now: str,
    shelves: tuple[tuple[pb.Pending, ...], tuple[pb.Archived, ...]],
    gate: str,
) -> Reflected:
    returns = attributable_returns(document) if gate == "review" else []
    if not returns:
        return Reflected(task_id)
    state: TaskState = {
        "id": f"reflect-{task_id}",
        "title": f"Reflection on {task_id}",
        "description": f"What the returns of {task_id} teach the role that made each mistake.",
    }
    capped = cfg.model_copy(update={"max_budget_usd": cfg.reflect_max_budget_usd})
    message = reflection_input(task_id, returns, playbooks, rejected)
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
        output.data["deltas"], output.cost_usd, document, task_id, playbooks, rejected, now, shelves
    )


def _fold(
    raw: Sequence[Mapping[str, Any]],
    cost: float,
    document: Mapping[str, Any],
    task_id: str,
    playbooks: Mapping[str, pb.Playbook],
    rejected: Sequence[pb.Rejected],
    now: str,
    shelves: tuple[tuple[pb.Pending, ...], tuple[pb.Archived, ...]],
) -> Reflected:
    candidates, archived = shelves
    deltas = [
        pb.Delta(
            op=d["op"],
            slug=d["slug"],
            category=d["category"],
            lesson=d["lesson"],
            item_id=d["item_id"],
            tasks=tuple(d["tasks"]),
        )
        for d in raw
    ]
    evidence = {
        task_id: pb.Evidence(
            closed=document.get("status") == "closed",
            task_type=str(document.get("type") or ""),
            return_history=[
                e for e in document.get("change_history") or [] if isinstance(e, Mapping)
            ],
        )
    }
    current = dict(playbooks)
    applied: list[pb.Delta] = []
    refused: list[pb.Refusal] = []
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
    )
