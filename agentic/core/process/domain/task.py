"""The task and its state, as the graph carries it. Imports nothing from the project.

Reducers:
- `add` accumulates cost across roles.
- `merge` is the per-key override for dict fields. Resetting a pair's counter
  when the human answers is an explicit `{pair: 0}` update, not a copied dict.
- `operator.add` keeps every role failure, retried or not, and every role run,
  for PjM's metrics and for reopening a role's session.

`flags` has no reducer on purpose: the validated set replaces the proposed one.

`apply` folds a partial patch into a state with those same reducers. The graph
engine does the same on its side; use cases need it to save the document with
the reducers already applied. `delta` is its inverse, for a pipeline that runs
several nodes over a copy of the state and has to hand the parent what it added.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict, get_args, get_origin, get_type_hints

Status = Literal[
    "open",
    "defined",
    "returned",
    "definition_ready",
    "planned",
    "developing",
    "ready_for_review",
    "waiting_on_human",
]
WAITING_ON_HUMAN: Status = "waiting_on_human"
# The system's own subtask: the integrator joins the others on the task branch
# (ADR-0011). Only when there is more than one.
INTEGRATION = "integration"
SubtaskStatus = Literal["pending", "delivered", "approved", "returned"]
# Why a review finding blocks, as a closed list (ADR-0016): `standard-*` is a hard
# violation of a documented standard, `spec-*` a gap against the brief or the
# definition, `process-*` a delivery that is not one, `plan-calibration` a threshold or
# parameter set wrong, `plan-invariant` a rule the plan set for every subtask that this
# one breaks, `integration-crosscut` a problem that spans subtasks.
FindingCategory = Literal[
    "standard-architecture",
    "standard-typing-silenced",
    "standard-seams",
    "standard-other",
    "spec-behaviour",
    "spec-missing-test",
    "spec-scope",
    "process-uncommitted",
    "process-suite-red",
    "plan-calibration",
    "plan-invariant",
    "integration-crosscut",
]
# How much a finding weighs (ADR-0016): only a `blocking` one returns the subtask, and
# only one the branch under review can fix; anything wider is the plan's.
Severity = Literal["blocking", "important", "observation"]
FindingScope = Literal["subtask", "plan"]
# Where a finding stands in the findings table: `open` until a later review of its
# subtask stops raising it (`fixed`), or `dismissed` when nobody can check it.
FindingStatus = Literal["open", "fixed", "dismissed"]
# A return's category: a review's takes its first blocking finding's; PM's returns of
# the definition, whose reason stays free text, are all `definition`.
ChangeCategory = FindingCategory | Literal["definition"]


def add(current: float, new: float) -> float:
    return current + new


def last[T](current: T, new: T) -> T:
    """The latest write wins. Developers running in parallel each stamp the role and
    the status; without a reducer the graph refuses two writes in one step."""
    return new


def merge[T](current: dict[str, T] | None, update: dict[str, T] | None) -> dict[str, T]:
    return {**(current or {}), **(update or {})}


class TaskInput(TypedDict):
    """What enters the graph. `id` is also the thread id (ADR-0003)."""

    id: str
    title: str
    description: str


class Failure(TypedDict):
    role: str
    attempt: int
    error: str
    # A failed attempt still cost money; PjM's metrics need it.
    cost_usd: float
    # The session the attempt left, if the harness got as far as opening one: the
    # retry continues it, and `claude -r <id>` reopens it for the human.
    session_id: str


class RoleRun(TypedDict):
    """One successful run of a role. `section` is what it wrote (a role may write
    several: one per subtask, one per interview); `version` numbers the deliveries of
    that section, so a superseded `<section> (vN)` maps back to its run."""

    role: str
    section: str
    version: int
    session_id: str
    model: str
    cost_usd: float
    # `cost_usd` by model: the role's own and its sub-agents' (ADR-0013, addendum).
    # `turns` counts the role's alone, so this is what explains a dear two-turn run.
    cost_by_model: dict[str, float]
    turns: int


class ChangesRequested(TypedDict):
    """One return, as the history keeps it: the pair, its category and the verifier's
    one-line reason (README.md, 3: "the same motive between the same roles", computable)."""

    pair: str
    category: ChangeCategory
    reason: str


class FindingRecord(TypedDict):
    """One finding as the table keeps it (issue #42): what the reviewer wrote, plus
    which subtask's review raised it and in which pass. Append-only and never rewritten:
    branches run at the same time and cannot see each other, so what a row means today
    is folded when it is read, not when it is written (`rules.findings_rows`).

    No `evidence`: it is the command and its output, it lives in the review's own
    section, and a copy per finding per pass in the frontmatter only grows the document.
    What the system needed it for, deciding whether anybody could check the finding, is
    already spent by the time the record is written."""

    subtask: str
    # The review pass that raised it, per subtask: a later pass that does not raise it
    # again is what marks the row fixed.
    version: int
    location: str
    claim: str
    severity: Severity
    scope: FindingScope
    category: FindingCategory
    # `open` or `dismissed` as written; `fixed` is only ever derived.
    status: FindingStatus


class Subtask(TypedDict, total=False):
    """A part of the task assigned to one Developer (README.md, 4). Lives in its own
    branch; the pair Tech Lead→Developer counts returns per subtask."""

    id: str
    title: str
    brief: str
    seams: list[str]
    depends_on: list[str]
    status: SubtaskStatus
    branch: str
    # Commit the branch started from: the first review diffs from it.
    base: str
    # Head the last review reached: the next one diffs from it, not from `base`
    # (ADR-0015). Absent until the subtask has been reviewed once.
    reviewed_head: str
    # The last review's reason, what the Developer fixes on a return, and its
    # category (ADR-0016).
    reason: str
    change_category: ChangeCategory
    # Branches the fan-in could not bring into the task branch on its own. Only the
    # integration subtask carries it: what the integrator has left to resolve.
    conflicts: list[str]


class TaskState(TaskInput, total=False):
    type: str
    status: Annotated[Status, last]
    current_role: Annotated[str, last]
    flags: dict[str, bool]
    escalated: Annotated[str | None, last]
    # One Markdown section per role, keyed by role name, holding the latest delivery.
    # A delivery superseded by a return stays under "<role> (vN)": the document is
    # the raw record of the task, and PjM escalates with the history (ADR-0006).
    sections: Annotated[dict[str, str], merge]
    # Returns used per pair, keyed by `Pair.key`, like "PM→Product Analyst".
    changes_requested: Annotated[dict[str, int], merge]
    # Every return in order, with its category: what measurement counts.
    change_history: Annotated[list[ChangesRequested], operator.add]
    failures: Annotated[list[Failure], operator.add]
    runs: Annotated[list[RoleRun], operator.add]
    # Short hash of each prompt that ran, keyed by role slug (ADR-0005).
    prompts: Annotated[dict[str, str], merge]
    cost_usd: Annotated[float, add]
    # The technical stage (ADR-0011): subtasks by id, each Developer patches its own.
    subtasks: Annotated[dict[str, Subtask], merge]
    # Rules of the plan every subtask honours (issue #42): the one channel between
    # reviews that works in the first wave, since it is written before anyone codes.
    invariants: list[str]
    # What the plan says has to be done to make the subtasks work together beyond
    # merging them; empty when merging is enough and no integrator runs.
    integration_brief: str
    # Every finding of every review, in order. Read folded, never rewritten.
    findings: Annotated[list[FindingRecord], operator.add]
    # Locations a review declared settled. A row closes because a reviewer said it did,
    # never because a pass stopped mentioning it: every pass after the first is
    # incremental (ADR-0015) and is told not to look at what it already approved, so
    # silence there means "did not look", not "fixed".
    settled_findings: Annotated[list[str], operator.add]
    # The task branch, and the pull request once delivered.
    branch: str
    pull_request: str | None
    # What an interviewee could not decide (ADR-0014); escalates with the task.
    questions_for_human: list[str]
    # Which subtask a branch of the fan-out is running: set by the fan-out, read by
    # `develop` and `review`, never persisted and never handed back to the parent.
    subtask_id: str


def _reducers() -> dict[str, object]:
    hints = get_type_hints(TaskState, include_extras=True)
    return {
        name: get_args(hint)[1] for name, hint in hints.items() if get_origin(hint) is Annotated
    }


REDUCERS = _reducers()


def apply(state: TaskState, patch: TaskState) -> TaskState:
    """`state` with `patch` folded in, reducer by reducer; plain fields are overridden."""
    merged: dict = dict(state)
    for key, value in patch.items():
        reducer = REDUCERS.get(key)
        if reducer is None or key not in merged:
            merged[key] = value
        else:
            merged[key] = reducer(merged[key], value)  # type: ignore[operator]
    return merged  # type: ignore[return-value]


def delta(before: TaskState, after: TaskState) -> TaskState:
    """The patch that turns `before` into `after` through the reducers:
    `apply(before, delta(before, after))` is `after` again.

    A pipeline of nodes runs over a copy of the state and ends holding the whole
    state, not what it added. Handing that back to the parent would fold it in a
    second time: `cost_usd` counted twice, every `runs`, `failures` and
    `change_history` entry duplicated. `subtask_id` never travels back: it says which
    branch ran, and the parent has as many answers as branches.
    """
    patch: dict = {}
    for key, value in after.items():
        if key == "subtask_id":
            continue
        if key not in before:
            patch[key] = value
            continue
        old, reducer = before[key], REDUCERS.get(key)
        if reducer is add:
            if value != old:
                patch[key] = value - old
        elif reducer is operator.add:
            if len(value) > len(old):
                patch[key] = value[len(old) :]
        elif reducer is merge:
            if changed := {k: v for k, v in value.items() if k not in old or old[k] != v}:
                patch[key] = changed
        elif value != old:
            patch[key] = value
    return patch  # type: ignore[return-value]
