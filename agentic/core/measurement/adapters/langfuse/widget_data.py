"""How many rows a dashboard widget reads, from Langfuse's ClickHouse (GH-274, ADR-0051).

Read-only, over the HTTP interface with `urllib`. `FINAL` collapses the replacing-merge
duplicates the way Langfuse's own views do, and every count is filtered to the project and
to the environment of the widget's own `environment` filter: a product's numbers are never
summed with another's. Query values travel as ClickHouse query parameters, never spliced
into the SQL."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ...domain import dashboards as domain

TIMEOUT_S = 30.0

_SCORES = (
    "select count() from scores final where project_id = {project:String} "
    "and is_deleted = 0 and environment in {environments:Array(String)}"
)
_OBSERVATIONS = (
    "select count() from events_core final where project_id = {project:String} "
    "and is_deleted = 0 and environment in {environments:Array(String)} and type = 'GENERATION'"
)


def _array(values: list[str]) -> str:
    quoted = (v.replace("\\", "\\\\").replace("'", "\\'") for v in values)
    return "[" + ",".join(f"'{q}'" for q in quoted) + "]"


def _values(widget: domain.WidgetSpec, column: str) -> list[str]:
    return [str(v) for f in widget.filters if f["column"] == column for v in f["value"]]


class ClickHouseWidgetData:
    """`WidgetData` over ClickHouse HTTP, for one project."""

    def __init__(self, url: str, user: str, password: str, project_id: str) -> None:
        self._url = url.rstrip("/") + "/"
        self._user = user
        self._password = password
        self._project = project_id

    def count(self, widget: domain.WidgetSpec) -> int:
        params = {
            "param_project": self._project,
            "param_environments": _array(_values(widget, "environment")),
        }
        if widget.view == "OBSERVATIONS":
            sql = _OBSERVATIONS
        else:
            sql = _SCORES + " and name in {names:Array(String)}"
            params["param_names"] = _array(_values(widget, "name"))
            if any(f["column"] == "booleanValue" and f["value"] is True for f in widget.filters):
                sql += " and value = 1"
        return self._query(sql, params)

    def _query(self, sql: str, params: dict[str, str]) -> int:
        request = urllib.request.Request(
            f"{self._url}?{urllib.parse.urlencode(params)}",
            data=f"{sql} format JSON".encode(),
            headers={"X-ClickHouse-User": self._user, "X-ClickHouse-Key": self._password},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            body: dict[str, Any] = json.load(response)
        return int(body["data"][0]["count()"])
