"""QA Performance's node in the wired graph (GH-318, ADR-0060): it exists, the fan-in can
reach it, and it always goes back to the fan-in (or escalates)."""

from agentic import composition


def edges() -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in composition.graph.compile().get_graph().edges}


def test_the_node_is_in_the_composed_graph():
    assert "verify" in composition.graph.nodes


def test_the_fan_in_reaches_it_and_it_returns_to_the_fan_in_or_escalates():
    found = edges()
    assert ("integrate", "verify") in found
    assert ("verify", "integrate") in found
    assert ("verify", "escalate") in found
    assert {t for s, t in found if s == "verify"} == {"integrate", "escalate"}


def test_the_existing_ways_out_of_the_fan_in_are_still_there():
    found = edges()
    for target in ("pipeline", "verify", "deliver", "escalate"):
        assert ("integrate", target) in found
