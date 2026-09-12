from agentic.state import add, merge


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
