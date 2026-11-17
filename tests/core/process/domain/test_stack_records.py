"""GH-897: the stacked task's records (`StackRestack`, `StackPause`), their reducers, the
restack row a closed `sync_master` writes and the pure views of `stack_parent`."""

from __future__ import annotations

from agentic.core.process.domain import landing
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


# ---- runs_base and the restack row --------------------------------------------------------


def _closed(**more) -> dict:
    return {
        "head": HEAD,
        "step": "sync_master",
        "refusal": "merge_conflict",
        "upstream": UP,
        "cost_usd": 2.5,
        "runs_base": 1,
        "resolved_at": "t5",
        **more,
    }


def _run(tokens: dict[str, int] | None = None, **more) -> dict:
    return {"role": "Developer", **({"tokens": tokens} if tokens is not None else {}), **more}


def test_with_cost_base_writes_both_bases_on_the_same_entry_in_one_call():
    entries = [{"head": HEAD, "step": "sync_master"}]

    (out,) = landing.with_cost_base(entries, HEAD, "sync_master", 4.0, 3)

    assert (out["cost_base"], out["runs_base"]) == (4.0, 3)
    assert landing.with_cost_base(entries, HEAD, "fix", 4.0, 3) == entries


def test_runs_base_survives_an_entry_of_round_trip():
    entry = {"head": HEAD, "step": "sync_master", "cost_base": 1.0, "runs_base": 4}

    assert landing.entry_of(landing.entry_of(entry))["runs_base"] == 4
    assert "runs_base" not in landing.entry_of({**entry, "runs_base": True})
    assert "runs_base" not in landing.entry_of({**entry, "runs_base": "4"})
    assert "runs_base" not in landing.entry_of({**entry, "runs_base": 4.0})


def test_a_conflicting_restack_records_conflict_with_its_cost():
    row = landing.restack_row(_closed(), [_run(), _run({"input": 3})], True)

    assert row == {
        "at": "t5",
        "upstream": UP,
        "conflict": True,
        "cost_usd": 2.5,
        "tokens": {"input": 3},
    }


def test_a_failed_checks_refusal_is_a_conflict_and_any_other_is_not():
    assert landing.restack_row(_closed(refusal="checks_failed"), [], True)["conflict"] is True
    assert landing.restack_row(_closed(refusal="attempts_exhausted"), [], True)["conflict"] is False
    assert landing.restack_row(_closed(refusal=None), [], True)["conflict"] is False


def test_an_unsynced_close_writes_a_row_stamped_closed_at():
    entry = {k: v for k, v in _closed().items() if k != "resolved_at"}

    row = landing.restack_row({**entry, "closed_at": "t6", "lacks": UP}, [], True)

    assert row is not None and row["at"] == "t6"


def test_no_row_unless_stacked_a_sync_master_and_closed():
    assert landing.restack_row(_closed(), [], False) is None
    assert landing.restack_row(_closed(step="fix"), [], True) is None
    assert (
        landing.restack_row({k: v for k, v in _closed().items() if k != "resolved_at"}, [], True)
        is None
    )
    assert landing.restack_row({"step": "sync_master", "resolved_at": "t"}, [], True) is not None


def test_a_run_inside_the_window_counts_in_tokens_and_a_failed_attempt_only_in_cost():
    runs = [_run({"input": 99}), _run(), _run({"input": 4, "output": 1})]

    row = landing.restack_row(_closed(cost_usd=7.0), runs, True)

    assert row["tokens"] == {"input": 4, "output": 1}  # the failure left no run: cost only
    assert row["cost_usd"] == 7.0


def test_tokens_window_edges():
    runs = [_run({"input": 1}), _run({"input": 2})]

    assert landing.restack_row(_closed(runs_base=2), runs, True)["tokens"] == {}
    assert "tokens" not in landing.restack_row(_closed(runs_base=3), runs, True)
    assert "tokens" not in landing.restack_row(_closed(runs_base=-1), runs, True)
    absent = {k: v for k, v in _closed().items() if k != "runs_base"}
    assert "tokens" not in landing.restack_row(absent, runs, True)


def test_the_runs_slice_is_stable_when_runs_only_grows():
    base = [_run({"input": 1}), _run({"input": 2})]
    later = [*base, _run({"input": 10})]

    first = landing.restack_row(_closed(runs_base=1), base, True)
    restarted = landing.restack_row(_closed(runs_base=1), base, True)

    assert first == restarted and first["tokens"] == {"input": 2}
    assert landing.restack_row(_closed(runs_base=1), later, True)["tokens"] == {"input": 12}


def test_restack_close_gives_a_row_only_for_an_entry_that_closed_in_this_step():
    opened = [{"head": HEAD, "step": "sync_master", "upstream": UP}]
    closed = [_closed()]
    state = {"runs": [_run(), _run({"input": 3})]}

    assert landing.restack_close(opened, closed, state, True)["tokens"] == {"input": 3}
    assert landing.restack_close(closed, closed, state, True) is None
    assert landing.restack_close([], closed, state, True) is None
    assert landing.restack_close(opened, opened, state, True) is None
    assert landing.restack_close(opened, closed, state, False) is None
