"""The Langfuse dashboards of a product, as data, and the diff that applies them (GH-274,
ADR-0051).

Pure: a product name in, the rows Langfuse stores out; two row sets in, the changes that
turn one into the other out. Nothing here reads a database or the clock, so the ports of
`application/dashboards.py` run on dicts.

Four properties hold by construction, not by convention:

- every widget carries the `environment` filter of the product it was built for:
  `WidgetSpec` refuses to exist without it, and `build_dashboards` is the one constructor;
- every score a widget reads is a member of `task_scores.SCORE_NAMES`: a family is taken
  from `task_scores.FAMILIES`, a base name is looked up there, never typed as a free literal;
- every id is derived from the product and the stable keys, so applying twice is a no-op;
- every row carries a marker naming its product, the only thing `plan` may delete by.

A widget never divides and never filters `unknown` out of a breakdown: a task whose
release is unknown is a visible series, not a hole."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from . import task_scores

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://agentic-system/langfuse-dashboards")
"""Where every dashboard and widget id is derived from; never changes, or a re-apply
would duplicate the rows."""

UNKNOWN = task_scores.UNKNOWN
MIN_VERSION = 2
"""The v2 views read `events_core`, the only table `langfuse:4` fills (ADR-0051 section 3)."""
ROW_LIMIT = 100

_MARKER = re.compile(r"\[agentic-dashboards product=(.+)\]")
_COLUMN_RELEASE = "traceRelease"
_NON_TIME_SERIES = frozenset({"HORIZONTAL_BAR", "VERTICAL_BAR", "PIE"})
_DROPPING_OPERATORS = frozenset({"none of", "<>"})

Filter = dict[str, Any]


def marker(product: str) -> str:
    """What every row's description ends with: the only thing a delete is scoped by."""
    return f"[agentic-dashboards product={product}]"


def _described(text: str, product: str) -> str:
    return f"{text}\n{marker(product)}"


def _id(product: str, *keys: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join((product, *keys))))


def environment_filter(product: str) -> Filter:
    return {
        "column": "environment",
        "type": "stringOptions",
        "operator": "any of",
        "value": [product],
    }


def _score(name: str) -> str:
    """A score name a widget reads, which must be one the code writes."""
    if name not in task_scores.SCORE_NAMES:
        raise ValueError(f"{name!r} is not a task score")
    return name


def _names(names: Iterable[str]) -> Filter:
    return {
        "column": "name",
        "type": "stringOptions",
        "operator": "any of",
        "value": [_score(n) for n in names],
    }


_DELIVERED: Filter = {"column": "booleanValue", "type": "boolean", "operator": "=", "value": True}


@dataclass(frozen=True)
class WidgetRow:
    """A `dashboard_widgets` row, less the columns Langfuse manages (`created_at`,
    `updated_at`, `created_by`, `updated_by`) and the project the store scopes it to."""

    id: str
    name: str
    description: str
    view: str
    dimensions: list[dict[str, str]]
    metrics: list[dict[str, str]]
    filters: list[Filter]
    chart_type: str
    chart_config: dict[str, Any]
    min_version: int

    def columns(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DashboardRow:
    """A `dashboards` row, with the same columns left out as `WidgetRow`."""

    id: str
    name: str
    description: str
    definition: dict[str, Any]
    filters: list[Filter]

    def columns(self) -> dict[str, Any]:
        return asdict(self)


Row = WidgetRow | DashboardRow


def product_of(row: Row) -> str | None:
    """The product a row's marker names, or `None` when its last line is not a marker."""
    last = row.description.rsplit("\n", 1)[-1] if "\n" in row.description else ""
    found = _MARKER.fullmatch(last)
    return found.group(1) if found else None


@dataclass(frozen=True)
class WidgetSpec:
    """One widget, for one product. The environment filter of that product must be among
    `filters`, no filter may drop `unknown` or touch the release axis, and every name it
    filters on must be a score name: anything else cannot be constructed."""

    product: str
    dashboard_key: str
    key: str
    title: str
    about: str
    view: str
    measure: str
    agg: str
    dimensions: tuple[str, ...]
    filters: tuple[Filter, ...]
    chart_type: str

    def __post_init__(self) -> None:
        if environment_filter(self.product) not in self.filters:
            raise ValueError(f"{self.key}: no environment filter for {self.product!r}")
        for name in self.scores:
            _score(name)
        for f in self.filters:
            if (
                f["column"] == _COLUMN_RELEASE
                or f["operator"] in _DROPPING_OPERATORS
                or UNKNOWN in (f["value"] if isinstance(f["value"], list) else [f["value"]])
            ):
                raise ValueError(f"{self.key}: a filter may not drop {UNKNOWN!r}: {f}")

    @property
    def scores(self) -> tuple[str, ...]:
        """The score names the widget reads, from its `name` filter; none for a widget
        that reads a native measure."""
        return tuple(n for f in self.filters if f["column"] == "name" for n in f["value"])

    @property
    def placed_by(self) -> str:
        """The timestamp that places what the widget counts in time: a score carries
        `task_time.end`, the end of the task's last run (never its merge date); an
        observation, its own start."""
        return "start_time" if self.view == "OBSERVATIONS" else "task_time.end"

    def row(self) -> WidgetRow:
        config: dict[str, Any] = {"type": self.chart_type}
        if self.chart_type in _NON_TIME_SERIES:
            config["row_limit"] = ROW_LIMIT
        return WidgetRow(
            id=_id(self.product, self.dashboard_key, self.key),
            name=self.title,
            description=_described(self.about, self.product),
            view=self.view,
            dimensions=[{"field": field} for field in self.dimensions],
            metrics=[{"measure": self.measure, "agg": self.agg}],
            filters=[dict(f) for f in self.filters],
            chart_type=self.chart_type,
            chart_config=config,
            min_version=MIN_VERSION,
        )


_COLUMNS = 2
_WIDTH = 6
_HEIGHT = 4


@dataclass(frozen=True)
class DashboardSpec:
    product: str
    key: str
    title: str
    about: str
    widgets: tuple[WidgetSpec, ...]

    def row(self) -> DashboardRow:
        placed = [
            {
                "type": "widget",
                "id": _id(self.product, self.key, widget.key, "placement"),
                "widgetId": _id(self.product, self.key, widget.key),
                "x": (i % _COLUMNS) * _WIDTH,
                "y": (i // _COLUMNS) * _HEIGHT,
                "x_size": _WIDTH,
                "y_size": _HEIGHT,
            }
            for i, widget in enumerate(self.widgets)
        ]
        return DashboardRow(
            id=_id(self.product, self.key),
            name=self.title,
            description=_described(self.about, self.product),
            definition={"widgets": placed},
            filters=[],
        )


def _widget(
    product: str,
    dashboard: str,
    key: str,
    title: str,
    about: str,
    *,
    view: str = "SCORES_NUMERIC",
    measure: str = "value",
    agg: str,
    dimensions: Sequence[str] = (),
    names: Iterable[str] = (),
    delivered: bool = False,
    chart: str,
) -> WidgetSpec:
    """The one place a widget is assembled, so it is the one place the environment filter
    is added."""
    names = tuple(names)
    filters = [environment_filter(product)]
    if names:
        filters.append(_names(names))
    if delivered:
        filters.append(dict(_DELIVERED))
    return WidgetSpec(
        product=product,
        dashboard_key=dashboard,
        key=key,
        title=title,
        about=about,
        view=view,
        measure=measure,
        agg=agg,
        dimensions=tuple(dimensions),
        filters=tuple(filters),
        chart_type=chart,
    )


def _overview(product: str) -> DashboardSpec:
    def w(key: str, title: str, about: str, **kw: Any) -> WidgetSpec:
        return _widget(product, "overview", key, title, about, **kw)

    families = task_scores.FAMILIES
    return DashboardSpec(
        product=product,
        key="overview",
        title=f"Panorama · {product}",
        about="Business overview: calendar week of delivery and system release.",
        widgets=(
            w(
                "cost_per_delivered_pr",
                "Costo por PR mergeado (mediana), por tipo",
                "Median cost of delivered tasks, by type.",
                agg="p50",
                dimensions=("name", _COLUMN_RELEASE),
                names=families["delivered_cost_usd.type"],
                chart="LINE_TIME_SERIES",
            ),
            w(
                "delivered_per_week",
                "PRs mergeados por semana de entrega",
                "Delivered tasks, placed by the end of their last run.",
                view="SCORES_BOOLEAN",
                measure="count",
                agg="count",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("delivered"),),
                delivered=True,
                chart="BAR_TIME_SERIES",
            ),
            w(
                "cycle_time",
                "Tiempo de ciclo (h, calendario)",
                "Median hours from the issue to the merge.",
                agg="p50",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("cycle_h"),),
                chart="LINE_TIME_SERIES",
            ),
            w(
                "returns_per_task",
                "Devoluciones por tarea",
                "Average returns per task.",
                agg="avg",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("returns"),),
                chart="LINE_TIME_SERIES",
            ),
            w(
                "failed_cost",
                "Plata perdida: costo de fallos",
                "Dollars spent on attempts that failed.",
                agg="sum",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("failed_cost_usd"),),
                chart="BAR_TIME_SERIES",
            ),
            w(
                "escalations_lost",
                "Plata perdida: escalaciones",
                "Escalations to a person, by class.",
                agg="sum",
                dimensions=("name",),
                names=families["escalations"],
                chart="BAR_TIME_SERIES",
            ),
            w(
                "original_vs_followup_spend",
                "Gasto: original vs. follow-ups",
                "Dollars spent, by the origin of the task.",
                agg="sum",
                dimensions=("name",),
                names=families["cost_usd.origin"],
                chart="AREA_TIME_SERIES",
            ),
        ),
    )


def _improvement(product: str) -> DashboardSpec:
    def w(key: str, title: str, about: str, **kw: Any) -> WidgetSpec:
        return _widget(product, "improvement", key, title, about, **kw)

    families = task_scores.FAMILIES
    return DashboardSpec(
        product=product,
        key="improvement",
        title=f"Mejora del sistema · {product}",
        about="What to fix in the next release: the axis is the system release.",
        widgets=(
            w(
                "returns_by_category",
                "Retrabajo por categoría de devolución",
                "Returns by category and release.",
                agg="sum",
                dimensions=("name", _COLUMN_RELEASE),
                names=families["returns"],
                chart="VERTICAL_BAR",
            ),
            w(
                "failures_by_reason",
                "Fallos por razón",
                "Failures by reason and release.",
                agg="sum",
                dimensions=("name", _COLUMN_RELEASE),
                names=families["failures"],
                chart="VERTICAL_BAR",
            ),
            w(
                "most_expensive_roles",
                "Roles más caros",
                "Dollars by role and release.",
                agg="sum",
                dimensions=("name", _COLUMN_RELEASE),
                names=families["cost_usd.role"],
                chart="VERTICAL_BAR",
            ),
            w(
                "active_hours",
                "Horas activas por versión",
                "Median run hours without queue waiting.",
                agg="p50",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("active_h"),),
                chart="VERTICAL_BAR",
            ),
            w(
                "findings_important",
                "Calidad de la entrega: hallazgos importantes",
                "Average important findings per task.",
                agg="avg",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("findings_important"),),
                chart="VERTICAL_BAR",
            ),
            w(
                "followups",
                "Calidad de la entrega: follow-ups",
                "Tasks that left follow-ups, on average.",
                agg="avg",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("followups"),),
                chart="VERTICAL_BAR",
            ),
            w(
                "checks_red",
                "Calidad de la entrega: chequeos en rojo",
                "Merge checks that failed, average per task.",
                agg="avg",
                dimensions=(_COLUMN_RELEASE,),
                names=(_score("checks_red"),),
                chart="VERTICAL_BAR",
            ),
        ),
    )


def _operation(product: str) -> DashboardSpec:
    def w(key: str, title: str, about: str, **kw: Any) -> WidgetSpec:
        return _widget(product, "operation", key, title, about, **kw)

    return DashboardSpec(
        product=product,
        key="operation",
        title=f"Operación · {product}",
        about="Whether to intervene: use the interface's last 7 days range.",
        widgets=(
            w(
                "escalations_by_class",
                "Escalaciones por clase",
                "Escalations to a person, by class.",
                agg="sum",
                dimensions=("name",),
                names=task_scores.FAMILIES["escalations"],
                chart="HORIZONTAL_BAR",
            ),
            w(
                "account_limit_failures_per_day",
                "Fallos por límite de cuenta, por día",
                "Account limit failures, by day.",
                agg="sum",
                dimensions=("timestampDay",),
                names=(_score("failures.account_limit"),),
                chart="VERTICAL_BAR",
            ),
            w(
                "deliveries_per_day",
                "Entregas por día",
                "Delivered tasks by day.",
                view="SCORES_BOOLEAN",
                measure="count",
                agg="count",
                dimensions=("timestampDay",),
                names=(_score("delivered"),),
                delivered=True,
                chart="VERTICAL_BAR",
            ),
        ),
    )


def _money(product: str) -> DashboardSpec:
    def w(key: str, title: str, about: str, **kw: Any) -> WidgetSpec:
        return _widget(product, "money", key, title, about, **kw)

    return DashboardSpec(
        product=product,
        key="money",
        title=f"Dónde va la plata · {product}",
        about="Which role to move to a cheaper model or cut back.",
        widgets=(
            w(
                "cost_by_role",
                "Costo por rol",
                "Dollars by role.",
                agg="sum",
                dimensions=("name",),
                names=task_scores.FAMILIES["cost_usd.role"],
                chart="HORIZONTAL_BAR",
            ),
            w(
                "cost_by_model",
                "Costo por modelo",
                "Cost of the model calls, by model.",
                view="OBSERVATIONS",
                measure="totalCost",
                agg="sum",
                dimensions=("providedModelName",),
                chart="VERTICAL_BAR",
            ),
        ),
    )


def build_dashboards(product: str) -> tuple[DashboardSpec, ...]:
    """The four dashboards of `product`, the only way to build them."""
    return (_overview(product), _improvement(product), _operation(product), _money(product))


def rows(dashboards: Iterable[DashboardSpec]) -> list[Row]:
    """The rows to store: every widget, then every dashboard that places them."""
    specs = list(dashboards)
    return [
        *(widget.row() for dash in specs for widget in dash.widgets),
        *(dash.row() for dash in specs),
    ]


@dataclass(frozen=True)
class Changes:
    """What turns the stored rows into the desired ones. `upserts` list widgets before the
    dashboards that place them; `deletes` list dashboards before their widgets."""

    upserts: tuple[Row, ...]
    deletes: tuple[Row, ...]
    unchanged: tuple[Row, ...]


def _widgets_first(found: Iterable[Row]) -> tuple[Row, ...]:
    return tuple(sorted(found, key=lambda r: isinstance(r, DashboardRow)))


def _dashboards_first(found: Iterable[Row]) -> tuple[Row, ...]:
    return tuple(sorted(found, key=lambda r: isinstance(r, WidgetRow)))


def plan(desired: Iterable[Row], existing: Iterable[Row]) -> Changes:
    """Desired against stored, by id and by content. A stored row is deleted only when it
    carries the marker of the product the desired rows are for and no desired row has its
    id: a row by hand, or another product's, is never touched. Without desired rows there
    is no product to scope a delete to, so nothing is deleted."""
    wanted = list(desired)
    products = {product_of(row) for row in wanted}
    if len(products) > 1:
        raise ValueError("the desired rows must belong to one product")
    product = products.pop() if products else None
    stored = {(type(row), row.id): row for row in existing}
    wanted_keys = {(type(row), row.id) for row in wanted}
    unchanged = [row for row in wanted if stored.get((type(row), row.id)) == row]
    upserts = [row for row in wanted if stored.get((type(row), row.id)) != row]
    deletes = [
        row
        for key, row in stored.items()
        if key not in wanted_keys and product is not None and product_of(row) == product
    ]
    return Changes(_widgets_first(upserts), _dashboards_first(deletes), tuple(unchanged))


ZERO_ROW_EXCEPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "overview",
        "escalations_lost",
        "a product that never escalated writes no escalation score: no rows is the good case",
    ),
    (
        "improvement",
        "returns_by_category",
        "a product whose tasks were never returned writes no returns.<category> score",
    ),
    (
        "improvement",
        "failures_by_reason",
        "a product without a failed run writes no failures.<reason> score",
    ),
    (
        "operation",
        "escalations_by_class",
        "a product that never escalated writes no escalation score: no rows is the good case",
    ),
    (
        "operation",
        "account_limit_failures_per_day",
        "a product that never hit the account limit writes no failures.account_limit score",
    ),
)
"""The widgets whose `--verify` count may be zero on a healthy product, because their score
is omitted when the thing did not happen (never written as 0). Only widgets fed by
`failures.*`, `escalations.*`, `returns.*` or `cost_usd.role.*` can be here: a base score,
`delivered_cost_usd` and the rest are written for every delivered task, so an empty widget
there is a break, not an absence."""

_MAY_BE_EMPTY = frozenset(
    name
    for family in ("returns", "failures", "escalations", "cost_usd.role")
    for name in task_scores.FAMILIES[family]
)


def exception_problems(
    exceptions: Iterable[tuple[str, str, str]], dashboards: Iterable[DashboardSpec]
) -> list[str]:
    """Why an exception list is not acceptable, one line per fault; empty when it is."""
    built = {(dash.key, w.key): w for dash in dashboards for w in dash.widgets}
    problems: list[str] = []
    for dashboard, widget, reason in exceptions:
        spec = built.get((dashboard, widget))
        if spec is None:
            problems.append(f"{dashboard}/{widget} names no widget")
            continue
        if not reason.strip():
            problems.append(f"{dashboard}/{widget} has no reason")
        if not spec.scores or not set(spec.scores) <= _MAY_BE_EMPTY:
            problems.append(
                f"{dashboard}/{widget} may not be empty: it reads a score always written"
            )
    return problems
