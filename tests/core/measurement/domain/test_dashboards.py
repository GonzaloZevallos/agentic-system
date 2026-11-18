"""The Langfuse dashboard definition, its diff and the README widget table (GH-274, GH-819,
ADR-0051): `agentic.core.measurement.domain.dashboards`. The golden fixture is the oracle for
the shape of every row."""

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentic.core.measurement.domain import dashboards as d
from agentic.core.measurement.domain import task_scores
from tests.readme_metrics import README

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[4] / "tests/fixtures/langfuse_dashboards/golden.json"
    ).read_text(encoding="utf-8")
)
TITLE = "¿Dónde se va el tiempo?"


def built() -> tuple[d.DashboardSpec, ...]:
    return d.build_dashboards()


def widgets() -> list[d.WidgetSpec]:
    return [w for dash in built() for w in dash.widgets]


def by_key(key: str) -> d.WidgetSpec:
    return next(w for w in widgets() if w.key == key)


def all_rows() -> list[d.Row]:
    return d.rows(built())


# --- one dashboard for both products ---------------------------------------------------------


def test_exactly_one_dashboard_is_built_and_it_names_no_old_one():
    assert [dash.key for dash in built()] == ["time"]
    assert built()[0].title == TITLE
    source = Path(d.__file__).read_text(encoding="utf-8")
    for old in ("_overview", "_improvement", "_operation", "_money", "Panorama", "Operación"):
        assert old not in source


def test_no_widget_carries_an_environment_filter_and_the_dashboard_stores_none():
    for widget in widgets():
        assert all(f["column"] != "environment" for f in widget.row().filters), widget.key
    assert built()[0].row().filters == []


def test_a_widget_with_an_environment_filter_cannot_be_constructed():
    good = widgets()[0]
    env = {
        "column": "environment",
        "type": "stringOptions",
        "operator": "any of",
        "value": ["agentic-system"],
    }
    with pytest.raises(ValueError, match="environment"):
        replace(good, filters=(*good.filters, env))


def test_a_widget_reading_a_name_that_is_not_a_score_cannot_be_constructed():
    good = widgets()[0]
    bad = tuple({**f, "value": ["made_up"]} if f["column"] == "name" else f for f in good.filters)
    with pytest.raises(ValueError, match="not a task score"):
        replace(good, filters=bad)


def test_a_filter_that_drops_unknown_or_touches_the_release_cannot_be_constructed():
    good = widgets()[0]
    release = {
        "column": "traceRelease",
        "type": "stringOptions",
        "operator": "none of",
        "value": ["unknown"],
    }
    with pytest.raises(ValueError, match="unknown"):
        replace(good, filters=(*good.filters, release))
    dropping = {**release, "column": "name", "operator": "none of", "value": ["cycle_h"]}
    with pytest.raises(ValueError, match="unknown"):
        replace(good, filters=(*good.filters, dropping))


def test_a_chart_config_key_nobody_verified_cannot_be_constructed():
    good = by_key("hours_by_stage")
    with pytest.raises(ValueError, match="not verified"):
        replace(good, extra={"made_up_key": True})
    with pytest.raises(ValueError, match="not verified"):
        replace(by_key("share_by_stage"), extra={"show_value_labels": True})


def test_a_widget_on_the_observations_view_cannot_be_built():
    bad: dict[str, Any] = {"view": "OBSERVATIONS"}
    with pytest.raises(ValueError, match="OBSERVATIONS"):
        replace(widgets()[0], **bad)


# --- the widgets the owner approved ------------------------------------------------------------


def test_the_main_chart_is_horizontal_bars_of_stage_hours_with_value_labels_and_a_pie_beside():
    bars, pie = by_key("hours_by_stage").row(), by_key("share_by_stage").row()
    for row, chart in ((bars, "HORIZONTAL_BAR"), (pie, "PIE")):
        assert row.chart_type == chart and row.view == "SCORES_NUMERIC"
        assert row.dimensions == [{"field": "name"}]
        assert row.metrics == [{"measure": "value", "agg": "sum"}]
        assert row.filters[0]["value"] == list(task_scores.FAMILIES["stage_h"])
    assert bars.chart_config == {
        "type": "HORIZONTAL_BAR",
        "row_limit": 100,
        "show_value_labels": True,
    }
    assert pie.chart_config == {"type": "PIE", "row_limit": 100}


def test_the_headline_numbers_are_the_two_medians_and_the_count_of_tasks_without_a_split():
    one = by_key("cycle_median").row()
    two = by_key("cycle_active_median").row()
    assert (one.chart_type, one.metrics, one.filters[0]["value"]) == (
        "NUMBER",
        [{"measure": "value", "agg": "p50"}],
        ["cycle_h"],
    )
    assert (two.metrics, two.filters[0]["value"]) == (
        [{"measure": "value", "agg": "p50"}],
        ["cycle_active_h"],
    )
    partial = by_key("tasks_without_split").row()
    assert partial.view == "SCORES_BOOLEAN" and partial.chart_type == "NUMBER"
    assert partial.metrics == [{"measure": "count", "agg": "count"}]
    assert partial.filters == [
        {
            "column": "name",
            "type": "stringOptions",
            "operator": "any of",
            "value": ["stage_split_known"],
        },
        {"column": "booleanValue", "type": "boolean", "operator": "=", "value": False},
    ]


def test_the_weekly_trend_stacks_the_eleven_stages_placed_by_the_merge():
    trend = by_key("hours_by_stage_per_week")
    assert trend.row().chart_type == "BAR_TIME_SERIES"
    assert trend.scores == task_scores.FAMILIES["stage_h"] and len(trend.scores) == 11
    assert trend.placed_by == "pr_merged_at"


def test_the_held_stage_is_among_the_widget_series_and_aggregates_by_sum_or_max():
    """A task scored before `held` has no `stage_h.held`: it reads as 0 because the stage hours
    are summed and the slowest stage is the max, so no history is rewritten."""
    stage_widgets = [
        w for w in widgets() if w.row().filters[0]["value"] == list(task_scores.FAMILIES["stage_h"])
    ]
    assert stage_widgets
    assert all(w.row().metrics == [{"measure": "value", "agg": "sum"}] for w in stage_widgets)
    assert "stage_h.held" in task_scores.FAMILIES["stage_h"]
    slowest = by_key("slowest_tasks").row()
    assert slowest.metrics == [{"measure": "value", "agg": "max"}]
    assert "slowest_stage.held" in slowest.filters[0]["value"]


def test_the_slowest_table_lists_ten_tasks_by_cycle_hours_with_their_slowest_stage():
    row = by_key("slowest_tasks").row()
    assert row.chart_type == "PIVOT_TABLE"
    assert row.dimensions == [{"field": "traceName"}, {"field": "name"}]
    assert row.metrics == [{"measure": "value", "agg": "max"}]
    assert row.filters[0]["value"] == list(task_scores.FAMILIES["slowest_stage"])
    assert row.chart_config == {
        "type": "PIVOT_TABLE",
        "row_limit": 10,
        "defaultSort": {"column": "max_value", "order": "DESC"},
    }


def test_no_range_and_no_bucket_are_stored():
    for row in all_rows():
        text = json.dumps(row.columns())
        for stored in ("granularity", "dateRange", "fromTimestamp", "bucket"):
            assert stored not in text


# --- the stored shape, against the golden ---------------------------------------------------------

WIDGET_COLUMNS = {
    c["column"]
    for c in GOLDEN["columns"]
    if c["table"] == "dashboard_widgets"
    and c["column"] not in {"created_at", "updated_at", "created_by", "updated_by", "project_id"}
}
DASHBOARD_COLUMNS = {
    c["column"]
    for c in GOLDEN["columns"]
    if c["table"] == "dashboards"
    and c["column"] not in {"created_at", "updated_at", "created_by", "updated_by", "project_id"}
}
BUNDLE_KEYS = set(d.BUNDLE_CONFIG_KEYS)
"""`chart_config` keys read in the image bundle, not in the golden (ADR-0051 addendum)."""


def test_a_widget_row_has_the_field_set_and_chart_config_keys_of_the_golden_or_the_bundle():
    preset = GOLDEN["preset_widgets"][0]
    golden_keys = set(preset["chart_config"]) | {"type", "row_limit"}
    for widget in widgets():
        stored = widget.row().columns()
        assert set(stored) == WIDGET_COLUMNS
        assert set(stored["chart_config"]) <= golden_keys | BUNDLE_KEYS, widget.key
        assert stored["chart_config"]["type"] == stored["chart_type"]
        assert all(set(x) == {"field"} for x in stored["dimensions"])
        assert all(set(x) == {"measure", "agg"} for x in stored["metrics"])
        assert stored["min_version"] == 2


def test_widget_values_are_ones_the_golden_catalogues():
    views = {
        "SCORES_NUMERIC": "scores-numeric",
        "SCORES_BOOLEAN": "scores-boolean",
        "SCORES_CATEGORICAL": "scores-categorical",
    }
    for widget in widgets():
        stored = widget.row().columns()
        assert stored["view"] in GOLDEN["enums"]['"DashboardWidgetViews"']
        assert stored["chart_type"] in GOLDEN["enums"]['"DashboardWidgetChartType"']
        view = GOLDEN["views_v2"][views[stored["view"]]]
        assert all(x["field"] in view["dimensions"] for x in stored["dimensions"]), widget.key
        assert all(m["measure"] in view["measures"] for m in stored["metrics"]), widget.key
        assert all(m["agg"] in GOLDEN["aggregations"] for m in stored["metrics"])
        for f in stored["filters"]:
            assert f["column"] in view["dimensions"], (widget.key, f)
            assert f["operator"] in GOLDEN["filter_types"][f["type"]], (widget.key, f)


def test_a_dashboard_row_has_the_field_set_of_the_golden_and_places_every_widget_once():
    preset = GOLDEN["preset_dashboard"]
    for dash in built():
        stored = dash.row().columns()
        assert set(stored) == DASHBOARD_COLUMNS
        placed = stored["definition"]["widgets"]
        assert [set(p) for p in placed] == [set(preset["definition"]["widgets"][0])] * len(
            dash.widgets
        )
        assert [p["widgetId"] for p in placed] == [w.row().id for w in dash.widgets]
        assert all(p["type"] == "widget" and p["x_size"] > 0 and p["y_size"] > 0 for p in placed)
        assert len({p["id"] for p in placed}) == len(placed)
        assert len({(p["x"], p["y"]) for p in placed}) == len(placed)


# --- ids and the marker -------------------------------------------------------------------


def test_ids_are_deterministic_and_unique_across_dashboards_widgets_and_placements():
    first = [r.id for r in all_rows()]
    assert first == [r.id for r in all_rows()]
    placements = [
        p["id"] for dash in built() for p in dash.row().columns()["definition"]["widgets"]
    ]
    every = [*first, *placements]
    assert len(set(every)) == len(every)


def test_an_id_is_derived_from_the_scope_and_the_stable_keys():
    dash = built()[0]
    widget = dash.widgets[0]
    assert dash.row().id == str(d.uuid.uuid5(d.NAMESPACE, f"all/{dash.key}"))
    assert widget.row().id == str(d.uuid.uuid5(d.NAMESPACE, f"all/{dash.key}/{widget.key}"))


def test_every_row_ends_its_description_with_the_scope_marker():
    assert d.MARKER == "[agentic-dashboards scope=all]"
    for row in all_rows():
        assert row.description.endswith("\n" + d.MARKER), row.id
        assert d.in_scope(row)


def test_a_row_without_the_marker_is_not_in_scope():
    for text in ("made by hand", f"see {d.MARKER} in the middle"):
        assert not d.in_scope(replace(all_rows()[0], description=text))


# --- descriptions are English; only titles are Spanish -----------------------------------------

_SPANISH_WORDS = re.compile(r"\b(?:de|la|el|las|los|del|por|que|para|una|con)\b", re.IGNORECASE)


def _spanish(text: str) -> bool:
    return not text.isascii() or _SPANISH_WORDS.search(text) is not None


def test_the_spanish_guard_rejects_spanish_with_and_without_accents():
    assert _spanish("Vista del negocio: versión")
    assert _spanish("Mediana del costo por tipo")
    assert not _spanish("Median cost of each role, newest release first.")


def test_every_description_is_english_once_its_marker_line_is_removed():
    for row in all_rows():
        text, _, last = row.description.rpartition("\n")
        assert last == d.MARKER, row.id
        assert text and not _spanish(text), (row.id, text)


def test_every_score_a_widget_reads_is_a_score_name():
    for widget in widgets():
        assert set(widget.scores) <= set(task_scores.SCORE_NAMES), widget.key


# --- plan ----------------------------------------------------------------------------------


def test_planning_the_desired_rows_against_themselves_changes_nothing():
    changes = d.plan(all_rows(), all_rows())
    assert changes.upserts == () and changes.deletes == ()
    assert len(changes.unchanged) == len(all_rows())


def test_planning_against_nothing_upserts_every_row_widgets_before_dashboards():
    changes = d.plan(all_rows(), [])
    assert list(changes.upserts) == all_rows()
    assert changes.deletes == () and changes.unchanged == ()
    kinds = [type(r) for r in changes.upserts]
    assert kinds == sorted(kinds, key=lambda k: k is d.DashboardRow)


def test_a_changed_row_is_upserted_and_the_rest_left_alone():
    existing = all_rows()
    edited = replace(existing[0], name="edited in the UI")
    changes = d.plan(all_rows(), [edited, *existing[1:]])
    assert [r.id for r in changes.upserts] == [existing[0].id]
    assert changes.deletes == ()


def test_a_marked_row_no_longer_desired_is_deleted_dashboards_before_widgets():
    desired = all_rows()
    extra_widget = replace(desired[0], id="old-widget")
    extra_dashboard = replace(
        next(r for r in desired if isinstance(r, d.DashboardRow)), id="old-dashboard"
    )
    changes = d.plan(desired, [*desired, extra_widget, extra_dashboard])
    assert list(changes.deletes) == [extra_dashboard, extra_widget]
    assert changes.upserts == ()


def test_a_legacy_product_row_an_unmarked_row_and_a_quoted_marker_survive_plan():
    desired = all_rows()
    legacy = replace(
        desired[0],
        id="legacy",
        description="old\n[agentic-dashboards product=agentic-system]",
    )
    unmarked = replace(desired[0], id="by-hand", description="made by hand")
    quoted = replace(desired[0], id="quoted", description=f"see {d.MARKER} in the middle")
    changes = d.plan(desired, [*desired, legacy, unmarked, quoted])
    assert changes.deletes == ()


def test_planning_without_desired_rows_deletes_only_what_carries_the_marker():
    stored = all_rows()
    legacy = replace(stored[0], id="legacy", description="x\n[agentic-dashboards product=p]")
    assert {r.id for r in d.plan([], [*stored, legacy]).deletes} == {r.id for r in stored}


# --- the rows a zero count may be --------------------------------------------------------


def test_the_only_zero_row_exception_is_the_partial_count_with_its_reason():
    assert d.ZERO_ROW_EXCEPTIONS == (
        ("time", "tasks_without_split", "zero is the good case: every merged task had its split"),
    )
    assert d.exception_problems(d.ZERO_ROW_EXCEPTIONS, built()) == []


def test_an_exception_for_a_widget_nobody_builds_is_refused():
    problems = d.exception_problems((("time", "nothing", "because"),), built())
    assert any("names no widget" in p for p in problems)


def test_an_exception_without_a_reason_is_refused():
    key = d.ZERO_ROW_EXCEPTIONS[0]
    assert any("reason" in p for p in d.exception_problems(((key[0], key[1], " "),), built()))


def test_an_exception_on_a_widget_that_reads_a_score_always_written_is_refused():
    for widget in widgets():
        if widget.key == "tasks_without_split":
            continue
        problems = d.exception_problems((("time", widget.key, "no rows yet"),), built())
        assert any("may not be empty" in p for p in problems), widget.key


# --- the README table ----------------------------------------------------------------------

HEADING = "## Dashboards"
CELL = re.compile(r"`([^`]+)`")


def documented_widgets(text: str) -> list[dict]:
    """The rows of the table under the dashboards heading: dashboard, title, view, measure,
    aggregation, scores read (families expanded from the domain), breakdown, time."""
    if HEADING not in text:
        raise AssertionError(f"{HEADING!r} heading not found")
    section = re.split(r"\n## ", text.split(HEADING, 1)[1], maxsplit=1)[0]
    table = section.split("| Dashboard |", 1)
    if len(table) != 2:
        raise AssertionError(f"no widget table under {HEADING!r}")
    body = re.split(r"\n\n", table[1], maxsplit=1)[0]
    rows = []
    for line in body.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 8:
            raise AssertionError(f"a widget row needs 8 cells: {line}")
        dashboard, title, view, measure, reads, breakdown, time, _ = cells
        names: list[str] = []
        for token in CELL.findall(reads):
            family = re.fullmatch(r"([a-z_.]+)\.<[A-Za-z]+>", token)
            if family is None:
                names.append(token)
            elif family.group(1) in task_scores.FAMILIES:
                names.extend(task_scores.FAMILIES[family.group(1)])
            else:
                raise AssertionError(f"{token} names a family nothing builds")
        tokens = CELL.findall(measure)
        rows.append(
            {
                "dashboard": dashboard,
                "title": title,
                "view": CELL.findall(view)[0] if CELL.findall(view) else "",
                "measure": tokens[0] if tokens else "",
                "agg": tokens[1] if len(tokens) > 1 else "",
                "reads": names,
                "breakdown": CELL.findall(breakdown),
                "time": time,
            }
        )
    return rows


def built_rows() -> list[dict]:
    out = []
    for dash in built():
        for w in dash.widgets:
            stored = w.row()
            out.append(
                {
                    "dashboard": dash.title,
                    "title": stored.name,
                    "view": stored.view,
                    "measure": stored.metrics[0]["measure"],
                    "agg": stored.metrics[0]["agg"],
                    "reads": list(w.scores),
                    "breakdown": [x["field"] for x in stored.dimensions],
                    "time": w.placed_by,
                }
            )
    return out


def test_the_readme_widget_table_equals_the_built_widgets():
    documented = documented_widgets(README.read_text(encoding="utf-8"))
    expected = built_rows()
    assert [(r["dashboard"], r["title"]) for r in documented] == [
        (r["dashboard"], r["title"]) for r in expected
    ]
    for got, want in zip(documented, expected, strict=True):
        assert got["view"] == want["view"], want["title"]
        assert (got["measure"], got["agg"]) == (want["measure"], want["agg"]), want["title"]
        assert sorted(got["reads"]) == sorted(want["reads"]), want["title"]
        assert got["breakdown"] == want["breakdown"], want["title"]
        assert f"`{want['time']}`" in got["time"], want["title"]


TABLE_HEAD = "| Dashboard | T | V | M | R | B | Time | N |\n|---|---|---|---|---|---|---|---|"


def test_a_table_missing_a_widget_is_rejected():
    head = f"{HEADING}\n\n{TABLE_HEAD}\n"
    row = "| Tiempo | Ciclo | `SCORES_NUMERIC` | `value` · `p50` | `cycle_h` | | | |\n"
    documented = documented_widgets(head + row + "\nrest")
    assert len(documented) == 1 != len(built_rows())


def test_a_table_naming_a_family_nothing_builds_is_rejected():
    head = f"{HEADING}\n\n{TABLE_HEAD}\n"
    row = "| Tiempo | x | `SCORES_NUMERIC` | `value` · `p50` | `made_up.<Nothing>` | | | |\n"
    with pytest.raises(AssertionError, match="names a family nothing builds"):
        documented_widgets(head + row + "\nrest")


def test_a_readme_without_the_section_is_rejected():
    with pytest.raises(AssertionError, match="heading not found"):
        documented_widgets("# nothing here")
