"""The dashboards of a product, in Langfuse's own Postgres (GH-274, ADR-0051).

The only module that imports `psycopg`. Every read and every write is scoped to one project
(the one that owns the public key) and, for rows we own, to one product through the marker
at the end of `description`: a preset row (`project_id` NULL), another project's row and
another product's row are never read, overwritten or deleted. One transaction per `apply`:
the rows are all written or none is."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ...domain import dashboards as domain

TABLES = ("dashboards", "dashboard_widgets")


class UnknownProject(ValueError):
    """The public key belongs to no project (or to an organization key)."""


_PROJECT = "select project_id from api_keys where public_key = %s and scope = 'PROJECT'"
_COLUMNS = (
    "select table_name, column_name from information_schema.columns "
    "where table_schema = 'public' and table_name = any(%s)"
)
_OURS = "project_id = %s and right(description, char_length(%s::text)) = %s::text"
_READ_WIDGETS = (
    "select id, name, description, view::text, dimensions, metrics, filters, "
    "chart_type::text, chart_config, min_version "
    f"from dashboard_widgets where {_OURS} order by id"
)
_READ_DASHBOARDS = (
    f"select id, name, description, definition, filters from dashboards where {_OURS} order by id"
)
_UPSERT_WIDGET = (
    "insert into dashboard_widgets (id, project_id, name, description, view, dimensions, "
    "metrics, filters, chart_type, chart_config, min_version) "
    'values (%s, %s, %s, %s, %s::"DashboardWidgetViews", %s, %s, %s, '
    '%s::"DashboardWidgetChartType", %s, %s) '
    "on conflict (id) do update set name = excluded.name, description = excluded.description, "
    "view = excluded.view, dimensions = excluded.dimensions, metrics = excluded.metrics, "
    "filters = excluded.filters, chart_type = excluded.chart_type, "
    "chart_config = excluded.chart_config, min_version = excluded.min_version, "
    "updated_at = current_timestamp "
    "where dashboard_widgets.project_id = excluded.project_id"
)
_UPSERT_DASHBOARD = (
    "insert into dashboards (id, project_id, name, description, definition, filters) "
    "values (%s, %s, %s, %s, %s, %s) "
    "on conflict (id) do update set name = excluded.name, description = excluded.description, "
    "definition = excluded.definition, filters = excluded.filters, "
    "updated_at = current_timestamp "
    "where dashboards.project_id = excluded.project_id"
)
_DELETE_WIDGET = f"delete from dashboard_widgets where id = %s and {_OURS}"
_DELETE_DASHBOARD = f"delete from dashboards where id = %s and {_OURS}"


def _json(value: Any) -> Jsonb:
    return Jsonb(value)


class PostgresDashboards:
    """`DashboardStore` over a Postgres DSN and the public key of the project to write in."""

    def __init__(self, dsn: str, public_key: str) -> None:
        self._dsn = dsn
        self._public_key = public_key

    def project_id(self) -> str:
        with psycopg.connect(self._dsn) as conn:
            return self._project(conn)

    def _project(self, conn: psycopg.Connection[Any]) -> str:
        found = conn.execute(_PROJECT, (self._public_key,)).fetchone()
        if found is None or found[0] is None:
            raise UnknownProject("AGENTIC_LANGFUSE_PUBLIC_KEY belongs to no Langfuse project")
        return str(found[0])

    def columns(self) -> frozenset[tuple[str, str]]:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(_COLUMNS, (list(TABLES),)).fetchall()
        return frozenset((str(table), str(column)) for table, column in rows)

    def read(self, product: str) -> Sequence[domain.Row]:
        with psycopg.connect(self._dsn) as conn:
            project = self._project(conn)
            ours = (project, domain.marker(product), domain.marker(product))
            widgets = conn.execute(_READ_WIDGETS, ours).fetchall()
            dashboards = conn.execute(_READ_DASHBOARDS, ours).fetchall()
        found: list[domain.Row] = [domain.WidgetRow(*row) for row in widgets]
        found += [domain.DashboardRow(*row) for row in dashboards]
        return found

    def apply(self, changes: domain.Changes) -> None:
        with psycopg.connect(self._dsn) as conn:
            project = self._project(conn)
            for row in changes.deletes:
                product = domain.product_of(row)
                if product is None:
                    continue
                marker = domain.marker(product)
                sql = _DELETE_DASHBOARD if isinstance(row, domain.DashboardRow) else _DELETE_WIDGET
                conn.execute(sql, (row.id, project, marker, marker))
            for row in changes.upserts:
                if isinstance(row, domain.WidgetRow):
                    written = conn.execute(
                        _UPSERT_WIDGET,
                        (
                            row.id,
                            project,
                            row.name,
                            row.description,
                            row.view,
                            _json(row.dimensions),
                            _json(row.metrics),
                            _json(row.filters),
                            row.chart_type,
                            _json(row.chart_config),
                            row.min_version,
                        ),
                    )
                else:
                    written = conn.execute(
                        _UPSERT_DASHBOARD,
                        (
                            row.id,
                            project,
                            row.name,
                            row.description,
                            _json(row.definition),
                            _json(row.filters),
                        ),
                    )
                if written.rowcount != 1:
                    raise RuntimeError(f"{row.id} belongs to another project: not overwritten")
