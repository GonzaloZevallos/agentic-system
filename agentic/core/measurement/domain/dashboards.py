"""The one Langfuse dashboard of the system, as data, and the diff that applies it (GH-274,
GH-819, ADR-0051).

Pure: the rows Langfuse stores out; two row sets in, the changes that turn one into the
other out. Nothing here reads a database or the clock, so the ports of
`application/dashboards.py` run on dicts.

One dashboard, "where development time goes", serves every product. The product is
Langfuse's own filter bar on the `environment` of each score, never a widget filter, so the
same rows are written whichever product's directory runs `apply`. Four properties hold by
construction, not by convention:

- no widget carries an `environment` filter, a release filter or an operator that drops
  `unknown`: `WidgetSpec` refuses to exist with one;
- every score a widget reads is a member of `task_scores.SCORE_NAMES`: a family is taken
  from `task_scores.FAMILIES`, a base name is looked up there, never typed as a free literal;
- every id is derived from the scope and the stable keys, so applying twice is a no-op;
- every row carries the scope marker, the only thing `plan` may delete by: a row by hand, a
  preset, another project's row and a row of the four earlier per-product dashboards (their
  marker named a product) are never touched.

A widget never divides and never filters `unknown` out of a breakdown. It stores no date
range and no bucket size: those belong to Langfuse's date picker (ADR-0051, section 3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, get_args

from . import task_scores

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://agentic-system/langfuse-dashboards")
"""Where every dashboard and widget id is derived from; never changes, or a re-apply
would duplicate the rows."""

SCOPE = "all"
MARKER = f"[agentic-dashboards scope={SCOPE}]"
"""What every row's description ends with: the only thing a delete is scoped by."""

UNKNOWN = task_scores.UNKNOWN
MIN_VERSION = 2
"""The v2 views read `events_core`, the only table `langfuse:4` fills (ADR-0051 section 3)."""
ROW_LIMIT = 100

_COLUMN_RELEASE = "traceRelease"
_COLUMN_ENVIRONMENT = "environment"
_NON_TIME_SERIES = frozenset({"HORIZONTAL_BAR", "VERTICAL_BAR", "PIE"})
_DROPPING_OPERATORS = frozenset({"none of", "<>"})

BUNDLE_CONFIG_KEYS: dict[str, str] = {
    "show_value_labels": "HORIZONTAL_BAR",
    "defaultSort": "PIVOT_TABLE",
}
"""The `chart_config` keys beyond the golden's `type` and `row_limit`, each with the one
chart type that takes it. They are not in `tests/fixtures/langfuse_dashboards/golden.json`:
they were read in the zod schema of the `langfuse-web` image bundle (`HorizontalBarChartConfig`
and `PivotTableChartConfig`, web 4.35.0), the same way ADR-0051 section 3 read the rest."""

Filter = dict[str, Any]


def _described(text: str) -> str:
    return f"{text}\n{MARKER}"


def _id(*keys: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join((SCOPE, *keys))))


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


def _boolean(value: bool) -> Filter:
    return {"column": "booleanValue", "type": "boolean", "operator": "=", "value": value}


WidgetView = Literal["SCORES_NUMERIC", "SCORES_BOOLEAN", "SCORES_CATEGORICAL"]
"""The views a widget of ours reads: every one of them is a score. No widget reads the
observations view since GH-283, so the type cannot name it."""


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


def in_scope(row: Row) -> bool:
    """Whether the last line of the row's description is exactly our marker: the only rows
    `plan` may delete. A legacy `product=` marker, no marker and a quoted marker are not."""
    last = row.description.rsplit("\n", 1)[-1] if "\n" in row.description else ""
    return last == MARKER


@dataclass(frozen=True)
class WidgetSpec:
    """One widget. No filter may name the `environment` (the product is the dashboard's
    filter bar), touch the release axis or drop `unknown`; every name it filters on must be a
    score name and every extra `chart_config` key one the bundle verified for its chart
    type: anything else cannot be constructed."""

    dashboard_key: str
    key: str
    title: str
    about: str
    view: WidgetView
    measure: str
    agg: str
    dimensions: tuple[str, ...]
    filters: tuple[Filter, ...]
    chart_type: str
    place: tuple[int, int, int, int]
    """`(x, y, x_size, y_size)` on the dashboard grid."""
    extra: Mapping[str, Any] = field(default_factory=dict)
    """`chart_config` keys beyond `type` and `row_limit`'s default, by their bundle name."""

    def __post_init__(self) -> None:
        if self.view not in get_args(WidgetView):
            raise ValueError(f"{self.key}: {self.view!r} is not a score view")
        for name in self.scores:
            _score(name)
        for f in self.filters:
            if f["column"] == _COLUMN_ENVIRONMENT:
                raise ValueError(f"{self.key}: an environment filter is the filter bar's: {f}")
            if (
                f["column"] == _COLUMN_RELEASE
                or f["operator"] in _DROPPING_OPERATORS
                or UNKNOWN in (f["value"] if isinstance(f["value"], list) else [f["value"]])
            ):
                raise ValueError(f"{self.key}: a filter may not drop {UNKNOWN!r}: {f}")
        for key in self.extra:
            if key == "row_limit":
                continue
            if BUNDLE_CONFIG_KEYS.get(key) != self.chart_type:
                raise ValueError(
                    f"{self.key}: chart_config key {key!r} is not verified for {self.chart_type}"
                )

    @property
    def scores(self) -> tuple[str, ...]:
        """The score names the widget reads, from its `name` filter."""
        return tuple(n for f in self.filters if f["column"] == "name" for n in f["value"])

    @property
    def placed_by(self) -> str:
        """The timestamp that places what the widget counts in time: `pr_merged_at` when
        every score it reads is dated at the merge (`task_scores.score_time`), else the end
        of the task's last run."""
        if self.scores and all(task_scores.merge_dated(n) for n in self.scores):
            return "pr_merged_at"
        return "task_time.end"

    def row(self) -> WidgetRow:
        config: dict[str, Any] = {"type": self.chart_type}
        if self.chart_type in _NON_TIME_SERIES:
            config["row_limit"] = ROW_LIMIT
        config.update(self.extra)
        return WidgetRow(
            id=_id(self.dashboard_key, self.key),
            name=self.title,
            description=_described(self.about),
            view=self.view,
            dimensions=[{"field": field_} for field_ in self.dimensions],
            metrics=[{"measure": self.measure, "agg": self.agg}],
            filters=[dict(f) for f in self.filters],
            chart_type=self.chart_type,
            chart_config=config,
            min_version=MIN_VERSION,
        )


@dataclass(frozen=True)
class DashboardSpec:
    key: str
    title: str
    about: str
    widgets: tuple[WidgetSpec, ...]

    def row(self) -> DashboardRow:
        placed = [
            {
                "type": "widget",
                "id": _id(self.key, widget.key, "placement"),
                "widgetId": _id(self.key, widget.key),
                "x": widget.place[0],
                "y": widget.place[1],
                "x_size": widget.place[2],
                "y_size": widget.place[3],
            }
            for widget in self.widgets
        ]
        return DashboardRow(
            id=_id(self.key),
            name=self.title,
            description=_described(self.about),
            definition={"widgets": placed},
            filters=[],
        )


def _widget(
    key: str,
    title: str,
    about: str,
    *,
    place: tuple[int, int, int, int],
    view: WidgetView = "SCORES_NUMERIC",
    measure: str = "value",
    agg: str,
    dimensions: Sequence[str] = (),
    names: Iterable[str],
    boolean: bool | None = None,
    chart: str,
    extra: Mapping[str, Any] | None = None,
) -> WidgetSpec:
    """The one place a widget is assembled: a name filter, and a `booleanValue` filter only
    when `boolean` is true or false."""
    filters = [_names(names)]
    if boolean is not None:
        filters.append(_boolean(boolean))
    return WidgetSpec(
        dashboard_key="time",
        key=key,
        title=title,
        about=about,
        view=view,
        measure=measure,
        agg=agg,
        dimensions=tuple(dimensions),
        filters=tuple(filters),
        chart_type=chart,
        place=place,
        extra=dict(extra or {}),
    )


def _time() -> DashboardSpec:
    stages = task_scores.FAMILIES["stage_h"]
    return DashboardSpec(
        key="time",
        title="¿Dónde se va el tiempo?",
        about="Where development time goes: hours per stage of the cycle, for every product.",
        widgets=(
            _widget(
                "cycle_median",
                "Ciclo mediano (h)",
                "Median hours from the issue to the merge.",
                place=(0, 0, 4, 2),
                agg="p50",
                names=(_score("cycle_h"),),
                chart="NUMBER",
            ),
            _widget(
                "cycle_active_median",
                "Ciclo mediano sin esperas (h)",
                "Median cycle hours without the human wait and the account limit.",
                place=(4, 0, 4, 2),
                agg="p50",
                names=(_score("cycle_active_h"),),
                chart="NUMBER",
            ),
            _widget(
                "tasks_without_split",
                "Tareas sin reparto",
                "Merged tasks with no stage split: left out of the stage charts, counted here.",
                place=(8, 0, 4, 2),
                view="SCORES_BOOLEAN",
                measure="count",
                agg="count",
                names=(_score("stage_split_known"),),
                boolean=False,
                chart="NUMBER",
            ),
            _widget(
                "hours_by_stage",
                "¿Dónde se va el tiempo? (horas por etapa)",
                "Hours per stage, largest first, summed over the range.",
                place=(0, 2, 6, 6),
                agg="sum",
                dimensions=("name",),
                names=stages,
                chart="HORIZONTAL_BAR",
                extra={"show_value_labels": True},
            ),
            _widget(
                "share_by_stage",
                "Parte del total por etapa",
                "The same hours per stage, as a share of the total.",
                place=(6, 2, 6, 6),
                agg="sum",
                dimensions=("name",),
                names=stages,
                chart="PIE",
            ),
            _widget(
                "hours_by_stage_per_week",
                "Horas por etapa, por semana",
                "Hours per stage over time, placed by the week the pull request was merged.",
                place=(0, 8, 12, 6),
                agg="sum",
                dimensions=("name",),
                names=stages,
                chart="BAR_TIME_SERIES",
            ),
            _widget(
                "slowest_tasks",
                "Las 10 tareas más lentas",
                "The 10 merged tasks with the longest cycle: the task, the stage that took "
                "most hours and the cycle hours.",
                place=(0, 14, 12, 6),
                agg="max",
                dimensions=("traceName", "name"),
                names=task_scores.FAMILIES["slowest_stage"],
                chart="PIVOT_TABLE",
                extra={"row_limit": 10, "defaultSort": {"column": "max_value", "order": "DESC"}},
            ),
        ),
    )


def build_dashboards() -> tuple[DashboardSpec, ...]:
    """The dashboards we write: one, for every product."""
    return (_time(),)


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
    """Desired against stored, by id and by content. A stored row is deleted only when its
    description ends with `MARKER` and no desired row has its id: a row by hand, a preset, a
    row of another project and a row of the four earlier per-product dashboards (a
    `product=` marker) are never touched."""
    wanted = list(desired)
    stored = {(type(row), row.id): row for row in existing}
    wanted_keys = {(type(row), row.id) for row in wanted}
    unchanged = [row for row in wanted if stored.get((type(row), row.id)) == row]
    upserts = [row for row in wanted if stored.get((type(row), row.id)) != row]
    deletes = [row for key, row in stored.items() if key not in wanted_keys and in_scope(row)]
    return Changes(_widgets_first(upserts), _dashboards_first(deletes), tuple(unchanged))


ZERO_ROW_EXCEPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "time",
        "tasks_without_split",
        "zero is the good case: every merged task had its split",
    ),
)
"""The widgets whose `--verify` count may be zero on a healthy system, because what they
count is the absence of something good. Only a widget that counts `stage_split_known` = false
can be here: every other widget reads scores written for every merged task, so an empty one
is a break, not an absence."""


def _counts_the_absent(spec: WidgetSpec) -> bool:
    return set(spec.scores) == {"stage_split_known"} and _boolean(False) in spec.filters


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
        if not _counts_the_absent(spec):
            problems.append(
                f"{dashboard}/{widget} may not be empty: it reads a score always written"
            )
    return problems
