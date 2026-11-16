"""`ClickHouseWidgetData.count`: the SQL and the query parameters, with the opener replaced."""

import io
import json
import urllib.parse
import urllib.request
from types import SimpleNamespace
from typing import Any

import pytest

from agentic.core.measurement.adapters.langfuse import widget_data
from agentic.core.measurement.adapters.langfuse.widget_data import ClickHouseWidgetData


def _filter(column: str, value: Any, kind: str = "stringOptions") -> dict[str, Any]:
    return {"column": column, "type": kind, "operator": "any of", "value": value}


def _widget(*filters: dict[str, Any]) -> Any:
    return SimpleNamespace(filters=filters)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, list[str]]]]:
    calls: list[tuple[str, dict[str, list[str]]]] = []

    def opener(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        assert request.data is not None
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        calls.append((request.data.decode(), query))
        return io.BytesIO(json.dumps({"data": [{"count()": 7}]}).encode())

    monkeypatch.setattr(widget_data.urllib.request, "urlopen", opener)
    return calls


def _count(widget: Any) -> int:
    return ClickHouseWidgetData("http://ch:8123", "u", "p", "proj").count(widget)


def test_the_environment_clause_is_applied_only_when_the_widget_filters_by_one(
    sent: list[tuple[str, dict[str, list[str]]]],
) -> None:
    assert _count(_widget(_filter("name", ["cycle_h"]))) == 7
    assert (
        _count(_widget(_filter("name", ["cycle_h"]), _filter("environment", ["apostrophe"]))) == 7
    )
    without, with_env = sent
    assert "environment" not in without[0] and "param_environments" not in without[1]
    assert "environment in {environments:Array(String)}" in with_env[0]
    assert with_env[1]["param_environments"] == ["['apostrophe']"]


def test_the_boolean_filter_maps_true_and_false_to_the_value_and_nothing_else_differs(
    sent: list[tuple[str, dict[str, list[str]]]],
) -> None:
    name = _filter("name", ["stage_split_known"])
    for flag in (True, False):
        _count(_widget(name, _filter("booleanValue", flag, "boolean")))
    _count(_widget(name))
    true_sql, false_sql, plain_sql = (call[0] for call in sent)
    assert true_sql.endswith(" and value = 1 format JSON")
    assert false_sql.endswith(" and value = 0 format JSON")
    assert true_sql.replace("value = 1", "value = 0") == false_sql
    assert "value =" not in plain_sql
    assert sent[0][1] == sent[1][1]


def test_values_travel_as_parameters_and_never_inside_the_sql(
    sent: list[tuple[str, dict[str, list[str]]]],
) -> None:
    _count(_widget(_filter("name", ["a'; drop table scores; --"])))
    sql, params = sent[0]
    assert "drop table" not in sql
    assert "drop table" in params["param_names"][0]
