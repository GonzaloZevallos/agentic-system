"""The class pages of every layer (`scripts/docs/classes.py`, GH-430 part 4): grouped by cluster,
adapter kind or layer, from the dot text of a layer, plus `packages.subpackage`.

The node lines are the attributes pylint really writes (`-o dot -k --filter-mode PUB_ONLY`,
pyreverse 4.1.3); pyreverse draws a `typing.Protocol`, a `Generic` subclass and an enum as any
other class, so those inputs differ from a plain class only in their name.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from agentic.core.process.domain.docs import DIAGRAM_BUDGET
from tests.docs_scripts import classes, groups, packages

BUDGET = DIAGRAM_BUDGET.classes
GROUPING = {
    "knowledge/domain": "layer",
    "process/adapters": "kind",
    "process/application": "clusters",
    "process/domain": "clusters",
}


def _dot(context: str, layer: str, names: Sequence[str]) -> str:
    package = classes.package_of(context, layer)
    lines = ['digraph "classes_x" {', "rankdir=BT", 'charset="utf-8"']
    lines += [
        f'"{package}.{n}" [color="black", fontcolor="black", label=<x>, shape="record", '
        'style="solid"];'
        for n in names
    ]
    return "\n".join([*lines, "}"])


def _parsed(context: str, layer: str, names: Sequence[str]) -> Any:
    return classes.parse(_dot(context, layer, names), context, layer)


def _pages(
    parsed: Any,
    context: str,
    layer: str,
    clusters: Any = None,
    grouping: Any = GROUPING,
    separate: Any = (),
) -> dict[str, Any]:
    return classes.pages(*parsed, context, layer, grouping, clusters or {}, separate)


@pytest.mark.parametrize("name", ["ports.Presubmit", "ports.Box", "ports.CheckPriority"])
def test_protocol_generic_and_enum_classes_are_plain_public_nodes(name: str) -> None:
    parsed = _parsed("process", "application", [name])
    assert [n.qname for n in parsed[0]] == [name] and parsed[1] == ()
    result = _pages(parsed, "process", "application", {"application.ports": "support"})
    assert list(result) == ["process-application", "process-application-support"]
    short = name.rpartition(".")[2]
    assert result["process-application-support"].nodes == ((f"ports__{short}", short),)


def test_homonyms_of_two_layers_are_two_nodes_on_two_pages() -> None:
    first = _pages(
        _parsed("process", "domain", ["a.Entry"]), "process", "domain", {"domain.a": "x"}
    )
    second = _pages(_parsed("knowledge", "domain", ["a.Entry"]), "knowledge", "domain")
    assert first["process-domain-x"].nodes == second["knowledge-domain-all"].nodes
    assert first.keys().isdisjoint(second)
    with pytest.raises(ValueError, match="is not a class of agentic.core.knowledge.domain"):
        classes.parse(_dot("process", "domain", ["a.Entry"]), "knowledge", "domain")


def test_the_run_of_a_file_alone_is_merged_by_qualified_name_and_the_page_says_so() -> None:
    whole = _parsed("process", "adapters", ["github.tracker.T"])
    alone = _parsed("process", "adapters", ["github.board.B"])
    merged = classes.merge(whole, alone, alone)
    assert [n.qname for n in merged[0]] == ["github.board.B", "github.tracker.T"]
    page = _pages(merged, "process", "adapters", None, GROUPING, {"github.board"})[
        "process-adapters-github"
    ]
    assert {i for i, _ in page.nodes} == {"github__board__B", "github__tracker__T"}
    assert "Las relaciones entre las clases de `github.board` y las demás clases" in page.sentence
    quiet = _pages(merged, "process", "adapters")["process-adapters-github"]
    assert "corrida aparte" not in quiet.sentence


def test_an_adapters_layer_has_one_page_per_kind_and_the_loose_modules_in_toplevel() -> None:
    names = ["claude_sdk.hooks.H", "git.workspace.W", "telemetry.T", "filesystem.lock.L"]
    result = _pages(_parsed("process", "adapters", names), "process", "adapters")
    assert list(result) == [
        "process-adapters",
        "process-adapters-claude_sdk",
        "process-adapters-filesystem",
        "process-adapters-git",
        "process-adapters-toplevel",
    ]
    assert result["process-adapters-toplevel"].nodes == (("telemetry__T", "T"),)
    assert result["process-adapters-git"].parent == "process-adapters"


def test_a_layer_with_no_grouping_line_raises_naming_it_and_the_line_to_add() -> None:
    parsed = _parsed("knowledge", "adapters", ["a.A"])
    with pytest.raises(ValueError) as raised:
        _pages(parsed, "knowledge", "adapters")
    assert "'knowledge/adapters'" in str(raised.value)
    assert '"knowledge/adapters": "<clusters|kind|layer>",' in str(raised.value)
    with pytest.raises(ValueError, match="grouped by 'weird'"):
        _pages(parsed, "knowledge", "adapters", None, {"knowledge/adapters": "weird"})


def test_a_layer_grouped_page_over_the_budget_fails_naming_the_layer() -> None:
    names = [f"a.C{n:02d}" for n in range(BUDGET + 1)]
    with pytest.raises(ValueError, match=rf"knowledge/domain has {BUDGET + 1} public classes"):
        _pages(_parsed("knowledge", "domain", names), "knowledge", "domain")


def test_a_module_with_no_cluster_in_another_context_names_the_line_to_add() -> None:
    parsed = _parsed("measurement", "domain", ["mystery.M"])
    with pytest.raises(ValueError) as raised:
        _pages(parsed, "measurement", "domain", {}, {"measurement/domain": "clusters"})
    assert '"domain.mystery": "<cluster>",' in str(raised.value)
    assert "measurement" in str(raised.value)


def test_the_grouping_tables_are_sorted_one_per_line_and_name_real_groupings() -> None:
    for table in (groups.CLASS_GROUPING, groups.MEASUREMENT_CLUSTERS):
        assert list(table) == sorted(table)
    assert set(groups.CLASS_GROUPING.values()) <= set(classes.GROUPINGS)


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("domain.task", "domain"),
        ("application.ports", "application"),
        ("adapters.telemetry", "adapters"),
        ("adapters.git.workspace", "adapters.git"),
        ("adapters.claude_sdk.hooks", "adapters.claude_sdk"),
    ],
)
def test_subpackage_is_the_layer_or_the_adapter_kind(relative: str, expected: str) -> None:
    assert packages.subpackage(relative) == expected
