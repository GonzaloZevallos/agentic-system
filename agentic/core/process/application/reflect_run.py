"""The `reflect` verb's delivery (ADR-0043): which closed tasks to reflect on, the candidate
branch `agentic/playbook`, its files and its one open pull request.

`reflect.reflect_task` (the reflector's use case) decides what a task teaches; this module
only gathers what is already known, calls it and delivers the result for a person to
merge. Nothing here writes to the default branch, a closed task document, a checkpoint or
an issue: every write goes on the candidate branch, in a worktree. A failure of one task's
reflection is recorded in `reflected.yaml` with its reason and never raised.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import playbook as pb
from ..domain import playbook_candidates as pc
from ..domain import rules
from ..domain import system_lessons as sl
from ..domain.reaping import ReapedProcess
from .closed import closed_documents as closed_documents  # re-exported: plan.py reads it here
from .ports import GitError, Inbox, ListedPullRequest, Ports, Tracker
from .reflect import Reflected, backings, reflect_task
from .release import release_task, summary

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
class SystemRepo:
    """The system's own repo as two reads and one write the verb needs: the `tracker` that
    opens the issue and the `inbox` that lists the open ones. Built by the composition
    root over the stage0 checkout; the product's ports are never used for it (ADR-0002)."""

    tracker: Tracker
    inbox: Inbox


@dataclass(frozen=True)
class Outcome:
    reflected: int
    left: int
    url: str = ""


def _read(ports: Ports, ref: str, relative: str) -> str:
    return ports.workspace.show(ref, relative) or ""


def _at_merge(ports: Ports) -> Callable[[str, str], pb.Playbook]:
    """`slug`'s playbook as the file stood at `commit`."""

    def playbook_at_merge(commit: str, slug: str) -> pb.Playbook:
        return pb.parse(_read(ports, commit, pc.playbook_file(slug)), slug)

    return playbook_at_merge


def gather(cfg: Configuration, ports: Ports) -> Known:
    """Records at `HEAD`, on the candidate branch and in the blocks of every candidate PR,
    whatever its state. A record that cannot be read raises `ValueError`: the verb does not
    guess what was already paid for."""
    listed = ports.tracker.list_pull_requests()
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

    only = [c for _, c in candidates]
    rejected = pc.merge_rejected(
        head_rejected, branch_rejected, pc.rejected_from(only, _at_merge(ports))
    )
    return Known(
        ledger=pc.merge_ledgers([head_ledger, branch_ledger], only),
        rejected=rejected,
        candidates=candidates,
        listed=listed,
        branch_ledger=branch_ledger,
        head_ledger=head_ledger,
    )


Door = tuple[Mapping[str, Any], pc.Gate]
_ISSUE = re.compile(r"^GH-(\d+)$")


def _documents(ports: Ports, task_id: str | None) -> list[dict[str, Any]]:
    docs = (ports.documents.load(i) for i in ports.documents.ids())
    return [d for d in docs if d and (task_id is None or d.get("id") == task_id)]


def _closed_escalations(ports: Ports, documents: Sequence[Mapping[str, Any]]) -> set[str]:
    """The escalated documents whose issue is closed: one `issue_state` ask per escalated
    document. A doubt (gh fails, an id that is no issue) reads as still open."""
    closed: set[str] = set()
    for document in documents:
        found = _ISSUE.match(str(document.get("id") or ""))
        if document.get("status") != "waiting_on_human" or not found:
            continue
        try:
            if ports.tracker.issue_state(int(found[1])).state == "CLOSED":
                closed.add(str(document["id"]))
        except (ValueError, OSError, GitError, KeyError, TypeError):
            continue
    return closed


def _todo(
    cfg: Configuration,
    ports: Ports,
    *,
    since: str | None,
    task_id: str | None,
    known: Known | None = None,
) -> tuple[list[Door], Known | None, str]:
    """The documents to reflect through each door, review first, each oldest first, and the
    threshold they were counted from. Cheap first: with no document that landed and none
    that carries a decision, nothing is asked of `gh` or git at all. With neither a ledger
    nor `since`, the threshold is `first_pass_floor` over every document of both doors: the
    `reflect_max_tasks_per_run` most recent ones, never `now`."""
    pool = _documents(ports, None)
    landed = [d for d in pool if pc.reflectable(d)]
    asked = [d for d in pool if pc.decision_count(d)]
    if not landed and not asked:
        return [], known, ""
    known = known or gather(cfg, ports)
    ledger = known.ledger
    first_pass = not ledger.started_at and since is None
    mine = [d for d in asked if task_id is None or d.get("id") == task_id]
    escalations = _closed_escalations(ports, asked if first_pass else mine)
    floor = ""
    if first_pass:
        clocks = [
            *(pc.clock(d, "review") for d in landed),
            *(pc.clock(d, "decision", escalations) for d in asked),
        ]
        floor = pc.first_pass_floor(clocks, cfg.reflect_max_tasks_per_run)
    threshold = since or ledger.started_at or floor
    review = pc.eligible(
        [d for d in landed if task_id is None or d.get("id") == task_id],
        ledger.done_keys,
        ledger.started_at,
        threshold,
        gate="review",
    )
    decision = pc.eligible(
        mine,
        ledger.done_keys,
        ledger.started_at,
        threshold,
        gate="decision",
        closed_escalations=escalations,
        decisions_read=ledger.decisions_read,
    )
    doors: list[Door] = [*((d, "review") for d in review), *((d, "decision") for d in decision)]
    return doors, known, threshold


def pending(
    cfg: Configuration, ports: Ports, *, since: str | None = None, task_id: str | None = None
) -> list[Mapping[str, Any]]:
    """The documents `reflect` would reflect on, oldest first by door."""
    doors, _, _ = _todo(cfg, ports, since=since, task_id=task_id)
    return [document for document, _ in doors]


def _delta_dict(delta: pb.Delta, *, filed: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "op": delta.op,
        "slug": delta.slug,
        "category": delta.category,
        "lesson": delta.lesson,
        "item_id": delta.item_id,
        "tasks": list(delta.tasks),
    }
    # The fields ADR-0056 adds are written only when they say something: a record of a
    # delta that rates nothing stays what it was before, and reads the same.
    if delta.confidence != "high":
        out["confidence"] = delta.confidence
    if delta.scope != "product":
        out["scope"] = delta.scope
    if delta.decision:
        out["decision"] = delta.decision
    if not filed:
        out["filed"] = False  # a lesson for the system still to be opened as an issue
    return out


def _delta_of(raw: Mapping[str, Any]) -> pb.Delta:
    return pb.Delta(
        op=raw["op"],
        slug=raw["slug"],
        category=raw.get("category", ""),
        lesson=raw.get("lesson", ""),
        item_id=raw.get("item_id", ""),
        tasks=tuple(raw.get("tasks") or ()),
        confidence=raw.get("confidence") or "high",
        scope=raw.get("scope", "product"),
        decision=int(raw.get("decision") or 0),
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
    recent = pc.entries_since(ledger, ledger.summarised)
    applied = [_delta_of(raw) for entry in recent for raw in entry.applied if not _is_system(raw)]
    gained = sum(1 for d in applied if d.op == "add")
    text, retirements = summarise(docs, playbooks, applied, gained)
    for delta in retirements:
        outcome = pb.apply(playbooks[delta.slug], [delta], {}, [], section_hash="empty", now=now)
        playbooks[delta.slug] = outcome.playbook
    return text


def _is_system(raw: Mapping[str, Any]) -> bool:
    return raw.get("scope") == "system"


def _entry(result: Reflected, prior: pc.Entry | None, decisions: int) -> pc.Entry:
    """The ledger entry a reflection leaves: what it cost over every attempt, the deltas it
    applied (after those of an earlier read of the same key) and the decisions it read. A
    failure is a retry until `REFLECT_ATTEMPTS` attempts are used, then it is `failed`."""
    waiting = prior is not None and prior.waits_retry
    cost = result.cost_usd + (prior.cost_usd if prior else 0.0)
    earlier = prior.applied if prior and not waiting else ()
    if not result.failed:
        applied = (
            *earlier,
            *(_delta_dict(d) for d in result.applied),
            *(_delta_dict(d, filed=False) for d in result.system),
        )
        return pc.Entry(cost_usd=cost, applied=applied, decisions=decisions)
    attempts = (prior.attempts if prior and waiting else 0) + 1
    failed = result.error if attempts >= pc.REFLECT_ATTEMPTS else ""
    # While a retry is due, the decisions read stay those of the last good read, so the
    # retry reads the ones that failed; only a failure for good counts them all as seen.
    read = decisions if failed else (prior.decisions if prior else 0)
    return pc.Entry(cost, failed, earlier, read, attempts)


def _ended(document: Mapping[str, Any]) -> str:
    runs = document.get("runs")
    ended = [str(r["ended_at"]) for r in runs or [] if isinstance(r, Mapping) and r.get("ended_at")]
    return max(ended, default="")


def _stamp_prompt_dates(
    ports: Ports, known: Known, playbooks: Mapping[str, pb.Playbook]
) -> dict[str, pb.Playbook]:
    """Give every item that has been in a prompt its `in_prompt_since`: the merge date of
    the first merged candidate PR whose playbook at the merge commit holds the item's id.
    Done before a run changes anything, so a bump of the run cannot hide the merge."""
    merged = [c for _, c in known.candidates if c.state == "MERGED"]
    at_merge = _at_merge(ports)

    def date(commit: str) -> str:
        return ports.workspace.commit_date(commit) or ""

    stamped: dict[str, pb.Playbook] = {}
    for slug, playbook in playbooks.items():
        items = tuple(
            item
            if item.in_prompt_since
            else replace(
                item,
                in_prompt_since=pc.in_prompt_since(
                    item.id, merged, at_merge, date, not_before=item.last_applied
                )
                or "",
            )
            for item in playbook.items
        )
        stamped[slug] = pb.Playbook(slug, items)
    return stamped


def _archive_stale(
    documents: Sequence[Mapping[str, Any]],
    tasks: Mapping[str, pc.Entry],
    playbooks: dict[str, pb.Playbook],
    archived: tuple[pb.Archived, ...],
    now: str,
) -> tuple[pb.Archived, ...]:
    """Move every item that stopped applying out of its playbook, in place, into the
    archive. `playbook.archive_due` decides; this counts the role's tasks since the item
    reached a prompt and whether one backed or cited it. The count is the ledger's
    (`reflected_tasks`) over the role's tasks: one not reflected yet, or waiting for a
    retry, does not age an item."""
    for slug, playbook in list(playbooks.items()):
        aging: dict[str, tuple[int, bool]] = {}
        for item in playbook.items:
            if not item.in_prompt_since:
                continue
            ran = [
                d
                for d in documents
                if slug in (d.get("prompts") or {}) and _ended(d) > item.in_prompt_since
            ]
            backed = item.last_applied > item.in_prompt_since or any(
                b.slug == slug and b.category == item.category for d in ran for b in backings(d)
            )
            ids = {str(d.get("id") or "") for d in ran}
            mine = {k: e for k, e in tasks.items() if pc.split_key(k)[0] in ids}
            aging[item.id] = (pc.reflected_tasks(pc.Ledger(tasks=mine)), backed)
        playbooks[slug], archived = pb.archive_stale(playbook, archived, aging, now=now)
    return archived


def _unfiled(tasks: Mapping[str, pc.Entry]) -> bool:
    return any(raw.get("filed") is False for entry in tasks.values() for raw in entry.applied)


def _file_system(system: SystemRepo | None, tasks: dict[str, pc.Entry]) -> None:
    """Open the one issue the lessons for the system still unfiled ask for, in the system's
    repo, with `needs-triage` and never `agentic`. A lesson already in the ledger or in an
    open issue's marker is not proposed again; those are simply marked filed. A failure
    (reading the open issues or opening the issue) is a line on stderr and leaves the
    lessons unfiled for the next run: it never fails the product's reflection."""
    waiting = [
        (key, raw)
        for key, entry in tasks.items()
        for raw in entry.applied
        if raw.get("filed") is False
    ]
    if system is None or not waiting:
        return
    known = {
        sl.lesson_of(raw)
        for entry in tasks.values()
        for raw in entry.applied
        if _is_system(raw) and raw.get("filed") is not False
    }
    try:
        known |= sl.proposed_in(body for _, body in system.inbox.open_issues())
        fresh = sl.fresh([raw for _, raw in waiting], known)
        if fresh:
            title, body = sl.render_issue(fresh)
            system.tracker.open_issue(title, body, label=sl.LABEL)
    except (ValueError, OSError, GitError) as exc:
        print(f"agentic: reflect: could not open the system issue: {exc}", file=sys.stderr)
        return
    for key in {key for key, _ in waiting}:
        entry = tasks[key]
        done = tuple(
            {k: v for k, v in raw.items() if k != "filed"} if raw.get("filed") is False else raw
            for raw in entry.applied
        )
        tasks[key] = replace(entry, applied=done)


async def reflect(
    cfg: Configuration,
    ports: Ports,
    *,
    now: str,
    summarise: Summariser,
    since: str | None = None,
    task_id: str | None = None,
    system: SystemRepo | None = None,
) -> Outcome:
    """One invocation: reflect on at most `cfg.reflect_max_tasks_per_run` eligible
    documents through their doors and deliver what they taught on the candidate branch, in
    one open pull request."""
    known = gather(cfg, ports)
    first = not known.ledger.started_at
    todo, _, threshold = _todo(cfg, ports, since=since, task_id=task_id, known=known)
    # A first pass never starts the clock at `now` while a document says where it began:
    # `since` when given, else `first_pass_floor`, which is what `_todo` counted from.
    started = known.ledger.started_at or since or threshold or now
    batch, left = (
        todo[: cfg.reflect_max_tasks_per_run],
        max(len(todo) - cfg.reflect_max_tasks_per_run, 0),
    )
    if not first and not batch and not _unfiled(known.ledger.tasks):
        return Outcome(0, 0)

    replace_branch = known.stale
    if replace_branch:
        # The previous pull request is over: its branch is kept aside, a fresh one starts
        # from the default branch with everything the old blocks remembered.
        head = ports.workspace.branch_head(BRANCH) or "old"
        _say_reaped(release_task(ports, TASK_ID))
        ports.workspace.rename_branch(BRANCH, f"archive/playbook-{head}")
    checkout = ports.workspace.task_checkout(TASK_ID)
    reuse = not replace_branch
    base = pc.merge_ledgers(
        [known.branch_ledger if reuse else pc.Ledger(), known.ledger],
        [c for _, c in known.candidates],
    )
    read = {
        slug: pb.parse(_read(ports, BRANCH, pc.playbook_file(slug)), slug)
        for slug in sorted(pb.PLAYBOOK_ROLES)
    }
    playbooks = _stamp_prompt_dates(ports, known, read)
    candidates = tuple(pc.parse_candidates(_read(ports, BRANCH, pc.CANDIDATES_FILE)))
    archived = tuple(pc.parse_archived(_read(ports, BRANCH, pc.ARCHIVED_FILE)))
    before = (dict(read), candidates, archived)
    tasks = dict(base.tasks)
    for document, gate in batch:
        key = pc.ledger_key(str(document["id"]), gate)
        before_entry = tasks.get(key)
        try:
            result = await reflect_task(
                cfg,
                ports,
                {**document, "status": "closed"},
                playbooks,
                known.rejected,
                now=now,
                candidates=candidates,
                archived=archived,
                gate=gate,
                decisions=before_entry.decisions if before_entry else 0,
            )
        except (OSError, GitError) as exc:
            # The environment failed, not the model: no entry, no attempt. The documents
            # that finished are written below; this one and the rest wait for the next run.
            # Anything else is a bug and propagates, with the ledger unwritten.
            print(
                f"agentic: reflect: {document['id']} stopped by {type(exc).__name__}; "
                "its cost was not recorded",
                file=sys.stderr,
            )
            break
        playbooks.update(result.playbooks)
        candidates = candidates if result.candidates is None else result.candidates
        archived = archived if result.archived is None else result.archived
        tasks[key] = _entry(result, before_entry, pc.decision_count(document))
    _file_system(system, tasks)
    documents = _documents(ports, None)
    archived = _archive_stale(documents, tasks, playbooks, archived, now)
    ledger = pc.Ledger(started, tasks, base.summaries, base.summarised)

    summary = ""
    if pc.summary_due(ledger.since_summary):
        number = ledger.summaries + 1
        summary = _summary(
            ledger, playbooks, [d for d in documents if d.get("closed_at")], summarise, now
        )
        _write(checkout.path, pc.summary_file(number), summary)
        ledger = pc.Ledger(started, tasks, number, pc.reflected_tasks(ledger))

    for slug, current in playbooks.items():
        if current != before[0][slug]:
            _write(checkout.path, pc.playbook_file(slug), pb.render_file(current))
    if candidates != before[1]:
        _write(checkout.path, pc.CANDIDATES_FILE, pc.render_candidates(candidates))
    if archived != before[2]:
        _write(checkout.path, pc.ARCHIVED_FILE, pc.render_archived(archived))
    _write(checkout.path, pc.LEDGER_FILE, pc.render_ledger(ledger))
    _write(checkout.path, pc.REJECTED_FILE, pc.render_rejected(known.rejected))
    sha = ports.workspace.commit_all(checkout.path, _message(len(batch), first))

    open_pr = known.open_pr
    url = open_pr.url if open_pr else ""
    if sha is not None or open_pr is None:
        # The playbook branch is the system's own (ADR-0043): its replace is not a
        # `push_plan` decision, it leases on whatever the remote holds now.
        lease = ports.workspace.remote_tip(checkout.path, BRANCH) if replace_branch else None
        ports.workspace.push(BRANCH, lease=lease)
    record = _record(ledger, known)
    if open_pr is None:
        url = ports.tracker.open_pull_request(TASK_ID, BRANCH, TITLE, _body(INTRO, summary, record))
    elif sha is not None:
        text = open_pr.body.partition(pc.MARKER)[0].rstrip() or INTRO
        ports.tracker.update_pull_request_body(open_pr.number, _body(text, summary, record))
    _say_reaped(release_task(ports, TASK_ID))
    return Outcome(len(batch), left, url)


def _say_reaped(reaped: tuple[ReapedProcess, ...]) -> None:
    """Stderr only (GH-282): a reflection has no task document to record a reap in."""
    if reaped:
        print(f"agentic: reflect: {summary(reaped)}", file=sys.stderr)


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
        pc.normal_key(task)
        for pr, candidate in known.candidates
        if pr.number != open_number
        for task in candidate.record.tasks
    }
    mine = {
        task: entry
        for task, entry in ledger.tasks.items()
        if task not in elsewhere and task not in known.head_ledger.tasks and not entry.waits_retry
    }
    adds = tuple(
        pc.Add(raw["slug"], raw.get("category", ""), pb.normalise(raw.get("lesson", "")))
        for entry in mine.values()
        for raw in entry.applied
        if raw.get("op") == "add" and not _is_system(raw)
    )
    return pc.Record(
        {task: entry.cost_usd for task, entry in mine.items()},
        adds,
        ledger.started_at,
        ledger.summaries,
        ledger.summarised,
    )
