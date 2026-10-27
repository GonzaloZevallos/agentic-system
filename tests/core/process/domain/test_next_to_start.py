"""`rules.next_to_start` and `rules.resume_source` (GH-364): the pure decisions of the
subtask loop, so a subtask starts when it is ready and a crash resumes from the right
source, with no graph run."""

import pytest

from agentic.core.process.domain import rules


def plan(**subtasks):
    """A state whose subtasks are `id=(status, [depends_on])`, in the order given."""
    return {
        "subtasks": {
            sid: {"id": sid, "status": status, "depends_on": list(deps)}
            for sid, (status, deps) in subtasks.items()
        }
    }


def test_critical_path_beats_plan_order():
    # `a` is first in the plan but nothing depends on it; `c` heads a chain of two.
    state = plan(
        a=("pending", []),
        b=("pending", []),
        c=("pending", []),
        d=("pending", ["c"]),
        e=("pending", ["d"]),
    )
    assert rules.next_to_start(state, [], 5) == ["c", "a", "b"]


def test_critical_path_is_the_longest_chain_of_dependents():
    state = plan(
        a=("pending", []),
        b=("pending", []),
        x=("pending", ["a"]),
        y=("pending", ["x"]),
        z=("pending", ["b"]),
    )
    assert rules.next_to_start(state, [], 5) == ["a", "b"]


def test_ties_keep_plan_order():
    state = plan(c=("pending", []), a=("pending", []), b=("pending", []))
    assert rules.next_to_start(state, [], 3) == ["c", "a", "b"]


def test_the_limit_counts_what_is_already_in_flight():
    state = plan(a=("pending", []), b=("pending", []), c=("pending", []), d=("pending", []))
    assert rules.next_to_start(state, ["a"], 3) == ["b", "c"]
    assert rules.next_to_start(state, [], 2) == ["a", "b"]


def test_a_full_or_overfull_limit_starts_nothing():
    state = plan(a=("pending", []), b=("pending", []), c=("pending", []))
    assert rules.next_to_start(state, ["a", "b"], 2) == []
    assert rules.next_to_start(state, ["a", "b"], 1) == []


def test_a_subtask_in_flight_is_not_started_again():
    state = plan(a=("pending", []), b=("pending", []))
    assert rules.next_to_start(state, {"a"}, 4) == ["b"]


def test_an_escalated_task_starts_nothing():
    state = {**plan(a=("pending", [])), "escalated": "Developer hit 2 returns: x"}
    assert rules.next_to_start(state, [], 4) == []


def test_a_subtask_starts_while_an_unrelated_sibling_is_still_in_flight():
    # `b` depends on `a`, approved; `c` is unrelated and still being built.
    state = plan(a=("approved", []), b=("pending", ["a"]), c=("pending", []))
    assert rules.next_to_start(state, ["c"], 2) == ["b"]


def test_a_subtask_waits_for_a_dependency_that_is_not_approved():
    state = plan(a=("delivered", []), b=("pending", ["a"]))
    assert rules.next_to_start(state, [], 4) == ["a"]


def test_a_delivered_subtask_is_included_so_a_stopped_review_resumes():
    state = plan(a=("delivered", []), b=("returned", []), c=("approved", []))
    assert rules.next_to_start(state, [], 4) == ["a", "b"]


@pytest.mark.parametrize(
    ("next_node", "crashed", "source"),
    [
        (("pipeline",), True, "document"),
        ("pipeline", True, "document"),
        (["pipeline"], True, "document"),
        (("pipeline",), False, "checkpoint"),
        (("integrate",), True, "checkpoint"),
        (("escalate",), True, "checkpoint"),
        ((), True, "checkpoint"),
        (None, True, "checkpoint"),
        (("pipeline", "integrate"), True, "checkpoint"),
    ],
)
def test_resume_source_is_the_document_only_for_a_crash_with_the_pipeline_pending(
    next_node, crashed, source
):
    assert rules.resume_source(next_node, crashed) == source
