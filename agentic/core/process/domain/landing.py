"""Aterrizaje (GH-239, ADR-0048): what the system does with an approved pull request that
`rules.auto_merge_decision` refused to merge, instead of leaving it for a human.

`auto_merge_decision` stays the only place that decides *whether* to merge;
`landing_step` is the only place that decides *what to do instead*, from the refusal and
the budgets already spent. No adapter, poller, CLI or executor chooses a recovery action
from a refusal by itself. Pure: facts in, a step out; the I/O of each step is the
application's."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from itertools import zip_longest
from typing import Any, Literal, Protocol, get_args

from . import ci, rules
from .adr_numbers import Renaming
from .roles import LANDING_DIAGNOSER
from .task import FailureReason, LandingEntry, Subtask, SubtaskStatus, TaskState

LandingStepKind = Literal[
    "wait",
    "sync_master",
    "re_review",
    "fix",
    "rerun",
    "file_followups_and_land",
    "renumber",
    "diagnose",
    "none",
]
"""What the landing does about a refusal: `wait` (the next tick decides again), `sync_master`
(merge the upstream into the task branch), `re_review` (a Tech Lead review of the head),
`fix` (a Developer pass on `units`), `rerun` (the CI or the checks, once per head),
`file_followups_and_land` (file the important findings, then merge), `renumber` (rename the
ADR whose number master took and rewrite the references inside the branch's own work,
GH-229), `diagnose` (the
terminal path: the landing diagnoser, then a restart) and `none` (nothing the system can
do: the human's)."""

CONFLICTING_SYNC_REFUSALS: tuple[rules.MergeRefusal, ...] = ("checks_failed", "merge_conflict")
"""The refusals under which a recorded `sync_master` was a conflicting one, hence a model pass."""


def run_kind_of(step: LandingStepKind | None, refusal: rules.MergeRefusal | None) -> rules.RunKind:
    """The kind a `land` child registers under for the step it is about to carry out
    (`None` for the recoveries that have no step). Only `land_checks` or `land_model`; a
    step this table does not name is `land_model`, the kind that counts against the cap.
    `sync_master` is always `land_model`, even a clean one: it reopens the task and spawns a
    resume when the merge overlaps the reviewed paths, conflicts at run time, or a sync or
    push fails, and none of that is known before it runs. `refusal` is kept for a step whose
    kind may depend on it."""
    match step:
        case "rerun" | "file_followups_and_land":
            return "land_checks"
        case _:
            return "land_model"


def run_kind_admits(
    registered: rules.RunKind, step: LandingStepKind | None, refusal: rules.MergeRefusal | None
) -> bool:
    """Whether a `land` child registered as `registered` may carry out `step` (`None` for
    the recoveries). A `land_model` child may do anything; any other kind only a step that
    `run_kind_of` says spends no model, so a child the poller did not count against the cap
    never starts a pass that would have."""
    return registered == "land_model" or run_kind_of(step, refusal) == "land_checks"


LANDING_PASSES_MAX = 3
"""Model passes (`re_review`, `fix`, a conflicting `sync_master`) per task."""

LANDING_SYNCS_MAX = 6
"""Clean `sync_master` per task."""

LANDING_RENUMBERS_MAX = 3
"""`renumber` steps per task (GH-229), counted from the `landing` list and charged neither to
`LANDING_SYNCS_MAX` nor to `LANDING_PASSES_MAX`; declared, not measured. A fourth collision
is the `diagnose`: no further push."""

LANDING_UNSYNCED_MAX = 2
"""`sync_master` passes closed without their upstream (`lacks`) per upstream sha; declared, not
measured. The second is the `diagnose`: a branch that cannot take master in is not retried."""

entry_closed = rules.entry_closed
"""Whether a `landing` entry is finished, re-exported from `rules`, which owns the reading."""

RESTART_DEPTH_MAX = 1
"""Restarts of one issue by the landing; a second exhaustion escalates."""

LANDING_RESUME_SPAWNS_MAX = 2
"""Resume children spawned per landing entry; a third look at one reopening is the human's."""

LANDING_RESUME_GRACE_S = 300
"""Seconds a spawned resume has to record its first run before it is presumed dead; declared,
not measured."""


@dataclass(frozen=True)
class LandingStep:
    """`kind` is the step, `refusal` the reason it answers. `units` is the subtasks a `fix`
    reopens; empty means the reviewed unit of the document."""

    kind: LandingStepKind
    refusal: rules.MergeRefusal
    units: tuple[str, ...] = ()


@dataclass(frozen=True)
class LandingSpent:
    """What the task document says is already spent: model `passes` and clean `syncs`
    (counted from the `landing` list), `reruns_on_head` (entries of kind `rerun` on the
    current head), whether the diagnosis ran, and the seconds waited on an undecidable
    gate (`None`: no anchor); `renumbers` is the `renumber` entries on the list."""

    passes: int
    syncs: int
    reruns_on_head: int
    diagnosed: bool
    waited_s: float | None
    # `renumber` entries across all heads (GH-229): the cap's own counter.
    renumbers: int = 0
    # `sync_master` entries closed with `lacks` equal to the current upstream sha (GH-267).
    unsynced_for_upstream: int = 0


def landing_step(
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    spent: LandingSpent,
    blocking_owners: tuple[str, ...],
) -> LandingStep:
    """The step for `refusal`. A model pass or a sync past its budget is the `diagnose`;
    a task already diagnosed has no step left (`none`). `blocking_owners` is the units that
    own the open blocking records (`blocking_owners`)."""

    def step(kind: LandingStepKind, units: tuple[str, ...] = ()) -> LandingStep:
        return LandingStep(kind, refusal, units)

    def pass_(kind: LandingStepKind, units: tuple[str, ...] = ()) -> LandingStep:
        return step(kind, units) if spent.passes < LANDING_PASSES_MAX else step("diagnose")

    if spent.diagnosed:
        return step("none")
    match refusal:
        case "not_ready" | "pr_not_open" | "eval_set":
            return step("none")
        case "eval_set_unreadable" | "ci_missing" | "ci_unreadable":
            waited = spent.waited_s
            return (
                step("diagnose")
                if waited is not None and waited >= ci.CI_WAIT_MAX_S
                else step("wait")
            )
        case "not_approved" | "head_not_reviewed":
            return pass_("re_review")
        case "open_findings_blocking":
            return pass_("fix", blocking_owners)
        case "open_findings":
            return step("file_followups_and_land")
        case "ci_failed":
            return step("rerun") if spent.reruns_on_head == 0 else pass_("fix", blocking_owners)
        case "checks_failed":
            if facts.failed_step == "conflict":
                return _sync(step, spent, conflicting=True)
            return step("rerun") if spent.reruns_on_head == 0 else pass_("fix", blocking_owners)
        case "merge_conflict":
            return _sync(step, spent, conflicting=True)
        case "attempts_exhausted":
            return _sync(step, spent, conflicting=False)
        case "adr_number_taken":
            # A clean renaming has its own capped step; anything else -- an ambiguous result
            # included -- is the diagnose, so no Developer ever picks an ADR number.
            if isinstance(facts.adr_collision, Renaming) and (
                spent.renumbers < LANDING_RENUMBERS_MAX
            ):
                return step("renumber")
            return step("diagnose")
        case "merge_failed":
            return step("diagnose")
        case "master_moved":
            return step("wait")


def _sync(
    step: Callable[..., LandingStep], spent: LandingSpent, *, conflicting: bool
) -> LandingStep:
    """`sync_master` while the sync budget lasts; a sync known to conflict is also a model
    pass and needs that budget too."""
    if (
        spent.syncs >= LANDING_SYNCS_MAX
        or spent.unsynced_for_upstream >= LANDING_UNSYNCED_MAX
        or (conflicting and spent.passes >= LANDING_PASSES_MAX)
    ):
        return step("diagnose")
    return step("sync_master")


def blocking_owners(state: TaskState) -> tuple[str, ...]:
    """The subtasks that own an open `blocking` record, in the order the table first raised
    them: the units a `fix` reopens, wherever the approval stands (a sibling's blocking
    record is not settled by the integration's approval). A name that is not a subtask of
    the document is no owner."""
    known = state.get("subtasks", {})
    owners: list[str] = []
    for row in rules.open_findings(state):
        if row["severity"] != "blocking":
            continue
        for name in row["subtask"].split(", "):
            if name in known and name not in owners:
                owners.append(name)
    return tuple(owners)


def review_carries(
    conflicted: bool, merge_paths: Iterable[str], branch_paths: Iterable[str]
) -> bool:
    """Whether the Tech Lead's verdict on the branch stands for the head after a sync: the
    merge was clean and the paths it brought in are disjoint from the branch's own."""
    if conflicted:
        return False
    return set(merge_paths).isdisjoint(branch_paths)


@dataclass(frozen=True)
class LineChange:
    """One changed line of a rewrite: the `path` it is in, the line as it was (`old`) and as
    it is (`new`), either empty when the line was added or removed."""

    path: str
    old: str
    new: str


def line_changes(path: str, old: str, new: str) -> list[LineChange]:
    """The lines that differ between two texts of `path`, paired in order; the side a line
    has no counterpart on is empty."""
    before, after = old.splitlines(), new.splitlines()
    found: list[LineChange] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != "equal":
            found.extend(
                LineChange(path, x, y)
                for x, y in zip_longest(before[i1:i2], after[j1:j2], fillvalue="")
            )
    return found


def renumber_carries(changes: Sequence[LineChange]) -> bool:
    """Whether the Tech Lead's verdict stands for the head after a renumber (GH-229): every
    changed line is under `docs/` or is, on both sides, a `#` comment line once its leading
    whitespace is stripped. A docstring, a string literal or a trailing comment is code that
    runs or is read by a tool, so it does not carry. The twin of `review_carries`, which
    decides the same for a sync merge."""

    def comment(line: str) -> bool:
        return not line.strip() or line.lstrip().startswith("#")

    return all(
        change.path.startswith("docs/") or (comment(change.old) and comment(change.new))
        for change in changes
    )


def already_dispatched(landing: Sequence[Mapping[str, object]], head: str, step: str) -> bool:
    """Whether `(head, step)` is already in the document's `landing` list: the idempotency
    key, written before the dispatch. A diagnose entry that never delivered is an attempt
    (`diagnose_retry`), not a taken step; so is a sync closed with `lacks` (GH-267), which
    the unsynced budget counts instead."""
    return any(
        entry.get("head") == head
        and entry.get("step") == step
        and "lacks" not in entry
        and (step != "diagnose" or entry.get("delivered") is True)
        for entry in landing
    )


def merge_owner(
    merge_head: str | None,
    upstream: str | None,
    head: str,
    entries: Sequence[Mapping[str, object]],
) -> Literal["none", "own", "foreign"]:
    """Whose merge `MERGE_HEAD` is (GH-267): `none` when there is no merge in progress,
    `own` when it is a sha the landing merged (this round's upstream or head, or the
    `head` / `upstream` of any recorded entry), `foreign` for any other, a person's merge
    the landing must leave alone."""
    if merge_head is None:
        return "none"
    known: set[object] = {upstream, head}
    for entry in entries:
        known.update((entry.get("head"), entry.get("upstream")))
    return "own" if merge_head in known else "foreign"


def landing_open(state: TaskState) -> bool:
    """A landing pass is in flight: the last `landing` entry is not resolved and some
    subtask is reopened for it (`rules.landing_open`, where `resume_target` reads it)."""
    return rules.landing_open(state)


def entry_of(raw: Mapping[str, object]) -> LandingEntry:
    """The `landing` entry `raw` says, each known key kept when it has its type."""
    out: LandingEntry = {}
    if isinstance(head := raw.get("head"), str):
        out["head"] = head
    if isinstance(step := raw.get("step"), str):
        out["step"] = step
    if isinstance(refusal := raw.get("refusal"), str):
        out["refusal"] = refusal
    if isinstance(at := raw.get("at"), str):
        out["at"] = at
    if isinstance(resolved := raw.get("resolved_at"), str):
        out["resolved_at"] = resolved
    for key in ("upstream", "closed_at", "lacks"):
        if isinstance(text := raw.get(key), str):
            out[key] = text
    if isinstance(cost := raw.get("cost_usd"), int | float) and not isinstance(cost, bool):
        out["cost_usd"] = float(cost)
    if isinstance(base := raw.get("cost_base"), int | float) and not isinstance(base, bool):
        out["cost_base"] = float(base)
    if isinstance(checks := raw.get("checks"), list) and all(isinstance(c, str) for c in checks):
        out["checks"] = [c for c in checks if isinstance(c, str)]
    if isinstance(spawns := raw.get("resume_spawns"), int) and not isinstance(spawns, bool):
        out["resume_spawns"] = spawns
    if isinstance(spawned := raw.get("resume_spawned_at"), str):
        out["resume_spawned_at"] = spawned
    if isinstance(delivered := raw.get("delivered"), bool):
        out["delivered"] = delivered
    if (failure := _failure_of(raw.get("failure"))) is not None:
        out["failure"] = failure
    return out


def _failure_of(value: object) -> FailureReason | None:
    """`value` as a `FailureReason`, `None` when it is not one: the closed set is the type's
    own, never a second list."""
    for reason in get_args(FailureReason):
        if value == reason:
            found: FailureReason = reason
            return found
    return None


def diagnose_attempts(raw: Mapping[str, object]) -> list[LandingEntry]:
    """The diagnose entries of the document's `landing` list that never delivered, after the
    last one that did: the attempts the retry calendar counts, oldest first."""
    entries = raw.get("landing")
    kept = (
        [entry_of(e) for e in entries if isinstance(e, Mapping)]
        if isinstance(entries, list)
        else []
    )
    diagnoses = [e for e in kept if e.get("step") == "diagnose"]
    delivered = [i for i, e in enumerate(diagnoses) if e.get("delivered")]
    after = diagnoses[delivered[-1] + 1 :] if delivered else diagnoses
    return [e for e in after if not e.get("delivered")]


def diagnose_retry(
    raw: Mapping[str, object], now: datetime
) -> Literal["none", "due", "waiting", "escalate"]:
    """What the landing does about a diagnosis that did not deliver (GH-257): `none` when no
    attempt is waiting on one, `escalate` when the last attempt has no external `failure` or
    the calendar is spent, else `due` or `waiting` on the shared retake calendar
    (`rules.retake_calendar`), measured from the last attempt's own `at`. No run delivers
    between two attempts, so the chain is the flat 429 calendar and never the hourly week.
    Timestamps are compared as aware datetimes, never as strings: an unreadable or naive one
    waits."""
    attempts = diagnose_attempts(raw)
    if not attempts:
        return "none"
    last = attempts[-1]
    reason = last.get("failure")
    if reason is None or reason not in rules.EXTERNAL_REASONS:
        return "escalate"
    count = len(attempts)
    limit = rules.auto_resume_limit(_landing_view(raw), reason)
    since = _aware(last.get("at"))
    candidate = rules.ResumeCandidate(0, reason, since or now, count, limit, count)
    if rules.resume_exhausted(candidate):
        return "escalate"
    if since is None or now.tzinfo is None or now.utcoffset() is None:
        return "waiting"
    schedule, prior, slots = rules.retake_calendar(reason, count, count, limit)
    due = rules.next_attempt_at(since, prior, slots, now, schedule=schedule)
    return "due" if due is not None else "waiting"


def unescalated(raw: Mapping[str, object]) -> dict[str, Any]:
    """The one patch that clears a landing step's escalation: the task is back at the status
    it had before it (`rules.resumed_status`) and the epoch is closed with a `resumed` entry.
    Never writes `cost_usd`. No other module builds it."""
    view = _landing_view(raw)
    if isinstance(pull_request := raw.get("pull_request"), str):
        view["pull_request"] = pull_request
    return {
        "escalated": None,
        "escalation_class": None,
        "status": rules.resumed_status(view),
        "pause_log": [{"kind": "resumed"}],
    }


ESCALATED_COMMENT = "Escalated: "
"""How an escalation comment starts. A copy of `application.escalate.ESCALATED`, which a test
pins: the domain cannot import the application."""


class Commented(Protocol):
    """A tracker comment as `escalated_after` reads it: `application.ports.IssueComment`
    satisfies it as it is."""

    @property
    def body(self) -> str: ...

    @property
    def created_at(self) -> str: ...


def _diagnose_entries(raw: Mapping[str, object]) -> list[Mapping[str, object]]:
    entries = raw.get("landing")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, Mapping) and e.get("step") == "diagnose"]


def diagnose_at(raw: Mapping[str, object]) -> object:
    """The `at` of the document's last diagnose entry, as written (`None` without one)."""
    entries = _diagnose_entries(raw)
    return entries[-1].get("at") if entries else None


def _closing_epoch(raw: Mapping[str, object]) -> list[Mapping[str, object]] | None:
    """The `pause_log` entries after the last `resumed`, `None` when the log is not a list of
    mappings."""
    log = raw.get("pause_log")
    if not isinstance(log, list) or not all(isinstance(e, Mapping) for e in log):
        return None
    last = max((i for i, e in enumerate(log) if e.get("kind") == "resumed"), default=-1)
    return log[last + 1 :]


def _stranded_failure(raw: Mapping[str, object]) -> FailureReason | None:
    """The last failure of the epoch being closed, when the epoch is exactly the stranded
    shape: one or more `failure` entries, each external and from the diagnoser's own
    section, and an `escalation` entry that reads `failure`; `None` for any other epoch (a
    real escalation, another role's failure)."""
    epoch = _closing_epoch(raw)
    if epoch is None:
        return None
    failures = [e for e in epoch if e.get("kind") == "failure"]
    escalations = [e for e in epoch if e.get("kind") == "escalation"]
    if not failures or not escalations:
        return None
    if any(e.get("value") != "failure" for e in escalations):
        return None
    if any(
        e.get("section") != LANDING_DIAGNOSER.name or e.get("value") not in rules.EXTERNAL_REASONS
        for e in failures
    ):
        return None
    return _failure_of(failures[-1].get("value"))


def stranded_diagnose(raw: Mapping[str, object], escalated_after: bool) -> bool:
    """A document GH-257 stranded: the Landing Diagnoser's failed run escalated through the
    graph's own patch, outside the graph, and nobody was told (ADR-0048, addendum). True only
    for the whole legacy shape: waiting on a human as class `failure`, a pull request, the
    diagnoser the current role, the last diagnose entry with neither `delivered` nor
    `failure`, and an epoch of external failures of the diagnoser alone ending in the
    escalation. `escalated_after` (`escalated_after`) says a comment already told a human, so
    the stop is real and stays."""
    if escalated_after:
        return False
    pull_request = raw.get("pull_request")
    if (
        raw.get("status") != "waiting_on_human"
        or raw.get("escalation_class") != "failure"
        or not isinstance(pull_request, str)
        or not pull_request
        or raw.get("current_role") != LANDING_DIAGNOSER.name
    ):
        return False
    entries = _diagnose_entries(raw)
    if not entries or "delivered" in entries[-1] or "failure" in entries[-1]:
        return False
    return _stranded_failure(raw) is not None


def escalated_after(comments: Iterable[Commented], at: object, prefix: str) -> bool:
    """Whether a comment starting with `prefix` (the escalation's) was created after `at`:
    a human was told of this stop and it is theirs. Aware datetimes are compared, never
    strings; a naive or unreadable time on `at` or on a matching comment gives True, so the
    document stays escalated."""
    mine = [c for c in comments if c.body.startswith(prefix)]
    if not mine:
        return False
    since = _aware(at)
    if since is None:
        return True
    for comment in mine:
        created = _aware(comment.created_at.replace("Z", "+00:00"))
        if created is None or created > since:
            return True
    return False


def migration(raw: Mapping[str, object]) -> dict[str, Any]:
    """The one write that frees a stranded document (`stranded_diagnose`): `unescalated`,
    plus the failure of the epoch it closes stamped on the last diagnose entry, so the
    ordinary retry takes it from there. `record_closure` replaces a key whole, so the
    `pause_log` is the history with the `resumed` marker appended. Never writes `cost_usd`."""
    patch = unescalated(raw)
    reason = _stranded_failure(raw)
    entries = raw.get("landing")
    written = (
        [dict(e) for e in entries if isinstance(e, Mapping)] if isinstance(entries, list) else []
    )
    last = max((i for i, e in enumerate(written) if e.get("step") == "diagnose"), default=None)
    if last is not None and reason is not None:
        written[last] = {**written[last], "failure": reason}
    log = raw.get("pause_log")
    patch["pause_log"] = [*(log if isinstance(log, list) else []), *patch["pause_log"]]
    patch["landing"] = written
    return patch


def _aware(value: object) -> datetime | None:
    """`value` as an aware datetime, or `None` when it is not an ISO string with an offset."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _status_of(value: object) -> SubtaskStatus | None:
    match value:
        case "pending":
            return "pending"
        case "delivered":
            return "delivered"
        case "approved":
            return "approved"
        case "returned":
            return "returned"
    return None


def _landing_view(raw: Mapping[str, object]) -> TaskState:
    """The part of the document `rules.landing_open` reads -- the `landing` entries and each
    subtask's status -- as a `TaskState`, so the one definition of an open landing is reused."""
    state: TaskState = {"id": "", "title": "", "description": ""}
    entries = raw.get("landing")
    state["landing"] = (
        [entry_of(entry) for entry in entries if isinstance(entry, Mapping)]
        if isinstance(entries, list)
        else []
    )
    units = raw.get("subtasks")
    subtasks: dict[str, Subtask] = {}
    for name, unit in units.items() if isinstance(units, Mapping) else []:
        if isinstance(unit, Mapping) and (status := _status_of(unit.get("status"))):
            subtasks[str(name)] = {"status": status}
    state["subtasks"] = subtasks
    return state


def resume_owed(raw: Mapping[str, object], now: datetime) -> bool:
    """A reopened landing whose resume child is owed: the pass is open, the task is not
    escalated, fewer than `LANDING_RESUME_SPAWNS_MAX` children were spawned for the last entry,
    the last one is older than `LANDING_RESUME_GRACE_S` and no run started since it. A timestamp
    that is present but unparseable or naive gives False: it cannot be shown that the child
    is gone. Timestamps are compared as aware datetimes, never as strings."""
    state = _landing_view(raw)
    if not rules.landing_open(state) or raw.get("escalated"):
        return False
    last = state["landing"][-1]
    if last.get("resume_spawns", 0) >= LANDING_RESUME_SPAWNS_MAX:
        return False
    spawned_at = last.get("resume_spawned_at")
    if spawned_at is None:
        return True
    spawned = _aware(spawned_at)
    if spawned is None or now - spawned < timedelta(seconds=LANDING_RESUME_GRACE_S):
        return False
    runs = raw.get("runs")
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, Mapping) or run.get("started_at") is None:
            continue
        started = _aware(run["started_at"])
        if started is None or started >= spawned:
            return False
    return True


def with_cost_base(
    entries: Sequence[Mapping[str, object]], head: str, step: str, cost_base: float
) -> list[LandingEntry]:
    """`entries` with `cost_base` set on the last entry, when it is keyed `(head, step)`;
    an earlier entry is never written to (GH-267)."""
    out = [entry_of(entry) for entry in entries]
    if out and out[-1].get("head") == head and out[-1].get("step") == step:
        out[-1]["cost_base"] = cost_base
    return out


def resolve_open(
    entries: Sequence[Mapping[str, object]], at: str, cost_now: float
) -> list[LandingEntry]:
    """`entries` with the last one, when unresolved, closed at `at`: its `cost_usd` is
    `cost_now` less the `cost_base` it was written with. An already closed last entry,
    or none, leaves the list as it was."""
    out = [entry_of(entry) for entry in entries]
    if not out or entry_closed(out[-1]):
        return out
    out[-1]["resolved_at"] = at
    out[-1]["cost_usd"] = _spent_since_base(out[-1], cost_now)
    return out


def close_unsynced(
    entries: Sequence[Mapping[str, object]], at: str, cost_now: float, lacks: str
) -> list[LandingEntry]:
    """`resolve_open`'s twin for a sync whose delivered head lacks its upstream (GH-267): the
    last entry, when not closed, gets `closed_at`, `lacks` and its `cost_usd`; never
    `resolved_at`."""
    out = [entry_of(entry) for entry in entries]
    if not out or entry_closed(out[-1]):
        return out
    out[-1]["closed_at"] = at
    out[-1]["lacks"] = lacks
    out[-1]["cost_usd"] = _spent_since_base(out[-1], cost_now)
    return out


def _spent_since_base(entry: LandingEntry, cost_now: float) -> float:
    return round(max(cost_now - entry.get("cost_base", cost_now), 0.0), 4)


def sync_verdict(
    entry: Mapping[str, object], contains: bool | None
) -> Literal["synced", "unsynced", "not_a_sync"]:
    """What a delivered `sync_master` entry achieved (GH-267): `contains` is the answer to
    whether the delivered head holds the entry's `upstream`. `not_a_sync` for another step or an
    entry without a string `upstream` (written before this rule: it keeps resolving as it did).
    Only `True` is `synced`; `False` and `None` (unreadable) are `unsynced`. Pure: no git."""
    if entry.get("step") != "sync_master" or not isinstance(entry.get("upstream"), str):
        return "not_a_sync"
    return "synced" if contains is True else "unsynced"


def merge_owner(
    merge_head: str | None,
    upstream: str | None,
    head: str | None,
    entries: Sequence[Mapping[str, object]],
) -> Literal["none", "system", "foreign"]:
    """Whose merge a worktree's MERGE_HEAD is (GH-267): `none` without one, `system` when it is
    the round's `upstream`, the PR `head` or any `upstream` or `head` an entry recorded (through
    `rules.same_commit`), else `foreign`. Pure: no git."""
    if merge_head is None:
        return "none"
    known = [upstream, head]
    for entry in entries:
        known.extend(v for v in (entry.get("upstream"), entry.get("head")) if isinstance(v, str))
    return "system" if any(rules.same_commit(merge_head, k) for k in known) else "foreign"


def followup_marker(task_id: str, lineage: int) -> str:
    """The first line of a follow-up issue body, which the deduplication reads back."""
    return f"<!-- agentic:followup task={task_id} lineage={lineage} -->"


def marker_lineage(body: str, task_id: str) -> int | None:
    """The lineage in `task_id`'s follow-up marker in `body`, or `None`. Matched byte for
    byte on the marker's own shape, never through the tracker's search."""
    found = re.search(
        rf"<!-- agentic:followup task={re.escape(task_id)} lineage=([0-9]+) -->", body
    )
    return int(found.group(1)) if found else None


def restart_depth(raw: Mapping[str, object]) -> int:
    """How many times the landing restarted this issue, from the task document."""
    value = raw.get("landing_restarts", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def marker_parent(body: str) -> tuple[str, int] | None:
    """`(task id, lineage)` of the follow-up marker in `body`, whichever task wrote it: a
    follow-up issue carries its parent's id, so a task whose own request holds one is
    itself a follow-up, and files nothing further (GH-239). `None` without a marker."""
    found = re.search(r"<!-- agentic:followup task=(\S+) lineage=([0-9]+) -->", body)
    return (found.group(1), int(found.group(2))) if found else None


def flake_marker(check: str) -> str:
    """The first line of a flake issue body, which the deduplication reads back: one open
    issue per check name, whichever task saw it flake."""
    return f"<!-- agentic:flake check={check} -->"
