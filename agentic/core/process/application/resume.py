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
from pathlib import Path
from typing import Literal, cast, get_type_hints

from agentic.shared.configuration import Configuration

from ..domain import document, rules
from ..domain import landing as landing_rules
from ..domain.task import TaskState, TrackerFailure, TrackerFailureRecord, apply
from . import landing_plan
from .archive import STARTED_OVER
from .deliver import READY
from .discard import DISCARDED
from .escalate import ESCALATED, NEEDS_HUMAN
from .plan import PART_BUILT_WHOLE
from .poll import (
    AUTO_MERGE_REFUSED,
    AUTO_MERGED,
    AUTO_RESUMED,
    LANDING,
    RESTARTED_BY_LANDING,
    RETAKEN,
    RETAKEN_AFTER_CRASH,
    RETAKEN_ORPHANED,
    Abandoned,
    StackUnreadable,
    assess_merge,
)
from .ports import GitError, Ports, Workspace
from .run_role import task_cap
from .tracker_only import TRACKER_WORK

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
    RETAKEN_AFTER_CRASH,
    RETAKEN_ORPHANED,
    AUTO_MERGE_REFUSED,
    AUTO_MERGED,
    LANDING,
    RESTARTED_BY_LANDING,
    TRACKER_WORK,
    PART_BUILT_WHOLE,
)
# What the document's frontmatter carries that is not state: the version record.
STATE_FIELDS = frozenset(get_type_hints(TaskState)) - {"subtask_id"}

Source = Literal["checkpoint", "document"]
# The summary's label for the decision, whose later lines align under its first.
DECISION = "decision:  "


class WaitingOnHuman(Exception):
    """The task has questions for the human and no answer to them yet."""


@dataclass(frozen=True)
class Increment:
    """The commits an integration review never saw (GH-392): the task branch `branch` is at
    `head`, a strict descendant of the `approved` head the review approved. `checkout` is the
    task's worktree when there is one, and `dirty` whether the stopped run left files in it."""

    approved: str
    head: str
    branch: str
    checkout: Path | None
    dirty: bool


@dataclass(frozen=True)
class Seed:
    """A state seeded from the document, and the increment it carries, if any."""

    state: TaskState
    increment: Increment | None


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
    # GH-392: the commits after the integration's approved head that this resume takes as an
    # increment to review, when the document was seeded past that head.
    increment: Increment | None = None

    def answer(self) -> dict[str, float | str | None]:
        """What the graph resumes with: the decision, and the `--estimate` when given."""
        if self.estimate is None:
            return {"decision": self.decision}
        return {"decision": self.decision, "estimate": self.estimate}


def state_from(raw: dict) -> TaskState:
    """The state a raw document record says: only the keys the state has."""
    return cast(TaskState, {k: v for k, v in raw.items() if k in STATE_FIELDS and v is not None})


def load_document(task_id: str, ports: Ports) -> TaskState:
    """The state the document records, not yet checked against git."""
    raw = ports.documents.load(task_id)
    if raw is None:
        raise ValueError(f"{task_id} has no task document to resume from")
    return state_from(raw)


def _disagree(task_id: str, problems: Sequence[str]) -> ValueError:
    listed = "\n".join(f"- {p}" for p in problems)
    return ValueError(f"the document of {task_id} and git disagree:\n{listed}")


def from_document(task_id: str, ports: Ports) -> TaskState:
    """The state the document records, checked against git."""
    state = load_document(task_id, ports)
    if problems := disagreements(state, ports.workspace):
        raise _disagree(task_id, problems)
    return state


def issue_flag(task_id: str, ports: Ports) -> str:
    """`--issue N` for the task, or `--issue N  # <task_id>` when its issue is not known: the
    flag every refusal that names a command to run puts in it."""
    number = ports.inbox.issue_of(task_id)
    return f"--issue {number}" if number is not None else f"--issue N  # {task_id}"


def seed_increment(task_id: str, cfg: Configuration, ports: Ports) -> Seed:
    """The state a resume from the document starts from (GH-392). Like `from_document`, but a
    task branch found strictly past the integration's approved head -- a later subtask merge,
    say, after the run stopped -- is not a disagreement: `rules.integration_increment` decides
    it, the unit goes back to review from the approved head, and the commits after it are the
    increment. Reads only; nothing is committed here. Every other disagreement raises exactly
    what `from_document` raises."""
    state = load_document(task_id, ports)
    unit = rules.reviewed_unit_name(state)
    sub = state.get("subtasks", {}).get(unit) if unit else None
    approved = sub.get("reviewed_head") if sub and sub.get("status") == "approved" else None
    branch = sub.get("branch") if sub else None
    head = ports.workspace.branch_head(branch) if branch else None
    if unit is None or sub is None or not approved or not branch or head is None:
        return Seed(_checked(task_id, state, ports), None)
    if rules.same_commit(head, approved):
        return Seed(_checked(task_id, state, ports), None)
    descends, unreadable, unread_commits = False, None, None
    commits: tuple[rules.Commit, ...] = ()
    if branch == rules.task_branch(task_id):
        try:
            descends = ports.workspace.is_ancestor(cfg.product_dir, approved, head)
        except (GitError, ValueError) as failed:
            unreadable = failed
        if descends:
            try:
                commits = ports.workspace.first_parent_commits(approved, head)
            except (GitError, ValueError) as failed:
                unread_commits = failed
    verdict = (
        "refuse"
        if unreadable
        else rules.integration_increment(state, task_id, head, descends, commits)
    )
    if verdict == "reintegrate":
        return _reintegrated(task_id, state, ports)
    if verdict != "increment":
        problems = disagreements(state, ports.workspace)
        today = f"{unit}: approved at {approved}, but `{branch}` is at {head}"
        if today in problems:
            reintegration = _reintegration_line(
                task_id, state, (unit, branch, approved), descends, commits, unread_commits
            )
            problems[problems.index(today)] = _moved_line(
                task_id,
                ports,
                cfg,
                state,
                (unit, branch, approved, head),
                descends,
                unreadable,
                reintegration,
            )
        raise _disagree(task_id, problems)
    checkout, dirty = _task_checkout(task_id, ports)
    seeded = apply(state, rules.increment_patch(state))
    if problems := disagreements(seeded, ports.workspace, exempt=frozenset({unit})):
        raise _disagree(task_id, problems)
    return Seed(seeded, Increment(approved, head, branch, checkout, dirty))


def _reintegrated(task_id: str, state: TaskState, ports: Ports) -> Seed:
    """The seed of a reintegration (GH-408): the integration goes back to `pending`, its
    reviewed head kept, depending on every subtask not approved; nothing is reviewed as an
    increment, so there is no `Increment`. The integration checkout must hold nothing of a human's
    or of the stopped run's: a merge in progress or uncommitted files refuse, and nothing here
    commits them. Every document-against-git check runs again, with no unit exempt."""
    checkout, dirty = _task_checkout(task_id, ports)
    if dirty and checkout is not None:
        raise _disagree(
            task_id,
            [
                f"the integration checkout `{checkout}` has uncommitted changes the stopped run "
                f"left: inspect them with `git -C {checkout} status`, commit or discard them by "
                f"hand, then `agentic resume {issue_flag(task_id, ports)} --from-document`."
            ],
        )
    seeded = apply(state, rules.reintegrate_patch(state))
    if problems := disagreements(seeded, ports.workspace):
        raise _disagree(task_id, problems)
    return Seed(seeded, None)


def _reintegration_line(
    task_id: str,
    state: TaskState,
    moved: tuple[str, str, str],
    descends: bool,
    commits: tuple[rules.Commit, ...],
    unread: Exception | None,
) -> str | None:
    """The refusal line of a branch that moved past the approved head with some subtasks
    returned, when its commits are what is wrong: the first one not accepted and why, or that
    they could not be read. `None` when another condition of the rule fails, whose line stays."""
    unit, branch, approved = moved
    reason = rules.refused_reintegration(state, task_id, descends, commits)
    if reason is None:
        return None
    if unread is not None:
        return (
            f"{unit}: could not read the commits between {approved} and `{branch}` ({unread}): "
            "retry the resume."
        )
    return (
        f"{unit}: `{branch}` moved past the approved head {approved} but not only by merges of "
        f"approved subtasks while others are returned: {reason}."
    )


def _checked(task_id: str, state: TaskState, ports: Ports) -> TaskState:
    if problems := disagreements(state, ports.workspace):
        raise _disagree(task_id, problems)
    return state


def _moved_line(
    task_id: str,
    ports: Ports,
    cfg: Configuration,
    state: TaskState,
    moved: tuple[str, str, str, str],
    descends: bool,
    unreadable: Exception | None,
    reintegration: str | None = None,
) -> str:
    """The refusal line for the reviewed unit's moved branch: what is wrong and a command that
    is safe to run. A condition of the rule other than ancestry, the pull request and the
    commits of a reintegration (`reintegration`, GH-408) keeps today's line."""
    unit, branch, approved, head = moved
    if unreadable is not None:
        return (
            f"{unit}: could not check whether `{branch}` descends from {approved} "
            f"({unreadable}): retry the resume."
        )
    if branch == rules.task_branch(task_id) and not descends:
        return (
            f"{unit}: `{branch}` is at {head}, which does not contain the approved head "
            f"{approved} (history rewritten). Inspect it; to discard it, start over with "
            f"`agentic run {issue_flag(task_id, ports)} --fresh`."
        )
    if branch == rules.task_branch(task_id) and state.get("pull_request"):
        return _landing_owns_line(task_id, ports, cfg, state)
    if reintegration is not None:
        return reintegration
    return f"{unit}: approved at {approved}, but `{branch}` is at {head}"


_LANDING_WAITS: dict[landing_plan.ReleaseWhy, str] = {
    "waiting_on_human": (
        "it waits for a human comment after the Escalated: comment, the needs-human label "
        "removed and the issue open"
    ),
    "waiting_on_base_ci": ("it waits for the base branch CI to turn green, or an owner comment"),
    "task_cap": "its task cap is spent: raise the task budget",
}


def _landing_owns_line(task_id: str, ports: Ports, cfg: Configuration, state: TaskState) -> str:
    """The refusal for a task with a pull request whose branch moved after approval (GH-442).
    `agentic land` is named unless the document is escalated and the table's step for it is
    the one `land` holds back (`landing_plan.release_asked`, the step `land` computes over the
    same merge facts); then `landing_plan.release`, the decision `land` asks, says whether
    `land` can move it. A task `land` moves without asking keeps today's line; one it holds
    back, or answers `not_live` for, states the wait that decision gives, or the restart only
    a human starts when its shape answers `needs_restart`."""
    flag = issue_flag(task_id, ports)
    owns = f"{task_id} has a pull request: its branch moved after approval, which the landing owns"
    land = f"{owns}; run `agentic land {flag}`."
    number = ports.inbox.issue_of(task_id)
    if not state.get("escalated") or number is None or not landing_plan.live_entries(state):
        return land
    try:
        master = ports.workspace.upstream().sha
    except GitError:
        return land
    if master is None:
        return land
    try:
        assessed = assess_merge(ports, task_id, state, number, frozenset(), master)
    except StackUnreadable:
        return land
    if isinstance(assessed, Abandoned) or assessed.decision.reason is None:
        return land
    facts, refusal = assessed.facts, assessed.decision.reason
    head = facts.pr_head
    if assessed.decision.kind != "refuse" or head is None:
        return land
    step = landing_plan.step_for(
        state,
        refusal,
        facts,
        lambda: landing_plan.slice_inputs(state, cfg, ports, master, head),
        landing_plan.head_ancestry(ports, cfg, head, master),
    )
    if (
        step.kind == "escalate"
        and state.get("escalated")
        and landing_rules.already_dispatched(
            landing_plan.live_entries(state), head, step.kind, master
        )
    ):
        # The escalation itself, taken on this head: `land` answers "already taken" and moves
        # nothing until the base's CI is green and the table gives another step.
        return f"{owns}, and it waits for the base branch CI to turn green."
    asked = landing_plan.release_asked(state, step.kind)
    if not asked and landing_plan.takes_action(step.kind, landing_plan.LIVE_STEPS):
        return land
    # Either `land` holds the step back for the release, or it answers `not_live` and writes
    # nothing (a ledger or depth-1 escalation): the shared decision says whether it moves.
    outcome = landing_plan.release(ports, cfg, task_id, state, number, head=head, upstream=master)
    if asked and outcome.released:
        return land
    if outcome.why == "needs_restart":
        return (
            f"{owns}, and its escalation is one only a restart moves: `agentic run {flag} --fresh`."
        )
    wait = _LANDING_WAITS.get(outcome.why)
    if wait is None:
        return f"{owns}, and `agentic land` has nothing to do for it now."
    return f"{owns}, and {wait}."


def _task_checkout(task_id: str, ports: Ports) -> tuple[Path | None, bool]:
    """The task's own worktree, if there is one, and whether the stopped run left files in it.
    Refuses a checkout holding an unfinished merge or conflicts: a human is in the middle of
    something, and nothing here commits over it."""
    path = next((p for p in ports.workspace.worktrees(task_id) if p.name == task_id), None)
    if path is None:
        return None, False
    try:
        if ports.workspace.merge_in_progress(path):
            raise _disagree(task_id, [_merge_line(path)])
        return path, not ports.workspace.is_clean(path)
    except GitError as failed:
        raise _disagree(
            task_id,
            [f"could not read the integration checkout `{path}` ({failed}): retry the resume."],
        ) from failed


def _merge_line(path: Path) -> str:
    return (
        f"the integration checkout `{path}` holds an unfinished merge or conflicts: "
        "finish or abort it by hand, then resume."
    )


def disagreements(
    state: TaskState, workspace: Workspace, *, exempt: frozenset[str] = frozenset()
) -> list[str]:
    """Where git no longer holds what the document says: a branch gone, a base that is
    not the one git recorded, an approved subtask whose branch moved after its review. A unit
    named in `exempt` skips only that last check: its moved branch is an increment (GH-392)."""
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
        moved = sub.get("status") == "approved" and reviewed
        if moved and sid not in exempt and not rules.same_commit(reviewed, head):
            problems.append(f"{sid}: approved at {reviewed}, but `{branch}` is at {head}")
    return problems


def _text(body: str) -> str:
    """A comment as the decision reads it: stripped, line endings normalised."""
    return "\n".join(body.strip().splitlines())


def human_decision(comments: Sequence[str]) -> str | None:
    """The human's decision: every comment after the system's last one, joined. The
    human may answer in several comments; all of them, and only them, enter. With no
    system comment there is nothing the human answered: what the issue says before
    the system ever spoke is the task, not a decision."""
    after: list[str] | None = None
    for body in comments:
        text = _text(body)
        if text.startswith(SYSTEM_COMMENTS):
            after = []
        elif text and after is not None:
            after.append(text)
    return "\n\n".join(after or []) or None


def decision_since(comments: Sequence[str], prefix: str) -> str | None:
    """The human's decision on a landing escalation (GH-328): every comment after the last one
    that starts with `prefix` (the escalation's), joined as `human_decision` joins them. A
    comment of any other system prefix is dropped on its own and never resets the count, so
    the poller's refusal comment after the escalation does not swallow the answer before it.
    `None` when nothing follows. The only place a landing reads a decision off the issue."""
    after: list[str] | None = None
    for body in comments:
        text = _text(body)
        if text.startswith(prefix):
            after = []
        elif text and after is not None and not text.startswith(SYSTEM_COMMENTS):
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
    increment: Increment | None = None,
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
    if estimate is not None and paused.get("fit_question"):
        raise ValueError(
            f"{task_id} is paused asking whether to build the part whole, not for an "
            "estimate: --estimate does not apply; comment on the issue and resume"
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
        if decision is None and paused.get("fit_question"):
            # A part over the line asked once (ADR-0054): without a comment the question is
            # repeated as it was asked, and stopping stays the absence of a resume.
            raise WaitingOnHuman(_fit_question(task_id, paused))
        if decision is None and estimate is None and paused.get("escalation_class") == "estimate":
            # Never retaken on its own (ADR-0054): the estimate in force changes only on a
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
        owed = _owed_publication(task_id, paused, source, cfg, ports)
        paused = apply(paused, {"publish_owed": owed})
        resumed = apply(paused, rules.resumed(paused, decision, estimate))
        if paused.get("escalation_class") == "estimate":
            _refuse_spent_estimate(task_id, resumed, paused.get("cost_usd", 0.0))
        next_node = rules.resume_target(resumed)
        if next_node is None and rules.landing_pass(resumed) == "unstarted":
            # The pass was recorded and nothing reopened (GH-450): `resume` has no stage to
            # continue, `land` runs the step on the same entry.
            step = resumed["landing"][-1].get("step")
            raise ValueError(
                f"{task_id} has a landing pass ({step}) recorded and not started: "
                f"run `agentic land --issue {task_id.removeprefix('GH-')}` to start it"
            )
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
        increment=increment,
    )


def _owed_publication(
    task_id: str, paused: TaskState, source: Source, cfg: Configuration, ports: Ports
) -> str | None:
    """The `publish_owed` a resume writes (GH-373): the approved head a stopped run never
    published, from three facts read through the workspace on the product's root checkout --
    whether the remote holds it, and whether the task branch is at it or differs only in the
    task document. Only a task seeded from its document is asked (the checkpoint knows where
    it stopped), and never a worktree or a setup is made to find out: `rules` decides, this
    only reads. A read that fails is its own error, a retry and never a fresh start."""
    candidate = rules.publication_candidate(paused) if source == "document" else None
    if candidate is None:
        return None
    root, branch = cfg.product_dir, rules.task_branch(task_id)
    try:
        tip = ports.workspace.remote_tip(root, branch)
        published = tip is not None and ports.workspace.is_ancestor(root, candidate, tip)
        head = ports.workspace.branch_head(branch)
        at_head = head is not None and (
            rules.same_commit(head, candidate)
            or not ports.workspace.differs_outside(
                root, candidate, head, document.relative_path(task_id).as_posix()
            )
        )
    except (GitError, ValueError) as failed:
        raise ValueError(
            f"{task_id}: approved head {candidate[:7]} could not be checked against origin: "
            f"retry ({failed})"
        ) from failed
    return rules.owed_publication(paused, published, at_head)


def _fit_question(task_id: str, paused: TaskState) -> str:
    """The question a part over the line asked: the escalation's own text, which
    `rules.fit_question_text` wrote; rebuilt from the marker if the state lost it."""
    if asked := paused.get("escalated"):
        return asked
    part_of = rules.split_part_of(paused.get("description", "")) or (task_id, 0)
    return rules.fit_question_text(task_id, part_of, None)


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
    lines.extend(_increment_lines(r))
    lines.extend(f"session:   {line}" for line in _sessions(r))
    for path in ports.workspace.worktrees(r.task_id):
        dirty = r.interrupted and not ports.workspace.is_clean(path)
        lines.append(f"worktree:  {path}" + ("  (uncommitted changes)" if dirty else ""))
    return "\n".join(lines)


def _increment_lines(r: Resumption) -> list[str]:
    if (inc := r.increment) is None:
        return []
    lines = [
        f"integration: approved at {inc.approved}, `{inc.branch}` is at {inc.head}, a "
        f"descendant: reviewing the increment {inc.approved}..{inc.head}"
    ]
    if inc.dirty:
        lines.append(f"uncommitted files in `{inc.checkout}` will be committed into the increment")
    return lines


def _commit_leftovers(r: Resumption, ports: Ports) -> None:
    """The one commit of what the stopped run left in the task checkout (GH-392), made here
    and nowhere else: a seed or a dry run never writes. A merge that began since the seed is
    asked again, and refuses before anything is unlabelled or said."""
    inc = r.increment
    if inc is None or not inc.dirty or inc.checkout is None:
        return
    try:
        if ports.workspace.merge_in_progress(inc.checkout):
            raise _disagree(r.task_id, [_merge_line(inc.checkout)])
        ports.workspace.commit_all(
            inc.checkout, f"{r.task_id}: commit what the stopped run left uncommitted"
        )
    except GitError as failed:
        raise _disagree(
            r.task_id,
            [
                f"could not commit what the stopped run left in `{inc.checkout}` ({failed}): "
                "retry the resume."
            ],
        ) from failed


def announce(r: Resumption, ports: Ports) -> list[TrackerFailureRecord]:
    """Take the task out of the queue and say on the issue that it resumed. A task seeded
    from its document first forgets the worktrees the human removed by hand, so asking
    for those checkouts again recreates them; a dry run never gets here.

    `r.interrupted` (glossary: Corrida interrumpida) adds one clause naming the role the
    previous invocation was interrupted at, so "el issue quedó mintiendo" (the request's
    own words) gets an answer without a new comment prefix: `RESUMED` is the only one
    this ever wrote, `SYSTEM_COMMENTS` stays as it is, and a resume that was not
    interrupted emits the exact same bytes as before (subtask `signals`).

    Both writes are informative (GH-389): a GitHub error that outlasts the retries does not
    end the resume. The failures come back as rows for `tracker_failures`, empty when both
    landed; the caller records them through the state's reducer."""
    failures: list[TrackerFailure | None] = []
    _commit_leftovers(r, ports)
    if r.source == "document":
        ports.workspace.prune()
    # `gh issue edit --remove-label` fails for a label the repository never had, and an
    # account-limit-only pause (GH-381) is never labelled, so it is not unlabelled either.
    if rules.needs_a_human(r.paused):
        failures.append(ports.tracker.drop_label(r.task_id, NEEDS_HUMAN))
    answered = " with your decision" if r.decision else ""
    role = r.paused.get("current_role") or "before any role"
    interrupted = f", interrupted at {role}" if r.interrupted else ""
    inc = r.increment
    increment = f", reviewing the increment {inc.approved}..{inc.head}" if inc else ""
    failures.append(
        ports.tracker.note(
            r.task_id,
            f"{RESUMED}{r.target}{answered}{interrupted}{increment}, "
            f"under a task cap of {r.cap_usd:.2f} USD.",
        )
    )
    return [failure.record() for failure in failures if failure is not None]


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
