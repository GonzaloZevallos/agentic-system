"""What the landing reads off a task document to ask `domain.landing.landing_step` for a
step (GH-239): the budgets already spent, counted from the document's own `landing` list.

Shared by the poller (which decides whether to spawn `agentic land`), `merge.py` (which
decides which comment a refusal gets) and `landing.py` (which dispatches), so the three
ask the same question the same way. It imports nothing from them."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from agentic.shared.configuration import Configuration

from ..domain import landing, rules
from ..domain.landing import LandingStep, LandingStepKind
from ..domain.roles import Role
from ..domain.task import (
    INTEGRATION,
    UPSTREAM_KEY,
    VERDICT_KEY,
    VERDICT_SYNC,
    VERDICT_UNLANDABLE,
    DiagnosisVerdict,
    TaskState,
)
from .checkouts import task_base
from .escalate import ESCALATED, NEEDS_HUMAN
from .ports import GitError, IssueComment, Launcher, Ports, Upstream
from .run_role import task_cap

LIVE_STEPS: frozenset[LandingStepKind] = frozenset(
    {
        "sync_master",
        "re_review",
        "fix",
        "fix_important",
        "rerun",
        "file_followups_and_land",
        "renumber",
        "land_slice",
        "diagnose",
    }
)
"""Every step has its executor: `landing_sync`'s (`sync_master`, `re_review`, `renumber`),
`landing_fix`'s (`fix`, `fix_important`, `rerun`, `file_followups_and_land`, `diagnose`) and
`landing_slice`'s (`land_slice`, GH-297). A
step outside the set is neither recorded nor spawned, so a refusal whose step is inert logs
as it always did."""


def takes_action(kind: LandingStepKind, live: frozenset[LandingStepKind]) -> bool:
    """Whether `kind` is a step the system takes now: built (`live`) and not `wait` (the
    next tick decides again) or `none` (the human's), which are never recorded or spawned
    whatever `live` says."""
    return kind in live and kind not in ("wait", "none")


def entries(raw: Mapping[str, object]) -> list[dict[str, object]]:
    """The document's `landing` list, tolerant of its absence or a malformed value."""
    value = raw.get("landing")
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def _conflicting(entry: Mapping[str, object]) -> bool:
    return entry.get("refusal") in landing.CONFLICTING_SYNC_REFUSALS


def _readings(
    epoch: Sequence[Mapping[str, object]], upstream: str | None
) -> tuple[DiagnosisVerdict | None, int, bool]:
    """What the diagnoses of the `epoch` read as, walked in order (GH-330): the latest
    delivered one's verdict, the syncs that followed a `sync` verdict for `upstream`, and
    whether the latest verdict is a `sync` for `upstream` that no `sync_master` has followed.
    A `sync` verdict is served by the first `sync_master` for its sha after it; a `sync` for a
    sha that already had `LANDING_VERDICT_SYNCS_MAX` of them (served or not), or with no sha,
    reads as `unlandable`, and so does a delivered entry with no verdict."""
    served: dict[str, int] = {}
    seen: dict[str, int] = {}
    waiting: set[str] = set()
    last: DiagnosisVerdict | None = None
    latest: str | None = None
    for entry in epoch:
        sha = entry.get(UPSTREAM_KEY)
        if entry.get("step") == "sync_master" and isinstance(sha, str) and sha in waiting:
            waiting.discard(sha)
            served[sha] = served.get(sha, 0) + 1
        if entry.get("step") != "diagnose" or entry.get("delivered") is not True:
            continue
        verdict = landing.verdict_of(entry.get(VERDICT_KEY))
        if (
            verdict == VERDICT_SYNC
            and isinstance(sha, str)
            and seen.get(sha, 0) < landing.LANDING_VERDICT_SYNCS_MAX
        ):
            seen[sha] = seen.get(sha, 0) + 1
            last, latest = VERDICT_SYNC, sha
            waiting = {sha}
        else:
            last, latest = VERDICT_UNLANDABLE, None
            waiting = set()
    due = upstream is not None and latest == upstream and latest in waiting
    return last, served.get(upstream, 0) if upstream is not None else 0, due


def spent(
    raw: Mapping[str, object],
    head: str | None,
    waited_s: float | None,
    upstream: str | None = None,
) -> landing.LandingSpent:
    """The passes, syncs and reruns on `head` the `landing` list records. `passes` is the
    `re_review` and `fix` entries and nothing else: a `sync_master` is never a pass (GH-330).
    `syncs` counts every `sync_master`, and `conflicting_syncs_for_upstream` those answering one
    of `landing.CONFLICTING_SYNC_REFUSALS` for `upstream` (the round's master sha). With the
    entries closed with `lacks` equal to `upstream` (`unsynced_for_upstream`, GH-267) and the
    `verdict_syncs_for_upstream` they are counted by sha and never by head; without a sha they
    are 0. All of them and the diagnoses' readings (`last_verdict`, `verdict_sync_due`) count
    only the entries after the last resolved `land_slice` (`landing.sync_epoch`, GH-297);
    `passes` never reset. `important_passes` counts the `fix_important` entries (GH-294)."""
    done = entries(raw)
    epoch = landing.sync_epoch(done)

    def count(predicate: Callable[[dict[str, object]], bool]) -> int:
        return sum(1 for entry in done if predicate(entry))

    def count_epoch(predicate: Callable[[Mapping[str, object]], bool]) -> int:
        return sum(1 for entry in epoch if predicate(entry))

    def for_upstream(entry: Mapping[str, object]) -> bool:
        return upstream is not None and entry.get(UPSTREAM_KEY) == upstream

    last_verdict, verdict_syncs, due = _readings(epoch, upstream)
    return landing.LandingSpent(
        passes=count(lambda e: e.get("step") in ("re_review", "fix")),
        syncs=count_epoch(lambda e: e.get("step") == "sync_master"),
        reruns_on_head=count(lambda e: e.get("step") == "rerun" and e.get("head") == head),
        diagnoses=count(lambda e: e.get("step") == "diagnose" and e.get("delivered") is True),
        waited_s=waited_s,
        renumbers=count(lambda e: e.get("step") == "renumber"),
        important_passes=count(lambda e: e.get("step") == "fix_important"),
        unsynced_for_upstream=(
            count_epoch(lambda e: e.get("lacks") == upstream) if upstream is not None else 0
        ),
        conflicting_syncs_for_upstream=count_epoch(
            lambda e: e.get("step") == "sync_master" and _conflicting(e) and for_upstream(e)
        ),
        verdict_syncs_for_upstream=verdict_syncs,
        verdict_sync_due=due,
        last_verdict=last_verdict,
    )


def step_for(
    raw: TaskState,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    slicing: landing.Slice | landing.RestartWhy | Callable[[], landing.SliceInputs] | None = None,
) -> LandingStep:
    """The one question every caller asks: `landing_step` over the document's spent budgets.
    `slicing` is what `landing_step` decides a slice from at the exhaustion (`slice_inputs`,
    read lazily), `None` as before GH-297."""
    return landing.landing_step(
        refusal,
        facts,
        spent(raw, facts.pr_head, facts.ci_waited_s, facts.current_master),
        landing.blocking_owners(raw),
        important_units=landing.important_owners(raw),
        slicing=slicing,
    )


def reviewed_heads(raw: TaskState, units: Sequence[str]) -> dict[str, str]:
    """The `reviewed_head` of each of `units` that has one, in the order given."""
    subtasks = raw.get("subtasks", {})
    found = {name: subtasks.get(name, {}).get("reviewed_head") for name in units}
    return {name: head for name, head in found.items() if isinstance(head, str) and head}


def unit_reads(
    raw: TaskState, units: Sequence[str], cfg: Configuration, ports: Ports, master: str
) -> tuple[dict[str, bool], dict[str, frozenset[str]]]:
    """Per unit: whether `landing.review_carries` holds for it against what `master` changed
    since its `reviewed_head` branched, and the paths the unit changed since the task's base.
    `changed_paths` is a three-dot diff, so `(reviewed_head, master)` is what master did since
    the merge-base. Raises `GitError`."""
    base = task_base(raw, ports)
    carries: dict[str, bool] = {}
    paths: dict[str, frozenset[str]] = {}
    for name, head in reviewed_heads(raw, units).items():
        own = ports.workspace.changed_paths(base, head)
        carries[name] = landing.review_carries(
            False, ports.workspace.changed_paths(head, master), own
        )
        paths[name] = frozenset(own)
    return carries, paths


def slice_inputs(
    raw: TaskState, cfg: Configuration, ports: Ports, master: str, task_head: str
) -> landing.SliceInputs:
    """The facts `landing.slice_or_restart` decides from (GH-297), read from git and the
    document. A git failure makes them unreadable, which is a restart reason and never a
    crash: the landing falls back to what it did before slices."""
    subtasks = raw.get("subtasks", {})
    approved = tuple(
        landing.SliceUnit(name, tuple(unit.get("depends_on", ())))
        for name, unit in subtasks.items()
        if unit.get("status") == "approved" and name != INTEGRATION
    )
    refused = landing.refused_slices(entries(raw))
    blocking = frozenset(landing.blocking_owners(raw))
    names = [unit.name for unit in approved]
    try:
        carries, paths = unit_reads(raw, names, cfg, ports, master)
        heads = reviewed_heads(raw, names)
        landed = frozenset(
            name
            for name, head in heads.items()
            if ports.workspace.is_ancestor(cfg.product_dir, head, master)
        )
        integration = ports.workspace.integration_paths(
            cfg.product_dir, list(heads.values()), task_head, master=master
        )
    except GitError:
        return landing.SliceInputs(approved, {}, None, {}, frozenset(), refused, blocking, False)
    return landing.SliceInputs(
        approved, carries, integration, paths, landed, refused, blocking, True
    )


def diagnose_gate(
    raw: TaskState, kind: LandingStepKind, now: datetime | None
) -> Literal["go", "skip", "escalate"]:
    """What a `diagnose` step does when earlier attempts never delivered (GH-257): `go`
    (dispatch, a first attempt or a retry that is `due`), `skip` (not yet due, or already
    escalated) or `escalate` (without running the role). Any other step, or none waiting,
    is `go`. An unreadable `now` waits."""
    if kind != "diagnose" or not landing.diagnose_attempts(raw):
        return "go"
    if now is None:
        return "skip"
    match landing.diagnose_retry(raw, now):
        case "none" | "due":
            return "go"
        case "waiting":
            return "skip"
        case "escalate":
            return "skip" if raw.get("escalated") else "escalate"


def stranded(raw: Mapping[str, object], comments: Sequence[IssueComment]) -> bool:
    """Whether `raw` is a stranded diagnose (GH-257): the one question the poller's resume
    gather and `land` both ask, over the comments the caller already has."""
    return landing.stranded_diagnose(
        raw, landing.escalated_after(comments, landing.diagnose_at(raw), ESCALATED)
    )


def stranded_now(ports: Ports, task_id: str, raw: Mapping[str, object]) -> bool:
    """`stranded`, reading the issue's comments only when the document already has the
    stranded shape: no tracker call for any other task. A tracker that cannot answer leaves
    the document escalated."""
    if not landing.stranded_diagnose(raw, False):
        return False
    try:
        comments = ports.tracker.timestamped_comments(task_id)
    except (ValueError, OSError):
        return False
    return stranded(raw, comments)


ReleaseWhy = Literal["released", "waiting_on_human", "needs_restart", "task_cap"]
"""Why an escalated landing diagnose is or is not released (GH-328): a human's decision
released it, it waits for one (or for the label to go, or for an open issue), its shape is a
restart only a human starts, or the task cap is spent. Closed: `land` maps each to its log
line and its `LandResult.reason`."""


@dataclass(frozen=True)
class Release:
    """`decision` is the human's answer when `why` is `released`, else `None`."""

    decision: str | None
    why: ReleaseWhy


def _unreleased(why: ReleaseWhy) -> Release:
    return Release(None, why)


def release(ports: Ports, cfg: Configuration, task_id: str, raw: TaskState, number: int) -> Release:
    """Whether a human's answer releases the escalated landing diagnose of `raw` (GH-328).
    Only the shape `landing_fix.escalate_failed_diagnosis` leaves is releasable: class
    `failure` with undelivered diagnose attempts; any other escalation (the depth-1 one, after
    a delivered diagnosis) is the human's restart, and the tracker is not read for it. The
    release needs every one of: a non-system comment after the last `Escalated:` comment
    (`resume.decision_since`, the one reader), the issue open, its labels readable and without
    `needs-human`, and the task under its cap (`rules.task_spent`, the figure `run_role`
    measures). A tracker that cannot answer is a wait, never a release."""
    from .resume import decision_since  # resume imports poll, which imports this module

    if raw.get("escalation_class") != "failure" or not landing.diagnose_attempts(raw):
        return _unreleased("needs_restart")
    try:
        comments = ports.tracker.timestamped_comments(task_id)
    except (ValueError, OSError):
        return _unreleased("waiting_on_human")
    if not landing.escalated_after(comments, landing.diagnose_at(raw), ESCALATED):
        return _unreleased("waiting_on_human")
    decision = decision_since([comment.body for comment in comments], ESCALATED)
    if decision is None:
        return _unreleased("waiting_on_human")
    try:
        issue = ports.tracker.issue_state(number)
    except (ValueError, OSError):
        return _unreleased("waiting_on_human")
    if issue.state != "OPEN" or issue.labels is None or NEEDS_HUMAN in issue.labels:
        return _unreleased("waiting_on_human")
    if rules.task_spent(raw) >= task_cap(cfg):
        return _unreleased("task_cap")
    return Release(decision, "released")


def pending(
    raw: TaskState,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    live: frozenset[LandingStepKind],
    now: datetime | None = None,
    released: bool = False,
) -> LandingStep | None:
    """The step worth a `land` child: built (`live`) and not yet dispatched on this head.
    `released` (GH-328) says a human's answer released the escalated diagnose: the gate that
    would skip it for the escalation is not asked."""
    step = step_for(raw, refusal, facts)
    if not takes_action(step.kind, live) or facts.pr_head is None:
        return None
    gate = diagnose_gate(raw, step.kind, now)
    if (gate == "skip" and not (released and step.kind == "diagnose")) or (
        landing.already_dispatched(entries(raw), facts.pr_head, step.kind, facts.current_master)
    ):
        return None
    return step


class RoleRun(Protocol):
    """What the diagnoser's span needs: `finish` is called once, with the patch the role
    produced, so the span carries the step and the refusal it ran for."""

    def finish(self, patch: TaskState) -> None: ...


RoleSpanOpener = Callable[[Role], AbstractContextManager[RoleRun]]
"""Opens the span a role runs under. Injected by the composition root: the application
does not import the telemetry adapter."""


@dataclass(frozen=True)
class LandingContext:
    """What an executor is handed: the step already recorded for `head`, never decided again."""

    task_id: str
    cfg: Configuration
    ports: Ports
    step: LandingStep
    head: str
    upstream: Upstream
    facts: rules.MergeFacts
    log: Callable[[str], None]
    # Spawns `agentic resume` for an issue number and answers its pid: how a step that
    # reopened the technical stage hands the task to it. `None` where nothing can spawn.
    resume: Callable[[int], int | None] | None = None
    launcher: Launcher | None = None
    argv0: str = "agentic"
    role_span: RoleSpanOpener | None = None
    # Waits for the resume just spawned to register (GH-270); `None` waits for nothing.
    await_resume: Callable[[str], bool] | None = None
    # The clock the entry was written with (GH-297); `None`: the executor reads the time.
    clock: Callable[[], str] | None = None
