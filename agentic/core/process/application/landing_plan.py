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

from ..domain import ci, landing, plan_settlement, rules
from ..domain.landing import LandingStep, LandingStepKind
from ..domain.roles import Role
from ..domain.task import (
    INTEGRATION,
    UPSTREAM_KEY,
    VERDICT_KEY,
    VERDICT_SYNC,
    VERDICT_UNLANDABLE,
    DiagnosisVerdict,
    ReleasePatch,
    TaskState,
    apply,
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
        "settle",
        "escalate_ledger",
        "escalate_adr",
    }
)
"""Every step has its executor: `landing_sync`'s (`sync_master`, `re_review`, `renumber`),
`landing_fix`'s (`fix`, `fix_important`, `rerun`, `file_followups_and_land`, `diagnose`,
`settle`, `escalate_ledger`, `escalate_adr`) and
`landing_slice`'s (`land_slice`, GH-297). A
step outside the set is neither recorded nor spawned, so a refusal whose step is inert logs
as it always did."""


ALWAYS_TAKEN: frozenset[LandingStepKind] = frozenset({"escalate"})
"""Steps taken whatever `live` says (GH-371): `escalate` runs no model, spends nothing and is
recorded once per head, so it has no rollout to hold back; its executor is
`landing_fix.escalate_red_on_base`."""


def takes_action(kind: LandingStepKind, live: frozenset[LandingStepKind]) -> bool:
    """Whether `kind` is a step the system takes now: built (`live`) or one of `ALWAYS_TAKEN`,
    and not `wait` (the next tick decides again) or `none` (the human's), which are never
    recorded or spawned whatever `live` says."""
    return (kind in live or kind in ALWAYS_TAKEN) and kind not in ("wait", "none")


def all_entries(raw: Mapping[str, object]) -> list[dict[str, object]]:
    """The document's whole `landing` list, tolerant of its absence or a malformed value:
    for the budget counters that a restoration never hands back (`passes`, `renumbers`) and for
    the writers, which rewrite the list by index. A reader of a terminal, idempotency or
    in-flight fact takes `live_entries` instead (GH-406)."""
    value = raw.get("landing")
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


entries = all_entries
"""The name the suites written before GH-406 call `all_entries` by; no code reads it."""


def live_entries(raw: Mapping[str, object]) -> list[dict[str, object]]:
    """The `landing` entries of the run now living (`landing.live_entries`), as the plain
    dicts the counters read."""
    return [e for e in landing.live_entries(raw) if isinstance(e, dict)]


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


Ancestry = Callable[[], bool | None]
"""Whether the head holds the upstream sha, read only when a CI release is waiting for its step
(`landing.ci_release_due`); `None` from it is an ancestry that could not be read."""


def head_ancestry(ports: Ports, cfg: Configuration, head: str, upstream: str) -> Ancestry:
    """The `Ancestry` over git: `upstream` an ancestor of `head`. A git failure is unreadable."""

    def read() -> bool | None:
        try:
            return ports.workspace.is_ancestor(cfg.product_dir, upstream, head)
        except GitError:
            return None

    return read


def spent(
    raw: Mapping[str, object],
    head: str | None,
    waited_s: float | None,
    upstream: str | None = None,
    ancestry: Ancestry | None = None,
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
    done = all_entries(raw)
    live = live_entries(raw)
    epoch = landing.sync_epoch(live)

    def count(predicate: Callable[[dict[str, object]], bool]) -> int:
        """The budgets a restoration never hands back, over the whole list."""
        return sum(1 for entry in done if predicate(entry))

    def count_live(predicate: Callable[[dict[str, object]], bool]) -> int:
        return sum(1 for entry in live if predicate(entry))

    def count_epoch(predicate: Callable[[Mapping[str, object]], bool]) -> int:
        return sum(1 for entry in epoch if predicate(entry))

    def for_upstream(entry: Mapping[str, object]) -> bool:
        return upstream is not None and entry.get(UPSTREAM_KEY) == upstream

    last_verdict, verdict_syncs, due = _readings(epoch, upstream)
    release_due = landing.ci_release_due(live, head, upstream)
    return landing.LandingSpent(
        passes=count(lambda e: e.get("step") in ("re_review", "fix")),
        syncs=count_epoch(lambda e: e.get("step") == "sync_master"),
        reruns_on_head=count_live(lambda e: e.get("step") == "rerun" and e.get("head") == head),
        diagnoses=count_live(lambda e: e.get("step") == "diagnose" and e.get("delivered") is True),
        waited_s=waited_s,
        renumbers=count(lambda e: e.get("step") == "renumber"),
        important_passes=(
            landing.LANDING_IMPORTANT_PASSES_MAX if rules.important_pass_spent(raw) else 0
        ),
        unsynced_for_upstream=(
            count_epoch(lambda e: e.get("lacks") == upstream) if upstream is not None else 0
        ),
        conflicting_syncs_for_upstream=count_epoch(
            lambda e: e.get("step") == "sync_master" and _conflicting(e) and for_upstream(e)
        ),
        conflict_ci_syncs_on_pair=count_epoch(
            lambda e: (
                e.get("step") == "sync_master"
                and e.get("refusal") == "ci_blocked_by_conflict"
                and e.get("head") == head
                and for_upstream(e)
            )
        ),
        verdict_syncs_for_upstream=verdict_syncs,
        verdict_sync_due=due,
        last_verdict=last_verdict,
        checks_rerun_on_head=_gate_reran(raw, head),
        ci_release_due=release_due,
        head_has_upstream=ancestry() if release_due and ancestry is not None else None,
    )


def _after_restoration(written: object, restored_at: str | None) -> bool:
    """Whether a record dated `written` is of the run now living (GH-406): it carries an aware
    time that is not before the restoration's. A record with no time, or with one that cannot
    be read, is the archived run's -- `merge.run_checks` stamps `at` on the records of a
    restored task only."""
    stamp, restored = _aware(written), _aware(restored_at)
    return stamp is not None and restored is not None and stamp >= restored


def _aware(value: object) -> datetime | None:
    """`value` as an aware datetime, `None` when it is not an ISO string with an offset."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _gate_reran(raw: Mapping[str, object], head: str | None) -> bool:
    """The recorded `merge_checks` is of `head` and holds a red check whose last row is the
    gate's own rerun (ADR-0066). Tolerant of a record from before the rerun, which has no
    marker, and of one that is not the shape the merge writes."""
    record = raw.get("merge_checks")
    if head is None or not isinstance(record, Mapping) or record.get("head") != head:
        return False
    if rules.restored(raw) and not _after_restoration(record.get("at"), rules.restored_at(raw)):
        return False
    checks = record.get("checks")
    return isinstance(checks, list) and any(
        isinstance(entry, Mapping) and entry.get("passed") is False and entry.get("rerun") is True
        for entry in checks
    )


def step_for(
    raw: TaskState,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    slicing: landing.Slice | landing.RestartWhy | Callable[[], landing.SliceInputs] | None = None,
    ancestry: Ancestry | None = None,
) -> LandingStep:
    """The one question every caller asks: `landing_step` over the document's spent budgets.
    `slicing` is what `landing_step` decides a slice from at the exhaustion (`slice_inputs`,
    read lazily), `None` as before GH-297. `ancestry` (GH-442) is read only for a document whose
    CI release is waiting for its step."""
    return landing.landing_step(
        refusal,
        facts,
        spent(raw, facts.pr_head, facts.ci_waited_s, facts.current_master, ancestry),
        landing.blocking_owners(raw),
        important_units=landing.important_owners(raw),
        slicing=slicing,
        ledger_only=plan_settlement.ledger_only(refusal, facts, raw),
        pending=plan_settlement.pending(raw),
        escalated=escalation_recorded(raw, facts.pr_head, refusal, "escalate_ledger"),
        adr_escalated=escalation_recorded(
            raw, facts.pr_head, refusal, "escalate_adr", resolved_only=True
        ),
    )


def escalation_recorded(
    raw: Mapping[str, object],
    head: str | None,
    refusal: str,
    kind: LandingStepKind,
    *,
    resolved_only: bool = False,
) -> bool:
    """Whether the `landing` list holds an entry of step `kind` for this head and refusal: the
    one question the human was asked, never twice. `resolved_only` counts only an entry that
    carries `resolved_at`, i.e. one written after its comment was posted, so a question whose
    comment failed is asked again."""
    return any(
        entry.get("step") == kind
        and entry.get("head") == head
        and entry.get("refusal") == refusal
        and (not resolved_only or bool(entry.get("resolved_at")))
        for entry in live_entries(raw)
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
    refused = landing.refused_slices(live_entries(raw))
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


ReleaseWhy = Literal[
    "released",
    "released_by_ci",
    "waiting_on_human",
    "waiting_on_base_ci",
    "needs_restart",
    "task_cap",
]
"""Why an escalated landing is or is not released (GH-328, GH-442): a human's decision released
it (`released`), the base's CI went green and released a CI escalation (`released_by_ci`), it
waits for a human (or for the label to go, or for an open issue), a CI escalation waits for the
base's CI or an owner comment (`waiting_on_base_ci`), its shape is a restart only a human
starts, or the task cap is spent. Closed: `land` maps each to its log line and its
`LandResult.reason`."""


@dataclass(frozen=True)
class Release:
    """`released` is the one answer a caller reads: whether the escalation is released. Never
    infer it from `decision`, which is the human's answer when one released it and `None` for
    a release by CI (GH-442)."""

    decision: str | None
    why: ReleaseWhy
    released: bool = False


def _unreleased(why: ReleaseWhy) -> Release:
    return Release(None, why)


def _owner_release(
    ports: Ports, cfg: Configuration, task_id: str, raw: TaskState, number: int
) -> Release:
    """A human's answer releasing the escalation of `raw`: every one of a non-system comment
    after the last `Escalated:` comment (`resume.decision_since`, the one reader), the issue
    open, its labels readable and without `needs-human`, and the task under its cap
    (`rules.task_spent`, the figure `run_role` measures). A tracker that cannot answer is a
    wait, never a release."""
    from .resume import decision_since  # resume imports poll, which imports this module

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
    return Release(decision, "released", True)


def _escalated_head(raw: TaskState) -> str | None:
    """The head of the last live `escalate` entry: what the CI shape is judged on when the
    caller did not say which head it holds."""
    found = [e for e in live_entries(raw) if e.get("step") == "escalate"]
    head = found[-1].get("head") if found else None
    return head if isinstance(head, str) else None


def _base_is_green(
    ports: Ports, raw: TaskState, entry: Mapping[str, object], report: ci.CiReport
) -> bool:
    """Whether the base branch's latest CI is green over the names `entry` was about (its
    `checks`, else the stack's required ones, `rules.ci_gate_names`). Anything unreadable, still
    running or incomplete is not green."""
    if not report.base_ref:
        return False
    try:
        declared = ports.stack.ci(task_base(raw, ports))
        plan = raw.get("ci_required")
        required = rules.ci_gate_names(declared, plan if isinstance(plan, list) else []).names
        names = landing.escalation_checks(entry, required)
        return ci.base_green(ports.tracker.branch_ci(report.base_ref), names)
    except (ValueError, OSError):
        return False


def release_asked(raw: Mapping[str, object], kind: LandingStepKind) -> bool:
    """Whether the escalated document `raw` is held back by its escalation at the table's step
    `kind`: only a `diagnose` is (GH-328), every other step is dispatched as if the document
    were not escalated. The one condition `land`, the poller and `resume` ask before they ask
    `release`, so none holds a copy of it."""
    return kind == "diagnose" and bool(raw.get("escalated"))


def release(
    ports: Ports,
    cfg: Configuration,
    task_id: str,
    raw: TaskState,
    number: int,
    *,
    head: str | None = None,
    upstream: str | None = None,
) -> Release:
    """Whether the escalated landing of `raw` is released (GH-328, GH-442), the one decision
    `resume`, `land` and the poller ask; they read `Release.released`.

    A CI escalation (`landing.ci_escalation`) is released once per (head, upstream sha) pair
    when the base branch's CI is green again, or by an owner's comment through the same route
    as below; otherwise it waits (`waiting_on_base_ci`). `head` and `upstream` are the caller's
    (`MergeFacts.pr_head`, `current_master`); without them the pull request's report and the
    workspace's upstream are read. A tracker that cannot answer is a wait.

    Any other escalation keeps its answer (GH-328): only class `failure` with undelivered
    diagnose attempts is releasable, by a human's answer (`_owner_release`); every other one, the
    depth-1 escalation after a delivered diagnosis included, is the human's restart, and the
    tracker is not read for it."""
    shape = _escalated_head(raw)
    if shape is not None and head in (None, shape) and landing.ci_escalation(raw, shape):
        return _ci_release(ports, cfg, task_id, raw, number, shape, upstream)
    if raw.get("escalation_class") != "failure" or not landing.diagnose_attempts(raw):
        return _unreleased("needs_restart")
    return _owner_release(ports, cfg, task_id, raw, number)


def _ci_release(
    ports: Ports,
    cfg: Configuration,
    task_id: str,
    raw: TaskState,
    number: int,
    head: str,
    upstream: str | None,
) -> Release:
    wait = _unreleased("waiting_on_base_ci")
    entry = landing.ci_escalation_entry(raw, head)
    reference = raw.get("pull_request")
    if entry is None or not reference:
        return wait
    if upstream is None:
        upstream = ports.workspace.upstream().sha
    if upstream is None or landing.ci_released_for(entry, upstream):
        return wait
    try:
        report = ports.tracker.ci_report(str(reference))
    except (ValueError, OSError):
        report = None
    if (
        isinstance(report, ci.CiReport)
        and rules.same_commit(report.head_sha, head)
        and _base_is_green(ports, raw, entry, report)
    ):
        return Release(None, "released_by_ci", True)
    owner = _owner_release(ports, cfg, task_id, raw, number)
    return owner if owner.released else wait


def released_step(
    raw: TaskState,
    outcome: Release,
    facts: rules.MergeFacts,
    refusal: rules.MergeRefusal,
    *,
    at: str,
    slicing: landing.Slice | landing.RestartWhy | Callable[[], landing.SliceInputs] | None = None,
    ancestry: Ancestry | None = None,
) -> LandingStep:
    """The step the table gives on the document as the release `outcome` leaves it (GH-442):
    `landing.released_by_ci` for a release by CI (stamped with `facts.current_master`),
    `landing.released` for a human's decision, the document as it is for no release. The one
    function the poller and `land` ask, so neither decides the step after a release itself."""
    patch = release_patch(raw, outcome, facts.current_master, at)
    view = apply(raw, patch) if patch else raw
    return step_for(view, refusal, facts, slicing, ancestry)


def release_patch(raw: TaskState, outcome: Release, upstream: str | None, at: str) -> ReleasePatch:
    """What the release `outcome` writes to `raw`, and `{}` for no release: `landing.released`
    for a human's decision, `landing.released_by_ci` (stamped with `upstream`) for a release by
    CI. The one place that picks the builder, so `released_step` and `land` write what the step
    was computed on."""
    if not outcome.released:
        return {}
    if outcome.decision is not None:
        return landing.released(raw, outcome.decision, at)
    return landing.released_by_ci(raw, upstream, at) if upstream is not None else {}


def pending(
    raw: TaskState,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    live: frozenset[LandingStepKind],
    now: datetime | None = None,
    released: bool = False,
    ancestry: Ancestry | None = None,
) -> LandingStep | None:
    """The step worth a `land` child: built (`live`) and not yet dispatched on this head.
    `released` (GH-328) says a human's answer released the escalated diagnose: the gate that
    would skip it for the escalation is not asked."""
    step = step_for(raw, refusal, facts, ancestry=ancestry)
    if not takes_action(step.kind, live) or facts.pr_head is None:
        return None
    gate = diagnose_gate(raw, step.kind, now)
    if (gate == "skip" and not (released and step.kind == "diagnose")) or (
        landing.already_dispatched(
            live_entries(raw),
            facts.pr_head,
            step.kind,
            facts.current_master,
            unstarted=landing.landing_pass(raw) == "unstarted",
        )
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
