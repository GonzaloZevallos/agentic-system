"""`apply_dashboards` over dict fakes of its two ports (GH-274, ADR-0051)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.measurement.application import dashboards as app
from agentic.core.measurement.domain import dashboards as dom

PRODUCT = "agentic-system"
GOLDEN = Path(__file__).resolve().parents[3] / "fixtures" / "langfuse_dashboards" / "golden.json"


class FakeStore:
    """A `DashboardStore` over a dict keyed by (kind, id); `tamper` mangles what `read`
    returns, the way a database that silently rewrote a row would."""

    def __init__(self, columns=None, rows=(), tamper=None):
        self._columns = app.RECORDED_COLUMNS if columns is None else columns
        self.rows = {(type(r), r.id): r for r in rows}
        self.applied = []
        self.tamper = tamper

    def columns(self):
        return self._columns

    def read(self, product):
        found = [r for r in self.rows.values() if dom.product_of(r) == product]
        return self.tamper(found) if self.tamper else found

    def apply(self, changes):
        self.applied.append(changes)
        for row in changes.deletes:
            self.rows.pop((type(row), row.id), None)
        for row in changes.upserts:
            self.rows[(type(row), row.id)] = row


class FakeData:
    def __init__(self, zero=(), default=3):
        self.zero = set(zero)
        self.default = default
        self.asked = []

    def count(self, widget):
        self.asked.append((widget.dashboard_key, widget.key))
        return 0 if (widget.dashboard_key, widget.key) in self.zero else self.default


def widget_count():
    return sum(len(d.widgets) for d in dom.build_dashboards(PRODUCT))


def test_the_first_apply_creates_every_row_and_the_second_changes_nothing():
    store = FakeStore()
    first = app.apply_dashboards(PRODUCT, store)
    assert len(first.changes.upserts) == widget_count() + 4
    assert first.ok and first.applied
    second = app.apply_dashboards(PRODUCT, store)
    assert second.changes.upserts == () and second.changes.deletes == ()
    assert len(second.changes.unchanged) == widget_count() + 4
    assert second.ok


def test_a_dry_run_plans_and_writes_nothing():
    store = FakeStore()
    report = app.apply_dashboards(PRODUCT, store, dry_run=True)
    assert len(report.changes.upserts) == widget_count() + 4
    assert store.applied == [] and store.rows == {} and not report.applied


def test_a_row_edited_in_the_ui_is_put_back():
    store = FakeStore()
    app.apply_dashboards(PRODUCT, store)
    widget = next(r for r in store.rows.values() if isinstance(r, dom.WidgetRow))
    store.rows[(dom.WidgetRow, widget.id)] = replace(widget, name="edited by hand")
    report = app.apply_dashboards(PRODUCT, store)
    assert [r.id for r in report.changes.upserts] == [widget.id]
    assert store.rows[(dom.WidgetRow, widget.id)] == widget


def test_a_stale_row_of_the_product_is_deleted_and_another_products_is_not():
    store = FakeStore()
    app.apply_dashboards(PRODUCT, store)
    app.apply_dashboards("apostrophe", store)
    stale = replace(
        next(r for r in store.rows.values() if isinstance(r, dom.WidgetRow)),
        id="gone",
        description=dom._described("old", PRODUCT),
    )
    store.rows[(dom.WidgetRow, "gone")] = stale
    before = len(store.rows)
    report = app.apply_dashboards(PRODUCT, store)
    assert [r.id for r in report.changes.deletes] == ["gone"]
    assert len(store.rows) == before - 1


def test_the_preflight_refuses_on_column_drift_before_writing_anything():
    drifted = app.RECORDED_COLUMNS | {("dashboards", "owner_team")}
    store = FakeStore(columns=drifted)
    with pytest.raises(app.ColumnDrift, match="dashboards.owner_team"):
        app.apply_dashboards(PRODUCT, store)
    assert store.applied == []
    missing = app.RECORDED_COLUMNS - {("dashboard_widgets", "chart_config")}
    with pytest.raises(app.ColumnDrift, match="dashboard_widgets.chart_config"):
        app.apply_dashboards(PRODUCT, FakeStore(columns=missing), dry_run=True)


def test_the_recorded_columns_are_the_goldens():
    golden = json.loads(GOLDEN.read_text())
    assert app.RECORDED_COLUMNS == {(c["table"], c["column"]) for c in golden["columns"]}
    assert app.RECORDED_IMAGE == golden["image"]


def test_a_read_back_that_differs_from_the_plan_fails():
    def drop_one(found):
        return found[1:]

    report = app.apply_dashboards(PRODUCT, FakeStore(tamper=drop_one))
    assert not report.ok
    assert any("read-back" in p for p in report.problems)


def test_a_read_back_with_a_changed_row_fails():
    def mangle(found):
        return [replace(r, name="x") if isinstance(r, dom.DashboardRow) else r for r in found]

    report = app.apply_dashboards(PRODUCT, FakeStore(tamper=mangle))
    assert not report.ok


def test_a_zero_row_widget_fails_unless_it_is_an_exception():
    zero = ("overview", "cycle_time")
    report = app.apply_dashboards(PRODUCT, FakeStore(), data=FakeData(zero=[zero]))
    assert not report.ok
    assert any("overview/cycle_time" in p for p in report.problems)
    allowed = ("overview", "escalations_lost")
    assert allowed in {(d, w) for d, w, _ in dom.ZERO_ROW_EXCEPTIONS}
    report = app.apply_dashboards(PRODUCT, FakeStore(), data=FakeData(zero=[allowed]))
    assert report.ok
    assert (("overview", "escalations_lost", 0)) in report.counts


def test_verify_counts_every_widget_once_and_reports_rows():
    data = FakeData()
    report = app.apply_dashboards(PRODUCT, FakeStore(), dry_run=True, data=data)
    assert len(data.asked) == widget_count() and len(report.counts) == widget_count()
    assert report.counts[0] == ("overview", "cost_per_delivered_pr", 3)


def test_without_data_nothing_is_counted():
    assert app.apply_dashboards(PRODUCT, FakeStore()).counts == ()
