"""Apply a product's Langfuse dashboards through two ports (GH-274, ADR-0051).

`DashboardStore` is where the rows live (Langfuse's Postgres); `WidgetData` is where the
numbers live (ClickHouse). The use case builds the rows (`domain.dashboards`), plans the
diff against what the store holds, writes it, reads it back, and with `data` counts what
each widget would draw. Both ports are small enough to be dict fakes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..domain import dashboards as domain

RECORDED_IMAGE = "docker.langfuse.com/langfuse/langfuse:4 (web 4.35.0)"
"""The image the recorded columns were read from (ADR-0051, section 1). Moving the image
tag means reading the columns again and refreshing the golden."""

RECORDED_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        # dashboard_widgets
        ("dashboard_widgets", "chart_config"),
        ("dashboard_widgets", "chart_type"),
        ("dashboard_widgets", "created_at"),
        ("dashboard_widgets", "created_by"),
        ("dashboard_widgets", "description"),
        ("dashboard_widgets", "dimensions"),
        ("dashboard_widgets", "filters"),
        ("dashboard_widgets", "id"),
        ("dashboard_widgets", "metrics"),
        ("dashboard_widgets", "min_version"),
        ("dashboard_widgets", "name"),
        ("dashboard_widgets", "project_id"),
        ("dashboard_widgets", "updated_at"),
        ("dashboard_widgets", "updated_by"),
        ("dashboard_widgets", "view"),
        # dashboards
        ("dashboards", "created_at"),
        ("dashboards", "created_by"),
        ("dashboards", "definition"),
        ("dashboards", "description"),
        ("dashboards", "filters"),
        ("dashboards", "id"),
        ("dashboards", "name"),
        ("dashboards", "project_id"),
        ("dashboards", "updated_at"),
        ("dashboards", "updated_by"),
    }
)
"""`(table, column)` of `dashboards` and `dashboard_widgets` on `RECORDED_IMAGE`: any
difference from what the store reports refuses the write before it happens."""

Counts = tuple[tuple[str, str, int], ...]


class ColumnDrift(RuntimeError):
    """The tables are not the ones `RECORDED_COLUMNS` was read from: nothing was written."""


class UnknownProject(ValueError):
    """The public key belongs to no Langfuse project (or to an organization key)."""


class StoreUnavailable(RuntimeError):
    """The database could not be reached or refused the statement; the message says why."""


class DashboardStore(Protocol):
    def columns(self) -> frozenset[tuple[str, str]]:
        """`(table, column)` of the two dashboard tables, as the database reports them."""
        ...

    def read(self, product: str) -> Sequence[domain.Row]:
        """The rows of this store's project that carry `product`'s marker, and no others."""
        ...

    def apply(self, changes: domain.Changes) -> None:
        """Write the upserts and the deletes, all or none."""
        ...


class WidgetData(Protocol):
    def count(self, widget: domain.WidgetSpec) -> int:
        """How many rows the widget reads: scores of its names, or observations."""
        ...


@dataclass(frozen=True)
class Report:
    changes: domain.Changes
    created: frozenset[str]
    applied: bool
    counts: Counts
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def _drift(found: frozenset[tuple[str, str]]) -> str:
    added = sorted(f"{t}.{c}" for t, c in found - RECORDED_COLUMNS)
    removed = sorted(f"{t}.{c}" for t, c in RECORDED_COLUMNS - found)
    return (
        f"the dashboard tables differ from the recorded {RECORDED_IMAGE}: "
        f"new {added or 'none'}, missing {removed or 'none'}"
    )


def _read_back(desired: Sequence[domain.Row], stored: Sequence[domain.Row]) -> list[str]:
    wanted = {(type(r), r.id): r for r in desired}
    found = {(type(r), r.id): r for r in stored}
    problems = [f"read-back: {key[1]} was not stored" for key in wanted if key not in found]
    problems += [f"read-back: {key[1]} was not deleted" for key in found if key not in wanted]
    problems += [
        f"read-back: {key[1]} differs from the plan"
        for key, row in wanted.items()
        if key in found and found[key] != row
    ]
    return problems


def apply_dashboards(
    product: str,
    store: DashboardStore,
    *,
    dry_run: bool = False,
    data: WidgetData | None = None,
) -> Report:
    """Build, plan and (unless `dry_run`) write `product`'s dashboards, then read them
    back. Raises `ColumnDrift` before writing when the tables are not the recorded ones.
    With `data`, a widget that reads zero rows is a problem unless it is one of
    `ZERO_ROW_EXCEPTIONS`."""
    found = store.columns()
    if found != RECORDED_COLUMNS:
        raise ColumnDrift(_drift(found))
    specs = domain.build_dashboards(product)
    desired = domain.rows(specs)
    stored = store.read(product)
    changes = domain.plan(desired, stored)
    stored_ids = {row.id for row in stored}
    created = frozenset(row.id for row in changes.upserts if row.id not in stored_ids)
    problems: list[str] = []
    if not dry_run:
        store.apply(changes)
        problems += _read_back(desired, store.read(product))
    counts: list[tuple[str, str, int]] = []
    if data is not None:
        allowed = {(dashboard, widget) for dashboard, widget, _ in domain.ZERO_ROW_EXCEPTIONS}
        for dash in specs:
            for widget in dash.widgets:
                n = data.count(widget)
                counts.append((dash.key, widget.key, n))
                if n == 0 and (dash.key, widget.key) not in allowed:
                    problems.append(f"{dash.key}/{widget.key}: reads 0 rows")
    return Report(changes, created, not dry_run, tuple(counts), tuple(problems))
