"""Resuming a paused task (README.md, 3 and 14; ADR-0003, addendum).

A task paused by an escalation waits in the checkpointer. The human answers in a
comment on the issue, if there is anything to decide, and runs `agentic resume`: the
answer enters as the `Human` section, the pair that hit its limit starts over, and the
task goes on from where it stopped. Before spending, the human sees what will resume,
and nothing resumes under a cap that is already spent.

The document is the rescue path: a task paused before the checkpointer existed, or
whose checkpoint was lost, is seeded from it. The document is the raw record, but git
may have moved since it was written, so the seed is checked against git first, and a
disagreement refuses rather than resumes over the wrong code.

The graph calls stay in the driving adapter; this module decides what they get."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast, get_type_hints

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.task import TaskState, apply
from .deliver import READY
from .escalate import ESCALATED, NEEDS_HUMAN
from .ports import Ports, Workspace
from .run_role import task_cap

# How the system's comment on a resume starts.
RESUMED = "Resumed: "
SYSTEM_COMMENTS = (ESCALATED, RESUMED, READY)
# What the document's frontmatter carries that is not state: the version record.
STATE_FIELDS = frozenset(get_type_hints(TaskState)) - {"subtask_id"}

Source = Literal["checkpoint", "document"]


class WaitingOnHuman(Exception):
    """The task has questions for the human and no answer to them yet."""


@dataclass(frozen=True)
class Resumption:
    """What a resume will do, decided before spending anything."""

    task_id: str
    source: Source
    # The state as it paused, and as it resumes once the human's answer is in.
    paused: TaskState
    resumed: TaskState
    # The human's answer, if the issue has one after the system's last comment.
    decision: str | None
    # Where the task goes next: a node, or the nodes a crashed run left pending.
    target: str
    cap_usd: float


def from_document(task_id: str, ports: Ports) -> TaskState:
    """The state the document records, checked against git."""
    raw = ports.documents.load(task_id)
    if raw is None:
        raise ValueError(f"{task_id} has no task document to resume from")
    state = cast(TaskState, {k: v for k, v in raw.items() if k in STATE_FIELDS and v is not None})
    # A worktree the human removed by hand is still registered: forget it, so asking
    # for the checkout again recreates it.
    ports.workspace.prune()
    if problems := disagreements(state, ports.workspace):
        listed = "\n".join(f"- {p}" for p in problems)
        raise ValueError(f"the document of {task_id} and git disagree:\n{listed}")
    return state


def disagreements(state: TaskState, workspace: Workspace) -> list[str]:
    """Where git no longer holds what the document says: a branch gone, a base that is
    not the one git recorded, an approved subtask whose branch moved after its review."""
    problems = []
    if (branch := state.get("branch")) and workspace.branch_head(branch) is None:
        problems.append(f"task branch `{branch}` does not exist")
    for sid, sub in state.get("subtasks", {}).items():
        if not (branch := sub.get("branch")):
            continue
        head = workspace.branch_head(branch)
        if head is None:
            problems.append(f"{sid}: branch `{branch}` does not exist")
            continue
        recorded = workspace.base_of(branch)
        if sub.get("base") and recorded and not _same_commit(sub["base"], recorded):
            problems.append(f"{sid}: base {sub['base']} in the document, {recorded} in git")
        reviewed = sub.get("reviewed_head")
        if sub.get("status") == "approved" and reviewed and not _same_commit(reviewed, head):
            problems.append(f"{sid}: approved at {reviewed}, but `{branch}` is at {head}")
    return problems


def _same_commit(a: str, b: str) -> bool:
    # Short hashes grow with the repository: one may be a prefix of the other.
    return a.startswith(b) or b.startswith(a)


def human_reply(comment: str | None) -> str | None:
    """The human's answer: the issue's last comment, unless the system wrote it."""
    text = (comment or "").strip()
    if not text or text.startswith(SYSTEM_COMMENTS):
        return None
    return text


def prepare(
    task_id: str,
    paused: TaskState,
    source: Source,
    cfg: Configuration,
    ports: Ports,
    pending: tuple[str, ...] = (),
) -> Resumption:
    """Decide the resume. `pending` are the nodes a run that crashed left to run: it did
    not pause, so there is no answer to take and nothing to reset. Raises
    `WaitingOnHuman` when the questions are still unanswered, `ValueError` when the cap
    in force is already spent."""
    decision = None
    if pending:
        resumed, target = paused, ", ".join(pending)
    else:
        decision = human_reply(ports.tracker.last_comment(task_id))
        if decision is None and paused.get("questions_for_human"):
            raise WaitingOnHuman(
                f"{task_id} waits on {len(paused['questions_for_human'])} question(s): "
                "answer them in a comment on the issue, then resume"
            )
        resumed = apply(paused, rules.resumed(paused, decision))
        target = rules.resume_target(resumed) or "the end: nothing left to run"
    cap = task_cap(resumed, cfg)
    spent = paused.get("cost_usd", 0.0)
    if cap <= spent:
        raise ValueError(
            f"the task cap in force is {cap:.2f} USD and {spent:.2f} are already spent: "
            f"pass --task-budget above {spent:.2f}"
        )
    return Resumption(task_id, source, paused, resumed, decision, target, cap)


def summary(r: Resumption, cfg: Configuration, ports: Ports) -> str:
    """What the human confirms before the resume spends anything."""
    state = r.paused
    phases = ", ".join(f"{p} {usd:.2f}" for p, usd in rules.spent_by_phase(state).items())
    if cfg.task_budget_usd is not None:
        how = "--task-budget"
    else:
        how = (
            f"base {cfg.task_budget_base_usd:g} + {cfg.task_budget_per_subtask_usd:g} x "
            f"{rules.work_units(r.resumed)} units, ceiling {cfg.task_max_budget_usd:g}"
        )
    lines = [
        f"task:      {r.task_id} {state.get('title', '')}".rstrip(),
        f"from:      {r.source}",
        f"paused:    {state.get('current_role') or 'before any role'} "
        f"({state.get('status', 'open')})",
        f"reason:    {state.get('escalated') or 'none: the run stopped without escalating'}",
        f"spent:     {state.get('cost_usd', 0.0):.2f} USD ({phases})",
        f"task cap:  {r.cap_usd:.2f} USD ({how})",
        f"decision:  {_first_line(r.decision) if r.decision else 'none'}",
        f"resumes:   {r.target}{_subtasks(r)}",
    ]
    lines.extend(f"session:   {line}" for line in _sessions(r))
    lines.extend(f"worktree:  {path}" for path in ports.workspace.worktrees(r.task_id))
    return "\n".join(lines)


def announce(r: Resumption, ports: Ports) -> None:
    """Take the task out of the queue and say on the issue that it resumed."""
    ports.tracker.unlabel(r.task_id, NEEDS_HUMAN)
    answered = " with your decision" if r.decision else ""
    ports.tracker.comment(
        r.task_id, f"{RESUMED}{r.target}{answered}, under a task cap of {r.cap_usd:.2f} USD."
    )


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0]
    return line if len(line) <= 80 else line[:77] + "..."


def _resuming_subtasks(r: Resumption) -> list[str]:
    if r.target == "develop":
        return rules.ready_subtasks(r.resumed)
    if r.target == "review":
        return rules.delivered_subtasks(r.resumed)
    return []


def _subtasks(r: Resumption) -> str:
    subtasks = _resuming_subtasks(r)
    return f" ({', '.join(subtasks)})" if subtasks else ""


def _sessions(r: Resumption) -> list[str]:
    """The sessions behind what resumes, for `claude -r` before spending: each resuming
    subtask's last Developer and review, or the last run otherwise."""
    runs = r.paused.get("runs", [])
    subtasks = _resuming_subtasks(r)
    if not subtasks:
        last = next((run for run in reversed(runs) if run.get("session_id")), None)
        return [f"{last['section']}: {last['session_id']}"] if last else []
    lines = []
    for sid in subtasks:
        for section in (rules.developer_section(sid), rules.review_section(sid)):
            run = next((x for x in reversed(runs) if x.get("section") == section), None)
            if run and run.get("session_id"):
                lines.append(f"{section}: {run['session_id']}")
    return lines
