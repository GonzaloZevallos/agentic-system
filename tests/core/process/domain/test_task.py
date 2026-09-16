import pytest

from agentic.core.process.domain.task import REDUCERS, add, apply, delta, merge


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
    assert set(REDUCERS) == {
        "sections",
        "returns",
        "return_history",
        "failures",
        "runs",
        "prompts",
        "cost_usd",
        "subtasks",
        "findings",
        "status",
        "current_role",
        "escalated",
    }
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


def test_delta_is_what_a_branch_added_and_apply_puts_it_back():
    """A pipeline of nodes ends holding the whole state. Handing that to the parent
    would fold it in a second time, so it hands back the difference."""
    before = {
        "id": "T-1",
        "status": "planned",
        "cost_usd": 1.5,
        "sections": {"Tech Lead": "plan"},
        "runs": [{"role": "Tech Lead"}],
        "subtasks": {"api": {"status": "pending"}, "domain": {"status": "pending"}},
    }
    after = {
        **before,
        "status": "developing",
        "cost_usd": 2.2,
        "sections": {"Tech Lead": "plan", "Developer: api": "x"},
        "runs": [{"role": "Tech Lead"}, {"role": "Developer"}],
        "subtasks": {"api": {"status": "approved"}, "domain": {"status": "pending"}},
        "subtask_id": "api",
    }
    patch = delta(before, after)
    assert patch == {
        "status": "developing",
        "cost_usd": pytest.approx(0.7),
        "sections": {"Developer: api": "x"},
        "runs": [{"role": "Developer"}],
        "subtasks": {"api": {"status": "approved"}},
    }
    # Which subtask a branch ran never travels back: the parent has one per branch.
    assert "subtask_id" not in patch
    assert "id" not in patch  # unchanged, so nothing to say
    assert apply(before, patch) == {k: v for k, v in after.items() if k != "subtask_id"}


def test_delta_takes_a_field_the_branch_started_as_it_is():
    before = {"id": "T-1"}
    after = {"id": "T-1", "cost_usd": 0.4, "failures": [{"role": "PjM"}], "returns": {"p": 1}}
    assert delta(before, after) == {
        "cost_usd": 0.4,
        "failures": [{"role": "PjM"}],
        "returns": {"p": 1},
    }
