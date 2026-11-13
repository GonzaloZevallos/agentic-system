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
from typing import (
    Annotated,
    Any,
    Final,
    Literal,
    NotRequired,
    TypedDict,
    get_args,
    get_origin,
    get_type_hints,
)

from .ci_push import PushKind
from .landing_order import YieldReason
from .lead_time import LeadTime
from .plan_route import CriticState, Route
from .presubmit import FAILED_IDS_MAX, BudgetBreach, CheckResult, FlakeGate, RedCheck
from .reaping import ReapedProcess
from .size_budget import ActualSize, PlanSize
from .spec_delivery import SpecContract
from .task_path import Path as TaskPath
from .task_path import PathCause, PathLabel, PromoteRequest, Promotion
from .task_path import Size as TaskSize
from .tracker_items import TrackerItem

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
    # A task whose plan did not fit (ADR-0054): it built nothing and opened chained issues
    # instead, one per part. An end like `discarded`, with no pull request.
    "split",
    # A task whose plan is only work on the issue tracker (GH-336): the checklist is posted
    # for the human and the run ends with no pull request. An end like `split`.
    "tracker_only",
    # An evaluation run that reached the end of its road (GH-470, part 2): the graph sends a
    # task carrying `evaluation` here instead of `deliver`. An end like `tracker_only`, with
    # no pull request and no tracker write.
    "evaluated",
]
WAITING_ON_HUMAN: Status = "waiting_on_human"
# The system's own subtask: the integrator joins the others on the task branch
# (ADR-0011). Only when there is more than one.
INTEGRATION = "integration"
SubtaskStatus = Literal["pending", "delivered", "approved", "returned"]
# Who returned a subtask (ADR-0040): `review`, the default every reader's `.get` falls
# back to, including every document written before this key existed; `final_validation`
# only while PM's early, UI-only final validation is the open return -- `review.py`
# resets it to `review` the moment it reviews the subtask again, so a second, ordinary
# return never misreads as PM's. `qa_performance` (GH-318, ADR-0060) is the same for the
# system's performance node: the open return only while that node's verdict is it, and so is
# `full_checks` (GH-358) for the node that runs the whole suite once on the integrated head.
ReturnedBy = Literal["review", "final_validation", "qa_performance", "full_checks"]
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
# What a text finding is about (the Tech Writer's review): `copy-unwritten` is a person-facing
# text in the diff that Tech Writer never wrote, `copy-criteria-written` one it wrote that
# breaks the product's writing criteria, `copy-criteria-changed` one the Developer changed
# and that breaks them. Never a `FindingCategory`: the Tech Lead's review cannot return one.
CopyCategory = Literal["copy-unwritten", "copy-criteria-written", "copy-criteria-changed"]
# How much a finding weighs (ADR-0016): only a `blocking` one returns the subtask, and
# only one the branch under review can fix; anything wider is the plan's.
Severity = Literal["blocking", "important", "observation"]
FindingScope = Literal["subtask", "plan", "outside"]
# Where a finding stands in the findings table: `open` until a later review of its
# subtask stops raising it (`fixed`), or `dismissed` when nobody can check it.
FindingStatus = Literal["open", "fixed", "dismissed"]
# A return's category: a review's takes its first blocking finding's; PM's returns of
# the definition, whose reason stays free text, are all `definition`.
ChangeCategory = FindingCategory | CopyCategory | Literal["definition"]
# Why an attempt failed, decided at the raise site, never by parsing the error's text
# (README.md, 14; the Medición glossary's `agentic.role.failures`): `schema` is a
# structured output missing or invalid against the role's own schema, `check` a use
# case's own rule over otherwise-valid output, `max_turns` the harness cutting the
# model off, `limit` a per-attempt money cap (issue #66), `account_limit` an HTTP 429
# from the account's own quota — session, spend or rate — not our per-attempt cap,
# `upstream` an HTTP 5xx from the provider's own server, `network` an `api_error` with
# no HTTP status at all — the network down (`EAI_AGAIN`) or the same shape, never read
# from the prose (issue #137) — `sdk` the harness breaking in a way none of the named
# facts covers (narrowed by `account_limit`, `upstream` and `network`, ADR-0029), `other`
# the residual nothing else names, `stall` an attempt that hung with nothing to wait for
# and was killed by the watchdog (`domain/stall.py`, ADR-0057): a reason of its own, not
# an external one, so the poller never retakes it by itself, `disk_full` an attempt that
# broke with the disk it writes to full, derived from what was measured (free bytes and
# inodes, an `ENOSPC` caught as structured data), never from the error's text (GH-391,
# ADR-0069): external, because the machine, not the role, failed, `render` a role prompt
# that could not be made (a placeholder nobody fills, a file the run's snapshot could not
# read; GH-409): our own bug, never retried and never charged, so not external.
FailureReason = Literal[
    "schema",
    "check",
    "max_turns",
    "limit",
    "account_limit",
    "upstream",
    "network",
    "disk_full",
    "sdk",
    "stall",
    "render",
    "other",
]
# What the Landing Diagnoser says about an approved pull request the landing could not land
# (ADR-0048): `sync` (the work lands once master is merged into its branch) or `unlandable`
# (the approved work itself cannot land). Closed, and defined only here: every reader and writer
# of a `diagnose` entry's `verdict` imports these and writes no literal.
DiagnosisVerdict = Literal["sync", "unlandable"]
VERDICT_SYNC: DiagnosisVerdict = "sync"
VERDICT_UNLANDABLE: DiagnosisVerdict = "unlandable"
# The keys of a `diagnose` entry that carry the verdict and the master sha it was made against,
# defined once for the same reason (they are also `LandingEntry` fields).
VERDICT_KEY: Final = "verdict"
UPSTREAM_KEY: Final = "upstream"
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
# cap) is not here: it stops one attempt, not the task, so it is a `FailureReason`. An
# account limit (HTTP 429, `FailureReason`'s `account_limit`) lands here too, as
# `failure`: it still pauses the task like any other role failure (ADR-0029); only the
# reason series tells the two apart. `system_delivery` (GH-358) is `deliver` refusing twice, on
# the same head, to push a tiered product's branch without a passed `full_checks` record: the
# system's own plumbing failing to deliver, not a role and not a return. `ci` (GH-462) is a
# `full_checks` of a product that declares `ci:` for which GitHub gave no verdict (`CiCause`
# says why): the task stops for the human instead of running the suite on this machine.
EscalationClass = Literal[
    "classification",
    "returns",
    "failure",
    "cap",
    "questions",
    "unmergeable",
    "setup",
    "estimate",
    "system_delivery",
    "ci",
]
# The causes of an escalation that need no judgment (GH-593), decided at the raise site from the
# error's class and recorded type, never from its message text: `ci_*` a `full_checks` whose CI
# gave no verdict (`CiCause` `gh_unavailable`, `gh_unreadable`, `run_unfinished`), `delivery_
# transient` a `system_delivery` whose push failed on a transient error. Closed, and written
# only through `rules.escalation(..., cause=...)`; the poller retakes them by themselves.
MechanicalCause = Literal[
    "ci_gh_unavailable",
    "ci_gh_unreadable",
    "ci_run_unfinished",
    "delivery_transient",
]
# What the poller's calendar and cap are keyed by (`rules.auto_resumable`): a failed attempt's
# `FailureReason` or an escalation's `MechanicalCause`. One union, one place that maps either
# to a calendar (`rules.schedule_for`) or a cap (`rules.auto_resume_limit`).
ResumeKey = FailureReason | MechanicalCause


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
    `parent_count` is what tells a merge commit apart from an ordinary one -- two or
    more parents, whatever its title -- since a hand-written headline (a merge that
    resolved a conflict, say) can read just like a plain commit's; `message_headline`
    is data only, never a criterion; `additions`/`deletions` are this commit's own
    line counts."""

    author_emails: tuple[str, ...]
    message_headline: str
    additions: int
    deletions: int
    parent_count: int


@dataclass(frozen=True)
class PullRequest:
    """The facts of a task's own pull request, as `Tracker.pull_request` hands them
    back over the `gh` CLI: a value object over a port boundary, so the port can hand
    facts to `measurement/domain/metrics.py`'s pure rules without either side
    learning the other's technology. The only type describing a pull request's
    facts -- nobody declares a second, and every consumer imports this one.
    `merged_at` is `None` for a pull request that never merged (open or closed
    unmerged); `commits` is every commit on the branch, oldest first;
    `merge_commit` is the sha GitHub says the merge produced on the base branch, `None`
    when it never merged (ADR-0038). `head_oid` is the sha of the pull request's final
    head commit, `None` when `gh` did not say: the exact commit an automatic merge is
    decided on and `Tracker.merge` is pinned to, and what `delivery_outcome` compares
    the document's `auto_merge_head` against (ADR-0045). `mergeable` is GitHub's own reading
    of whether the head merges into the base (`None` when `gh` said nothing recognised) and
    `base_oid` the base tip that reading was made against (GH-326): only
    `rules.merge_conflicts_from` turns them into a fact."""

    state: Literal["OPEN", "MERGED", "CLOSED"]
    created_at: str
    merged_at: str | None
    commits: tuple[PullRequestCommit, ...]
    merge_commit: str | None = None
    head_oid: str | None = None
    mergeable: Literal["MERGEABLE", "CONFLICTING", "UNKNOWN"] | None = None
    base_oid: str | None = None


def add(current: float, new: float) -> float:
    return current + new


def last[T](current: T, new: T) -> T:
    """The latest write wins. Developers running in parallel each stamp the role and
    the status; without a reducer the graph refuses two writes in one step."""
    return new


def merge[T](current: dict[str, T] | None, update: dict[str, T] | None) -> dict[str, T]:
    return {**(current or {}), **(update or {})}


def first[T](current: dict[str, T] | None, update: dict[str, T] | None) -> dict[str, T]:
    """The earliest write per key wins: a key already there is never overwritten, a new
    key is added. For a value that is a fact about the first time something happened."""
    return {**(update or {}), **(current or {})}


class FlakeRecord(TypedDict):
    """One flake of a task (ADR-0066): a check that went red and then green on its one rerun,
    at a head, seen by a gate. `tests` are the ids `failed_ids` parsed from the first failure,
    empty when it named none. No other free text: it feeds a tracker comment."""

    check: str
    head: str
    gate: FlakeGate
    tests: list[str]
    # ISO 8601 UTC instant the gate recorded it (GH-464): what the issue threshold counts. A
    # record from before it has none and does not count.
    at: NotRequired[str]


def _flake_key(record: FlakeRecord) -> tuple[str, str, str]:
    return (record["check"], record["head"], record["gate"])


def add_flakes(existing: list[FlakeRecord], new: list[FlakeRecord]) -> list[FlakeRecord]:
    """The flakes of `existing` and `new` as one list: one record per `(check, head, gate)`,
    its `tests` the two sides' ids as a sorted set capped at `FAILED_IDS_MAX` (sorted, because
    first-seen order would differ with the side that arrives first), the output sorted by key.
    When records of one key carry `at`, the earliest is kept, for the same reason.
    Idempotent and commutative, so parallel branches that saw the same flake merge to one
    record, whichever lands first. The only way `flakes` is ever written."""
    joined: dict[tuple[str, str, str], FlakeRecord] = {}
    for record in (*existing, *new):
        key = _flake_key(record)
        if key not in joined:
            joined[key] = {**record, "tests": []}
        at, held = record.get("at"), joined[key].get("at")
        if at is not None and (held is None or at < held):
            joined[key]["at"] = at
        known = joined[key]["tests"]
        known.extend(t for t in record["tests"] if t not in known)
    for record in joined.values():
        record["tests"] = sorted(record["tests"])[:FAILED_IDS_MAX]
    return [joined[key] for key in sorted(joined)]


SpecEscalationCause = Literal["contradiction", "conflict", "data_loss"]
ASSUMPTION_MAX_CHARS = 240


class AssumptionRow(TypedDict):
    """One delivery assumption (GH-594): the role that made it and its canonical text."""

    role: str
    text: str


def assumption_text(raw: str) -> str | None:
    """The canonical text of a delivery assumption, or `None` when `raw` is not one: it holds a
    line break, or its whitespace-collapsed form is empty or longer than 240 characters. The
    only place the rule is written: the reducer, the document loader and the pull request body
    renderer all call it."""
    if "\n" in raw or "\r" in raw:
        return None
    text = " ".join(raw.split())
    return text if 0 < len(text) <= ASSUMPTION_MAX_CHARS else None


def add_assumptions(existing: list[AssumptionRow], new: list[AssumptionRow]) -> list[AssumptionRow]:
    """The assumptions of `existing` and `new` as one list in order, one row per canonical text
    (the first row and its role kept). A row `assumption_text` refuses raises `ValueError`: a
    writer that sends one has a bug, unlike a hand-edited document, whose loader drops it."""
    joined: dict[str, AssumptionRow] = {}
    for item in (*existing, *new):
        text = assumption_text(item["text"])
        if text is None:
            raise ValueError("assumption text must be 1-240 characters on one line")
        joined.setdefault(text, {"role": item["role"], "text": text})
    return list(joined.values())


TrackerTarget = Literal["comment", "label"]
TrackerFailureClass = Literal["timed_out", "unreachable", "transient", "rate_limited", "refused"]
TRACKER_FAILURES_MAX = 10


@dataclass(frozen=True)
class TrackerFailure:
    """A best-effort tracker write that did not land (GH-389): what it was aimed at, the
    closed class of the failure and when. Never `gh`'s text: the class comes from the type
    of the error and nothing else is kept."""

    target: TrackerTarget
    cls: TrackerFailureClass
    at: str

    def record(self) -> TrackerFailureRecord:
        return {"target": self.target, "cls": self.cls, "at": self.at}


class TrackerFailureRecord(TypedDict):
    """One row of `tracker_failures` as the document keeps it: `TrackerFailure`'s fields."""

    target: TrackerTarget
    cls: TrackerFailureClass
    at: str


def add_tracker_failures(
    existing: list[TrackerFailureRecord], new: list[TrackerFailureRecord]
) -> list[TrackerFailureRecord]:
    """The failures of `existing` and `new` as one list: one row per `(target, cls)`, the
    first seen keeping its `at`, at most `TRACKER_FAILURES_MAX` rows (the oldest stay). The
    only way `tracker_failures` is ever written."""
    joined: dict[tuple[str, str], TrackerFailureRecord] = {}
    for record in (*existing, *new):
        joined.setdefault((record["target"], record["cls"]), record)
    return list(joined.values())[:TRACKER_FAILURES_MAX]


HEAD_YIELDS_MAX = 10


class HeadYield(TypedDict):
    """One time a member gave up its turn as head (GH-621): when, on which pull request head
    sha, and why."""

    at: str
    head: str
    reason: YieldReason


def add_head_yield(existing: list[HeadYield], new: HeadYield) -> list[HeadYield]:
    """`existing` with `new` appended, at most `HEAD_YIELDS_MAX` rows. The opposite of
    `add_tracker_failures`: the newest rows stay, the oldest are dropped."""
    return [*existing, new][-HEAD_YIELDS_MAX:]


class TrackerActionDone(TypedDict):
    """The record of one typed tracker item, keyed by `tracker_items.item_hash` (GH-620): `at`
    when it was raised, `kind` what the system did (a system item, GH-689), `url` where (when it
    has one) and `answered` when the human did."""

    at: str
    kind: NotRequired[str]
    url: NotRequired[str]
    answered: NotRequired[str]


def add_tracker_actions(
    existing: dict[str, TrackerActionDone] | None, new: dict[str, TrackerActionDone] | None
) -> dict[str, TrackerActionDone]:
    """The records of `existing` and `new` as one, by hash. A hash already there keeps its `at`
    and `url` for good; the only thing a later write adds is a missing `answered`. The only way
    `tracker_actions_done` is ever written."""
    joined: dict[str, TrackerActionDone] = dict(existing or {})
    for key, record in (new or {}).items():
        held = joined.get(key)
        if held is None:
            joined[key] = record
        elif "answered" not in held and "answered" in record:
            joined[key] = {**held, "answered": record["answered"]}
    return joined


class Restoration(TypedDict):
    """One `agentic restore` of an archived, approved task (GH-406, ADR-0048 addendum):
    `archive` the prefix it came from, `head` the approved head it put back, `upstream` the
    base sha the pull request had then, `at` when, and `landing_cut` the length of the
    `landing` list at that moment -- the entries before it belong to the run that was
    archived, and `rules.live_landing` hides them. The record lives beside `landing`, never
    inside it."""

    archive: str
    head: str
    upstream: str
    at: str
    landing_cut: int


def add_restorations(existing: list[Restoration], new: list[Restoration]) -> list[Restoration]:
    """The restorations of `existing` and `new` as one list: append-only, one row per
    `archive` (a retry of the same restore adds nothing), the first seen kept whole. The
    only way `restorations` is ever written."""
    joined: dict[str, Restoration] = {}
    for row in (*existing, *new):
        joined.setdefault(row["archive"], row)
    return list(joined.values())


ShadowStatus = Literal["ran", "late", "unavailable", "skipped"]
ShadowCause = Literal["failure", "cap", "late", "setup", "no_checkout", "single_unit"]


class ShadowRecord(TypedDict):
    """What one shadow review of a subtask left (GH-473, `review_mode` `single`): `head` the
    subtask head it read, `status` how it ended, `cause` why it did not run to its end (`None`
    when it ran), `findings` what it raised, `cost_usd` what it spent (the observed part when
    it was late) and `changed_lines` the size of the subtask's diff, when known. Never read
    by a role and never folded into `findings`: the record only measures what the single
    review misses."""

    subtask: str
    head: str
    status: ShadowStatus
    cause: ShadowCause | None
    findings: list[FindingRecord]
    cost_usd: float
    changed_lines: int | None


def add_shadow_reviews(existing: list[ShadowRecord], new: list[ShadowRecord]) -> list[ShadowRecord]:
    """The shadow records of `existing` and `new` as one list: append-only, one row per
    `subtask` (a rerun that finds a record adds nothing), the first seen kept whole. The only
    way `shadow_reviews` is ever written."""
    joined: dict[str, ShadowRecord] = {}
    for row in (*existing, *new):
        joined.setdefault(row["subtask"], row)
    return list(joined.values())


ReviewScope = Literal["carried", "resolution", "full"]
ScopeCause = Literal[
    "first",
    "no_merge",
    "clean",
    "resolved",
    "rewritten",
    "own_before_merge",
    "foreign_merge",
    "unreadable",
    "no_remerge_diff",
    "over_cap",
    "renumber",
]


class ReviewScopeRecord(TypedDict):
    """What one review pass of a unit covered (GH-589, ADR-0089): `unit` the subtask, `head`
    the head the pass moved `reviewed_head` to, `base` the accepted tip it was measured from
    (`None` for a first review), `scope` carried / resolution / full and `cause` the single
    condition of `landing.review_scope` that gave it, `at` the ISO 8601 UTC instant. Written
    only through `landing.scope_patch`; never read by a verdict, a gate or a landing step."""

    unit: str
    head: str
    base: str | None
    scope: ReviewScope
    cause: ScopeCause
    at: str


def add_review_scopes(
    existing: list[ReviewScopeRecord], new: list[ReviewScopeRecord]
) -> list[ReviewScopeRecord]:
    """The review scopes of `existing` and `new` as one list: append-only, one row per
    `(unit, head, base)` (a retried write adds nothing), the first seen kept whole, in
    arrival order. The only way `review_scopes` is ever written."""
    joined: dict[tuple[str, str, str | None], ReviewScopeRecord] = {}
    for row in (*existing, *new):
        joined.setdefault((row["unit"], row["head"], row["base"]), row)
    return list(joined.values())


class ArmAssignment(TypedDict):
    """`TaskState.assignment`: the arm a product's experiment gave the task (GH-470)."""

    experiment: str
    arm: str
    switches: dict[str, str]


class PlanRoute(TypedDict):
    """`TaskState.plan_route`: which stage of planning the task took and why (GH-472,
    ADR-0086). Written whenever the sizer ran, on every arm of the experiment, so the control
    is measured on the same scale; `pre_estimate_usd` and `files` are `None` when the sizer
    failed. `critic` says whether the critic `ran`, `failed` or was `skipped`."""

    pre_estimate_usd: float | None
    files: int | None
    threshold_usd: float
    route: Route
    misrouted: bool
    critic: CriticState
    # True when a promotion re-planned the task (GH-591): the plan revised, no new interview.
    after_promotion: NotRequired[bool]


class EvaluationRef(TypedDict):
    """`TaskState.evaluation`: what marks a task as an evaluation run (GH-470) -- the issue
    it re-runs, the commit it runs from, and that its full check always runs locally. The
    key's presence is the marker (`measurement.domain.evaluation.is_evaluation`); whether a
    process is wired as one is decided by its root, never by this."""

    issue: int
    base: str
    ci: Literal["local"]


class TaskInput(TypedDict):
    """What enters the graph. `id` is also the thread id (ADR-0003)."""

    id: str
    title: str
    description: str
    # Whether the request carried a `## Decided` heading when the task entered
    # (`rules.entering`, called only from `cli._run`), so its interviews run one round
    # (`rules.interview_cap`; ADR-0014, addendum of 2026-10-06). Declared here because the
    # graph's `input_schema` keeps only declared keys. Absent reads as not decided.
    decided: NotRequired[bool]
    # What a landing restart carries over (GH-239, ADR-0048): `rules.entering` sets them from
    # the archived run, read before `archive_previous_run` moved it. Declared here for the
    # same reason as `decided`. Absent reads as a first run: depth 0, nothing carried.
    landing_restarts: NotRequired[int]
    landing_cost_carried: NotRequired[float]
    # The `Human` section of the archived run, seeded verbatim by `rules.entering` so every
    # decision the human took survives a landing restart (GH-297). Only that key is ever
    # seeded; absent reads as a run with no section. Declared for the same reason as `decided`.
    sections: NotRequired[dict[str, str]]
    # The informative tracker writes that failed before the graph started (GH-389): the
    # archive notice of `cli._run`, entering through the reducer `add_tracker_failures`.
    # Declared for the same reason as `decided`. Absent reads as none.
    tracker_failures: NotRequired[list[TrackerFailureRecord]]
    # The arm the product's experiment gave the task (GH-470): `cli._run` sets it from
    # `--arm`, or carries it on `--fresh`. Declared for the same reason as `decided`: without
    # it the graph's `input_schema` drops the key and the arm never reaches the state.
    # Absent reads as unassigned.
    assignment: NotRequired[ArmAssignment]
    # What marks an evaluation run (GH-470): `FileInbox` sets it from the frozen entry.
    # Declared for the same reason as `decided`. Absent reads as a real task.
    evaluation: NotRequired[EvaluationRef]


@dataclass(frozen=True)
class LandedSliceRef:
    """A slice the landing already merged: the units it carried and its pull request."""

    units: tuple[str, ...]
    pull_request: str


@dataclass(frozen=True)
class Carried:
    """What a landing restart carries from the archived run into the fresh one (GH-297):
    the depth and cost (`restarts`, `cost`), the Landing Diagnoser's section (`diagnosis`),
    every human decision (`human`, the `Human` section verbatim) and the slices already
    landed. Built only by `landing.carry_over`, read by `rules.entering` and the CLI. Lives
    here, not in `landing`, because `rules` cannot import `landing`."""

    restarts: int
    cost: float
    diagnosis: str
    human: str
    landed: tuple[LandedSliceRef, ...]


class LandedSlice(TypedDict, total=False):
    """One slice of the task document's `landed_slices` list (GH-297), every key optional:
    `units` the subtask ids it carried, `pull_request` its pull request, `merged_sha` what
    master held after the merge and `at` when it was recorded. Git ancestry, never this
    list alone, says whether a unit landed."""

    units: list[str]
    pull_request: str
    merged_sha: str
    at: str


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
    # The turn cap this attempt ran with, from `rules.retry_turns` (GH-449): the one a
    # reader compares `main_thread_turns` against, never the role's own `max_turns`, which
    # the retry after a `max_turns` cut can exceed. Absent on a failure recorded before
    # this field existed, which reads as `role.max_turns`.
    max_turns: NotRequired[int]
    # The reset an `account_limit` error states, ISO-8601 with an offset, when its text
    # parses inside the session window (`account_reset.reset_at`; ADR-0039, addendum GH-381):
    # only a wait hint, never the reason. Absent on any other failure and on one recorded
    # before this field existed.
    resets_at: NotRequired[str]
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
    # addendum, revised by its 2026-09-22 addendum) fired during this attempt, off
    # `RawResult.turn_notice`/`AttemptFailed.turn_notice`. `False` for every role
    # `turn_notice.threshold_for` does not cover, which is every role but
    # `DEVELOPER`/`DEVELOPER_INTEGRATOR`. Absent on a failure recorded before this field
    # existed, which reads as `False`.
    turn_notice: NotRequired[bool]
    # How many times the notice fired during this attempt, off `RawResult.
    # turn_notice_firings`/`AttemptFailed.turn_notice_firings`: a fact beside
    # `turn_notice`, never replacing it (ADR-0008: added, never renamed). Absent on a
    # failure recorded before this field existed, which reads as `0`
    # (`run_role._run_notice_firings`'s own `.get(..., 0)`).
    turn_notice_firings: NotRequired[int]
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
    # `TurnCount.value` at the point this attempt broke, off `RawResult.main_thread_
    # turns`/`AttemptFailed.main_thread_turns` (`domain/turn_notice.py`; ADR-0031,
    # issue #117). Written at every attempt, cut as well as delivering, because a cut
    # is the population that can refute the counted-quantity assumption `domain/
    # turn_notice.py`'s docstring states. Absent on a failure recorded before this
    # field existed, which reads as `0`.
    main_thread_turns: NotRequired[int]
    # What the reaper stopped when the attempt ended (GH-282, ADR-0052): the processes a
    # role left running in the background, off `RawResult.reaped`/`AttemptFailed.reaped`.
    # Written only when something was reaped, so a document with no leak renders as it
    # always did; read with `.get("reaped", [])`.
    reaped: NotRequired[list[ReapedProcess]]
    # When this attempt began and when its `AttemptFailed` was caught, ISO-8601 UTC, stamped
    # by `run_role` from its injected clock. Absent on a failure recorded before these fields
    # existed, which reads as unknown (`None`), never as 0.
    started_at: NotRequired[str]
    ended_at: NotRequired[str]
    # The Developer's own check runs this attempt launched before it failed (`CheckRun`), the
    # last 50 only, and how many older ones were dropped. Absent when none was recorded.
    check_runs: NotRequired[list[CheckRun]]
    check_runs_dropped: NotRequired[int]


class CheckRun(TypedDict):
    """One Bash call of a Developer that ran a declared check (`domain/check_runs.py`).
    `command` is the matched segment only, scrubbed, at most 200 characters, never with a
    leading `NAME=value` assignment. `check` is the declared name on an exact match and `None`
    otherwise. `started_at` is ISO-8601 UTC. `duration_s: None` means the call never finished
    or was detached; a set `duration_s` with `exit_code: None` means the code is unknown (a
    pipe or a later chained segment decided it), never success."""

    command: str
    check: str | None
    started_at: str
    duration_s: float | None
    exit_code: int | None


class CiJobTiming(TypedDict):
    name: str
    duration_s: float


class CiTiming(TypedDict):
    """The instants a CI wait is derived from (`ci.waited_seconds`, the only place the wait is
    computed): when the head was `pushed_at`, when the node `node_started_at` waiting for it,
    and when the run that judged it started and ended (`None` when unknown). `verdict` is the
    one the gate decided; recording it changes no verdict. All instants ISO-8601 UTC."""

    pushed_at: str
    node_started_at: str
    run_started_at: str | None
    run_ended_at: str | None
    verdict: Literal["green", "red", "no_verdict"]
    jobs: NotRequired[list[CiJobTiming]]


class GateRecord(TypedDict, total=False):
    """One invocation of a check gate that ran its checks (GH-361, ADR-0070): the wait for a
    share of the machine's cores kept apart from the run. `started_at` is the UTC ISO time the
    gate asked for its share; `wait_s` what this call spent waiting for it and `run_s` what
    the witness and the checks took after the grant, so a check's `duration_s` stays pure run
    time; `share` the cores it was given, `peers` how many gates held a share beside it (a
    debugging aid, never the figure "other gates alongside", which `overlap_peers` computes
    from intervals), `priority` the closed `CheckPriority` value and `overdue` whether the
    wait ran out. Every key is optional on read (ADR-0041): a document
    written before this record carries none. No free text in any field."""

    started_at: str
    wait_s: float
    run_s: float
    share: int
    peers: int
    priority: str
    overdue: bool


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
    # The turn cap the delivering attempt ran with (`rules.retry_turns`, GH-449): the role's
    # own `max_turns`, or the higher one the attempt after a `max_turns` cut gets from
    # `RETRY_TURNS`. What `agentic.role.max_turns` reads, so a calibration can compare
    # `turns` against the budget it actually ran under. Absent on a run recorded before
    # this field existed; read with `.get()`, no sample emitted then.
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
    # One record per invocation of the Developer's delivery gate that ran the checks, in
    # order (GH-361): the wait for a share apart from the run. Absent on a run recorded
    # before this field existed, and on a run that never ran a gate.
    gates: NotRequired[list[GateRecord]]
    # Calls to the model in this run: one per `AssistantMessage` the runner saw before
    # the delivery (`ClaudeSdkRunner.run_once`'s own count). Absent on a run recorded
    # before this field existed.
    calls: NotRequired[int]
    # `tool_use` blocks of this run, by tool name; a name outside the known set counts
    # as `other`. Absent on a run recorded before this field existed.
    tool_calls: NotRequired[dict[str, int]]
    # The Developer's own check runs in the delivering attempt (`CheckRun`), the last 50 only,
    # how many older ones were dropped, and how many hook errors were swallowed recording
    # them. Each count is written only when above 0; absent on a run recorded before them.
    check_runs: NotRequired[list[CheckRun]]
    check_runs_dropped: NotRequired[int]
    check_runs_errors: NotRequired[int]
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
    # `RawResult.turn_notice` (`domain/turn_notice.py`; ADR-0004, seventh addendum,
    # revised by its 2026-09-22 addendum). The run's total, for the section note
    # (`turn_notice.notice_note`), also folds in every recorded `Failure.turn_notice`:
    # this field alone is only the delivering attempt's, the same split `denials`
    # already has. Absent on a run recorded before this field existed, which reads as
    # `False`.
    turn_notice: NotRequired[bool]
    # How many times the notice fired during the delivering attempt, off `RawResult.
    # turn_notice_firings`: a fact beside `turn_notice`, never replacing it (ADR-0008).
    # The run's total, what `notice_note`'s own `firings` argument reads, also folds in
    # every recorded `Failure.turn_notice_firings` (`run_role._run_notice_firings`).
    # Absent on a run recorded before this field existed, which reads as `0`.
    turn_notice_firings: NotRequired[int]
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
    # `TurnCount.value` at the end of the delivering attempt, off `RawResult.main_
    # thread_turns` (`domain/turn_notice.py`; ADR-0031, issue #117): the main-thread
    # `AssistantMessage` count the notice's positions are stated in, the first record
    # of that quantity kept past the attempt itself. `0` for a role the notice does not
    # cover. No run-total fold like `turn_notice_firings` has: this field, and its
    # `Failure.main_thread_turns` counterpart on every recorded failure, are each one
    # attempt's own count, since it is a single cut attempt's relation to its own
    # `max_turns` that can refute the counted-quantity assumption, not a sum across
    # retries. Absent on a run recorded before this field existed.
    main_thread_turns: NotRequired[int]
    # What the reaper stopped when the attempt ended (GH-282, ADR-0052): the processes a
    # role left running in the background, off `RawResult.reaped`/`AttemptFailed.reaped`.
    # Written only when something was reaped, so a document with no leak renders as it
    # always did; read with `.get("reaped", [])`.
    reaped: NotRequired[list[ReapedProcess]]
    # QA Performance's node verdict (GH-318, ADR-0060) and the full sha of the head it
    # measured: written only on that role's runs, so every existing run reads without them
    # (`.get`). The merge gate reads the pair; nothing else does.
    perf_verdict: NotRequired[Literal["approved", "returned", "unverified"]]
    perf_head: NotRequired[str]


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
    # `"landing"` on a return the landing made to reopen a delivered pull request (GH-239):
    # absent on every return a review made, so a document written before it reads the same.
    # It never enters `changes_requested`, which counts the pair's limit.
    by: NotRequired[Literal["landing"]]
    # `2` on a return of a restarted attempt (GH-593, `restart.attempt`): absent on a return of
    # the first attempt, so a document written before restarts reads every entry as attempt 1.
    attempt: NotRequired[int]


class EscalationPatch(TypedDict):
    """The two keys `rules.escalation` always writes together, spread into a bigger
    patch literal by its callers (`rules.unmergeable` and the setup-failure patch)."""

    escalated: str | None
    escalation_class: EscalationClass | None
    # The `MechanicalCause` of a mechanical pause, `None` for a judgment one (GH-593);
    # `rules.logged` copies it onto the `escalation` pause entry.
    escalation_cause: NotRequired[MechanicalCause | None]
    # The hashes of the typed tracker items a `questions` pause raised (GH-620), `[]` otherwise.
    tracker_human: NotRequired[list[str]]
    # Why a spec delivery's `questions` pause was raised (GH-594), written only with a spec.
    spec_cause: NotRequired[SpecEscalationCause | None]


class SlicesClosedPatch(TypedDict):
    """What `record_closure` writes to close a task its slices carried to master (GH-334):
    the time and the slices' pull requests. Never a `delivery_outcome`."""

    closed_at: str
    closed_by_slices: list[str]


class ReleasePatch(TypedDict, total=False):
    """What `landing.released` writes when a human's answer releases a landing escalation
    (GH-328): the keys of `unescalated` plus the epoch's `landing` entries and the `Human`
    section. Empty when there is nothing to release. A partial patch, so `apply` takes it
    as a mapping, not as a whole `TaskState`."""

    escalated: str | None
    escalation_class: EscalationClass | None
    status: Status
    pause_log: list[PauseEntry]
    landing: list[LandingEntry]
    sections: dict[str, str]
    # GH-463: the `merge_ci` pair a `ci_no_verdict` release stamps with `released_at`.
    merge_ci: list[MergeCiRecord]


class FinishedPatch(TypedDict):
    """The two keys `rules.finished` writes together, spread into a bigger patch literal."""

    status: Literal["discarded", "split", "tracker_only", "evaluated"]
    finished_at: str


class EvaluatedPatch(FinishedPatch, total=False):
    """What the `evaluated` node writes when an evaluation run ends (GH-470): the keys of
    `rules.finished` plus the role that closes it and the processes the release reaped. A
    partial patch, so `apply` takes it as a mapping, not as a whole `TaskState`."""

    current_role: str
    reaped_at_cleanup: list[ReapedProcess]


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
    category: FindingCategory | CopyCategory
    # `open` or `dismissed` as written; `fixed` is only ever derived.
    status: FindingStatus
    # The corrected text a copy row proposes (`domain.copy`); absent on every other row.
    proposed: NotRequired[str]
    # What the reviewer reported when the system lowered it (`lower_unbacked_blocks`); the key
    # is absent on a finding the system did not lower.
    reported_severity: NotRequired[Severity]
    # The checks only the pull request's CI can show, as the reviewer named them (set by
    # `lower_landing_blocks`); the key is absent unless the list is non-empty, and
    # `rules.landing_notes` is its one reader.
    landing_checks: NotRequired[list[str]]


class DesignItemRecord(TypedDict):
    """One remark from a lead's (UX Lead's or UI Lead's) walk or comparisons, or from
    PM's consolidation (ADR-0040), as `design_items` keeps it: the role that raised it
    (`Role.slug`), never derived from a section's prose (`rules.design_effect_by_role`'s
    own per-role count, the plan's own invariant: design items come from structured
    fields alone)."""

    role_slug: str
    kind: Literal["observation", "blocking"]
    step: str
    text: str


class Subtask(TypedDict, total=False):
    """A part of the task assigned to one Developer (README.md, 4). Lives in its own
    branch; the pair Tech Lead→Developer counts returns per subtask."""

    id: str
    title: str
    brief: str
    seams: list[str]
    depends_on: list[str]
    # The screens of the design (ADR-0040) this subtask builds, for a front subtask of
    # a `touches_ui` task; the Developer's brief names them (`plan_view._brief`).
    # Persisted only when non-empty; absent on every document written before this key
    # existed and on a subtask with no screen of its own -- every read is
    # `.get("screens", [])`.
    screens: list[str]
    status: SubtaskStatus
    branch: str
    # Commit the branch started from: the first review diffs from it.
    base: str
    # Head the last review reached: the next one diffs from it, not from `base`
    # (ADR-0015). Absent until the subtask has been reviewed once.
    reviewed_head: str
    # Where a carried verdict came from (GH-239, `landing.review_carries`): the head the
    # Tech Lead really reviewed, when `reviewed_head` advanced over a clean sync of master
    # without a new review. Absent on every subtask reviewed on the head it names.
    reviewed_head_carried_from: str
    # The last review's reason, what the Developer fixes on a return, and its
    # category (ADR-0016).
    reason: str
    change_category: ChangeCategory
    # Who returned this subtask (ADR-0040): absent, and read `.get("returned_by",
    # "review")`, on every document written before PM's early final validation existed,
    # and on every ordinary review return. `returned_developer_delta`'s own
    # `rules.return_section` reads it to find which section carries the delta.
    returned_by: ReturnedBy
    # The other verifiers that returned the same subtask in the same verifier set
    # (GH-469), in the fixed verifier order; `returned_by` is the first. Absent on every
    # document written before the set existed and on every return by one party alone:
    # `rules.returned_by_all` is the one reader of both fields.
    returned_with: list[ReturnedBy]
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
    # A promotion this subtask's branch asked for (GH-591, `task_path.record_request`): the
    # Developer, its review or the `risk_hits` net leave it here, in the branch's own key, and
    # only serialized system code applies it. Absent on a subtask that asked for nothing and on
    # every document written before this key existed -- every read is `.get("promote_request")`.
    promote_request: PromoteRequest
    # Set by `plan_revision.revise` on a final unit whose work was approved before a
    # promotion re-planned the task and whose brief did not change: its review is a full
    # one against the new PRD. Absent everywhere else.
    prd_recheck: bool
    # Set by the approval patch (`rules.approval_patch`) and never cleared by any patch (GH-593):
    # the subtask was once approved, so a restart cannot throw away reviewed-good work.
    approved_once: bool
    # `2` on a subtask a clean restart gave a new attempt (`restart.restart_patch`); absent on
    # the first attempt, read `.get("attempt", 1)`.
    attempt: int


# One entry of `TaskState.pause_log` (issue #137): the append-only register the
# poller's own eligibility reads, never `escalation_class` or any failure's `error`
# text. A discriminated union on `kind`, one `TypedDict` per shape, rather than one
# `TypedDict` with optional fields: `auto_resumable` reads a `FailurePauseEntry`'s own
# `value` as the `FailureReason` it always is, with nothing to narrow by hand (ADR-0021
# forbids a `cast` to make a wider field fit). `ResumedPauseEntry` (`rules.resumed`'s
# own patch) marks a fresh epoch boundary, with neither `value` nor `section`.
# `EscalationPauseEntry` carries the `EscalationClass` a patch's own `escalation_class`
# already named, composed by `rules.logged`. `FailurePauseEntry` carries one `Failure`'s
# own `reason` and `section`, one entry per failure `run_role.escalation` hands
# `rules.logged` as `exc.failures`. The *current epoch* (`rules.auto_resumable`'s own
# reading) is every entry after the last `ResumedPauseEntry`, or the whole log when
# there is none.


#
# Each of the three entries that mark a pause carries `at`, the ISO-8601 UTC instant it was
# written, passed in by the application layer and built only by the constructors in `rules`.
# Absent on an entry written before it existed, which reads as unknown; no reader of the
# epoch looks at it. A `lapse` carries none.


class ResumedPauseEntry(TypedDict):
    kind: Literal["resumed"]
    at: NotRequired[str]


class EscalationPauseEntry(TypedDict):
    kind: Literal["escalation"]
    value: EscalationClass
    at: NotRequired[str]
    # The CI timing of a `ci` escalation that wrote no `FullChecksRecord` (a `no_verdict` one).
    ci: NotRequired[CiTiming]
    # The mechanical cause the raise site knew (GH-593), from the error's class and recorded
    # type; absent when it was not known or the escalation is a judgment, which reads as one.
    cause: NotRequired[MechanicalCause]


class FailurePauseEntry(TypedDict):
    kind: Literal["failure"]
    value: FailureReason
    section: str
    at: NotRequired[str]
    # The failure's `resets_at`, copied by `rules.logged` only when it has one; absent on an
    # entry written before this field existed (`rules.auto_resumable` never requires it).
    resets_at: NotRequired[str]


class LapsePauseEntry(TypedDict):
    """A bookkeeping step that failed after the work was done (GH-342), as `step:
    ExceptionClass`. Not an attempt and not an escalation: no reader of the epoch counts it,
    so it never changes whether a pause is retaken."""

    kind: Literal["lapse"]
    value: str


PauseEntry = ResumedPauseEntry | EscalationPauseEntry | FailurePauseEntry | LapsePauseEntry


class SettledRow(TypedDict):
    """An approval's settlement (`rules.settled_on_approval`): the records of `subtask`
    at this `location` and `claim` with severity `blocking`, scope `subtask` and a
    `version` below this one are settled. Per record, never per folded key: a later
    record of the same key is not covered."""

    subtask: str
    location: str
    claim: str
    version: int


class PlanSettledRow(TypedDict):
    """A `PM: final validation` settlement (`plan_settlement`, GH-374): the records at this
    `location` and `claim` (normalised) with a `version` below this one are settled. `by` says
    which run decided it: `approved` (PM approved at this version) or `superseded` (a returning
    PM round did not raise the key again). Per record, never per folded key."""

    location: str
    claim: str
    version: int
    by: Literal["approved", "superseded"]


RenumberMode = Literal["number", "dated"]
RenumberOutcome = Literal["renamed", "declined", "retry"]
RenumberCause = Literal["slug_taken", "unrewritable", "generate_failed", "unreadable"]
"""How a `renumber` entry ended on a product that names its ADRs by date (GH-611), closed:
`mode` says which rename it was (`number` is the renumbering of always). `renamed` made the
rename; `declined` is final on that head (`slug_taken`: a slug is already an ADR's;
`unrewritable`: a reference cannot be rewritten without guessing) and the next round asks the
owner; `retry` is transient (`generate_failed`, `unreadable`): it counts toward the renumber cap
but the next round tries again."""

AdrDecisionOutcome = Literal[
    "applied",
    "stale_moves",
    "out_of_bounds",
    "role_failed",
    "checks_red",
    "checks_timeout",
    "still_colliding",
    "git_failed",
    "no_collision",
    "ambiguous",
]
"""How an `apply_adr_decision` entry ended (GH-544), closed; `landing` re-exports it:
`applied` (a new head was pushed),
`stale_moves` (master moved, or the rule now picks other numbers than the question named; GH-582:
never any other cause),
`out_of_bounds` (the pass edited a line that is not a spot), `role_failed` (the Developer pass
failed), `checks_red` (the product's checks failed on the result) and `still_colliding` (the
result still holds a number master took). GH-582 names the refusals that used to read as
`stale_moves`: `git_failed` (a git read, commit or push failed; nothing was pushed),
`no_collision` (the head no longer collides with master) and `ambiguous` (the collision is one
the rule cannot decide even with an owner's decision). GH-585 adds `checks_timeout` (a check of
the gate timed out and no check was red: nothing was pushed). Every one but `applied` and
`no_collision` asks again; `git_failed`, `no_collision`, `ambiguous` and `checks_timeout` spend
no decision slot, and neither does a `checks_red` with no `gate_tier`
(`landing.adr_decisions_spent`)."""


AdrDecidedBy = Literal["rule"]
"""Who decided an `apply_adr_decision` entry (D-be76583a), closed: `rule`, the numbering rule on its
own, with a synthesized "keep the rule's moves" decision. An entry that answers an owner's decision
carries no `decided_by` and a `decision_at` instead; `landing` re-exports it."""


class LandingEntry(TypedDict, total=False):
    """One landing step of the task document's `landing` list (GH-239): the idempotency
    key is `(head, step)`; `refusal` is the `MergeRefusal` it answered, `at` when it was
    written, `cost_usd` what it spent and `resolved_at` when the re-delivery closed it.
    `resume_spawns` counts the resume children spawned for the entry and `resume_spawned_at`
    says when the last one was (`landing.resume_owed` reads both)."""

    head: str
    step: str
    refusal: str
    at: str
    cost_usd: float
    resolved_at: str
    # The task's `cost_usd` when a reopening step was written: the re-delivery subtracts it
    # from its own to get this entry's `cost_usd`.
    cost_base: float
    checks: list[str]
    # Resume children actually spawned for this entry, and when the last one was.
    resume_spawns: int
    resume_spawned_at: str
    # True on the `fix_important` entry the pass writes before the pull request exists
    # (GH-354): no reader that assumes a pull request treats such an entry as one
    # (`rules.before_pr`). Absent everywhere else.
    before_pr: NotRequired[bool]
    # Diagnose entries only (GH-257): `delivered` is written before anything else once the
    # Landing Diagnoser delivered; `failure` is the reason an attempt that did not deliver
    # failed with, which the retry calendar reads. Both optional: a document without them
    # reads and renders as before.
    delivered: bool
    failure: FailureReason
    # GH-328, `diagnose` entries: when a human's decision released the escalation this entry's
    # failure led to (`landing.released`). The retry calendar counts attempts after it.
    released_at: str
    # GH-442, `escalate` entries: the upstream sha whose CI release (`landing.released_by_ci`)
    # was recorded for this entry's head, with `released_at` its time. Absent until then.
    ci_released: str
    # GH-463, `escalate` entries answering `ci_no_verdict`: why GitHub gave no verdict, from the
    # closed `CiCause`; absent when the cause could not be read.
    cause: CiCause | RenumberCause
    # GH-611, `renumber` entries: which rename it was and how it ended (`RenumberOutcome`, stored
    # in `outcome` beside the `AdrDecisionOutcome`s); `escalate_adr` entries: the upstream sha at
    # which a numeric question was released because the product names its ADRs by date
    # (`landing.released_by_naming`), with `released_at` its time.
    mode: RenumberMode
    naming_released: str
    # GH-267, all optional: a document without them reads and renders as before.
    # `upstream` is the master sha a `sync_master` entry was dispatched for; on a `diagnose`
    # entry it is the master sha the diagnosis was made against.
    upstream: str
    # `diagnose` entries: what the diagnoser concluded (`DiagnosisVerdict`). A delivered
    # entry without one reads as `unlandable`.
    verdict: DiagnosisVerdict
    # When a pass was closed without succeeding (`landing.close_unsynced`).
    closed_at: str
    # The upstream sha the delivered head did not contain.
    lacks: str
    # GH-297, all optional: a document without them reads and renders as before.
    # `land_slice` entries: the units of the slice and its (never main) pull request.
    slice_units: list[str]
    slice_pull_request: str
    # `land_slice` entries: whether the product's checks passed on the slice, so a CI wait
    # across ticks does not run them again. `upstream` is the master the slice was built on.
    checks_passed: bool
    # `diagnose` entries: the `landing.RestartWhy` that no slice was possible for.
    restart_why: str
    # `diagnose` entries: the integrator's paths kept every slice from landing (the restart
    # comment's `(integration work)`).
    integration_work: bool
    # GH-449, `diagnose` entries: what reading each red job's CI log gave, `{job, bytes, cause}`
    # with `cause` from the closed `ci.JobLogCause`. Never the text of the log.
    ci_logs: list[CiLogRecord]
    # GH-544, `apply_adr_decision` entries, all optional (an entry written before reads as it
    # did): the `decision_at` (the `released_at` of the `escalate_adr` it answers), the closed
    # `landing.AdrDecisionOutcome`, the `[old, new]` path pairs the rule recomputed and the head
    # pushed. `escalate_adr` entries never carry them.
    decision_at: str
    # D-be76583a, an `apply_adr_decision` entry the system took on its own (no `decision_at`).
    decided_by: AdrDecidedBy
    outcome: AdrDecisionOutcome | RenumberOutcome
    moves: list[list[str]]
    head_after: str
    # GH-570, an `apply_adr_decision` entry that ended `checks_red`, both optional: which checks
    # were red (`presubmit.red_checks`, sanitized there, at most `RED_CHECKS_MAX`) and how many
    # more were left out. Never the raw log; absent on an entry written before.
    red_checks: list[RedCheck]
    red_more: int
    # GH-585, an `apply_adr_decision` entry whose run built the gate: which checks it ran,
    # `delivery` (the product's `delivery_checks`) or `all` (the product declares none). Written by
    # `landing_adr._finish`, read by `landing.adr_decisions_spent` alone. A `str`, not the closed
    # pair, because a value a hand edit left unreadable must still read as present.
    gate_tier: str
    # GH-576, an `apply_adr_decision` entry: the `landing.ADR_EDIT_BOUNDS` it ran under (absent
    # reads as 1), and, when it ended `out_of_bounds`, the `{path, line}` rows that were out
    # (`landing.out_of_bounds_detail`, paths cleaned there, never the text of a line) and how
    # many more were left out.
    bounds: int
    out_of_bounds_lines: list[OutOfBoundsLine]
    out_of_bounds_more: int
    # GH-610, `escalate_api_break` entries: the reason the owner's answer gave
    # (`api_break_decided`), why an answer was refused (`rejected`, a closed
    # `pull_request.RejectReason`) and how many times the question was published again (`reasks`).
    api_break_decided: str
    rejected: str
    reasks: int


class OutOfBoundsLine(TypedDict):
    """One line an `apply_adr_decision` pass edited outside its bounds, as a `landing` entry keeps
    it (GH-576): the cleaned path and the line (0, the whole file). Never the text of the line."""

    path: str
    line: int


JobLogCause = Literal["read", "unreadable", "empty", "other_head", "timed_out"]


class CiLogRecord(TypedDict):
    """What reading one red job's CI log gave, as a `diagnose` entry keeps it (GH-449): the
    job, the bytes read and the closed cause. Never the text of the log."""

    job: str
    bytes: int
    cause: JobLogCause


class EstimateRecord(TypedDict):
    """The plan's estimate as the document keeps it (ADR-0054)."""

    cost_usd: float
    files: int
    # The Tech Lead's own figure, written only when the system raised `cost_usd` above it.
    declared_usd: NotRequired[float]


class EstimateRevision(TypedDict):
    """One estimate a human decided on: `human` is what they wrote, `proposed` the one the
    stopped task proposed and they accepted."""

    cost_usd: float
    source: Literal["human", "proposed"]


class PathRevision(TypedDict):
    """One revision of the plan's `required_paths` after a human decision (GH-538): `revises`
    is the plan's list at that moment, `paths` the list the review put in its place,
    `decision` the `### Decision N` it cites, `reason` the quote, `at` the ISO instant."""

    revises: list[str]
    paths: list[str]
    decision: int
    reason: str
    at: str


class TrackerPosted(TypedDict):
    """The record that the tracker checklist was posted (GH-336): `at` the ISO time and the
    `hash` of the items (`rules.tracker_hash`)."""

    at: str
    hash: str


class PartRecord(TypedDict):
    """One part of a split plan as the document keeps it (ADR-0054): the issue the
    partition opens. `depends_on` holds the numbers (1 is the first) of earlier parts.
    `depth` is how deep the part sits in the split (GH-592): 1 for a part of a task that is
    not a part, 2 for a part of a part. Absent on a record written before parts could split
    again, which reads as 1. `kind` (`change` or `migration`) and `guard` (how unfinished
    behaviour stays unreachable, empty for none) are absent on a record written before them
    (GH-592)."""

    title: str
    body: str
    depends_on: list[int]
    depth: NotRequired[int]
    kind: NotRequired[Literal["change", "migration"]]
    guard: NotRequired[str]


# Why a `full_checks` run that wanted GitHub CI ran the local full tier instead (GH-376): a
# closed value decided by the system, never text parsed from `gh`. Lives here, not in `ci`,
# because the record below needs it and `ci` reaches this module through `rules`; `ci`
# re-exports it.
FallbackCause = Literal[
    "gh_unavailable", "gh_unreadable", "no_run", "publish_failed", "not_started", "untrusted"
]

# Why GitHub CI gave a `full_checks` of a `ci:` product no verdict (GH-462): the same closed
# values, chosen from the step that failed, plus those of the dispatched run. `untrusted` is
# not one: it is no failure of the CI but a head the product's own workflow cannot judge.
CiCause = Literal[
    "gh_unavailable",
    "gh_unreadable",
    "no_run",
    "publish_failed",
    "not_started",
    "dispatch_refused",
    "jobs_missing",
    "unknown_names",
    "checkout_failed",
    "run_unfinished",
    "ghost_run",
]


class FullCheckEntry(TypedDict):
    """One check of a `full_checks` run: its name and whether it passed. Unlike
    `merge_checks`, no `tail`: command output goes only into the `Full checks` section."""

    name: str
    passed: bool
    # The run link `gh` reported for a CI job (GH-376); absent on a locally run check and on
    # every record written before it.
    link: NotRequired[str]
    # True when the system took the pass from a stored one instead of running the check
    # (ADR-0065); absent on a check that ran.
    reused: NotRequired[bool]
    # True when the check was red and green on its one rerun (ADR-0066): a flake passes, and
    # the record says `flake`, never plain passed. Absent on every other check.
    flake: NotRequired[bool]
    # Seconds the check ran, as `check_durations` summed them (GH-365); absent when the check
    # has no sample (a reused pass, or no result).
    duration_s: NotRequired[float]
    # True when the check timed out and a gating CI job covers its suite (GH-453): the entry
    # stays `passed: false`, is no failure and no pass, and the merge asks CI for the verdict.
    # Absent on every other check and on every record written before it.
    deferred: NotRequired[bool]


class CloseDocs(TypedDict):
    """The commit `final_validation` made on the task branch when PM, approving, returned
    capability files (the close-time pass): `parent` is the reviewed unit's `reviewed_head`
    the commit was made on, `head` the commit, `paths` what it holds, relative to the
    checkout. Replaced whole by every `pm_final` run, never accumulated."""

    parent: str
    head: str
    paths: list[str]


class PendingCloseDocs(TypedDict):
    """The capability files `final_validation` approved with and the verifier set has not
    committed yet (GH-469): `parent` the reviewed unit's `reviewed_head` PM's approval sits
    on, `definitions` the files as PM returned them (`CapabilityFile` dumped). Committed only
    on that `parent` (`rules.close_docs_committable`); cleared by any return and by
    `fix_important`."""

    parent: str
    definitions: list[dict[str, Any]]


class CiSpeculation(TypedDict):
    """The push `review()` made of the final unit's head to `agentic-ci/<id>` ahead of
    `full_checks` (GH-465): `head` the whole sha pushed, `pushed_at` the ISO instant. Replaced
    whole by every review of the final unit; carries no verdict."""

    head: str
    pushed_at: str


class CiCancelRerun(TypedDict):
    """The one rerun `full_checks` spent on a head whose declared jobs were left only by a
    superseded or cancelled workflow run (GH-539): `head` the whole sha judged, `run_id` the run
    asked to rerun and `rerun_at` the ISO instant. One fact, never a list: a second cancelled
    verdict on that head escalates instead of rerunning again. Carries no verdict and does not
    count for the merge gate's pair."""

    head: str
    run_id: str
    rerun_at: str


class CiGhostDispatch(TypedDict):
    """The one fresh run `full_checks` dispatched because every live run of a head was a ghost
    (GH-601): `head` the whole sha judged, `ghost_run_ids` the ghosts seen, `dispatched_at`
    the ISO instant of the accepted dispatch and `escalated` whether the task already escalated
    for it (set by a later part; a fact with it true never stops a new dispatch). One fact, never a
    list. Carries no verdict."""

    head: str
    ghost_run_ids: list[str]
    dispatched_at: str
    escalated: bool


OwnerRuleRejection = Literal["system", "newline", "template", "link", "too_long", "unknown_scope"]
"""Why an owner rule is not written, the closed class of a `Rejected` (`core.knowledge`
`owner_rules`): `system` is a rule for the system that this repo has no file for; `newline`,
`template`, `link` and `too_long` are the text limits of invariant 4; `unknown_scope` is a
marker with a parenthesis the grammar does not name."""


class OwnerRuleElsewhere(TypedDict):
    """An owner rule the `owner_rules` step did not write: its `rule` text as the owner
    marked it and the closed `cls` of why."""

    rule: str
    cls: OwnerRuleRejection


class FullChecksRecord(TypedDict):
    """The frontmatter record of one `full_checks` run (GH-358): the whole suite on the
    integrated head, once per task. Shaped like `merge_checks` (ADR-0045) plus `version`, which
    names the run so a finding row it raised closes only because a later, green one passed.
    `failed_step` is written only when the run did not pass. No free text in any field."""

    version: int
    head: str
    passed: bool
    duration_s: float
    declared: dict[str, str]
    results: list[FullCheckEntry]
    witness: dict[str, Any] | None
    failed_step: NotRequired[str]
    # The gate's wait and run apart (GH-361); absent on a record written before it.
    gate: NotRequired[GateRecord]
    # The checks that took longer than their declared budget (GH-365): a declared name and two
    # numbers, written by the system only, and only on a passed run that had a breach. It
    # returns and blocks nothing.
    over_budget: NotRequired[list[BudgetBreach]]
    # Where the run happened (GH-376): `ci` when the product's GitHub jobs gave the verdict,
    # `local` when this machine ran the tier, `ci_dispatch` when a dispatched run of the base's
    # workflow judged an untrusted head (GH-462). Absent on a record written before it and on a
    # product without `ci:`.
    ran_on: NotRequired[Literal["ci", "local", "ci_dispatch"]]
    # The id of the dispatched run that gave the verdict (GH-462); written only with
    # `ran_on: "ci_dispatch"`.
    run_id: NotRequired[int]
    # Why a trusted head was dispatched (GH-531): its pushes got no run, so the upstream's
    # workflow judged it; and the upstream revision whose `ci_dispatch` and `ci:` names that
    # verdict was read against. Only the no-run fallback writes them, with `ran_on:
    # "ci_dispatch"`, and nothing decides on them (`rules.ci_untrusted` stays from the paths).
    dispatch_cause: NotRequired[Literal["no_run", "ghost"]]
    stack_at: NotRequired[str]
    # Why a `ci:` product ran locally; written only with `ran_on: "local"`.
    ci_fallback: NotRequired[FallbackCause]
    # The checks deferred to CI (GH-453), a declared name each; written only when there is one,
    # so a record without a deferral is as it always was.
    deferred: NotRequired[list[str]]
    # Whether the CI run that gave the verdict was the one a review pushed ahead of this run
    # (GH-465, `speculation.consumable`); written only when it was, `ran_on` stays `ci`.
    speculative: NotRequired[bool]
    # How many tries the node's own push of the CI ref took and how git's error read at the
    # last failed one (GH-487); written only when it needed more than one, never when a
    # review's speculative push was consumed. A number and a closed word, no git text.
    push_attempts: NotRequired[int]
    push_kind: NotRequired[PushKind]
    # The instants the CI wait is derived from (`CiTiming`); absent on a record written before it.
    ci_timing: NotRequired[CiTiming]


class MergeCiRecord(TypedDict):
    """One pair of the merge gate's fresh GitHub run (GH-463): the pull request `head`
    merged with `master`, built as the commit `tested`, which `mode` published (`push`:
    `agentic-ci/<id>`; `dispatch`: `refs/agentic-ci-dispatch/<id>`, judged by a dispatched run
    of the base's workflow). All shas are full ids. Written before anything is published or
    dispatched, so a poll round never fires a second run for the same pair. `republished` says
    the one republish (or second dispatch) of the pair was spent and `superseded` holds the
    earlier `tested` shas of the pair, whose verdicts never apply. `run_id` is the run that
    decided the pair, when it could be read; `outcome` how the pair ended; `released_at` the
    instant the landing released a `no_verdict` pair. `failing` names the declared checks a `red`
    pair failed and `cause` the closed `CiCause` of a `no_verdict` one, so the landing reads
    both off the document and never from `gh`'s text. `rerun_at` is the instant the landing reran a
    `red` pair's run by its `run_id`, which also clears `outcome` and `failing`: the gate reads
    the run again and the wait for its new verdict counts from that instant. `reread_at` is the
    instant a terminal reread of a `no_verdict` pair's run ended without a verdict, written after
    the read together with `reread_state` (`timed_out` or `unreachable`), which spends the pair's
    reread (GH-564). A pair carrying `reread_at` without `reread_state` (written by GH-552's code)
    is still rereadable. No free text in any field."""

    head: str
    master: str
    tested: str
    mode: Literal["push", "dispatch"]
    published_at: str
    republished: bool
    superseded: list[str]
    run_id: NotRequired[str]
    outcome: NotRequired[Literal["stale", "no_verdict", "green", "red"]]
    released_at: NotRequired[str]
    failing: NotRequired[list[str]]
    cause: NotRequired[CiCause]
    rerun_at: NotRequired[str]
    reread_at: NotRequired[str]
    reread_state: NotRequired[Literal["timed_out", "unreachable"]]
    # The instants the CI wait is derived from (`CiTiming`); absent on a record written before it.
    ci_timing: NotRequired[CiTiming]


class MergeConflictRecord(TypedDict):
    """The conflict git found merging `head` with `master` (GH-492): full ids, `at` the ISO
    instant it was read. One row for the task, replaced whole on each write, and it holds only
    for that exact pair (`merge_conflict.applies`). No free text."""

    head: str
    master: str
    at: str


class MergeRefusalRecord(TypedDict):
    """A refusal the `merge` verb wrote nothing else for (GH-500): `reason` is the closed
    refusal, `head` and `master` the full ids it decided with, `at` the ISO instant. One row for
    the task, replaced whole on each write; the poller honours it only for that exact pair
    (`exempt_queue.backed_off`). No free text."""

    reason: Literal["ci_untrusted_no_dispatch"]
    head: str
    master: str
    at: str


def add_merge_ci(existing: list[MergeCiRecord], new: list[MergeCiRecord]) -> list[MergeCiRecord]:
    """The pairs of `existing` and `new` as one list in the order first seen, one row per
    `(head, master)`: a row for a known pair replaces it whole, so a record is written before
    the trigger and updated in place as the pair is republished, decided or released. The
    only way `merge_ci` is ever written; a document without the key reads as no record."""
    joined: dict[tuple[str, str], MergeCiRecord] = {}
    for row in (*existing, *new):
        joined[(row["head"], row["master"])] = row
    return list(joined.values())


class RestartPending(TypedDict):
    """A clean restart (GH-593) the system started and has not finished: the subtask and the head
    of the attempt it archives. Written by `restart.restart_patch`, so a stopped run resumes the
    archive and the dispatch instead of repeating the decision."""

    subtask: str
    archived_head: str


class RestartSettledRow(TypedDict):
    """The open findings of `subtask` with a `version` below this one, settled by a restart as
    `discarded` for the closed reason `restarted` (GH-593): the attempt that raised them is
    archived. Per record, never per folded key, like `SettledRow`."""

    subtask: str
    version: int
    outcome: Literal["discarded"]
    reason: Literal["restarted"]


class TaskState(TaskInput, total=False):
    type: str
    status: Annotated[Status, last]
    current_role: Annotated[str, last]
    flags: dict[str, bool]
    escalated: Annotated[str | None, last]
    # The closed reason `escalated` is text for (`EscalationClass`); written only by
    # `rules.escalation`, together with `escalated`, and cleared with it on resume.
    escalation_class: Annotated[EscalationClass | None, last]
    # The mechanical cause of that escalation when its raise site knew one (GH-593), written by
    # `rules.escalation` with the class and copied to the pause log by `rules.logged`.
    escalation_cause: Annotated[MechanicalCause | None, last]
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
    # Short hash of the playbook section each role first ran with, keyed by role slug
    # (ADR-0043): `first`, never overwritten, so a window compares a task by the
    # playbook it started with. Written only for a non-empty playbook, so a task with
    # none has no key and an old document reads `{}`.
    playbooks: Annotated[dict[str, str], first]
    # A later, different playbook hash of the same role, or any hash of a role whose
    # first run had none (`run_role.playbook_patch`): the window excludes the task.
    playbooks_changed: Annotated[dict[str, str], merge]
    cost_usd: Annotated[float, add]
    # The plan's estimate (ADR-0054): total cost in USD and the files the pull request will
    # show as changed. Written once, by the plan, and never rewritten; absent on a document
    # from before it, which reads as "no estimate" (`rules.estimate_in_force`), never zero.
    estimate: Annotated[EstimateRecord, last]
    # The estimates a human decided on after the task stopped at its cap, in order; the last
    # one is in force. Grows only on a resumption with a decision.
    estimate_revisions: Annotated[list[EstimateRevision], last]
    # The revisions of the plan's `required_paths` (GH-538), in order, each bound to the list
    # it revised; `rules.required_paths_in_force` reads them, the plan's own list never moves.
    required_paths_revisions: Annotated[list[PathRevision], operator.add]
    # The new estimate a stopped task proposed (the human decides on it, never the task).
    proposed_estimate: Annotated[float | None, last]
    # The parts of a plan that did not fit (ADR-0054), in order; `rules.after_plan` routes a
    # state holding any to `split`. Cleared when a human waives the fit on a resumption.
    parts: Annotated[list[PartRecord], last]
    # What the human must do on the issue tracker (GH-336), one line per item, written by the
    # plan; replaced whole when a plan is written again. Absent when the plan has none.
    tracker_checklist: Annotated[list[str], last]
    # The checklist comment, once posted (GH-336): when and the hash of the items it carried.
    # Written after the comment succeeded; the poller reads it to skip reading GitHub.
    tracker_checklist_posted: Annotated[TrackerPosted, last]
    # The typed tracker items of the plan (GH-620), replaced whole when a plan is written again;
    # absent when the plan has none. Never a `tracker_checklist` string and never read as one.
    tracker_items: Annotated[list[TrackerItem], last]
    # What was done for each typed item, keyed by `tracker_items.item_hash`, `add_tracker_actions`.
    tracker_actions_done: Annotated[dict[str, TrackerActionDone], add_tracker_actions]
    # The hashes of the items the current `questions` pause raised (GH-620); `rules.escalation`
    # writes it and `rules.resumed` clears it. Not a `MechanicalCause`: the pause is a human's.
    tracker_human: Annotated[list[str], last]
    # Why a spec delivery's `questions` pause was raised (GH-594): written only by
    # `rules.escalation`, with a spec in the state; never a `MechanicalCause`.
    spec_cause: Annotated[SpecEscalationCause | None, last]
    # The spec a delivery is built against (GH-594). Nothing sets it yet; its document
    # round-trip belongs to the part that does.
    spec: Annotated[SpecContract, last]
    # The delivery assumptions (GH-594), `add_assumptions`: one row per canonical text. Read
    # with `.get("assumptions", [])`: a document written before this field has none.
    assumptions: Annotated[list[AssumptionRow], add_assumptions]
    # The issue URL of each part already opened, keyed by the part's number as text: written
    # part by part, so a `gh` cut in the middle resumes without opening a part twice.
    split_issues: Annotated[dict[str, str], merge]
    # The split sweep's own record (GH-619), ISO UTC strings, read with `.get`. A `split`
    # document is terminal and never goes back through `finish`, so the sweep writes these only
    # through `Documents.record_closure`: `split_repointed_at` once every open dependent of the
    # parent was re-pointed over one complete read; `split_closed_at` and `split_abandoned_at`
    # belong to the part that closes the parent (not written by the re-point half).
    split_repointed_at: NotRequired[str]
    split_closed_at: NotRequired[str]
    split_abandoned_at: NotRequired[str]
    # A human decided the task builds whole although its estimate does not fit; the plan
    # check then accepts a plan that does not fit and refuses a split one.
    fit_waived: Annotated[bool, last]
    # The pause of a part whose plan is over the line (`rules.part_over_line`): the question
    # is "build it whole or stop". Set with the pause, written False when it is answered.
    fit_question: Annotated[bool, last]
    # The files the pull request changed, recorded at close through `rules.delivered_files`.
    actual_files: Annotated[int, last]
    # The size the plan declared and the size the delivery measured (GH-592), as the records
    # `size_budget` defines; the document writes them under `size_budget`'s own keys. Absent
    # on a document from before them, which reads as "no size", never as zero.
    planned_size: Annotated[PlanSize, last]
    actual_size: Annotated[ActualSize, last]
    # The technical stage (ADR-0011): subtasks by id, each Developer patches its own.
    subtasks: Annotated[dict[str, Subtask], merge]
    # Rules of the plan every subtask honours (issue #42): the one channel between
    # reviews that works in the first wave, since it is written before anyone codes.
    invariants: list[str]
    # The performance scenarios the plan convened QA Performance for (GH-318, ADR-0060),
    # `PerfScenario` dumped; absent or empty when not convened. The only source of
    # "convened": never the branch's stack.
    performance: list[dict[str, Any]]
    # The CI check names the plan says the issue's done criteria need green on the pull
    # request head, and the paths of which the integrated diff must touch one (GH-371).
    # Absent in a document written before, which reads as empty.
    ci_required: list[str]
    required_paths: list[str]
    # The reason of the API break the plan approved (GH-610), one sanitized line; absent or empty
    # when the plan approves none. `landing.recorded_api_break` is its one reader.
    api_break: str
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
    # Where a review's approval closed its own subtask's earlier blocking records
    # (`rules.settled_on_approval`), appended in the patch that approves and never
    # derived from the subtask's status at read time. Applied per record by
    # `rules.findings_rows`, after `settled_findings`.
    approval_settled: Annotated[list[SettledRow], operator.add]
    # The rows a clean restart discarded (`restart.restart_patch`, GH-593), appended in the patch
    # that restarts and applied per record by `rules.findings_rows`.
    restart_settled: Annotated[list[RestartSettledRow], operator.add]
    # Clean restarts used per pair, keyed by the pair's key like `changes_requested` (GH-593).
    # Written only by `restart.restart_patch`, in the same patch that zeroes the pair's returns.
    restarts: Annotated[dict[str, int], merge]
    # The restart in flight (`RestartPending`), `None` once its archive and dispatch finished.
    restart_pending: Annotated[RestartPending | None, last]
    # Runs of a section to forget when resuming a session, keyed by section (GH-593): the
    # `version` of the section's last run at the restart. `rules.last_session` skips every run
    # of the section at or below it, so a restarted Developer never resumes the archived attempt.
    session_floor: Annotated[dict[str, int], merge]
    # Where a `PM: final validation` run settled its own earlier rows
    # (`plan_settlement.on_approval` / `on_return`), appended in that run's patch and never
    # derived from `runs` at read time. Applied per record by `rules.findings_rows`.
    plan_settled: Annotated[list[PlanSettledRow], operator.add]
    # The head of the task branch the fan-in last integrated (GH-358), stamped only for a
    # product that declares `delivery_checks`; absent otherwise. `rules.full_checks_due`
    # compares the record's head with it. Persisted in the frontmatter.
    task_head: str
    # The latest `full_checks` run (GH-358), persisted as one flow line; absent until one ran.
    full_checks: FullChecksRecord
    # The merge gate's fresh GitHub runs (GH-463), one row per `(head, master)` pair in order,
    # `add_merge_ci`. Read with `.get("merge_ci", [])`: a document written before it has none.
    merge_ci: Annotated[list[MergeCiRecord], add_merge_ci]
    # The conflict git found building a fresh pair (GH-492), one row replaced whole on each write
    # (`last`). Read with `.get("merge_conflict")`: a document written before it has none, which
    # reads as no record.
    merge_conflict: Annotated[MergeConflictRecord, last]
    # The refusal the merge verb wrote nothing else for (GH-500), one row replaced whole on each
    # write (`last`). Read with `.get("merge_refusal")`: a document written before it has none,
    # which reads as no refusal.
    merge_refusal: Annotated[MergeRefusalRecord, last]
    # The head `deliver` last refused to push for lack of a passed record (GH-358): a second
    # refusal on the same head escalates instead of looping (`rules.full_checks_heal`).
    full_checks_healed: str
    # The task branch, and the pull request once delivered.
    branch: str
    pull_request: str | None
    # The sha `origin`'s task branch held before a delivery replaced it with a lease
    # (GH-204): saved before the push so it stays recoverable, cleared by every plan that
    # does not replace (`rules.replace_record`).
    replaced_remote_tip: str | None
    # The approved head a stopped run still owes the task branch and the pull request
    # (GH-373, "publicación debida"): written only by `resuming.prepare` (a sha or `None`
    # on every resume), cleared only by `deliver`'s single exit. Read with `.get`.
    publish_owed: NotRequired[str | None]
    # The capability files PM wrote at close, committed on the task branch (`CloseDocs`):
    # `None` is how a `pm_final` run clears the previous one. Written to the document only
    # when set, read with `.get`.
    close_docs: NotRequired[CloseDocs | None]
    # The capability files an approving `final_validation` returned, owed to the task branch
    # until the verifier set commits them (GH-469, `PendingCloseDocs`): `None` clears it. No
    # reducer, so the last write wins, like `close_docs`; written to the document only when
    # set, read with `.get`.
    pending_close_docs: NotRequired[PendingCloseDocs | None]
    # The head the `owner_rules` step last observed (`rules.owner_rules_due` compares it with
    # the current head; never a record of which rules were written). Written to the document
    # only when set, read with `.get`.
    owner_rules_seen: NotRequired[str | None]
    # The owner rules the step could not write into this repo (a closed class, no free text
    # beyond the rule itself, which carries no newline), for the ready comment. Written to the
    # document only when non-empty, read with `.get`.
    owner_rules_elsewhere: NotRequired[list[OwnerRuleElsewhere]]
    # The full sha `deliver` pushed and when (GH-216): the anchor of the CI wait, read with
    # `.get`, written only to the product checkout's document and only by a delivery.
    pushed_head: str
    pushed_at: str
    # The head the final unit's review published to the CI ref and when (GH-465): the push
    # fact, never a verdict. Written only by that review (its approval with a whole sha, `None`
    # on its return), written to the document only when set, read with `.get`.
    ci_speculation: NotRequired[CiSpeculation | None]
    # The single rerun of a cancelled CI run of one head (GH-539): written by `full_checks` when
    # it reruns, read only for that same head (`rules.same_commit`), written to the document only
    # when set, read with `.get`.
    ci_cancel_rerun: NotRequired[CiCancelRerun | None]
    # The fresh run dispatched for a head whose live runs were all ghosts (GH-601): written by
    # `full_checks` right after the dispatch is accepted, read only for that same head
    # (`rules.same_commit`), written to the document only when set, read with `.get`.
    ci_ghost_dispatch: NotRequired[CiGhostDispatch | None]
    # The processes a role left running that the release stopped when the task closed
    # (GH-282, ADR-0052): `deliver` and `discard` write it, only when something was reaped,
    # and it is read with `.get`.
    reaped_at_cleanup: list[ReapedProcess]
    # The criteria documents past the line cap at the head `deliver` pushed (GH-295,
    # ADR-0055): one message each, written only when there is one, read with `.get`.
    criteria_over_cap: list[str]
    # The landing's own record (GH-239, ADR-0048), every key optional and read with `.get`:
    # `landing` is append-only, one entry per `(head, step)` written before it dispatches;
    # `auto_merge_guarded` the guarded paths an automatic merge landed; `landing_restarts`
    # and `landing_cost_carried` the depth and the cost a `--fresh` restart carries over.
    landing: list[LandingEntry]
    # What each `renumber` step did (GH-229, ADR-0049): `{from, to, head_before, head_after,
    # carried}`. Written only when present, read with `.get`.
    adr_renumbered: list[dict[str, str | bool]]
    auto_merge_guarded: list[str]
    # Set by the landing once it filed the follow-up for the open important findings.
    followups_filed: bool
    landing_restarts: int
    landing_cost_carried: float
    # The landing order's keys (GH-588), ISO UTC strings and a full sha, read with `.get`.
    # `landing_queued_at`: when the delivered task joined its product's order (`deliver`, if
    # absent, and `restore`; removed only by the landing restart). `head_since_at` and
    # `head_since_head`: when the poller first saw `head_since_head` as the pull request's head
    # while it led the order (written only by the poller's second phase).
    landing_queued_at: NotRequired[str]
    head_since_at: NotRequired[str]
    head_since_head: NotRequired[str]
    # The head budget's facts (GH-621): the start of the member's current turn, the seconds it
    # was already charged, when it was last seen as head, when it last yielded and its yields.
    # The poller's writer writes them (GH-621 part 2); they are read only to build
    # `landing_order.Member`. The scope is per member, not per sha. The landing restart clears
    # them with a `None` patch, so there is no reducer on them.
    head_turn_at: NotRequired[str]
    head_charged_s: NotRequired[float]
    head_seen_at: NotRequired[str]
    head_yielded_at: NotRequired[str]
    head_yields: NotRequired[list[HeadYield]]
    # The slices the landing merged ahead of the task's pull request (GH-297, ADR-0048):
    # one record per slice, appended whole through `record_closure`. Optional, read with `.get`.
    landed_slices: list[LandedSlice]
    # The pull requests of the slices that carried every unit to master when the task's own
    # pull request was closed unmerged (GH-334): written with `closed_at`, never with a
    # `delivery_outcome`. Optional, read with `.get`.
    closed_by_slices: list[str]
    # What an interviewee could not decide (ADR-0014); escalates with the task.
    questions_for_human: list[str]
    # What the ephemeral interviewer proposed for the deliverable that follows
    # (issue #42), across the whole interview so far: a retake seeds its transcript
    # from this instead of starting empty. `last`, never an append reducer: the
    # value written each time is already the accumulated list, and appending it to
    # itself would duplicate it on every retake.
    proposed_invariants: Annotated[list[str], last]
    # The review-mode and ephemeral-arm switches this run ran under (issue #28),
    # `shared.configuration.experiment_snapshot`'s own shape: `{}` on the default path,
    # omitted from the document then (`document.render`). `last`, not optional: `review.py`
    # writes it from N sister branches in one superstep (ADR-0004, fifth addendum) and
    # `last`'s own docstring above (task.py:180) says a key with no reducer makes the
    # graph refuse two writes in the same step. `last` is right here because every
    # branch of a wave writes the identical snapshot.
    experiment: Annotated[dict[str, str], last]
    # The arm the product's experiment gave this task (GH-470, ADR-0030 amended): which
    # experiment, the arm's name and the switches it set, as `arm_label` reads them.
    # Optional and omitted from the document when absent; `metrics.arm_of` prefers it to
    # `experiment`. Written once when the run starts, so no reducer.
    assignment: ArmAssignment
    # The route the plan took (GH-472): written when the sizer ran, omitted from the document
    # otherwise. Written once by the plan node, so no reducer.
    plan_route: PlanRoute
    # The path the task takes and how it got there (GH-591, `domain/task_path.py`): recorded at
    # classify and read by nothing yet. `initial_path` is what `decide` said and never changes;
    # `path` only ever goes `light` -> `full`, through `task_path.promote`. `path_cause` explains
    # the initial path, `path_label` is the owner label's fact and `promotion` the one move.
    # Written by `task_path` alone; a document without them reads as `full` / `none` / `unknown`.
    initial_path: TaskPath
    path: TaskPath
    path_cause: PathCause
    path_label: PathLabel
    promotion: Promotion
    # Set with the classification and by nothing else (GH-599, `task_path.classification_patch`):
    # the marker `cycle.path_recorded` reads, so a document the router never classified is not
    # counted as one that ran a path.
    path_routed: bool
    # The size PjM declared (GH-599), one of `task_path.Size`; omitted when PjM gave none or an
    # unknown value. Read only by `metrics.task_size_value`.
    size: TaskSize
    # The lead time of a closed task (GH-599, `domain/lead_time.py`): `{labeled_at, merged_at}` or
    # `{unknown: no_merge | unreadable}`, written once by a close or a merge and never again, except
    # that a later merge replaces `no_merge`. Replaced whole (`last`); read with `.get`.
    lead_time: Annotated[LeadTime, last]
    # A promotion the plan node asked for (GH-591): the role's `promote` or `plan_over_cap`. The
    # plan node is serialized, so the key needs no reducer; the system step after the model
    # applies it through `task_path.promote` and clears it (sets `None`). Absent or `None` when
    # nothing is pending.
    plan_promote_request: PromoteRequest | None
    # Set when the task is an evaluation run (GH-470): the issue and base it re-runs from.
    # Written once when the run starts, from the frozen entry, so no reducer; omitted from
    # the document when absent.
    evaluation: EvaluationRef
    # Why a bug was discarded, in PM's words (issue #65): `discard` writes it on the
    # issue, and the frontmatter keeps it so the document says why the run ended.
    discard_reason: str
    # When a task ended without a delivery, `discarded` or `split` (ADR-0056): the clock
    # the Reflector's decision gate reads. Written only by `rules.finished`, and optional:
    # a document without it reads as before.
    finished_at: str
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
    presubmit_checks: list[list[CheckResult]]
    # Counts of the interview this patch ran, if any (`InterviewStats`): measurement
    # only, like `subtask_id` above — never rendered into the document.
    interview_stats: InterviewStats
    # The append-only pause register (issue #137, `PauseEntry`'s own docstring):
    # `operator.add` like `runs`/`failures`, so `delta` already knows how to subtract
    # it. The only source `rules.auto_resumable` reads for whether a pause is the
    # poller's to retry; never `escalation_class` or a failure's own `error` text.
    pause_log: Annotated[list[PauseEntry], operator.add]
    # The design branch's own append-only register (ADR-0040, `DesignItemRecord`'s own
    # docstring): every lead's remark and every consolidation observation, read with
    # `.get("design_items", [])` since a document written before this task has none.
    design_items: Annotated[list[DesignItemRecord], operator.add]
    # The flakes this task observed (ADR-0066), `add_flakes`: deduplicated on
    # `(check, head, gate)` so parallel branches merge their observations without repeating
    # them. Read with `.get("flakes", [])`: a document written before this field has none.
    flakes: Annotated[list[FlakeRecord], add_flakes]
    # The best-effort tracker writes that failed (GH-389), `add_tracker_failures`: one row
    # per `(target, cls)`, at most ten, never `gh`'s text. Read with
    # `.get("tracker_failures", [])`: a document written before this field has none.
    tracker_failures: Annotated[list[TrackerFailureRecord], add_tracker_failures]
    # The restorations of an archived task (GH-406), `add_restorations`: one row per archive
    # it was brought back from, each with the `landing` length at that moment. Read with
    # `.get("restorations", [])` -- only through `rules.live_landing` and `rules.restored`;
    # a document written before this field has none.
    restorations: Annotated[list[Restoration], add_restorations]
    # The shadow reviews of the integration review under `review_mode` `single` (GH-473),
    # `add_shadow_reviews`, and how long the node waited for them past the single review
    # (`shadow_wait_s`). Read with `.get("shadow_reviews", [])`: absent under every other mode,
    # and a document written before them has none. Nothing but the shadow use case writes them.
    shadow_reviews: Annotated[list[ShadowRecord], add_shadow_reviews]
    shadow_wait_s: float
    # What each review pass of a unit covered (GH-589, `ReviewScopeRecord`), `add_review_scopes`.
    # Read with `.get("review_scopes", [])`: a document written before this field has none, and
    # no verdict, gate or landing step reads it. Only `landing.scope_patch` writes it.
    review_scopes: Annotated[list[ReviewScopeRecord], add_review_scopes]


def _reducers() -> dict[str, object]:
    hints = get_type_hints(TaskState, include_extras=True)
    return {
        name: get_args(hint)[1] for name, hint in hints.items() if get_origin(hint) is Annotated
    }


REDUCERS = _reducers()


def apply(state: TaskState, patch: TaskState | ReleasePatch | EvaluatedPatch) -> TaskState:
    """`state` with `patch` folded in, reducer by reducer; plain fields are overridden. The
    patch is a `TaskState` or the partial `ReleasePatch` / `EvaluatedPatch`, so every key stays
    checked."""
    merged: dict[str, Any] = dict(state)
    for key, value in dict(patch).items():
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
    patch: dict[str, Any] = {}
    before_map: dict[str, Any] = dict(before)
    after_map: dict[str, Any] = dict(after)
    for key, value in after_map.items():
        if key in _BRANCH_LOCAL:
            continue
        if key not in before_map:
            patch[key] = value
            continue
        old, reducer = before_map[key], REDUCERS.get(key)
        if reducer is add:
            if value != old:
                patch[key] = value - old
        elif reducer is operator.add:
            if len(value) > len(old):
                patch[key] = value[len(old) :]
        elif reducer is first:
            if added := {k: v for k, v in value.items() if k not in old}:
                patch[key] = added
        elif reducer is add_flakes:
            old_tests = {_flake_key(r): r["tests"] for r in old}
            if grown := [
                r
                for r in value
                if _flake_key(r) not in old_tests
                or any(t not in old_tests[_flake_key(r)] for t in r["tests"])
            ]:
                patch[key] = grown
        elif reducer is add_assumptions:
            held_texts = {r["text"] for r in old}
            if fresh_rows := [r for r in value if r["text"] not in held_texts]:
                patch[key] = fresh_rows
        elif reducer is add_tracker_failures:
            known = {(r["target"], r["cls"]) for r in old}
            if fresh := [r for r in value if (r["target"], r["cls"]) not in known]:
                patch[key] = fresh
        elif reducer is add_tracker_actions:
            if add_tracker_actions(old, value) != old:
                patch[key] = value
        elif reducer is add_merge_ci:
            prior = {(r["head"], r["master"]): r for r in old}
            if grown := [r for r in value if prior.get((r["head"], r["master"])) != r]:
                patch[key] = grown
        elif reducer is add_shadow_reviews:
            held_shadows = {r["subtask"] for r in old}
            if fresh_shadows := [r for r in value if r["subtask"] not in held_shadows]:
                patch[key] = fresh_shadows
        elif reducer is add_review_scopes:
            seen = {(r["unit"], r["head"], r["base"]) for r in old}
            if added_scopes := [r for r in value if (r["unit"], r["head"], r["base"]) not in seen]:
                patch[key] = added_scopes
        elif reducer is add_restorations:
            held = {r["archive"] for r in old}
            if fresh := [r for r in value if r["archive"] not in held]:
                patch[key] = fresh
        elif reducer is merge:
            if changed := {k: v for k, v in value.items() if k not in old or old[k] != v}:
                patch[key] = changed
        elif value != old:
            patch[key] = value
    return patch  # type: ignore[return-value]
