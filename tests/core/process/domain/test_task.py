from agentic.core.process.domain.task import REDUCERS, add, apply, merge


def test_merge_overrides_per_key_and_keeps_the_rest():
    current = {"PM→Product Analyst": 2, "Tech Lead→Developer:s1": 1}
    # Resetting a pair when the human answers is an explicit update.
    assert merge(current, {"PM→Product Analyst": 0}) == {
        "PM→Product Analyst": 0,
        "Tech Lead→Developer:s1": 1,
    }


def test_merge_tolerates_missing_sides():
    assert merge(None, {"a": 1}) == {"a": 1}
    assert merge({"a": 1}, None) == {"a": 1}


def test_add_accumulates_cost():
    assert add(0.1, 0.2) == 0.1 + 0.2


def test_reducers_are_read_from_the_annotations():
    assert set(REDUCERS) == {"sections", "returns", "failures", "runs", "prompts", "cost_usd"}
    assert REDUCERS["cost_usd"] is add and REDUCERS["sections"] is merge


def test_apply_uses_the_reducers_and_overrides_the_rest():
    state = {
        "id": "T-1",
        "status": "open",
        "cost_usd": 0.1,
        "sections": {"PjM": "a"},
        "runs": [{"role": "PjM"}],
        "flags": {"touches_ui": False},
    }
    patch = {
        "status": "defined",
        "cost_usd": 0.2,
        "sections": {"Product Analyst": "b"},
        "runs": [{"role": "Product Analyst"}],
        "flags": {"touches_ui": True},
        "returns": {"PM→Product Analyst": 1},  # absent in the state: taken as is
    }
    merged = apply(state, patch)
    assert merged["status"] == "defined"
    assert merged["cost_usd"] == 0.1 + 0.2
    assert merged["sections"] == {"PjM": "a", "Product Analyst": "b"}
    assert merged["runs"] == [{"role": "PjM"}, {"role": "Product Analyst"}]
    assert merged["flags"] == {"touches_ui": True}  # no reducer: replaced
    assert merged["returns"] == {"PM→Product Analyst": 1}
    assert state["sections"] == {"PjM": "a"}  # the input is not mutated
