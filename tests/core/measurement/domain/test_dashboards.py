"""The Langfuse dashboard definitions, their diff and the README widget table (GH-274,
ADR-0051): `agentic.core.measurement.domain.dashboards`."""

import hashlib
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
PRODUCT = "agentic-system"


def built() -> tuple[d.DashboardSpec, ...]:
    return d.build_dashboards(PRODUCT)


def widgets() -> list[d.WidgetSpec]:
    return [w for dash in built() for w in dash.widgets]


def all_rows(product: str = PRODUCT) -> list[d.Row]:
    return d.rows(d.build_dashboards(product))


def env_filter(product: str) -> dict:
    return {
        "column": "environment",
        "type": "stringOptions",
        "operator": "any of",
        "value": [product],
    }


# --- the four dashboards and the environment filter ---------------------------------------


def test_the_four_dashboards_are_built_with_spanish_titles_and_the_product_suffix():
    titles = [dash.title for dash in built()]
    assert titles == [
        f"Panorama · {PRODUCT}",
        f"Mejora del sistema · {PRODUCT}",
        f"Operación · {PRODUCT}",
        f"Dónde va la plata · {PRODUCT}",
    ]
    assert [dash.key for dash in built()] == ["overview", "improvement", "operation", "money"]


def test_every_widget_carries_the_environment_filter_of_its_product():
    assert widgets()
    for widget in widgets():
        assert env_filter(PRODUCT) in widget.row().filters, widget.key


def test_widgets_are_built_for_the_product_asked_never_for_another():
    other = [w for dash in d.build_dashboards("other") for w in dash.widgets]
    for widget in other:
        assert env_filter("other") in widget.row().filters
        assert env_filter(PRODUCT) not in widget.row().filters


def test_a_widget_without_the_environment_filter_cannot_be_constructed():
    good = widgets()[0]
    with pytest.raises(ValueError, match="environment"):
        replace(good, filters=tuple(f for f in good.filters if f["column"] != "environment"))


def test_a_widget_whose_environment_filter_names_another_product_cannot_be_constructed():
    good = widgets()[0]
    wrong = tuple(env_filter("other") if f["column"] == "environment" else f for f in good.filters)
    with pytest.raises(ValueError, match="environment"):
        replace(good, filters=wrong)


def test_a_widget_reading_a_name_that_is_not_a_score_cannot_be_constructed():
    good = next(w for w in widgets() if w.scores)
    bad = tuple({**f, "value": ["made_up"]} if f["column"] == "name" else f for f in good.filters)
    with pytest.raises(ValueError, match="not a task score"):
        replace(good, filters=bad)


def test_no_widget_filters_unknown_out_of_a_breakdown():
    for widget in widgets():
        for f in widget.filters:
            assert d.UNKNOWN not in json.dumps(f["value"]), widget.key
            if f["column"] == "traceRelease":
                raise AssertionError(f"{widget.key} filters the release axis")


def test_a_filter_that_drops_unknown_cannot_be_constructed():
    good = widgets()[0]
    with pytest.raises(ValueError, match="unknown"):
        replace(
            good,
            filters=(
                *good.filters,
                {
                    "column": "traceRelease",
                    "type": "stringOptions",
                    "operator": "none of",
                    "value": ["unknown"],
                },
            ),
        )


# --- the stored shape ----------------------------------------------------------------------

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


def test_a_widget_row_has_the_field_set_of_the_golden():
    preset = GOLDEN["preset_widgets"][0]
    for widget in widgets():
        stored = widget.row().columns()
        assert (
            set(stored)
            == WIDGET_COLUMNS
            == set(preset) - {"created_at", "updated_at", "created_by", "updated_by", "project_id"}
        )
        assert set(stored["chart_config"]) <= set(preset["chart_config"]) | {"type", "row_limit"}
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
        dimensions = view["dimensions"]
        measures = view["measures"]
        assert all(x["field"] in dimensions for x in stored["dimensions"]), widget.key
        assert all(m["measure"] in measures for m in stored["metrics"]), widget.key
        assert all(m["agg"] in GOLDEN["aggregations"] for m in stored["metrics"])
        for f in stored["filters"]:
            assert f["column"] in dimensions, (widget.key, f)
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


# --- ids and the marker --------------------------------------------------------------------


def test_ids_are_deterministic_and_unique_across_dashboards_widgets_and_placements():
    first = [r.id for r in all_rows()]
    assert first == [r.id for r in all_rows()]
    placements = [
        p["id"] for dash in built() for p in dash.row().columns()["definition"]["widgets"]
    ]
    every = [*first, *placements]
    assert len(set(every)) == len(every)


def test_an_id_is_derived_from_the_product_and_the_stable_keys():
    dash = built()[0]
    widget = dash.widgets[0]
    assert dash.row().id == str(d.uuid.uuid5(d.NAMESPACE, f"{PRODUCT}/{dash.key}"))
    assert widget.row().id == str(d.uuid.uuid5(d.NAMESPACE, f"{PRODUCT}/{dash.key}/{widget.key}"))
    assert {r.id for r in all_rows("other")}.isdisjoint(r.id for r in all_rows())


def test_every_row_ends_its_description_with_the_marker_of_its_product():
    for row in all_rows():
        assert row.description.endswith("\n" + d.marker(PRODUCT)), row.id
        assert d.product_of(row) == PRODUCT


def test_a_row_without_a_marker_has_no_product():
    row = replace(all_rows()[0], description="made by hand")
    assert d.product_of(row) is None


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
        assert last == d.marker(PRODUCT), row.id
        assert text, row.id
        assert not _spanish(text), (row.id, text)


# --- scores read ---------------------------------------------------------------------------


def test_every_score_a_widget_reads_is_a_score_name():
    for widget in widgets():
        assert set(widget.scores) <= set(task_scores.SCORE_NAMES), widget.key


def test_a_widget_reading_a_family_reads_every_member_of_its_closed_set():
    by_key = {w.key: w for w in widgets()}
    assert by_key["returns_by_category"].scores == task_scores.FAMILIES["returns"]
    assert by_key["failures_by_reason"].scores == task_scores.FAMILIES["failures"]
    assert by_key["cost_by_role"].scores == task_scores.FAMILIES["cost_usd.role"]


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


def test_a_row_without_our_marker_or_of_another_product_is_never_deleted():
    desired = all_rows()
    unmarked = replace(desired[0], id="by-hand", description="made by hand")
    other = all_rows("other")[0]
    marker_text = replace(
        desired[0], id="quoted", description=f"see {d.marker(PRODUCT)} in the middle"
    )
    changes = d.plan(desired, [*desired, unmarked, other, marker_text])
    assert changes.deletes == ()


def test_planning_without_desired_rows_deletes_nothing():
    assert d.plan([], all_rows()).deletes == ()


def test_desired_rows_of_two_products_are_refused():
    with pytest.raises(ValueError, match="one product"):
        d.plan([*all_rows(), *all_rows("other")], [])


# --- the rows a zero count may be --------------------------------------------------------


def test_every_zero_row_exception_names_a_built_widget_and_gives_a_reason():
    assert d.ZERO_ROW_EXCEPTIONS
    assert d.exception_problems(d.ZERO_ROW_EXCEPTIONS, built()) == []


def test_an_exception_for_a_widget_nobody_builds_is_refused():
    problems = d.exception_problems((("overview", "nothing", "because"),), built())
    assert any("names no widget" in p for p in problems)


def test_an_exception_without_a_reason_is_refused():
    key = d.ZERO_ROW_EXCEPTIONS[0]
    assert any("reason" in p for p in d.exception_problems(((key[0], key[1], " "),), built()))


def test_an_exception_on_a_base_score_or_on_delivered_cost_is_refused():
    by_key = {(dash.key, w.key): w for dash in built() for w in dash.widgets}
    base = next(k for k, w in by_key.items() if set(w.scores) & set(task_scores.BASE_SCORE_NAMES))
    fixed = next(
        k
        for k, w in by_key.items()
        if "delivered_cost_usd" in w.scores
        or set(w.scores) & set(task_scores.FAMILIES["delivered_cost_usd.type"])
    )
    for key in (base, fixed):
        problems = d.exception_problems(((*key, "no rows yet"),), built())
        assert any("may not be empty" in p for p in problems), key


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
                    "dashboard": dash.title.removesuffix(f" · {PRODUCT}"),
                    "title": stored.name,
                    "view": stored.view,
                    "measure": stored.metrics[0]["measure"],
                    "agg": stored.metrics[0]["agg"],
                    "reads": list(w.scores) or [stored.metrics[0]["measure"]],
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
    row = "| Panorama | Costo | `SCORES_NUMERIC` | `value` · `p50` | `cycle_h` | | | |\n"
    documented = documented_widgets(head + row + "\nrest")
    assert len(documented) == 1 != len(built_rows())


def test_a_table_naming_a_family_nothing_builds_is_rejected():
    head = f"{HEADING}\n\n{TABLE_HEAD}\n"
    row = "| Panorama | x | `SCORES_NUMERIC` | `value` · `p50` | `made_up.<Nothing>` | | | |\n"
    with pytest.raises(AssertionError, match="names a family nothing builds"):
        documented_widgets(head + row + "\nrest")


def test_a_readme_without_the_section_is_rejected():
    with pytest.raises(AssertionError, match="heading not found"):
        documented_widgets("# nothing here")


def test_every_widget_is_placed_in_time_by_the_end_of_its_task():
    for widget in widgets():
        assert widget.placed_by == "task_time.end"


# --- Costo por modelo reads the per-model score family (GH-283) ----------------------------

# The sha256 of every row but "Costo por modelo" (widgets, then the dashboards that place
# them), as built before GH-283, for each product. A widget whose planned row moves breaks it.
# Re-pinned once by GH-299: the failure-reason family iterates `FailureReason`, so the new
# `stall` reason adds itself to the one row that lists the reasons (nothing else moved).
# Re-pinned again by GH-319: the role-cost and returns families iterate `ROLES` and
# `ChangeCategory`, so the Tech Writer roles and the copy categories add their score names.
# Re-pinned by GH-321: QA Performance joins `ROLES`, so its role-cost score names join the row.
UNCHANGED_ROWS = {
    "agentic-system": "be8c04a96d1e1c057dfb565fc229ae4d652ce1541844e376ab7288ff968a4bfd",
    "apostrophe": "c5f45a0bcc19ef521df9d178e63078f069e4d70dbada073738218a1ef4d68404",
}


def cost_by_model(product: str = PRODUCT) -> d.WidgetSpec:
    return next(
        w
        for dash in d.build_dashboards(product)
        for w in dash.widgets
        if (dash.key, w.key) == ("money", "cost_by_model")
    )


def test_costo_por_modelo_sums_the_model_score_family_by_name_in_the_product_environment():
    widget = cost_by_model()
    row = widget.row()
    assert row.name == "Costo por modelo"
    assert row.view == "SCORES_NUMERIC"
    assert row.metrics == [{"measure": "value", "agg": "sum"}]
    assert row.dimensions == [{"field": "name"}]
    assert row.chart_type == "VERTICAL_BAR"
    assert env_filter(PRODUCT) in widget.filters
    assert widget.scores == task_scores.FAMILIES["cost_usd.model"]
    assert widget.placed_by == "task_time.end"


@pytest.mark.parametrize("product", UNCHANGED_ROWS)
def test_no_widget_but_costo_por_modelo_changes_its_planned_row(product):
    others = [
        r.columns() for r in all_rows(product) if getattr(r, "name", "") != "Costo por modelo"
    ]
    digest = hashlib.sha256(json.dumps(others, sort_keys=True).encode()).hexdigest()
    assert digest == UNCHANGED_ROWS[product]


@pytest.mark.parametrize("product", UNCHANGED_ROWS)
def test_the_rows_re_applied_change_nothing(product):
    desired = all_rows(product)
    changes = d.plan(desired, all_rows(product))
    assert changes.upserts == () and changes.deletes == ()


def test_the_old_observations_row_is_replaced_not_kept():
    stale = replace(cost_by_model().row(), view="OBSERVATIONS", dimensions=[])
    desired = all_rows()
    stored = [stale if r.id == stale.id else r for r in desired]
    changes = d.plan(desired, stored)
    assert [r.id for r in changes.upserts] == [stale.id]


def test_a_widget_on_the_observations_view_cannot_be_built():
    bad: dict[str, Any] = {"view": "OBSERVATIONS"}
    with pytest.raises(ValueError, match="OBSERVATIONS"):
        replace(widgets()[0], **bad)
