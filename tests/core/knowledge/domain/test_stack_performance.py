"""The optional `performance` key of the product's stack (GH-318): what it reads, what it
refuses at load, and that its absence changes nothing for any stack in this repo."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain.performance import Limit, Scenario

REPO = Path(__file__).resolve().parents[4]

SCENARIO: dict[str, Any] = {
    "seed": "python scripts/seed.py",
    "measure": "python scripts/measure.py",
    "volume": 10000,
    "volume_unit": "rows",
    "limits": {
        "list_ms": {"limit": 200, "unit": "ms"},
        "list_queries": {"limit": 5, "unit": "queries"},
    },
}


def declared(**changes: Any) -> dict[str, Any]:
    scenario = {**copy.deepcopy(SCENARIO), **changes}
    return {"checks": {"tests": "pytest"}, "performance": {"list": scenario}}


def test_a_stack_without_the_key_has_no_scenario() -> None:
    assert stack.performance({"checks": {"tests": "pytest"}}, "x") == {}


def test_a_declared_scenario_is_read_in_file_order_with_typed_limits() -> None:
    data = declared()
    data["performance"]["detail"] = {
        **copy.deepcopy(SCENARIO),
        "volume": 2.5,
        "volume_unit": "years",
    }
    found = stack.performance(data, "x")
    assert list(found) == ["list", "detail"]
    assert found["list"] == Scenario(
        name="list",
        seed="python scripts/seed.py",
        measure="python scripts/measure.py",
        volume=10000,
        volume_unit="rows",
        limits={"list_ms": Limit(200, "ms"), "list_queries": Limit(5, "queries")},
    )
    assert found["detail"].volume == 2.5
    assert found["detail"].volume_unit == "years"


def test_the_commands_are_stripped() -> None:
    found = stack.performance(declared(seed="  ./seed.sh  ", measure="\t./m.sh "), "x")
    assert (found["list"].seed, found["list"].measure) == ("./seed.sh", "./m.sh")


def test_the_key_never_joins_checks_or_the_other_optional_keys() -> None:
    data = declared()
    assert stack.checks(data, "x") == {"tests": "pytest"}
    assert stack.setup(data, "x") == {}
    assert stack.affected_checks(data, "x") == {}
    assert stack.ci(data, "x") == ()


def _without(key: str) -> dict[str, Any]:
    data = declared()
    del data["performance"]["list"][key]
    return data


def _limits(limits: Any) -> dict[str, Any]:
    return declared(limits=limits)


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ({"checks": {"t": "p"}, "performance": None}, "declared empty"),
        ({"checks": {"t": "p"}, "performance": {}}, "declared empty"),
        ({"checks": {"t": "p"}, "performance": ["list"]}, "`performance`"),
        ({"checks": {"t": "p"}, "performance": {"list": None}}, "'list'"),
        ({"checks": {"t": "p"}, "performance": {"list": "seed"}}, "'list'"),
        ({"checks": {"t": "p"}, "performance": {1: SCENARIO}}, "scenario name"),
        (declared(extra="x"), "unknown key(s) extra"),
        (_without("seed"), "`seed`"),
        (_without("measure"), "`measure`"),
        (_without("volume"), "`volume`"),
        (_without("volume_unit"), "`volume_unit`"),
        (_without("limits"), "`limits`"),
        (declared(seed=""), "`seed`"),
        (declared(seed=3), "`seed`"),
        (declared(measure="a\nb"), "line"),
        (declared(measure="curl http://x"), "curl"),
        (declared(seed="git push"), "git push"),
        (declared(measure="gh pr list"), "gh"),
        (declared(volume=0), "`volume`"),
        (declared(volume=-3), "`volume`"),
        (declared(volume=True), "`volume`"),
        (declared(volume="many"), "`volume`"),
        (declared(volume=float("inf")), "`volume`"),
        (declared(volume=10**400), "`volume`"),
        (declared(volume_unit="weeks"), "`volume_unit`"),
        (declared(volume_unit=None), "`volume_unit`"),
        (_limits({}), "`limits`"),
        (_limits(None), "`limits`"),
        (_limits([1]), "`limits`"),
        (_limits({"list_ms": 200}), "'list_ms'"),
        (_limits({"list_ms": {"limit": 200}}), "'list_ms'"),
        (_limits({"list_ms": {"unit": "ms"}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": 200, "unit": "ms", "x": 1}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": 0, "unit": "ms"}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": -1, "unit": "ms"}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": True, "unit": "ms"}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": "200", "unit": "ms"}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": 200, "unit": "s"}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": 200, "unit": ["ms"]}}), "'list_ms'"),
        (_limits({"list_ms": {"limit": 10**400, "unit": "ms"}}), "'list_ms'"),
        # the metric's name must end with its unit's suffix
        (_limits({"list_ms": {"limit": 5, "unit": "queries"}}), "_queries"),
        (_limits({"list": {"limit": 5, "unit": "ms"}}), "_ms"),
        (_limits({"list_kb": {"limit": 5, "unit": "ms"}}), "_ms"),
    ],
)
def test_a_malformed_key_raises_at_load_naming_source_and_key(data: Any, needle: str) -> None:
    with pytest.raises(ValueError, match="somewhere") as caught:
        stack.performance(data, "somewhere")
    assert needle in str(caught.value)


def test_a_refused_command_quotes_the_reason_and_the_scenario() -> None:
    with pytest.raises(ValueError) as caught:
        stack.performance(declared(measure="wget x"), "src")
    message = str(caught.value)
    assert "src" in message
    assert "'list'" in message
    assert "wget" in message


def test_the_key_is_a_known_key_of_the_file() -> None:
    assert "performance" in stack.KEYS
    assert stack.checks(declared(), "x") == {"tests": "pytest"}


def _every_stack() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*stack.yaml"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    return sorted(REPO / name for name in tracked)


def test_every_stack_in_the_repo_declares_no_performance_and_loads_as_before() -> None:
    paths = _every_stack()
    assert paths  # this repo's own file and the template, at least
    for path in paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert stack.performance(data, path) == {}
        before = stack.checks(data, path)
        assert stack.checks({**data, "performance": SCENARIO_ONLY}, path) == before


SCENARIO_ONLY = {"list": copy.deepcopy(SCENARIO)}
