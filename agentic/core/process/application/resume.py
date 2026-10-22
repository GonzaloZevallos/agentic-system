"""Resuming a paused task (README.md, 3 and 14; ADR-0003, addendum).

A task paused by an escalation waits in the checkpointer. The human answers in
comments on the issue, if there is anything to decide, and runs `agentic resume`: the
answer enters as the `Human` section, the pair that hit its limit starts over, and the
task goes on from where it stopped. Before spending, the human sees what will resume,
and nothing resumes under a cap that is already spent.

The document is the rescue path: a task paused before the checkpointer existed, or
whose checkpoint was lost, is seeded from it. The document is the raw record, but git
may have moved since it was written, so the seed is checked against git first, and a
disagreement refuses rather than resumes over the wrong code.

The graph calls stay in the driving adapter; this module decides what they get."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast, get_type_hints

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.task import TaskState, apply
from .archive import STARTED_OVER
from .deliver import READY
from .discard import DISCARDED
from .escalate import ESCALATED, NEEDS_HUMAN
from .poll import (
    AUTO_MERGE_REFUSED,
    AUTO_MERGED,
    AUTO_RESUMED,
    LANDING,
    RESTARTED_BY_LANDING,
    RETAKEN,
)
from .ports import Ports, Workspace
from .run_role import task_cap

# How the system's comment on a resume starts.
RESUMED = "Resumed: "
# GH-137: `AUTO_RESUMED` joins the set a human's answer is read past (`human_decision`),
# so the poller's own comment never reads back as the human's decision on the next
# `agentic resume` -- defined in `poll.py` itself (the module that posts it), imported
# here in the same diff, per the plan's own invariant.
SYSTEM_COMMENTS = (
    ESCALATED,
    RESUMED,
    READY,
    DISCARDED,
    STARTED_OVER,
    AUTO_RESUMED,
    RETAKEN,
    AUTO_MERGE_REFUSED,
    AUTO_MERGED,
    LANDING,
    RESTARTED_BY_LANDING,
)
# What the document's frontmatter carries that is not state: the version record.
STATE_FIELDS = frozenset(get_type_hints(TaskState)) - {"subtask_id"}

Source = Literal["checkpoint", "document"]
# The summary's label for the decision, whose later lines align under its first.
DECISION = "decision:  "


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
    # The human's decision: what the issue says after the system's last comment, if any.
    decision: str | None
    # Where the task goes next: a node, or the nodes a crashed run left pending.
    target: str
    cap_usd: float
    # Glossary: Corrida interrumpida. True when `pending` drove this resume: nodes
    # with no interrupt waiting for them, which is a run that crashed rather than
    # paused. Never derived from `RunLock`'s presence or absence (its own docstring):
    # the checkpoint is the only source of this fact.
    interrupted: bool = False
    # The human's `--estimate`, which the graph's `pause` node puts in force with the decision.
    estimate: float | None = None

    def answer(self) -> dict[str, float | str | None]:
        """What the graph resumes with: the decision, and the `--estimate` when given."""
        if self.estimate is None:
            return {"decision": self.decision}
        return {"decision": self.decision, "estimate": self.estimate}


def state_from(raw: dict) -> TaskState:
    """The state a raw document record says: only the keys the state has."""
    return cast(TaskState, {k: v for k, v in raw.items() if k in STATE_FIELDS and v is not None})


def from_document(task_id: str, ports: Ports) -> TaskState:
    """The state the document records, checked against git."""
    raw = ports.documents.load(task_id)
    if raw is None:
        raise ValueError(f"{task_id} has no task document to resume from")
    state = state_from(raw)
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
        if sub.get("base") and recorded and not rules.same_commit(sub["base"], recorded):
            problems.append(f"{sid}: base {sub['base']} in the document, {recorded} in git")
        reviewed = sub.get("reviewed_head")
        if sub.get("status") == "approved" and reviewed and not rules.same_commit(reviewed, head):
            problems.append(f"{sid}: approved at {reviewed}, but `{branch}` is at {head}")
    return problems


def human_decision(comments: Sequence[str]) -> str | None:
    """The human's decision: every comment after the system's last one, joined. The
    human may answer in several comments; all of them, and only them, enter. With no
    system comment there is nothing the human answered: what the issue says before
    the system ever spoke is the task, not a decision."""
    after: list[str] | None = None
    for body in comments:
        text = "\n".join(body.strip().splitlines())
        if text.startswith(SYSTEM_COMMENTS):
            after = []
        elif text and after is not None:
            after.append(text)
    return "\n\n".join(after or []) or None


def prepare(
    task_id: str,
    paused: TaskState,
    source: Source,
    cfg: Configuration,
    ports: Ports,
    pending: tuple[str, ...] = (),
    estimate: float | None = None,
) -> Resumption:
    """Decide the resume. `pending` are the nodes a run that crashed left to run: it did
    not pause, so there is no answer to take and nothing to reset. Raises
    `WaitingOnHuman` when the questions, or a pair at its limit, have no answer yet, and
    `ValueError` when the cap in force is already spent.

    `estimate` is the human's `--estimate`: the cost estimate that goes in force when the
    task paused at the cap of its estimate. It is itself the human's decision (the poller
    never passes one); on any other pause it is a usage error. `--task-budget` stays the
    flat cap's, read below, whatever the estimate in force."""
    if estimate is not None and paused.get("escalation_class") != "estimate":
        raise ValueError(
            "--estimate applies only to a task paused at the cap of its estimate "
            f"(escalation class `estimate`); {task_id} is paused for "
            f"`{paused.get('escalation_class') or 'another reason'}`"
        )
    decision = None
    if pending:
        resumed, target = paused, ", ".join(pending)
    else:
        decision = human_decision(ports.tracker.issue_comments(task_id))
        if decision is None and paused.get("questions_for_human"):
            raise WaitingOnHuman(
                f"{task_id} waits on {len(paused['questions_for_human'])} question(s): "
                "answer them in comments on the issue, then resume"
            )
        if decision is None and estimate is None and paused.get("escalation_class") == "estimate":
            # Never retaken on its own (ADR-0053): the estimate in force changes only on a
            # resumption that carries a human decision.
            raise WaitingOnHuman(
                f"{task_id}: the estimate does not fit; decide in comments on the issue "
                "whether to go on, with which estimate, or to stop, then resume"
            )
        if decision is None and (pair := rules.returns_pair(paused)):
            # The counter starts over only on an answer (README.md, 3): with nothing new,
            # the pair would return for the same reason again.
            raise WaitingOnHuman(
                f"{task_id}: {pair} hit its return limit; decide in comments on the issue "
                "what the next attempt must do, then resume"
            )
        resumed = apply(paused, rules.resumed(paused, decision, estimate))
        if paused.get("escalation_class") == "estimate":
            _refuse_spent_estimate(task_id, resumed, paused.get("cost_usd", 0.0))
        next_node = rules.resume_target(resumed)
        if next_node is None:
            raise ValueError(
                f"{task_id} has nothing left to run ({paused.get('status', 'open')}): "
                "start it over with `agentic run --fresh` if that is what you want"
            )
        target = next_node
    cap = task_cap(cfg)
    spent = paused.get("cost_usd", 0.0)
    if cap <= spent:
        raise ValueError(
            f"the task cap in force is {cap:.2f} USD and {spent:.2f} are already spent: "
            f"pass --task-budget above {spent:.2f}"
        )
    return Resumption(
        task_id,
        source,
        paused,
        resumed,
        decision,
        target,
        cap,
        interrupted=bool(pending),
        estimate=estimate,
    )


def _refuse_spent_estimate(task_id: str, resumed: TaskState, spent: float) -> None:
    """A resume that puts an estimate in force must leave room under its cap, or the first
    attempt would stop again at once."""
    if (in_force := rules.estimate_in_force(resumed)) is not None and rules.estimate_cap_reached(
        spent, resumed
    ):
        raise ValueError(
            f"{task_id}: the estimate in force is {in_force:.2f} USD and {spent:.2f} are "
            f"already spent: its cap is reached; pass --estimate above "
            f"{rules.estimate_to_cover(spent):.2f}"
        )


def summary(r: Resumption, cfg: Configuration, ports: Ports) -> str:
    """What the human confirms before the resume spends anything."""
    state = r.paused
    phases = ", ".join(f"{p} {usd:.2f}" for p, usd in rules.spent_by_phase(state).items())
    if cfg.task_budget_usd is not None and cfg.task_budget_usd >= r.cap_usd:
        how = "--task-budget"
    else:
        how = f"ceiling {cfg.task_max_budget_usd:g}"
    lines = [
        f"task:      {r.task_id} {state.get('title', '')}".rstrip(),
        f"from:      {r.source}",
        f"paused:    {state.get('current_role') or 'before any role'} "
        f"({state.get('status', 'open')})",
        f"reason:    {state.get('escalated') or 'none: the run stopped without escalating'}",
        f"spent:     {state.get('cost_usd', 0.0):.2f} USD ({phases})",
        f"task cap:  {r.cap_usd:.2f} USD ({how})",
        f"{DECISION}{_indented(r.decision) if r.decision else 'none'}",
        f"resumes:   {r.target}{_subtasks(r)}",
    ]
    lines.extend(f"session:   {line}" for line in _sessions(r))
    for path in ports.workspace.worktrees(r.task_id):
        dirty = r.interrupted and not ports.workspace.is_clean(path)
        lines.append(f"worktree:  {path}" + ("  (uncommitted changes)" if dirty else ""))
    return "\n".join(lines)


def announce(r: Resumption, ports: Ports) -> None:
    """Take the task out of the queue and say on the issue that it resumed. A task seeded
    from its document first forgets the worktrees the human removed by hand, so asking
    for those checkouts again recreates them; a dry run never gets here.

    `r.interrupted` (glossary: Corrida interrumpida) adds one clause naming the role the
    previous invocation was interrupted at, so "el issue quedó mintiendo" (the request's
    own words) gets an answer without a new comment prefix: `RESUMED` is the only one
    this ever wrote, `SYSTEM_COMMENTS` stays as it is, and a resume that was not
    interrupted emits the exact same bytes as before (subtask `signals`)."""
    if r.source == "document":
        ports.workspace.prune()
    ports.tracker.unlabel(r.task_id, NEEDS_HUMAN)
    answered = " with your decision" if r.decision else ""
    role = r.paused.get("current_role") or "before any role"
    interrupted = f", interrupted at {role}" if r.interrupted else ""
    ports.tracker.comment(
        r.task_id,
        f"{RESUMED}{r.target}{answered}{interrupted}, under a task cap of {r.cap_usd:.2f} USD.",
    )


def _indented(text: str) -> str:
    # Whole: it is what enters the task document, and the human confirms it.
    pad = " " * len(DECISION)
    return "\n".join(pad + line if line else "" for line in text.splitlines()).lstrip()


def _resuming_subtasks(r: Resumption) -> list[str]:
    """What the next wave runs, for the human to read before spending. The fan-in
    dispatches it, so the target is `integrate` whatever the wave holds."""
    return rules.dispatched_subtasks(r.resumed) if r.target == "integrate" else []


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
