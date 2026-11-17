"""GH-897: the stacked task's records (`StackRestack`, `StackPause`), their reducers, the
restack row a closed `sync_master` writes and the pure views of `stack_parent`."""

from __future__ import annotations

from agentic.core.process.domain.task import (
    REDUCERS,
    StackPause,
    StackRestack,
    add_stack_pauses,
    add_stack_restacks,
    apply,
    delta,
)

UP = "b" * 40
HEAD = "a" * 40


def _restack(at: str = "t1", upstream: str = UP, **more) -> StackRestack:
    return {"at": at, "upstream": upstream, "conflict": False, "cost_usd": 1.0, **more}


def _pause(cause: str = "parent_unmerged", at: str = "t1", resumed_at=None) -> StackPause:
    return {"parent": 7, "cause": cause, "at": at, "resumed_at": resumed_at}


# ---- reducers ---------------------------------------------------------------------------


def test_the_keys_are_registered_reducers():
    assert REDUCERS["stack_restacks"] is add_stack_restacks
    assert REDUCERS["stack_pauses"] is add_stack_pauses


def test_restacks_keep_one_row_per_at_and_upstream_the_first_whole():
    first = _restack(cost_usd=1.0, tokens={"input": 5})
    again = _restack(cost_usd=9.0, conflict=True)
    other = _restack(at="t2")

    joined = add_stack_restacks([first], [again, other])

    assert joined == [first, other]
    assert add_stack_restacks(joined, joined) == joined  # a retried write adds nothing


def test_restacks_of_the_same_second_for_another_upstream_both_stay():
    assert len(add_stack_restacks([_restack()], [_restack(upstream="c" * 40)])) == 2


def test_a_pause_row_replaces_the_one_with_the_same_parent_cause_and_at_in_place():
    first, other = _pause(), _pause(cause="orphaned", at="t0")
    stamped = _pause(resumed_at="t9")

    joined = add_stack_pauses([first, other], [stamped, _pause(at="t2")])

    assert joined == [stamped, other, _pause(at="t2")]


def test_pauses_append_when_the_key_differs():
    assert add_stack_pauses([_pause()], [{**_pause(), "parent": 8}]) != [_pause()]
    assert len(add_stack_pauses([_pause()], [{**_pause(), "parent": 8}])) == 2


def test_apply_and_delta_fold_the_new_keys_through_their_reducers():
    before = {"id": "GH-1", "stack_restacks": [_restack()], "stack_pauses": [_pause()]}
    after = {
        "id": "GH-1",
        "stack_restacks": [_restack(), _restack(at="t2")],
        "stack_pauses": [_pause(resumed_at="t9")],
    }

    patch = delta(before, after)

    assert patch["stack_restacks"] == [_restack(at="t2")]
    assert patch["stack_pauses"] == [_pause(resumed_at="t9")]
    assert apply(before, patch) == after
    assert delta(after, after) == {}
