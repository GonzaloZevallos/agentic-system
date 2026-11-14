"""How long a task took, read over its document (GH-470): the cycle (first run started to
closed, or to the last run ended while it is open) and the active time (the union of its
run intervals, so overlapping Developers count once).

Pure domain, a read over `runs[]` and three scalar keys; nothing is recorded and neither
figure enters `scorecard.verdict`. A task missing a bound is `excluded`, never zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any, Literal, cast, get_args

from agentic.core.process.domain import ci, rules, task_path
from agentic.core.process.domain.rules import pause_kind
from agentic.core.process.domain.task import FailureReason, PauseEntry, RoleRun, Status

from . import parallel_factor

CycleClass = Literal["closed", "open", "excluded"]

# The ends that are no delivery of their own: the run built nothing for a pull request to
# carry (a split parent, a bug ruled expected, a plan that is only tracker work).
NO_DELIVERY: frozenset[Status] = frozenset({"split", "discarded", "tracker_only"})


def _runs(document: Mapping[str, Any]) -> list[RoleRun]:
    return list(document.get("runs") or [])


def moment(value: object) -> datetime | None:
    """An ISO-8601 timestamp as an aware datetime (naive reads as UTC), `None` when it is
    not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def cycle_class(document: Mapping[str, Any]) -> CycleClass:
    """Whether the task enters the time figures, and as what. In order: a task that is no
    delivery, or that its slices closed, is `excluded`; so is one with no run start; a
    `closed_at` makes it `closed`; else it is `open` when some run has ended, and
    `excluded` while the first is still running."""
    status: str = document.get("status", "open")
    if status in NO_DELIVERY or document.get("closed_by_slices"):
        return "excluded"
    runs = _runs(document)
    if not any(run.get("started_at") for run in runs):
        return "excluded"
    if document.get("closed_at"):
        return "closed"
    if any(run.get("ended_at") for run in runs):
        return "open"
    return "excluded"


def cycle_seconds(document: Mapping[str, Any]) -> float | None:
    """From the first `started_at` to `closed_at`, or to the last `ended_at` while open.
    `None` for an `excluded` task, an unreadable bound, or an end before the start."""
    klass = cycle_class(document)
    if klass == "excluded":
        return None
    runs = _runs(document)
    starts = [m for run in runs if (m := moment(run.get("started_at")))]
    if not starts:
        return None
    if klass == "closed":
        end = moment(document.get("closed_at"))
    else:
        ends = [m for run in runs if (m := moment(run.get("ended_at")))]
        end = max(ends) if ends else None
    if end is None:
        return None
    seconds = (end - min(starts)).total_seconds()
    return seconds if seconds >= 0 else None


def active_seconds(document: Mapping[str, Any]) -> float | None:
    """The union of the run intervals that have both bounds, in seconds; `None` when no
    run has both (never zero in its place)."""
    intervals = [i for run in _runs(document) if (i := parallel_factor.interval(run))]
    if not intervals:
        return None
    try:
        return parallel_factor.union_seconds(intervals)
    except TypeError:  # a naive and an aware timestamp in one document cannot be ordered
        return None


# What a task's cycle is made of (GH-467). Every instant below enters through `moment`, so the
# intervals are aware and `parallel_factor.clip`, `difference` and `union_seconds` can order them.
# The CI wait is read from `process.domain.ci.waited_seconds`, the one place it is derived.

PauseClass = Literal["external", "human"]
Interval = parallel_factor.Interval


@dataclass(frozen=True)
class Pause:
    """One closed pause: from its start to the `resumed` entry that closed it."""

    start: datetime
    end: datetime
    kind: PauseClass
    # What the pause waited on: the last escalation's cause, else its class, `failure` for an
    # epoch with no escalation (GH-593).
    cause: str = "failure"
    # The reasons of the failure entries that ended the run (`rules.terminal_failures`, the filter
    # `rules.pause_kind` reads), in log order: what subdivides an external pause (GH-750). Not read
    # by `time_split`.
    failure_reasons: tuple[FailureReason, ...] = ()


@dataclass(frozen=True)
class Pauses:
    """The closed pauses of a document. `complete` is false when a pause is still open (no
    later `resumed`) or a bound of one cannot be read: the paused figures are then unknown."""

    closed: tuple[Pause, ...]
    complete: bool


@dataclass(frozen=True)
class TimeSplit:
    """The seconds of a task's cycle by class, each counted once (order: active, failed,
    waiting, paused); `None` is unknown, never zero. The active class is
    `clipped_active_seconds`. `unattributed_s` is what no class covers."""

    failed_s: float | None
    waiting_s: float | None
    paused_external_s: float | None
    paused_human_s: float | None
    unattributed_s: float | None


_UNKNOWN = TimeSplit(None, None, None, None, None)


def _span(first: object, last: object) -> Interval | None:
    start, end = moment(first), moment(last)
    return (start, end) if start and end else None


def _bounds(document: Mapping[str, Any]) -> Interval | None:
    """The cycle's `[start, end]`, read as `cycle_seconds` reads it."""
    klass = cycle_class(document)
    if klass == "excluded":
        return None
    runs = _runs(document)
    starts = [m for run in runs if (m := moment(run.get("started_at")))]
    if not starts:
        return None
    if klass == "closed":
        end = moment(document.get("closed_at"))
    else:
        ends = [m for run in runs if (m := moment(run.get("ended_at")))]
        end = max(ends) if ends else None
    if end is None or end < min(starts):
        return None
    return min(starts), end


def _active(document: Mapping[str, Any]) -> list[Interval]:
    return [
        i for run in _runs(document) if (i := _span(run.get("started_at"), run.get("ended_at")))
    ]


def _failed(document: Mapping[str, Any]) -> list[Interval] | None:
    """The `Failure` intervals; `None` when one lacks a readable bound (a failure recorded
    before the fields existed)."""
    spans = [_span(f.get("started_at"), f.get("ended_at")) for f in document.get("failures") or []]
    return None if None in spans else [s for s in spans if s]


def _ci_records(document: Mapping[str, Any]) -> list[tuple[Any, str | None]]:
    """Every CI timing the document holds, with the `until` its wait is read against: the
    entry's `at` for a `ci` pause entry, `None` for a record."""
    records: list[tuple[Any, str | None]] = []
    for key in ("full_checks", "merge_ci"):
        held = document.get(key)
        for item in [held] if isinstance(held, Mapping) else list(held or []):
            if item.get("ci_timing"):
                records.append((item["ci_timing"], None))
    for entry in document.get("pause_log") or []:
        if entry["kind"] == "escalation" and entry["value"] == "ci" and entry.get("ci"):
            records.append((entry["ci"], entry.get("at")))
    return records


def waiting_intervals(document: Mapping[str, Any]) -> list[Interval] | None:
    """The CI waits as `[end - wait, end]`; `None` when one is unknown. A clamped wait is an
    empty interval."""
    waits: list[Interval] = []
    for timing, until in _ci_records(document):
        try:
            seconds, _clamped = ci.waited_seconds(timing, until)
            end = moment(timing["run_ended_at"] if timing["run_ended_at"] is not None else until)
        except (KeyError, TypeError):
            return None
        if seconds is None or end is None:
            return None
        waits.append((end - timedelta(seconds=seconds), end))
    return waits


def pause_intervals(document: Mapping[str, Any]) -> Pauses:
    """The pauses of `pause_log`, each from its start to the next `resumed` entry's `at`; a
    `RoleRun` never closes one. An epoch is the entries after the previous `resumed`; one of
    only `lapse` entries is no pause. Its start is the latest `Failure.ended_at` that matches
    one of its failure entries (same `section` and `reason`, ended after the previous
    `resumed` and no later than that entry), else the opening entry's `at`. It is `external`
    when `rules.pause_kind` says so (every failure an external reason, every escalation a
    `failure` one or carrying a `MechanicalCause`), `human` otherwise."""
    closed: list[Pause] = []
    complete = True
    lower: datetime | None = None
    lower_unreadable = False
    epoch: list[Mapping[str, Any]] = []
    for entry in document.get("pause_log") or []:
        if entry["kind"] != "resumed":
            epoch.append(entry)
            continue
        end = moment(entry.get("at"))
        if _opened(epoch):
            found = _epoch_pause(document, epoch, lower, lower_unreadable, end)
            if found:
                closed.append(found)
            else:
                complete = False
        lower, lower_unreadable, epoch = end, end is None, []
    return Pauses(tuple(closed), complete and not _opened(epoch))


def _opened(epoch: list[Mapping[str, Any]]) -> bool:
    return any(e["kind"] != "lapse" for e in epoch)


def _epoch_pause(
    document: Mapping[str, Any],
    epoch: list[Mapping[str, Any]],
    lower: datetime | None,
    lower_unreadable: bool,
    end: datetime | None,
) -> Pause | None:
    opening = next(e for e in epoch if e["kind"] != "lapse")
    failure_entries = [e for e in epoch if e["kind"] == "failure"]
    # A previous `resumed` with no readable `at` leaves no floor: no failure is trusted.
    start = None
    if failure_entries and not lower_unreadable:
        start = _failure_start(document, failure_entries, lower)
    if start is None:
        start = moment(opening.get("at"))
    if start is None or end is None or end < start:
        return None
    entries = cast(Sequence[PauseEntry], epoch)
    reasons = tuple(e["value"] for e in rules.terminal_failures(entries) if e["kind"] == "failure")
    return Pause(start, end, pause_kind(entries), _cause(epoch), reasons)


def _cause(epoch: list[Mapping[str, Any]]) -> str:
    """The cause label of an epoch: its last escalation's `cause`, else that entry's class."""
    escalations = [e for e in epoch if e["kind"] == "escalation"]
    return (
        str(escalations[-1].get("cause") or escalations[-1]["value"]) if escalations else "failure"
    )


def _failure_start(
    document: Mapping[str, Any], failure_entries: list[Mapping[str, Any]], lower: datetime | None
) -> datetime | None:
    found: list[datetime] = []
    for failure in document.get("failures") or []:
        ended = moment(failure.get("ended_at"))
        if ended is None or (lower is not None and ended <= lower):
            continue
        for entry in failure_entries:
            at = moment(entry.get("at"))
            if (
                at is not None
                and ended <= at
                and failure.get("section") == entry["section"]
                and failure.get("reason", "other") == entry["value"]
            ):
                found.append(ended)
                break
    return max(found) if found else None


def _length(intervals: list[Interval], bounds: Interval, earlier: list[Interval]) -> float | None:
    """The seconds `intervals` cover inside `bounds` that `earlier` does not, `None` when the
    instants cannot be ordered."""
    try:
        clipped = parallel_factor.clip(intervals, *bounds)
        return parallel_factor.union_seconds(parallel_factor.difference(clipped, earlier))
    except TypeError:
        return None


def _clipped(intervals: list[Interval], bounds: Interval) -> list[Interval]:
    return parallel_factor.clip(intervals, *bounds)


def clipped_active_seconds(document: Mapping[str, Any]) -> float | None:
    """The active time cut to the cycle bounds (what `TimeSplit` subtracts first), `None` for
    a task with no cycle or no run with both bounds. `active_seconds` is unchanged and unclipped."""
    bounds, active = _bounds(document), _active(document)
    if bounds is None or not active:
        return None
    return _length(active, bounds, [])


def time_split(document: Mapping[str, Any]) -> TimeSplit:
    """The failed, waiting, paused (external and human) and unattributed seconds of the cycle.
    Each class is cut to the cycle bounds, then loses what the earlier ones cover, in the order
    active, failed, waiting, paused, so a second counts once. `unattributed_s` is the cycle no
    class covers: `None` when any class is unknown, never negative."""
    bounds, active = _bounds(document), _active(document)
    if bounds is None:
        return _UNKNOWN
    failed, waiting, pauses = (
        _failed(document),
        waiting_intervals(document),
        pause_intervals(document),
    )
    external = [(p.start, p.end) for p in pauses.closed if p.kind == "external"]
    human = [(p.start, p.end) for p in pauses.closed if p.kind == "human"]
    try:
        earlier = _clipped(active, bounds)
        failed_c = _clipped(failed or [], bounds)
        waiting_c = _clipped(waiting or [], bounds)
        external_c, human_c = _clipped(external, bounds), _clipped(human, bounds)
    except TypeError:
        return _UNKNOWN
    failed_s = None if failed is None else _length(failed, bounds, earlier)
    waiting_before = earlier + failed_c
    # A class is only known when every class subtracted before it is: an unknown one cannot be
    # stood in for by an empty set, or the later classes would count seconds it covers.
    waiting_s = (
        None if waiting is None or failed is None else _length(waiting, bounds, waiting_before)
    )
    paused_before = waiting_before + waiting_c
    paused_known = pauses.complete and failed is not None and waiting is not None
    external_s = _length(external, bounds, paused_before) if paused_known else None
    human_s = _length(human, bounds, paused_before + external_c) if paused_known else None
    covered = paused_before + external_c + human_c
    known = bool(active) and None not in (failed_s, waiting_s, external_s, human_s)
    unattributed = _length([bounds], bounds, covered) if known else None
    return TimeSplit(failed_s, waiting_s, external_s, human_s, unattributed)


@dataclass(frozen=True)
class Escalation:
    """One escalation of a task and what kind it was: `mechanical` when its pause is external
    (`rules.pause_kind`), `judgment` when a human was needed (GH-593)."""

    cause: str
    kind: Literal["mechanical", "judgment"]


def _epochs(document: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    epochs: list[list[Mapping[str, Any]]] = [[]]
    for entry in document.get("pause_log") or []:
        if entry["kind"] == "resumed":
            epochs.append([])
        else:
            epochs[-1].append(entry)
    return epochs


def escalations(document: Mapping[str, Any]) -> tuple[Escalation, ...]:
    """The task's escalations, each labelled by its cause (else its class) and by the kind of
    pause it opened. An entry with no `cause` reads as a judgment one, as before GH-593."""
    found: list[Escalation] = []
    for epoch in _epochs(document):
        kind: Literal["mechanical", "judgment"] = (
            "mechanical"
            if pause_kind(cast(Sequence[PauseEntry], epoch)) == "external"
            else "judgment"
        )
        found.extend(
            Escalation(str(e.get("cause") or e["value"]), kind)
            for e in epoch
            if e["kind"] == "escalation"
        )
    return tuple(found)


def human_wait_seconds(document: Mapping[str, Any]) -> dict[str, float] | None:
    """The seconds the task waited on a human, by cause: the closed `human` pauses' length.
    `None` when a pause is open or unreadable, never a partial sum."""
    pauses = pause_intervals(document)
    if not pauses.complete:
        return None
    waited: dict[str, float] = {}
    for pause in pauses.closed:
        if pause.kind == "human":
            waited[pause.cause] = (
                waited.get(pause.cause, 0.0) + (pause.end - pause.start).total_seconds()
            )
    return waited


# The lead time and the path a closed task took (GH-599). `lead_time` is written once, by the
# process (`process.domain.lead_time`); this section only reads it. Nothing here enters
# `scorecard.verdict`.

LeadTimeClass = Literal["known", "unrecorded", "no_merge", "unreadable", "negative", "missing"]
# The reasons a closed task leaves the medians, in the order they are printed and tested: the
# first that applies wins, so a task is counted once.
EXCLUSION_ORDER: tuple[LeadTimeClass, ...] = (
    "unrecorded",
    "no_merge",
    "unreadable",
    "negative",
    "missing",
)
OTHER_KIND = "other"


def path_recorded(document: Mapping[str, Any]) -> bool:
    """Whether the router classified the document: it has an `initial_path` and the
    `path_routed` marker `task_path.classification_patch` writes. The one cut-off for every
    per-path figure: a document from before the router ran the full path even when it says
    `light`, so it is `unrecorded`. Nothing compares commits or reads `system_version`."""
    return bool(document.get("initial_path")) and document.get("path_routed") is True


def _ended(document: Mapping[str, Any]) -> bool:
    """Whether the task has an ending the report counts: a human close, a system merge, or an
    end with no pull request (discarded, split, tracker only)."""
    return bool(
        document.get("closed_at")
        or document.get("auto_merged_at")
        or document.get("status") in NO_DELIVERY
    )


def _lead_class(value: object) -> tuple[LeadTimeClass, float | None]:
    if not isinstance(value, Mapping):
        return "missing", None
    if "unknown" in value:
        reason = value["unknown"]
        return (reason, None) if reason in ("no_merge", "unreadable") else ("missing", None)
    labeled, merged = moment(value.get("labeled_at")), moment(value.get("merged_at"))
    if labeled is None or merged is None:
        return "missing", None
    seconds = (merged - labeled).total_seconds()
    return ("known", seconds) if seconds >= 0 else ("negative", None)


def lead_time_seconds(document: Mapping[str, Any]) -> float | None:
    """From the last addition of `agentic` to the pull request's `mergedAt`. `None`, never 0,
    when the key is missing or `{unknown: ...}`, a timestamp does not parse or is missing, or
    the difference is negative."""
    if "lead_time" not in document:
        return None
    return _lead_class(document["lead_time"])[1]


def lead_time_class(document: Mapping[str, Any]) -> LeadTimeClass:
    """Why a task's lead time is, or is not, in the medians. A document without the key, or
    one `path_recorded` does not hold for, is `unrecorded`; a malformed value is `missing`."""
    if "lead_time" not in document or not path_recorded(document):
        return "unrecorded"
    return _lead_class(document["lead_time"])[0]


@dataclass(frozen=True)
class PathFacts:
    """What the report reads off one task, carried on `scorecard.TaskFigures`: whether it ended,
    the bucket (`initial_path` when recorded), its lead time class and seconds, and its promotion
    kind (`None` for none)."""

    ended: bool = False
    initial_path: str | None = None
    lead_class: LeadTimeClass = "unrecorded"
    lead_s: float | None = None
    promotion_kind: str | None = None


def path_facts(document: Mapping[str, Any]) -> PathFacts:
    recorded = path_recorded(document)
    promotion = document.get("promotion")
    kind: str | None = None
    if recorded and promotion:
        found = promotion.get("kind") if isinstance(promotion, Mapping) else None
        kind = found if found in get_args(task_path.PromotionKind) else OTHER_KIND
    return PathFacts(
        ended=_ended(document),
        initial_path=str(document["initial_path"]) if recorded else None,
        lead_class=lead_time_class(document),
        lead_s=lead_time_seconds(document) if recorded else None,
        promotion_kind=kind,
    )


@dataclass(frozen=True)
class PathLeadTime:
    """One `initial_path` bucket: the median lead time over the `n` tasks with a known one,
    of the `m` closed recorded tasks in the bucket. `median_s` is `None` when `n` is 0."""

    median_s: float | None
    n: int
    m: int


@dataclass(frozen=True)
class PathReport:
    """The informative block of `eval compare`: lead time per `initial_path`, the excluded
    counts by reason (global, in `EXCLUSION_ORDER`) and the promotions by kind among the
    recorded tasks."""

    paths: Mapping[str, PathLeadTime]
    exclusions: tuple[tuple[LeadTimeClass, int], ...]
    promotions: Mapping[str, int]


def path_report(facts: Iterable[PathFacts]) -> PathReport:
    """The report over the tasks that ended; an open task is in no figure."""
    ended = [f for f in facts if f.ended]
    buckets: dict[str, list[PathFacts]] = {}
    for f in ended:
        if f.initial_path is not None:
            buckets.setdefault(f.initial_path, []).append(f)
    paths = {}
    for name, members in sorted(buckets.items()):
        known = [m.lead_s for m in members if m.lead_class == "known" and m.lead_s is not None]
        paths[name] = PathLeadTime(median(known) if known else None, len(known), len(members))
    kinds = (*get_args(task_path.PromotionKind), OTHER_KIND)
    promotions = dict.fromkeys(kinds, 0)
    for f in ended:
        if f.promotion_kind is not None:
            promotions[f.promotion_kind] += 1
    exclusions = tuple(
        (reason, sum(1 for f in ended if f.lead_class == reason)) for reason in EXCLUSION_ORDER
    )
    return PathReport(paths, exclusions, promotions)
