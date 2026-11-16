"""How many rows a dashboard widget reads, from Langfuse's ClickHouse (GH-274, ADR-0051).

Read-only, over the HTTP interface with `urllib`. `FINAL` collapses only the replacing-merge
duplicates that share a sort key (Langfuse's own views also group by id, ADR-0037), and every
count is filtered to the project and to what the widget's own filters say: its names, its
`environment` when it has one and its `booleanValue`. Query values travel as
ClickHouse query parameters, never spliced into the SQL."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from ...domain import dashboards as domain

TIMEOUT_S = 30.0

_SCORES = (
    "select count() from scores final where project_id = {project:String} "
    "and is_deleted = 0"
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
        """The rows the widget reads: its project, its names, its `environment` filter when
        it declares one and its `booleanValue` filter, true or false, as `value = 1|0`."""
        params = {"param_project": self._project}
        sql = _SCORES
        environments = _values(widget, "environment")
        if any(f["column"] == "environment" for f in widget.filters):
            sql += " and environment in {environments:Array(String)}"
            params["param_environments"] = _array(environments)
        sql += " and name in {names:Array(String)}"
        params["param_names"] = _array(_values(widget, "name"))
        for f in widget.filters:
            if f["column"] == "booleanValue":
                sql += " and value = 1" if f["value"] is True else " and value = 0"
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
