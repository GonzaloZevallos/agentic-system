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
from dataclasses import dataclass
from typing import Annotated, Literal, NotRequired, TypedDict, get_args, get_origin, get_type_hints

Status = Literal[
    "open",
    "defined",
    "returned",
    "definition_ready",
    "planned",
    "developing",
    "ready_for_review",
    "waiting_on_human",
    # A bug whose behaviour PM ruled expected (README.md, 3, "Cierre"; issue #65): the
    # run ends without a pull request, and the human closes the issue.
    "discarded",
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
# Why an attempt failed, decided at the raise site, never by parsing the error's text
# (README.md, 14; the Medición glossary's `agentic.role.failures`): `schema` is a
# structured output missing or invalid against the role's own schema, `check` a use
# case's own rule over otherwise-valid output, `max_turns` the harness cutting the
# model off, `limit` a per-attempt money cap (issue #66), `sdk` anything else the
# harness raised, `other` the residual nothing else names.
FailureReason = Literal["schema", "check", "max_turns", "limit", "sdk", "other"]
# Which review this is (ADR-0016; `rules.review_kind`): `first` until a return sends the
# subtask back, `incremental` (ADR-0015) after that, `integration` for the fan-in's own
# subtask always. What `agentic.review.findings` groups by alongside the finding itself.
ReviewKind = Literal["first", "incremental", "integration"]
# Why the task escalated, from the closed set a domain constructor writes together with
# `escalated` (ADR-0008, 2026-09-18 addendum; `agentic.task.paused`), so a patch cannot
# carry one without the other: `classification` PjM's own low-confidence call, `returns`
# a pair (PM<->Product Analyst or Tech Lead<->Developer) at its return limit, `failure` a
# role `run_with_retry` gave up on, `cap` the task's money cap reached before an attempt,
# `questions` an interview's open questions for the human, `unmergeable` a subtask whose
# dependency the system could not merge on its own, `setup` the product's setup step
# failing on a checkout the system just made. `limit` (issue #66's per-attempt budget
# cap) is not here: it stops one attempt, not the task, so it is a `FailureReason`.
EscalationClass = Literal[
    "classification", "returns", "failure", "cap", "questions", "unmergeable", "setup"
]


@dataclass(frozen=True)
class MergeOutcome:
    """What `Workspace.merge_keeping_conflicts` reports (the dependency-merge path in
    `develop.py`, never `Workspace.merge`'s own abort-on-conflict caller): a value
    object, not a git verb, so the pure verdict in `domain/rules.py` decides from it
    with no checkout of its own. `reason` is `None` for a merge that committed clean;
    otherwise it is why, the same text `Workspace.merge` already returns for a
    conflict or a refusal. `conflicted_paths` is the paths `git diff --name-only
    --diff-filter=U` named right after the attempt, empty when the merge never even
    started (an unknown branch, a dirty tree, a git error) — that emptiness alongside
    a non-`None` reason is what tells the verdict there is nothing for a Developer to
    resolve."""

    reason: str | None
    conflicted_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyState:
    """The facts a dependency merge leaves in a checkout, read off git in one call
    (`Workspace.dependency_state`): whether `contained` (the dependency branch is
    already an ancestor of HEAD), whether a merge is `merge_in_progress` (`MERGE_HEAD`
    still there, from this run's own attempt or a human's partial fix in the
    worktree), which of the paths asked about (`marked_paths`, a subset of what the
    caller passed) still hold a conflict marker in their actual content — not git's
    own conflict bookkeeping, which a `git add` past a marker already clears — and
    `evidence`, the diff against the dependency branch for those paths, written into
    the Developer's section for the review to check."""

    contained: bool
    merge_in_progress: bool
    marked_paths: tuple[str, ...]
    evidence: str = ""


@dataclass(frozen=True)
class PullRequestCommit:
    """One commit on a pull request's branch, as `gh` hands it back: `author_emails`
    is what `metrics.human_lines_changed` and friends match against
    `Configuration.GIT_AUTHOR_EMAIL` to tell a human's own commit from the system's
    (never a GitHub login, which a human's own account would satisfy just the same);
    `message_headline` is what a merge-commit constant matches against, since `gh`
    exposes no parents to exclude them any other way; `additions`/`deletions` are
    this commit's own line counts."""

    author_emails: tuple[str, ...]
    message_headline: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class PullRequest:
    """The facts of a task's own pull request, as `Tracker.pull_request` hands them
    back over the `gh` CLI: a value object over a port boundary, so the port can hand
    facts to `measurement/domain/metrics.py`'s pure rules without either side
    learning the other's technology. The only type describing a pull request's
    facts -- nobody declares a second, and every consumer imports this one.
    `merged_at` is `None` for a pull request that never merged (open or closed
    unmerged); `commits` is every commit on the branch, oldest first."""

    state: Literal["OPEN", "MERGED", "CLOSED"]
    created_at: str
    merged_at: str | None
    commits: tuple[PullRequestCommit, ...]


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
    # Why the attempt failed, from the closed set (`FailureReason`); absent on a
    # failure recorded before this field existed.
    reason: NotRequired[FailureReason]
    # `Role.slug`, so a failure of one of the six roles named "Tech Lead" says which;
    # absent on a failure recorded before this field existed.
    slug: NotRequired[str]
    # What the attempt was writing when it failed, the same string `RoleRun.section`
    # would have carried had it delivered; absent on a failure recorded before this
    # field existed.
    section: NotRequired[str]
    # Whether this attempt is the run's cold fallback: the caller passed a `resume` that
    # never opened (the harness left no `session_id`), so `run_with_retry` dropped it and
    # ran this attempt cold instead of retrying the stale id (ADR-0004, sixth addendum).
    # Decided once per run, in `run_with_retry`, the only place that knows it; absent on
    # a failure recorded before this field existed, which reads as `False`.
    cold_fallback: NotRequired[bool]
    # Write attempts the SDK denied during this attempt, before it ended one way or
    # another (glossary: Negación): read off `RawResult.denials`/`AttemptFailed.denials`,
    # never guessed later. Absent on a failure recorded before this field existed, which
    # reads as `0` (`rules.denial_note`'s callers always use `.get(..., 0)`).
    denials: NotRequired[int]
    # Whether the live turn-budget notice (`domain/turn_notice.py`; ADR-0004, seventh
    # addendum) fired during this attempt, off `RawResult.turn_notice`/
    # `AttemptFailed.turn_notice`. `False` for every role `turn_notice.margin_for` does
    # not cover, which is every role but `DEVELOPER`/`DEVELOPER_INTEGRATOR`. Absent on a
    # failure recorded before this field existed, which reads as `False`
    # (`run_role._run_notice`'s own `.get(..., False)`).
    turn_notice: NotRequired[bool]
    # Calls to the delivery tool this attempt saw before it failed (glossary: Emisión
    # de entrega), off `RawResult.deliveries`/`AttemptFailed.deliveries`. Absent on a
    # failure recorded before this field existed, which reads as `0`
    # (`run_role._run_deliveries`'s own `.get(..., 0)`) -- never "unknown".
    deliveries: NotRequired[int]
    # Denials the presubmit itself issued during this attempt (glossary: Puerta de
    # entrega), off `RawResult.presubmit_denials`/`AttemptFailed.presubmit_denials`; a
    # different fact from `deliveries` above and from `denials`, never folded into
    # either. Absent on a failure recorded before this field existed, which reads
    # as `0`.
    presubmit_denials: NotRequired[int]


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
    # The role's own `max_turns` at the time it ran (not the output's, which knows
    # nothing about the cap): what `agentic.role.max_turns` reads, so a calibration can
    # compare `turns` against the budget it actually ran under. Absent on a run
    # recorded before this field existed; read with `.get()`, no sample emitted then.
    max_turns: NotRequired[int]
    # `Role.slug`: `role` alone cannot tell the six roles named "Tech Lead" apart.
    # Absent on a run recorded before this field existed; read with `.get(..., "unknown")`.
    slug: NotRequired[str]
    # When the attempt started and ended, ISO-8601 UTC with millisecond precision, the
    # clock started only after the concurrency slot was acquired: the duration between
    # them is work inside the slot, never the wait for one. Absent on a run recorded
    # before this field existed.
    started_at: NotRequired[str]
    ended_at: NotRequired[str]
    # Seconds this attempt waited for a concurrency slot before it started, its own
    # series and never folded into the duration above. Absent on a run recorded before
    # this field existed.
    queue_wait_s: NotRequired[float]
    # Token usage of this run's delivering `ResultMessage`, by kind (`input`, `output`,
    # `cache_read`, `cache_write`): `agentic.role.tokens` groups on this key. Absent on
    # a run recorded before this field existed.
    tokens: NotRequired[dict[str, int]]
    # Calls to the model in this run: one per `AssistantMessage` the runner saw before
    # the delivery (`ClaudeSdkRunner.run_once`'s own count). Absent on a run recorded
    # before this field existed.
    calls: NotRequired[int]
    # `tool_use` blocks of this run, by tool name; a name outside the known set counts
    # as `other`. Absent on a run recorded before this field existed.
    tool_calls: NotRequired[dict[str, int]]
    # What of `cost_by_model` a sub-agent spent: every model but this run's own
    # (ADR-0013). Not additive with `cost_usd`, which already counts that dollar once.
    # Absent on a run recorded before this field existed.
    subagent_cost_usd: NotRequired[float]
    # The session this run actually resumed, taken from `run_with_retry`'s own decision,
    # never re-derived from the caller's `resume` argument (which lies on two paths: a
    # retry that continues a *failed* attempt's session unasked, and a cold fallback that
    # resumed nothing while the argument held a valid id). Empty when no session was
    # resumed. Absent on a run recorded before this field existed, which reads as `""`.
    resumed_from: NotRequired[str]
    # Whether the winning attempt is this run's cold fallback (ADR-0004, sixth addendum):
    # the caller asked to resume a session that never opened, so `run_with_retry` dropped
    # it and ran cold instead. Absent on a run recorded before this field existed, which
    # reads as `False`.
    cold_fallback: NotRequired[bool]
    # What this run's own failed attempts cost, already counted once inside `cost_usd`
    # (`run_with_retry` adds `spent(failures)` there before it returns): a fact of the
    # run, not a computed figure, recorded so `metrics.run_rework_usd` can tell a
    # `version == 1` run's failure money from its delivering call's without a second join
    # over `failures` (ADR-0008, 2026-09-18 second addendum). Absent on a run recorded
    # before this task, which reads as `0.0` and under-reports that run's failure money
    # equally wherever this figure is read.
    failed_cost_usd: NotRequired[float]
    # Write attempts the SDK denied during the delivering attempt (glossary: Negación),
    # off `RawResult.denials`. The run's total, for the document line and the escalation
    # (`rules.denial_note`), also folds in every recorded `Failure.denials`: this field
    # alone is only the delivering attempt's. Absent on a run recorded before this field
    # existed, which reads as `0`.
    denials: NotRequired[int]
    # Whether the live turn-budget notice fired during the delivering attempt, off
    # `RawResult.turn_notice` (`domain/turn_notice.py`; ADR-0004, seventh addendum). The
    # run's total, for the section note (`turn_notice.notice_note`), also folds in
    # every recorded `Failure.turn_notice`: this field alone is only the delivering
    # attempt's, the same split `denials` already has. Absent on a run recorded before
    # this field existed, which reads as `False`.
    turn_notice: NotRequired[bool]
    # Calls to the delivery tool during the delivering attempt (glossary: Emisión de
    # entrega), off `RawResult.deliveries`. The run's total, for the section note
    # (`rules.deliveries_note`), also folds in every recorded `Failure.deliveries`:
    # this field alone is only the delivering attempt's, the same split `denials`
    # already has. Absent on a run recorded before this field existed, which reads
    # as `0`.
    deliveries: NotRequired[int]
    # Denials the presubmit itself issued during the delivering attempt (glossary:
    # Puerta de entrega), off `RawResult.presubmit_denials`. A different fact from
    # `deliveries` above: the gate working as designed, never a re-emission of an
    # invalid shape. Absent on a run recorded before this field existed, which reads
    # as `0`.
    presubmit_denials: NotRequired[int]


class InterviewStats(TypedDict):
    """Counts of one interview (ADR-0014), for measurement only: `interview()`'s own
    patch key, read by `core.measurement` for `agentic.interview.*` and never rendered
    into the document — the same treatment `subtask_id` gets, since a panel needs it
    but a resumed task does not. `interview` is which stage ran it (`Interview.stage`);
    `rounds` is the question-answer cycles that reached an interviewee's answer;
    `questions_to_pm` counts the consultations that ran (Consulta, ADR-0014's addendum);
    `questions_to_human` is the net count left for the human once the interview ended,
    after any withdrawal; `invariants_proposed` is what the interviewer proposed, before
    the plan adopts any of them."""

    interview: Literal["definition", "solution"]
    rounds: int
    questions_to_pm: int
    questions_to_human: int
    invariants_proposed: int


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
    # Paths a dependency merge left conflicted, this subtask's own (never the
    # integration's `conflicts` above, a different key with a different meaning: this
    # one is what `develop()`'s marker scan reads back, that one is what the fan-in
    # still has to merge by hand). Written by the run that merged a dependency or found
    # one already in progress; scanned only on such a run; cleared by the delivery that
    # verifies the dependency landed clean. Absent on a subtask this task's path never
    # touched, and on every document written before this task existed — every read is
    # `.get("conflicted_paths", [])`.
    conflicted_paths: list[str]


class TaskState(TaskInput, total=False):
    type: str
    status: Annotated[Status, last]
    current_role: Annotated[str, last]
    flags: dict[str, bool]
    escalated: Annotated[str | None, last]
    # The closed reason `escalated` is text for (`EscalationClass`); written only by
    # `rules.escalation`, together with `escalated`, and cleared with it on resume.
    escalation_class: Annotated[EscalationClass | None, last]
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
    # What the ephemeral interviewer proposed for the deliverable that follows
    # (issue #42), across the whole interview so far: a retake seeds its transcript
    # from this instead of starting empty. `last`, never an append reducer: the
    # value written each time is already the accumulated list, and appending it to
    # itself would duplicate it on every retake.
    proposed_invariants: Annotated[list[str], last]
    # Why a bug was discarded, in PM's words (issue #65): `discard` writes it on the
    # issue, and the frontmatter keeps it so the document says why the run ended.
    discard_reason: str
    # Which subtask a branch of the fan-out is running: set by the fan-out, read by
    # `develop` and `review`, never persisted and never handed back to the parent.
    subtask_id: str
    # Which review this is (`rules.review_kind`): set by `review` on its own patch, read
    # once by the adapter for `agentic.review.findings`'s `review.kind` label. Measurement
    # only, like `subtask_id`: never rendered into the document, recomputed from
    # `reviewed_head` whenever it is needed again after a resume.
    review_kind: ReviewKind
    # Every invocation of a Developer's presubmit that actually ran the checks, in
    # order: one list of check results (`CheckResult`, `domain/presubmit.py`) per
    # invocation. Set by `develop` on its own patch, read once by the adapter
    # (`agentic.presubmit.checks`, `.duration`). Measurement only, like `subtask_id`:
    # never rendered into the document.
    presubmit_checks: list[list[dict[str, str | bool | float]]]
    # Counts of the interview this patch ran, if any (`InterviewStats`): measurement
    # only, like `subtask_id` above — never rendered into the document.
    interview_stats: InterviewStats


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


# Measurement-only keys a branch writes on its own patch and reads back inside the
# same node (`RoleRun.finish`): never handed to the parent. Two branches of the same
# wave both writing one of these in the same step would collide on the parent's single
# channel (`InvalidUpdateError`) even when their values happen to agree, exactly like
# `subtask_id` already could.
_BRANCH_LOCAL = {"subtask_id", "review_kind", "presubmit_checks"}


def delta(before: TaskState, after: TaskState) -> TaskState:
    """The patch that turns `before` into `after` through the reducers:
    `apply(before, delta(before, after))` is `after` again.

    A pipeline of nodes runs over a copy of the state and ends holding the whole
    state, not what it added. Handing that back to the parent would fold it in a
    second time: `cost_usd` counted twice, every `runs`, `failures` and
    `change_history` entry duplicated. `_BRANCH_LOCAL` never travels back: `subtask_id`
    says which branch ran, and `review_kind`/`presubmit_checks` are measurement-only
    keys already read by the branch's own node; the parent has as many answers as
    branches for any of them.
    """
    patch: dict = {}
    for key, value in after.items():
        if key in _BRANCH_LOCAL:
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
