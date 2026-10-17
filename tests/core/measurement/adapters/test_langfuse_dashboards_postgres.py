"""The psycopg adapter against a real Langfuse Postgres (GH-274, ADR-0051). Opt-in: skipped
unless `AGENTIC_LANGFUSE_DATABASE_URL` and `AGENTIC_LANGFUSE_PUBLIC_KEY` are set. It writes
under a throwaway product name and removes what it wrote."""

import os
import uuid
from dataclasses import replace

import pytest

from agentic.core.measurement.application import dashboards as app
from agentic.core.measurement.domain import dashboards as dom

DSN = os.environ.get("AGENTIC_LANGFUSE_DATABASE_URL")
KEY = os.environ.get("AGENTIC_LANGFUSE_PUBLIC_KEY")

pytestmark = pytest.mark.skipif(
    not (DSN and KEY), reason="needs AGENTIC_LANGFUSE_DATABASE_URL and the public key"
)


@pytest.fixture
def store():
    from agentic.core.measurement.adapters.langfuse.dashboards import PostgresDashboards

    assert DSN and KEY
    return PostgresDashboards(DSN, KEY)


@pytest.fixture
def products(store):
    mine = [f"it-{uuid.uuid4().hex[:8]}", f"it-{uuid.uuid4().hex[:8]}"]
    yield mine
    for product in mine:
        stale = store.read(product)
        store.apply(
            dom.Changes((), tuple(sorted(stale, key=lambda r: isinstance(r, dom.WidgetRow))), ())
        )


def test_columns_are_the_recorded_ones(store):
    assert store.columns() == app.RECORDED_COLUMNS


def test_what_is_written_is_read_back_equal_and_a_second_apply_changes_nothing(store, products):
    first = app.apply_dashboards(products[0], store)
    assert first.ok
    second = app.apply_dashboards(products[0], store)
    assert second.changes.upserts == () and second.changes.deletes == ()


def test_a_delete_never_touches_another_product_or_a_preset(store, products):
    a, b = products
    app.apply_dashboards(a, store)
    app.apply_dashboards(b, store)
    stale = store.read(a)
    store.apply(dom.Changes((), tuple(stale), ()))
    assert store.read(a) == []
    assert len(store.read(b)) == len(dom.rows(dom.build_dashboards(b)))


def test_a_delete_with_a_forged_marker_deletes_nothing(store, products):
    a, b = products
    app.apply_dashboards(b, store)
    row = store.read(b)[0]
    forged = replace(row, description=dom._described("x", a))
    store.apply(dom.Changes((), (forged,), ()))
    assert len(store.read(b)) == len(dom.rows(dom.build_dashboards(b)))


def test_an_unknown_public_key_is_refused():
    from agentic.core.measurement.adapters.langfuse.dashboards import PostgresDashboards

    assert DSN
    with pytest.raises(app.UnknownProject):
        PostgresDashboards(DSN, "pk-lf-no-such-key").read("p")


def test_an_unreachable_database_is_the_use_cases_own_error():
    from agentic.core.measurement.adapters.langfuse.dashboards import PostgresDashboards

    dead = PostgresDashboards("postgresql://x:y@127.0.0.1:1/z?connect_timeout=1", "k")
    with pytest.raises(app.StoreUnavailable):
        dead.columns()


@pytest.fixture
def other_project(store):
    """A second project owning one dashboard row at an id our product would write, removed
    (cascading to its rows) afterwards."""
    pid = f"it-proj-{uuid.uuid4().hex[:8]}"
    with store._connection() as conn:
        conn.execute(
            "insert into projects (id, name, org_id) "
            "select %s, %s, org_id from projects where id = %s",
            (pid, pid, store.project_id()),
        )
    yield pid
    with store._connection() as conn:
        conn.execute("delete from projects where id = %s", (pid,))


def test_a_row_of_another_project_at_our_id_is_refused_untouched_and_rolls_back_the_rest(
    store, products, other_project
):
    product = products[0]
    ours = dom.rows(dom.build_dashboards(product))
    foreign = ours[-1]  # a dashboard: every widget is upserted before it
    assert isinstance(foreign, dom.DashboardRow)
    with store._connection() as conn:
        conn.execute(
            "insert into dashboards (id, project_id, name, description, definition, filters) "
            "values (%s, %s, 'theirs', 'theirs', '{\"widgets\": []}', '[]')",
            (foreign.id, other_project),
        )
    with pytest.raises(RuntimeError, match="another project"):
        app.apply_dashboards(product, store)
    with store._connection() as conn:
        row = conn.execute(
            "select name, project_id from dashboards where id = %s", (foreign.id,)
        ).fetchone()
    assert row == ("theirs", other_project)
    assert store.read(product) == []
