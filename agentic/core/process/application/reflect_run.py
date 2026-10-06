"""The `reflect` verb's delivery (ADR-0043): which closed tasks to reflect on, the candidate
branch `agentic/playbook`, its files and its one open pull request.

`reflect.reflect_task` (the reflector's use case) decides what a task teaches; this module
only gathers what is already known, calls it and delivers the result for a person to
merge. Nothing here writes to the default branch, a closed task document, a checkpoint or
an issue: every write goes on the candidate branch, in a worktree. A failure of one task's
reflection is recorded in `reflected.yaml` with its reason and never raised.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import playbook as pb
from ..domain import playbook_candidates as pc
from ..domain import rules
from .ports import ListedPullRequest, Ports
from .reflect import reflect_task

TASK_ID = "playbook"
BRANCH = rules.task_branch(TASK_ID)
TITLE = "Playbook candidates from reviewed returns"
INTRO = (
    "Every lesson in this pull request is model text derived from reviews. Read each one "
    "before merging: edit it or delete it, and a lesson that is absent from the file at "
    "the merge commit is remembered as refused and never proposed again.\n\n"
    "About 10 closed tasks after the merge, compare the per-category return rates with the "
    "10 before and post them as a comment on the issue that asked for this (ADR-0043)."
)


@dataclass(frozen=True)
class Known:
    """What earlier runs left behind, from every place that says so."""

    ledger: pc.Ledger
    rejected: list[pb.Rejected]
    candidates: list[tuple[ListedPullRequest, pc.Candidate]]
    listed: list[ListedPullRequest]
    branch_ledger: pc.Ledger = field(default_factory=pc.Ledger)
    head_ledger: pc.Ledger = field(default_factory=pc.Ledger)

    @property
    def open_pr(self) -> ListedPullRequest | None:
        return next((pr for pr in self.listed if pr.branch == BRANCH and pr.state == "OPEN"), None)

    @property
    def stale(self) -> bool:
        """The branch exists and every pull request it ever had is closed or merged."""
        mine = [pr for pr in self.listed if pr.branch == BRANCH]
        return bool(mine) and not any(pr.state == "OPEN" for pr in mine)


@dataclass(frozen=True)
class Outcome:
    reflected: int
    left: int
    url: str = ""


def _read(ports: Ports, ref: str, relative: str) -> str:
    return ports.workspace.show(ref, relative) or ""


def gather(cfg: Configuration, ports: Ports) -> Known:
    """Records at `HEAD`, on the candidate branch and in the blocks of every candidate PR,
    whatever its state. A record that cannot be read raises `ValueError`: the verb does not
    guess what was already paid for."""
    listed = ports.tracker.pull_requests()
    candidates = [
        (pr, pc.Candidate(pr.state, pr.merge_commit, record))
        for pr in listed
        if (record := pc.parse_block(pr.body)) is not None
    ]
    on_branch = ports.workspace.branch_head(BRANCH) is not None

    def at(ref: str) -> tuple[pc.Ledger, list[pb.Rejected]]:
        return (
            pc.parse_ledger(_read(ports, ref, pc.LEDGER_FILE)),
            pc.parse_rejected(_read(ports, ref, pc.REJECTED_FILE)),
        )

    head_ledger, head_rejected = at("HEAD")
    branch_ledger, branch_rejected = at(BRANCH) if on_branch else (pc.Ledger(), [])

    def playbook_at_merge(commit: str, slug: str) -> pb.Playbook:
        return pb.parse(_read(ports, commit, pc.playbook_file(slug)), slug)

    only = [c for _, c in candidates]
    rejected = pc.merge_rejected(
        head_rejected, branch_rejected, pc.rejected_from(only, playbook_at_merge)
    )
    return Known(
        ledger=pc.merge_ledgers([head_ledger, branch_ledger], only),
        rejected=rejected,
        candidates=candidates,
        listed=listed,
        branch_ledger=branch_ledger,
        head_ledger=head_ledger,
    )


def closed_documents(ports: Ports) -> list[dict[str, Any]]:
    """Every task document with a recorded closure, as the state-shaped dicts `load` gives."""
    docs = (ports.documents.load(task_id) for task_id in ports.documents.ids())
    return [d for d in docs if d and d.get("closed_at")]


def pending(
    cfg: Configuration, ports: Ports, *, since: str | None = None, task_id: str | None = None
) -> list[Mapping[str, Any]]:
    """The closed tasks `reflect` would reflect on, oldest first. Cheap first: with no
    closed document that a person kept, nothing is asked of `gh` or git at all."""
    closed = [d for d in closed_documents(ports) if d.get("delivery_outcome") in pc.KEPT]
    if task_id is not None:
        closed = [d for d in closed if d.get("id") == task_id]
    if not closed:
        return []
    known = gather(cfg, ports)
    return pc.eligible(closed, set(known.ledger.tasks), known.ledger.started_at, since)


def _delta_dict(delta: pb.Delta) -> dict[str, Any]:
    return {
        "op": delta.op,
        "slug": delta.slug,
        "category": delta.category,
        "lesson": delta.lesson,
        "item_id": delta.item_id,
        "tasks": list(delta.tasks),
    }


def _delta_of(raw: Mapping[str, Any]) -> pb.Delta:
    return pb.Delta(
        op=raw["op"],
        slug=raw["slug"],
        category=raw.get("category", ""),
        lesson=raw.get("lesson", ""),
        item_id=raw.get("item_id", ""),
        tasks=tuple(raw.get("tasks") or ()),
    )


def _write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


# The window comparison lives in `core.measurement`, which this layer may not import
# (ADR-0010): the root hands it in. It takes the closed documents (frontmatter-shaped),
# the current playbooks, the deltas applied since the last summary and the items gained,
# and answers the summary text with the retirements it proposes.
Summariser = Callable[
    [Sequence[Mapping[str, Any]], Mapping[str, pb.Playbook], Sequence[pb.Delta], int],
    tuple[str, list[pb.Delta]],
]


def _summary(
    ledger: pc.Ledger,
    playbooks: dict[str, pb.Playbook],
    documents: Sequence[Mapping[str, Any]],
    summarise: Summariser,
    now: str,
) -> str:
    """The summary text, with every retirement it proposes already applied to `playbooks`
    (the proposals are deltas in the same PR, so a person reads numbers and changes in one
    place)."""
    # The window reads the frontmatter's own key for the returns.
    docs = [{**d, "return_history": d.get("change_history") or []} for d in documents]
    recent = list(ledger.tasks.values())[ledger.summarised :]
    applied = [_delta_of(raw) for entry in recent for raw in entry.applied]
    gained = sum(1 for d in applied if d.op == "add")
    text, retirements = summarise(docs, playbooks, applied, gained)
    for delta in retirements:
        outcome = pb.apply(playbooks[delta.slug], [delta], {}, [], section_hash="empty", now=now)
        playbooks[delta.slug] = outcome.playbook
    return text


async def reflect(
    cfg: Configuration,
    ports: Ports,
    *,
    now: str,
    summarise: Summariser,
    since: str | None = None,
    task_id: str | None = None,
) -> Outcome:
    """One invocation: reflect on at most `cfg.reflect_max_tasks_per_run` eligible tasks
    and deliver what they taught on the candidate branch, in one open pull request."""
    known = gather(cfg, ports)
    closed = closed_documents(ports)
    kept = [d for d in closed if d.get("delivery_outcome") in pc.KEPT]
    if task_id is not None:
        kept = [d for d in kept if d.get("id") == task_id]
    first = not known.ledger.started_at
    started = known.ledger.started_at or now
    todo = pc.eligible(kept, set(known.ledger.tasks), started, since)
    batch, left = (
        todo[: cfg.reflect_max_tasks_per_run],
        max(len(todo) - cfg.reflect_max_tasks_per_run, 0),
    )
    if not first and not batch:
        return Outcome(0, 0)

    replace = known.stale
    if replace:
        # The previous pull request is over: its branch is kept aside, a fresh one starts
        # from the default branch with everything the old blocks remembered.
        head = ports.workspace.branch_head(BRANCH) or "old"
        ports.workspace.cleanup(TASK_ID)
        ports.workspace.rename_branch(BRANCH, f"archive/playbook-{head}")
    checkout = ports.workspace.task_checkout(TASK_ID)
    reuse = not replace
    base = pc.merge_ledgers(
        [known.branch_ledger if reuse else pc.Ledger(), known.ledger],
        [c for _, c in known.candidates],
    )
    playbooks = {
        slug: pb.parse(_read(ports, BRANCH, pc.playbook_file(slug)), slug)
        for slug in sorted(pb.PLAYBOOK_ROLES)
    }
    before = dict(playbooks)
    tasks = dict(base.tasks)
    for document in batch:
        result = await reflect_task(
            cfg,
            ports,
            {**document, "status": "closed"},
            playbooks,
            known.rejected,
            now=now,
        )
        playbooks.update(result.playbooks)
        tasks[result.task_id] = pc.Entry(
            cost_usd=result.cost_usd,
            failed=result.error if result.failed else "",
            applied=tuple(_delta_dict(d) for d in result.applied),
        )
    ledger = pc.Ledger(started, tasks, base.summaries, base.summarised)

    summary = ""
    if pc.summary_due(ledger.since_summary):
        number = ledger.summaries + 1
        summary = _summary(ledger, playbooks, closed, summarise, now)
        _write(checkout.path, pc.summary_file(number), summary)
        ledger = pc.Ledger(started, tasks, number, len(tasks))

    for slug, current in playbooks.items():
        if current != before[slug]:
            _write(checkout.path, pc.playbook_file(slug), pb.render_file(current))
    _write(checkout.path, pc.LEDGER_FILE, pc.render_ledger(ledger))
    _write(checkout.path, pc.REJECTED_FILE, pc.render_rejected(known.rejected))
    sha = ports.workspace.commit_all(checkout.path, _message(len(batch), first))

    open_pr = known.open_pr
    url = open_pr.url if open_pr else ""
    if sha is not None or open_pr is None:
        ports.workspace.push(BRANCH, replace=replace)
    record = _record(ledger, known)
    if open_pr is None:
        url = ports.tracker.open_pull_request(TASK_ID, BRANCH, TITLE, _body(INTRO, summary, record))
    elif sha is not None:
        text = open_pr.body.partition(pc.MARKER)[0].rstrip() or INTRO
        ports.tracker.update_pull_request_body(open_pr.number, _body(text, summary, record))
    ports.workspace.cleanup(TASK_ID)
    return Outcome(len(batch), left, url)


def _message(count: int, first: bool) -> str:
    if first and not count:
        return "Start the playbook candidates: record when reflection began"
    return f"Reflect on {count} closed task{'s' if count != 1 else ''}: candidate playbook deltas"


def _body(text: str, summary: str, record: pc.Record) -> str:
    parts = [text.rstrip()]
    if summary:
        parts.append("## Summary\n\n" + summary.rstrip())
    parts.append(pc.block(record).rstrip())
    return "\n\n".join(parts) + "\n"


def _record(ledger: pc.Ledger, known: Known) -> pc.Record:
    """What this PR carries: the tasks no other place already had, and the adds they made."""
    open_number = known.open_pr.number if known.open_pr else None
    elsewhere = {
        task
        for pr, candidate in known.candidates
        if pr.number != open_number
        for task in candidate.record.tasks
    }
    mine = {
        task: entry
        for task, entry in ledger.tasks.items()
        if task not in elsewhere and task not in known.head_ledger.tasks
    }
    adds = tuple(
        pc.Add(raw["slug"], raw.get("category", ""), pb.normalise(raw.get("lesson", "")))
        for entry in mine.values()
        for raw in entry.applied
        if raw.get("op") == "add"
    )
    return pc.Record(
        {task: entry.cost_usd for task, entry in mine.items()},
        adds,
        ledger.started_at,
        ledger.summaries,
        ledger.summarised,
    )
