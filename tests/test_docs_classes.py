"""The class pages of `agentic/core/process/domain` (`scripts/docs/classes.py`, GH-430 part 3):
dot text from a real pyreverse run in, plain pages out.

The fixtures under `tests/fixtures/pyreverse/` are lines of a real run (`-o dot -k --filter-mode
PUB_ONLY`, pyreverse 4.1.3) with lines deleted, none edited. The real domain run has no edge
between two clusters and none of the kind `composes`; those inputs are built here, from the
attributes pylint itself writes (`dot_printer.ARROWS`).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pylint.pyreverse.dot_printer import ARROWS
from pylint.pyreverse.printer import EdgeType

from agentic.core.process.domain.docs import DIAGRAM_BUDGET
from tests.docs_scripts import classes, groups, packages

FIXTURES = Path(__file__).parent / "fixtures" / "pyreverse"
DOMAIN = "agentic.core.process.domain"
BUDGET = DIAGRAM_BUDGET.classes


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _dot(nodes: Sequence[str], edges: Sequence[tuple[str, str, str]] = ()) -> str:
    """A dot text over `module.Class` names; an edge is `(source, target, arrowhead)`."""
    lines = ['digraph "classes_x" {', "rankdir=BT", 'charset="utf-8"']
    lines += [f'"{DOMAIN}.{n}" [color="black", label=<x>, shape="record"];' for n in nodes]
    lines += [
        f'"{DOMAIN}.{s}" -> "{DOMAIN}.{t}" [arrowhead="{head}", arrowtail="none"];'
        for s, t, head in edges
    ]
    return "\n".join([*lines, "}"])


def _pages(parsed: Any, clusters: Any = groups.CLUSTERS) -> dict[str, Any]:
    return classes.pages(*parsed, clusters)


def test_the_domain_fixture_parses_to_public_qualified_classes_and_two_kinds() -> None:
    nodes, relations = classes.parse(_fixture("process-domain.dot"))
    assert [n.qname for n in nodes] == [
        "check_schedule.CheckPriority",
        "check_schedule.Entry",
        "check_schedule.Request",
        "cost_history.Pair",
        "coverage.Entry",
        "coverage.Refusal",
        "coverage.Rejected",
        "playbook.Playbook",
        "roles.Interview",
        "roles.Pair",
        "roles.Role",
        "schemas.Answers",
        "schemas.Classification",
        "schemas.RoleOutput",
        "schemas.Verdict",
        "stall.Verdict",
    ]
    assert [(n.module, n.name) for n in nodes] == sorted((n.module, n.name) for n in nodes)
    assert {(r.source, r.target, r.kind) for r in relations} == {
        ("schemas.Answers", "schemas.RoleOutput", "inherits"),
        ("schemas.Classification", "schemas.RoleOutput", "inherits"),
        ("schemas.Verdict", "schemas.RoleOutput", "inherits"),
        ("check_schedule.Entry", "check_schedule.CheckPriority", "associates"),
        ("check_schedule.Request", "check_schedule.CheckPriority", "associates"),
        ("coverage.Rejected", "coverage.Refusal", "associates"),
        ("roles.Interview", "roles.Role", "associates"),
        ("roles.Pair", "roles.Role", "associates"),
    }


def test_a_private_class_and_its_edges_are_not_on_any_page() -> None:
    """`PUB_ONLY` filters members, not classes: the real run emits `playbook._Shelves`."""
    text = _fixture("process-domain.dot")
    assert "playbook._Shelves" in text
    nodes, relations = classes.parse(text)
    assert all(not n.name.startswith("_") for n in nodes)
    assert all("_Shelves" not in r.source + r.target for r in relations)


def test_every_pyreverse_edge_kind_is_told_apart_by_its_arrowhead() -> None:
    """The kinds are read from the attributes pylint writes, so a pylint that changes them
    fails here and not by drawing the wrong arrow."""
    expected = {
        EdgeType.INHERITS: "inherits",
        EdgeType.COMPOSITION: "composes",
        EdgeType.AGGREGATION: "aggregates",
        EdgeType.ASSOCIATION: "associates",
    }
    for edge_type, kind in expected.items():
        head = ARROWS[edge_type]["arrowhead"]
        text = _dot(["a.A", "a.B"], [("a.A", "a.B", head)])
        assert {r.kind for r in classes.parse(text)[1]} == {kind}
    assert len({ARROWS[t]["arrowhead"] for t in expected}) == 4


def test_an_edge_of_a_kind_it_cannot_tell_raises_naming_the_edge() -> None:
    text = _dot(["a.A", "a.B"], [("a.A", "a.B", ARROWS[EdgeType.USES]["arrowhead"])])
    with pytest.raises(ValueError, match=r"cannot tell the kind.*a\.A.*a\.B"):
        classes.parse(text)


def test_an_edge_to_a_class_that_is_not_in_the_output_raises_naming_it() -> None:
    text = _dot(["a.A"], [("a.A", "a.Ghost", "vee")])
    with pytest.raises(ValueError, match=r"not a class of the output.*a\.Ghost"):
        classes.parse(text)


@pytest.mark.parametrize(
    "qname",
    [
        "agentic.core.process.application.x.Y",
        f"{DOMAIN}.Entry",
        f"{DOMAIN}..Entry",
        "Entry",
    ],
)
def test_a_name_it_cannot_place_raises_naming_it(qname: str) -> None:
    with pytest.raises(ValueError, match="is not a class of") as raised:
        classes.canonical(qname)
    assert repr(qname) in str(raised.value)
    with pytest.raises(ValueError, match="is not a class of"):
        classes.parse(f'"{qname}" [label=<x>];')


def test_a_line_that_is_not_dot_raises() -> None:
    with pytest.raises(ValueError, match="cannot read this line.*surprise"):
        classes.parse('digraph "x" {\nsurprise\n}')


@pytest.mark.parametrize(
    ("module", "name"), [("a__b", "C"), ("_a", "C"), ("a_", "C"), ("a", "C_"), ("", "C")]
)
def test_an_id_that_two_names_could_share_raises(module: str, name: str) -> None:
    with pytest.raises(ValueError, match="cannot be an id"):
        classes.node_id(module, name)


def test_node_id_is_module_double_underscore_class() -> None:
    assert classes.node_id("cost_history", "Pair") == "cost_history__Pair"


def test_homonyms_on_one_page_stay_two_nodes_with_qualified_labels() -> None:
    result = _pages(classes.parse(_fixture("process-domain.dot")))
    page = result["process-domain-checks"]
    entries = [(i, label) for i, label in page.nodes if label.endswith("Entry")]
    assert entries == [
        ("check_schedule__Entry", "check_schedule.Entry"),
        ("coverage__Entry", "coverage.Entry"),
    ]
    # a name that is alone on its page keeps its short label, even when it has homonyms elsewhere
    assert ("coverage__Refusal", "Refusal") in page.nodes
    assert ("roles__Pair", "Pair") in result["process-domain-roles"].nodes
    assert ("cost_history__Pair", "Pair") in result["process-domain-landing"].nodes


def test_the_two_fixtures_merge_with_each_class_once() -> None:
    domain = classes.parse(_fixture("process-domain.dot"))
    board = classes.parse(_fixture("process-domain-board.dot"))
    assert [n.qname for n in board[0]] == ["board.Card", "board.FieldWrite", "board.IssueFacts"]
    merged = classes.merge(domain, board)
    assert len(merged[0]) == len(domain[0]) + 3
    assert classes.merge(merged, board, domain) == merged
    assert classes.merge(board, board) == board


def test_shuffled_input_lines_give_identical_pages() -> None:
    lines = _fixture("process-domain.dot").splitlines()
    body = lines[3:-1]
    random.Random(7).shuffle(body)
    shuffled = "\n".join([*lines[:3], *body, lines[-1]])
    assert shuffled != "\n".join(lines)
    assert _pages(classes.parse(shuffled)) == _pages(classes.parse("\n".join(lines)))


def test_parse_dedupes_relations_by_source_target_and_kind() -> None:
    """`Interview` has three attributes of type `Role` in the real run: one relation."""
    text = _fixture("process-domain.dot")
    assert text.count('"agentic.core.process.domain.roles.Interview" ->') == 3
    relations = classes.parse(text)[1]
    assert sum(1 for r in relations if r.source == "roles.Interview") == 1
    lines = text.splitlines()
    doubled = "\n".join([*lines[:-1], *lines[3:-1], lines[-1]])
    assert classes.parse(doubled)[1] == relations


def _crowd(count: int) -> tuple[list[str], str]:
    names = [f"a.C{number:02d}" for number in range(count)]
    return names, _dot(names)


def test_a_cluster_over_the_budget_splits_into_pages_made_by_chunks() -> None:
    names, text = _crowd(2 * BUDGET + 1)
    result = _pages(classes.parse(text), {"domain.a": "big"})
    expected = packages.chunks(names)
    assert len(expected) >= 3
    keys = [key for key in result if key != "index"]
    assert keys == [f"process-domain-big-{n}" for n in range(1, len(expected) + 1)]
    assert [[i for i, _ in result[k].nodes] for k in keys] == [
        [n.replace(".", "__") for n in chunk] for chunk in expected
    ]
    assert sum(len(chunk) for chunk in expected) == len(names)
    assert all(0 < len(chunk) <= BUDGET for chunk in expected)
    for number, key in enumerate(keys, start=1):
        page = result[key]
        assert page.sentence.startswith("Esta página muestra")
        parte = f"dividió por cantidad: esta es la parte {number} de {len(keys)}"
        assert parte in page.sentence
        assert page.parent == "index"
        assert page.siblings == tuple(k for k in keys if k != key)


def test_a_cluster_at_the_budget_is_one_page_and_is_not_said_to_be_split() -> None:
    _, text = _crowd(BUDGET)
    result = _pages(classes.parse(text), {"domain.a": "big"})
    assert list(result) == ["index", "process-domain-big"]
    assert "por cantidad" not in result["process-domain-big"].sentence
    _, over = _crowd(BUDGET + 1)
    assert len(_pages(classes.parse(over), {"domain.a": "big"})) == 3


def test_a_relation_to_a_class_on_another_page_is_listed_not_drawn() -> None:
    names, _ = _crowd(2 * BUDGET + 1)
    runs = packages.chunks(names)
    first, last = runs[0], runs[-1]
    edges = [
        (first[0], first[1], "vee"),
        (first[0], last[-1], "empty"),
        (last[-2], last[-1], "vee"),
    ]
    result = _pages(classes.parse(_dot(names, edges)), {"domain.a": "big"})
    one, end = result["process-domain-big-1"], result[f"process-domain-big-{len(runs)}"]
    ident = classes.node_id
    assert one.edges == ((ident("a", first[0][2:]), ident("a", first[1][2:]), "associates"),)
    assert one.elsewhere == ((first[0], last[-1], "inherits"),)
    assert end.edges == ((ident("a", last[-2][2:]), ident("a", last[-1][2:]), "associates"),)
    assert end.elsewhere == ((first[0], last[-1], "inherits"),)
    for number in range(2, len(runs)):
        middle = result[f"process-domain-big-{number}"]
        assert middle.edges == () and middle.elsewhere == ()


def test_a_relation_between_two_clusters_is_listed_on_both_pages() -> None:
    text = _dot(["a.A", "b.B"], [("a.A", "b.B", "odiamond")])
    result = _pages(classes.parse(text), {"domain.a": "one", "domain.b": "two"})
    wanted = (("a.A", "b.B", "aggregates"),)
    assert result["process-domain-one"].elsewhere == wanted
    assert result["process-domain-two"].elsewhere == wanted
    assert result["process-domain-one"].edges == result["process-domain-two"].edges == ()


def test_each_page_has_a_spanish_sentence_a_parent_and_its_siblings() -> None:
    result = _pages(classes.parse(_fixture("process-domain.dot")))
    assert result["index"].parent is None and result["index"].siblings == ()
    assert result["index"].sentence.startswith("Esta página es el índice")
    keys = [k for k in result if k != "index"]
    assert keys == sorted(keys)
    for key in keys:
        page = result[key]
        assert page.sentence.startswith("Esta página muestra")
        assert page.parent == "index"
        assert page.siblings == tuple(k for k in keys if k != key)


def test_a_module_with_no_cluster_raises_naming_it_and_the_line_to_add() -> None:
    parsed = classes.parse(_dot(["mystery.M"]))
    with pytest.raises(ValueError) as raised:
        _pages(parsed, {"domain.other": "x"})
    message = str(raised.value)
    assert "'domain.mystery'" in message
    assert '"domain.mystery": "<cluster>",' in message
    assert "groups.py" in message


def test_a_relation_to_a_class_that_was_not_given_raises_naming_it() -> None:
    nodes, _ = classes.parse(_dot(["a.A"]))
    relation = classes.Relation("a.A", "a.Ghost", "associates")
    with pytest.raises(ValueError, match=r"a\.Ghost"):
        classes.pages(nodes, [relation], {"domain.a": "x"})


def test_a_relation_kind_outside_the_four_raises() -> None:
    with pytest.raises(ValueError, match="'uses' is not one of"):
        classes.Relation("a.A", "a.B", "uses")


def test_the_board_classes_of_the_second_run_land_on_a_page() -> None:
    merged = classes.merge(
        classes.parse(_fixture("process-domain.dot")),
        classes.parse(_fixture("process-domain-board.dot")),
    )
    result = _pages(merged)
    on_pages = {label for key, page in result.items() if key != "index" for _, label in page.nodes}
    assert {"Card", "FieldWrite", "IssueFacts"} <= on_pages
