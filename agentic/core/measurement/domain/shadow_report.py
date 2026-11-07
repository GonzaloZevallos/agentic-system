"""The missed-findings report of the `single` review arm (GH-473, ADR-0088): what the shadow
review found that the single review, the reviewed unit's own, did not.

A pure function from parsed documents to a typed report, and a renderer. It reads no clock
and no tracker, and lists the ids it read. `agentic eval shadow` is the only caller that
touches a filesystem. **The match is decided only here**, from two structured fields:
`finding_location.finding_file` of the `location` and `finding_location.category_family` of
the `category`; never from a claim or evidence.

The single review is the `rules.findings_rows` rows whose `rules.row_owners` include
`rules.reviewed_unit_name`, in every status: a row the fold later closed was still raised.
Each shadow finding of a `ran` record lands in exactly one bucket: `unlocated` (its location
names no file, never counted as missed), `missed` (no single row on its key), `caught_lower`
(the single review's highest severity on the key is lower) or `caught`. Only `ran` records
count: ADR-0088 says a `late` shadow leaves no findings, so the findings of any other record
are ignored, whatever they hold; such a record adds only to its status count, the dropped
subtasks, their changed lines and its cost.

The figure is a **lower bound**: the family is wide, so a single row of the same family on
the same file hides a different defect, and the heads differ. Renamed files are not followed:
a file renamed between the two reviews is two files here.

A task enters the denominators (buckets, cost, time) only when at least one record `ran` and
the document has a reviewed unit; any other task is listed with its causes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from agentic.core.process.domain import rules
from agentic.core.process.domain.finding_location import category_family, finding_file
from agentic.core.process.domain.task import FindingRecord, Severity, ShadowRecord, TaskState

from . import cycle

SEVERITIES: tuple[Severity, ...] = get_args(Severity)
Bucket = Literal["missed", "caught_lower", "caught", "unlocated"]
BUCKETS: tuple[Bucket, ...] = get_args(Bucket)
NO_REVIEWED_UNIT = "no_reviewed_unit"


@dataclass(frozen=True)
class Buckets:
    """How many shadow findings landed in each bucket, per shadow severity."""

    counts: Mapping[tuple[Severity, Bucket], int]

    def count(self, severity: Severity, bucket: Bucket) -> int:
        return self.counts.get((severity, bucket), 0)

    @property
    def ran_rows(self) -> int:
        """Every shadow finding of a `ran` record, unlocated ones included."""
        return sum(self.counts.values())

    def rate(self, severity: Severity) -> float | None:
        """`missed / (missed + caught_lower + caught)` at `severity`; `None` when no located
        row stands there, never a fabricated zero."""
        located = sum(self.count(severity, b) for b in ("missed", "caught_lower", "caught"))
        return self.count(severity, "missed") / located if located else None

    def unlocated_share(self) -> float | None:
        """The share of the `ran` rows whose location names no file; `None` with no rows."""
        rows = self.ran_rows
        unlocated = sum(self.count(s, "unlocated") for s in SEVERITIES)
        return unlocated / rows if rows else None

    def plus(self, other: Buckets) -> Buckets:
        merged = Counter(self.counts)
        merged.update(other.counts)
        return Buckets(dict(merged))


@dataclass(frozen=True)
class Figures:
    """The buckets and the money and time of one task, or of the tasks that count."""

    buckets: Buckets
    shadow_cost: float
    cost_gross: float
    cost_net: float
    time_gross: float | None
    time_net: float | None

    @property
    def ran_rows(self) -> int:
        return self.buckets.ran_rows


@dataclass(frozen=True)
class TaskShadow:
    """One task with shadow records: its figures, its record counts by status and, for a
    record that did not run, how many of each `cause`."""

    id: str
    counted: bool
    out_cause: str | None
    ran: int
    late: int
    unavailable: int
    skipped: int
    causes: Mapping[tuple[str, str], int]
    dropped: int
    dropped_lines: int
    dropped_unknown_size: int
    figures: Figures

    @property
    def buckets(self) -> Buckets:
        return self.figures.buckets

    @property
    def shadow_cost(self) -> float:
        return self.figures.shadow_cost

    @property
    def cost_gross(self) -> float:
        return self.figures.cost_gross

    @property
    def cost_net(self) -> float:
        return self.figures.cost_net

    @property
    def time_gross(self) -> float | None:
        return self.figures.time_gross

    @property
    def time_net(self) -> float | None:
        return self.figures.time_net


@dataclass(frozen=True)
class ShadowReport:
    """The report. `total` sums the tasks that count; the record counts and the dropped
    subtasks cover every document read. `time_left_out` is how many counted tasks have no
    gross time and so are in neither time total."""

    ids: tuple[str, ...]
    tasks: tuple[TaskShadow, ...]
    total: Figures
    counted_tasks: int
    ran: int
    late: int
    unavailable: int
    skipped: int
    dropped: int
    dropped_lines: int
    dropped_unknown_size: int
    time_left_out: int


def _single_review(document: TaskState, unit: str) -> dict[tuple[str, str], int]:
    """The highest severity, as an index of `SEVERITIES` (0 is the highest), of the single
    review's rows on each locatable `(file, family)` key."""
    best: dict[tuple[str, str], int] = {}
    for row in rules.findings_rows(document):
        if unit not in rules.row_owners(document, row):
            continue
        file = finding_file(row["location"])
        if file is None:
            continue
        key = (file, category_family(row["category"]))
        rank = SEVERITIES.index(row["severity"])
        best[key] = min(best.get(key, rank), rank)
    return best


def _bucket(finding: FindingRecord, single: Mapping[tuple[str, str], int]) -> Bucket:
    file = finding_file(finding["location"])
    if file is None:
        return "unlocated"
    held = single.get((file, category_family(finding["category"])))
    if held is None:
        return "missed"
    return "caught_lower" if held > SEVERITIES.index(finding["severity"]) else "caught"


def _count_buckets(
    records: Sequence[ShadowRecord], single: Mapping[tuple[str, str], int]
) -> Buckets:
    counts: Counter[tuple[Severity, Bucket]] = Counter()
    for record in records:
        for finding in record["findings"]:
            counts[(finding["severity"], _bucket(finding, single))] += 1
    return Buckets(dict(counts))


def _task(document: TaskState) -> TaskShadow:
    records = document.get("shadow_reviews") or []
    ran = [r for r in records if r["status"] == "ran"]
    dropped = [r for r in records if r["status"] != "ran"]
    unit = rules.reviewed_unit_name(document)
    counted = bool(ran) and unit is not None
    out_cause = NO_REVIEWED_UNIT if ran and unit is None else None
    buckets = (
        _count_buckets(ran, _single_review(document, unit))
        if counted and unit is not None
        else Buckets({})
    )
    gross = float(document.get("cost_usd", 0.0))
    time_gross = cycle.cycle_seconds(document)
    wait = float(document.get("shadow_wait_s", 0.0))
    figures = Figures(
        buckets=buckets,
        shadow_cost=rules.shadow_cost(document),
        cost_gross=gross,
        cost_net=rules.cost_without_shadow(gross, document),
        time_gross=time_gross,
        time_net=None if time_gross is None else max(time_gross - wait, 0.0),
    )
    by_status = Counter(r["status"] for r in records)
    return TaskShadow(
        id=document["id"],
        counted=counted,
        out_cause=out_cause,
        ran=by_status["ran"],
        late=by_status["late"],
        unavailable=by_status["unavailable"],
        skipped=by_status["skipped"],
        causes=dict(Counter((r["status"], str(r["cause"])) for r in dropped)),
        dropped=len(dropped),
        dropped_lines=sum(r["changed_lines"] or 0 for r in dropped),
        dropped_unknown_size=sum(1 for r in dropped if r["changed_lines"] is None),
        figures=figures,
    )


def build(documents: Sequence[TaskState]) -> ShadowReport:
    """The report over `documents`: only those with `shadow_reviews` are read."""
    tasks = tuple(_task(d) for d in documents if d.get("shadow_reviews"))
    counted = [t for t in tasks if t.counted]
    timed = [t for t in counted if t.time_gross is not None]
    total = Figures(
        buckets=_sum_buckets(counted),
        shadow_cost=sum(t.shadow_cost for t in counted),
        cost_gross=sum(t.cost_gross for t in counted),
        cost_net=sum(t.cost_net for t in counted),
        time_gross=sum(t.time_gross or 0.0 for t in timed) if timed else None,
        time_net=sum(t.time_net or 0.0 for t in timed) if timed else None,
    )
    return ShadowReport(
        ids=tuple(t.id for t in tasks),
        tasks=tasks,
        total=total,
        counted_tasks=len(counted),
        ran=sum(t.ran for t in tasks),
        late=sum(t.late for t in tasks),
        unavailable=sum(t.unavailable for t in tasks),
        skipped=sum(t.skipped for t in tasks),
        dropped=sum(t.dropped for t in tasks),
        dropped_lines=sum(t.dropped_lines for t in tasks),
        dropped_unknown_size=sum(t.dropped_unknown_size for t in tasks),
        time_left_out=len(counted) - len(timed),
    )


def _sum_buckets(tasks: Sequence[TaskShadow]) -> Buckets:
    total = Buckets({})
    for task in tasks:
        total = total.plus(task.buckets)
    return total


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}s"


def _tail(task: TaskShadow) -> str | None:
    parts = [
        f"{name} {n}" for name in ("late", "unavailable", "skipped") if (n := getattr(task, name))
    ]
    if not parts:
        return None
    lines = f"{task.dropped_lines} changed line(s) dropped"
    if task.dropped_unknown_size:
        lines += f", {task.dropped_unknown_size} of unknown size"
    return f"  also: {', '.join(parts)}; {lines}"


def _task_lines(task: TaskShadow) -> list[str]:
    if not task.counted:
        if task.out_cause is not None:
            return [
                f"{task.id}: no shadow counted ({task.out_cause}); left out of the denominators"
            ]
        causes = ", ".join(
            f"{status} {cause} x{n}" for (status, cause), n in sorted(task.causes.items())
        )
        return [f"{task.id}: no shadow ran ({causes}); left out of the denominators"]
    if task.buckets.ran_rows == 0:
        head = f"{task.id}: shadow ran, 0 findings"
    else:
        shown = "; ".join(
            f"{s} " + ", ".join(f"{b} {task.buckets.count(s, b)}" for b in BUCKETS)
            for s in SEVERITIES
            if any(task.buckets.count(s, b) for b in BUCKETS)
        )
        head = f"{task.id}: {shown}"
    lines = [
        head,
        f"  shadow ${task.shadow_cost:.2f}; cost ${task.cost_gross:.2f} gross, "
        f"${task.cost_net:.2f} net; time {_seconds(task.time_gross)} gross, "
        f"{_seconds(task.time_net)} net",
    ]
    if (tail := _tail(task)) is not None:
        lines.append(tail)
    return lines


def render(report: ShadowReport) -> str:
    """The report as plain text. A counted task prints its buckets, or `<id>: shadow ran, 0
    findings` when its ran records raised nothing, plus a tail with what was dropped; a task
    with no ran record prints only its causes and joins no denominator."""
    if not report.ids:
        return "No task document has shadow reviews."
    total = report.total
    lines = [
        f"Shadow report: read {len(report.ids)} document(s): {', '.join(report.ids)}",
        f"{report.counted_tasks} task(s) counted; the figure is a lower bound "
        "(family match, different heads).",
        f"Records: ran {report.ran}, late {report.late}, unavailable {report.unavailable}, "
        f"skipped {report.skipped}. Dropped: {report.dropped} subtask(s), "
        f"{report.dropped_lines} changed line(s)"
        + (
            f", {report.dropped_unknown_size} of unknown size."
            if report.dropped_unknown_size
            else "."
        ),
        "",
        "Total, by shadow severity:",
    ]
    for severity in SEVERITIES:
        cells = ", ".join(f"{b} {total.buckets.count(severity, b)}" for b in BUCKETS)
        lines.append(f"  {severity}: {cells}; missed rate {_pct(total.buckets.rate(severity))}")
    lines += [
        f"  unlocated share of the ran rows: {_pct(total.buckets.unlocated_share())}",
        f"  shadow ${total.shadow_cost:.2f}; cost ${total.cost_gross:.2f} gross, "
        f"${total.cost_net:.2f} net; time {_seconds(total.time_gross)} gross, "
        f"{_seconds(total.time_net)} net ({report.time_left_out} task(s) without a time)",
        "",
        "Per task:",
    ]
    for task in report.tasks:
        lines.extend(_task_lines(task))
    return "\n".join(lines)
