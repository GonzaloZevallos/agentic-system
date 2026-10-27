"""The findings and returns report (GH-354): where `important` findings come from, who fixes
them and when, and how many returns a subtask costs, read off closed task documents.

A pure function from parsed documents and an explicit window to a typed report. It reads no
clock and no tracker, and lists the ids it read, so a before/after comparison names the
documents each side stands on. `agentic eval findings` is the only caller that touches a
filesystem.

Two decisions live elsewhere and are called, never repeated: whether a `change_history`
entry is a return and which review raised a row are `returns.is_return` and
`returns.finding_origin`; which unit owns an open row is `rules.important_partition`.

What the document cannot say is said by the report, not guessed: a row is *fixed by a
pass* when its subtask's last review raised it and it was later closed, the only way an
`important` closes after its review approved. A return's origin separates `full_checks` (by
the reason `rules.full_checks_return` writes) from other suite-red returns, where CI and a
delivery let through red leave the same entry.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from agentic.core.process.domain import rules
from agentic.core.process.domain.returns import Origin, before_pr, finding_origin, is_return
from agentic.core.process.domain.task import FindingRecord, TaskState

Scope = Literal["owned", "plan", "outside"]
Outcome = Literal[
    "fixed_in_subtask",
    "fixed_before_pr",
    "fixed_after_pr",
    "accepted_without_fix",
    "dismissed",
]
ReturnOrigin = Literal["review", "full_checks", "suite_red"]

FIX_STEP = "fix_important"
FULL_CHECKS_REASON = "full_checks failed"
SUITE_RED = "process-suite-red"
CLOSED_UNMERGED = "closed"


class FindingsDocument(TaskState, total=False):
    """The closure keys the report reads beyond `TaskState`, as `agentic close` writes them."""

    closed_at: str
    delivery_outcome: str
    time_to_merge_seconds: str


@dataclass(frozen=True)
class FindingLine:
    """One `important` row of one task, as the report counts it."""

    task: str
    origin: Origin
    scope: Scope
    category: str
    lowered: bool
    outcome: Outcome
    open_at_pr: bool


@dataclass(frozen=True)
class Figures:
    """The success figures. A share is `None` when there is no pull request to divide by, a
    median when no document carries the two instants it needs: never a fabricated zero."""

    pull_requests: int
    share_open_importants: float | None
    share_fix_important_after_pr: float | None
    median_pr_open_to_merge_hours: float | None
    median_start_to_merge_hours: float | None
    returns: int
    subtasks: int
    returns_per_subtask: float | None


@dataclass(frozen=True)
class FindingsReport:
    ids: tuple[str, ...]
    outside_window: int
    findings: tuple[FindingLine, ...]
    returns_by_category: Mapping[str, int]
    returns_by_origin: Mapping[str, int]
    figures: Figures


def build(documents: Sequence[FindingsDocument], since: date, until: date) -> FindingsReport:
    """The report over the documents closed from `since` to `until`, both included. A
    document with no `closed_at`, or one its slices carried (`closed_by_slices`), is not a
    judged pull request and counts as outside the window."""
    chosen = [d for d in documents if _in_window(d, since, until)]
    lines = [line for d in chosen for line in _lines(d)]
    categories: Counter[str] = Counter()
    origins: Counter[str] = Counter()
    for document in chosen:
        for entry in document.get("change_history", []):
            if is_return(entry):
                categories[str(entry.get("category"))] += 1
                origins[_return_origin(entry)] += 1
    return FindingsReport(
        ids=tuple(d.get("id", "?") for d in chosen),
        outside_window=len(documents) - len(chosen),
        findings=tuple(lines),
        returns_by_category=dict(sorted(categories.items())),
        returns_by_origin=dict(sorted(origins.items())),
        figures=_figures(chosen, lines),
    )


def _in_window(document: FindingsDocument, since: date, until: date) -> bool:
    if document.get("closed_by_slices"):
        return False
    moment = _moment(document.get("closed_at"))
    return moment is not None and since <= moment.date() <= until


def _moment(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _return_origin(entry: Mapping[str, object]) -> ReturnOrigin:
    if entry.get("category") != SUITE_RED:
        return "review"
    if str(entry.get("reason", "")).startswith(FULL_CHECKS_REASON):
        return "full_checks"
    return "suite_red"


# ---- findings ------------------------------------------------------------------------


def _lines(document: FindingsDocument) -> list[FindingLine]:
    rows = [row for row in rules.findings_rows(document) if row["severity"] == "important"]
    scopes = _scopes(document, rows)
    steps = [e for e in document.get("landing", []) if e.get("step") == FIX_STEP]
    before = any(before_pr(e) for e in steps)
    after = any(not before_pr(e) for e in steps)
    last = _last_review(document)
    lines = []
    for row in rows:
        origin = finding_origin(row, document)
        outcome = _outcome(row, origin, last, before, after)
        lines.append(
            FindingLine(
                task=document.get("id", "?"),
                origin=origin,
                scope=scopes[_key(row)],
                category=str(row["category"]),
                lowered="reported_severity" in row,
                outcome=outcome,
                open_at_pr=_open_at_pr(origin, outcome),
            )
        )
    return lines


def _key(row: FindingRecord) -> tuple[str, str]:
    return (row["location"], row["claim"])


def _scopes(state: TaskState, rows: list[FindingRecord]) -> dict[tuple[str, str], Scope]:
    """The scope of every row, fixed ones too: `important_partition` reads open rows only,
    so it is asked about the same rows with their status held open."""
    held: list[FindingRecord] = [{**row, "status": "open"} for row in rows]
    probe = TaskState(
        id=state.get("id", "?"),
        title=state.get("title", ""),
        description=state.get("description", ""),
        subtasks=state.get("subtasks", {}),
        findings=held,
    )
    split = rules.important_partition(probe)
    found: dict[tuple[str, str], Scope] = {}
    for owned in split.owned.values():
        for row in owned:
            found.setdefault(_key(row), "owned")
    for row in split.plan:
        found[_key(row)] = "plan"
    for row in split.outside:
        found[_key(row)] = "outside"
    return found


def _last_review(document: FindingsDocument) -> dict[str, int]:
    """The newest review version per subtask."""
    last: dict[str, int] = {}
    for run in document.get("runs", []):
        section = run["section"]
        if section.startswith(rules.REVIEW_PREFIX):
            name = section[len(rules.REVIEW_PREFIX) :]
            last[name] = max(last.get(name, 0), run["version"])
    return last


def _outcome(
    row: FindingRecord, origin: Origin, last: Mapping[str, int], earlier: bool, later: bool
) -> Outcome:
    if row["status"] == "dismissed":
        return "dismissed"
    if row["status"] == "open":
        return "accepted_without_fix"
    raised_last = any(last.get(name) == row["version"] for name in row["subtask"].split(", "))
    if not raised_last:
        return "fixed_in_subtask"
    if earlier and origin != "landing_review":
        return "fixed_before_pr"
    return "fixed_after_pr" if later else "fixed_in_subtask"


def _open_at_pr(origin: Origin, outcome: Outcome) -> bool:
    """Whether the row was still open when the pull request opened: never settled, or settled
    by the pass after it, unless the landing's own re-review raised it, which is after the
    pull request by definition."""
    if origin == "landing_review":
        return False
    return outcome in ("accepted_without_fix", "fixed_after_pr")


# ---- figures -------------------------------------------------------------------------


def _figures(documents: Sequence[FindingsDocument], lines: Sequence[FindingLine]) -> Figures:
    total = len(documents)
    opened = {
        line.task
        for line in lines
        if line.open_at_pr and not line.lowered and line.scope in ("owned", "plan")
    }
    fixed_after = sum(1 for d in documents if _fixed_after_pr(d))
    returns = sum(1 for d in documents for e in d.get("change_history", []) if is_return(e))
    subtasks = sum(len(d.get("subtasks", {})) for d in documents)
    merged = [d for d in documents if d.get("delivery_outcome") != CLOSED_UNMERGED]
    return Figures(
        pull_requests=total,
        share_open_importants=len(opened) / total if total else None,
        share_fix_important_after_pr=fixed_after / total if total else None,
        median_pr_open_to_merge_hours=_median_hours(
            [s for d in merged if (s := _seconds(d.get("time_to_merge_seconds"))) is not None]
        ),
        median_start_to_merge_hours=_median_hours(
            [s for d in merged if (s := _start_to_merge(d)) is not None]
        ),
        returns=returns,
        subtasks=subtasks,
        returns_per_subtask=returns / subtasks if subtasks else None,
    )


def _fixed_after_pr(document: FindingsDocument) -> bool:
    return any(e.get("step") == FIX_STEP and not before_pr(e) for e in document.get("landing", []))


def _seconds(value: object) -> float | None:
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _start_to_merge(document: FindingsDocument) -> float | None:
    starts = [
        m for run in document.get("runs", []) if (m := _moment(run.get("started_at"))) is not None
    ]
    closed = _moment(document.get("closed_at"))
    if not starts or closed is None:
        return None
    return (closed - min(starts)).total_seconds()


def _median_hours(seconds: Sequence[float]) -> float | None:
    return statistics.median(seconds) / 3600 if seconds else None


# ---- text ----------------------------------------------------------------------------


def render(report: FindingsReport, product: str, since: date, until: date) -> str:
    """The report as Markdown tables, for a human to paste into a task document."""
    f = report.figures
    out = [
        f"## Findings and returns -- {product}, {since} to {until}",
        "",
        f"Read {len(report.ids)} document(s) closed in the window "
        f"({report.outside_window} outside it): {', '.join(report.ids) or 'none'}",
        "",
        "### Figures",
        "",
        "| Figure | Value |",
        "|---|---|",
        f"| Pull requests | {f.pull_requests} |",
        f"| Share opened with open `owned`/`plan` importants | {_pct(f.share_open_importants)} |",
        f"| Share that ran `fix_important` after the PR | {_pct(f.share_fix_important_after_pr)} |",
        f"| Median PR-open-to-merge (h) | {_num(f.median_pr_open_to_merge_hours)} |",
        f"| Median task-start-to-merge (h) | {_num(f.median_start_to_merge_hours)} |",
        f"| Returns | {f.returns} |",
        f"| Subtasks | {f.subtasks} |",
        f"| Returns per subtask | {_num(f.returns_per_subtask)} |",
        "",
        "### Important findings",
        "",
        "| Origin | Scope | Category | Lowered | Outcome | Count |",
        "|---|---|---|---|---|---|",
    ]
    grouped = Counter(
        (x.origin, x.scope, x.category, x.lowered, x.outcome) for x in report.findings
    )
    for (origin, scope, category, lowered, outcome), count in sorted(grouped.items()):
        mark = "yes" if lowered else "no"
        out.append(f"| {origin} | {scope} | {category} | {mark} | {outcome} | {count} |")
    out += ["", "### Returns", "", "| By | Name | Count |", "|---|---|---|"]
    out += [f"| category | {k} | {v} |" for k, v in report.returns_by_category.items()]
    out += [f"| origin | {k} | {v} |" for k, v in report.returns_by_origin.items()]
    return "\n".join(out) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
