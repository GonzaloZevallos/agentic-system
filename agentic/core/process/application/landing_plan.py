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
from ..domain.task import INTEGRATION, TaskState
from .checkouts import task_base
from .escalate import ESCALATED
from .ports import GitError, IssueComment, Launcher, Ports, Upstream

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


def spent(
    raw: Mapping[str, object],
    head: str | None,
    waited_s: float | None,
    upstream: str | None = None,
) -> landing.LandingSpent:
    """The passes, clean syncs and reruns on `head` the `landing` list records. A
    `sync_master` answering one of `landing.CONFLICTING_SYNC_REFUSALS` is the conflicting
    one, a model pass; it never counts as a clean sync. `unsynced_for_upstream` counts the
    entries closed with `lacks` equal to `upstream` (the round's master sha), by sha and never
    by head (GH-267); without a sha it is 0. `syncs` and `unsynced_for_upstream` count only the
    entries after the last resolved `land_slice` (`landing.sync_epoch`, GH-297); `passes` never
    reset. `important_passes` counts the `fix_important`
    entries (GH-294), which never enter `passes`."""
    done = entries(raw)
    epoch = landing.sync_epoch(done)

    def count(predicate: Callable[[dict[str, object]], bool]) -> int:
        return sum(1 for entry in done if predicate(entry))

    def count_epoch(predicate: Callable[[Mapping[str, object]], bool]) -> int:
        return sum(1 for entry in epoch if predicate(entry))

    return landing.LandingSpent(
        passes=count(
            lambda e: (
                e.get("step") in ("re_review", "fix")
                or (e.get("step") == "sync_master" and _conflicting(e))
            )
        ),
        syncs=count_epoch(lambda e: e.get("step") == "sync_master" and not _conflicting(e)),
        reruns_on_head=count(lambda e: e.get("step") == "rerun" and e.get("head") == head),
        diagnosed=count(lambda e: e.get("step") == "diagnose" and e.get("delivered") is True) > 0,
        waited_s=waited_s,
        renumbers=count(lambda e: e.get("step") == "renumber"),
        important_passes=count(lambda e: e.get("step") == "fix_important"),
        unsynced_for_upstream=(
            count_epoch(lambda e: e.get("lacks") == upstream) if upstream is not None else 0
        ),
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


def pending(
    raw: TaskState,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    live: frozenset[LandingStepKind],
    now: datetime | None = None,
) -> LandingStep | None:
    """The step worth a `land` child: built (`live`) and not yet dispatched on this head."""
    step = step_for(raw, refusal, facts)
    if not takes_action(step.kind, live) or facts.pr_head is None:
        return None
    if diagnose_gate(raw, step.kind, now) == "skip" or landing.already_dispatched(
        entries(raw), facts.pr_head, step.kind
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
