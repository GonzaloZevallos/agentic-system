from typing import cast

import pytest

from agentic.core.process.domain.task import (
    REDUCERS,
    DrawingEscalation,
    PullRequest,
    PullRequestCommit,
    TaskInput,
    TaskState,
    add,
    add_drawing_escalation,
    apply,
    delta,
    last,
    merge,
)


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
        "changes_requested",
        "change_history",
        "failures",
        "runs",
        "prompts",
        "playbooks",
        "playbooks_changed",
        "cost_usd",
        "subtasks",
        "findings",
        "required_paths_revisions",
        "settled_findings",
        "approval_settled",
        "plan_settled",
        "merge_ci",
        "full_checks_reads",
        "merge_conflict",
        "merge_refusal",
        "worktree_release_lapse",
        "lead_time",
        "status",
        "current_role",
        "escalated",
        "escalation_class",
        "escalation_cause",
        "tracker_human",
        "owner_decision_unbuilt",
        "tracker_items",
        "tracker_actions_done",
        "restarts",
        "restart_pending",
        "restart_settled",
        "session_floor",
        "estimate",
        "estimate_revisions",
        "proposed_estimate",
        "actual_files",
        "planned_size",
        "actual_size",
        "parts",
        "tracker_checklist",
        "tracker_checklist_posted",
        "split_issues",
        "carve_out",
        "fit_waived",
        "fit_question",
        "plan_due",
        "proposed_invariants",
        "experiment",
        "pause_log",
        "design_items",
        "flakes",
        "tracker_failures",
        "restorations",
        "review_scopes",
        "stack_restacks",
        "stack_pauses",
        "shadow_reviews",
        "drawing_departures",
        "drawing_escalation",
        "spec_cause",
        "spec_refusal",
        "spec",
        "spec_delivery",
        "spec_draft",
        "assumptions",
    }
    assert REDUCERS["cost_usd"] is add and REDUCERS["sections"] is merge
    assert REDUCERS["experiment"] is last
    assert REDUCERS["carve_out"] is merge


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
        "changes_requested": {"PM→Product Analyst": 1},  # absent in the state: taken as is
    }
    merged = apply(state, patch)
    assert merged["status"] == "defined"
    assert merged["cost_usd"] == 0.1 + 0.2
    assert merged["sections"] == {"PjM": "a", "Product Analyst": "b"}
    assert merged["runs"] == [{"role": "PjM"}, {"role": "Product Analyst"}]
    assert merged["flags"] == {"touches_ui": True}  # no reducer: replaced
    assert merged["changes_requested"] == {"PM→Product Analyst": 1}
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


def test_delta_drops_the_measurement_only_keys_a_branch_wrote():
    """`review_kind` and `presubmit_checks` are read once by the adapter inside the
    branch's own node and never travel back: two branches of the same wave would
    otherwise both write the parent's single channel in the same step, which LangGraph
    refuses (`InvalidUpdateError`) even when the two values happen to agree."""
    before = {"id": "T-1", "subtask_id": "api"}
    after = {
        **before,
        "review_kind": "first",
        "presubmit_checks": [[{"name": "tests", "passed": True}]],
    }
    patch = delta(before, after)
    assert "review_kind" not in patch and "presubmit_checks" not in patch


def test_pull_request_is_a_frozen_value_object_equal_by_value():
    commit = PullRequestCommit(
        author_emails=("dev@example.com",),
        message_headline="fix: x",
        additions=3,
        deletions=1,
        parent_count=1,
    )
    one = PullRequest(
        state="MERGED", created_at="2026-09-01T00:00:00Z", merged_at=None, commits=(commit,)
    )
    other = PullRequest(
        state="MERGED", created_at="2026-09-01T00:00:00Z", merged_at=None, commits=(commit,)
    )
    assert one == other
    with pytest.raises(AttributeError):
        setattr(one, "state", "CLOSED")


def test_delta_takes_a_field_the_branch_started_as_it_is():
    before = {"id": "T-1"}
    after = {
        "id": "T-1",
        "cost_usd": 0.4,
        "failures": [{"role": "PjM"}],
        "changes_requested": {"p": 1},
    }
    assert delta(before, after) == {
        "cost_usd": 0.4,
        "failures": [{"role": "PjM"}],
        "changes_requested": {"p": 1},
    }


def test_experiment_uses_last_and_two_branch_patches_fold_without_colliding():
    """`review.py` writes `experiment` from N sister branches in one superstep
    (ADR-0004, fifth addendum); `last`'s own docstring says a key without a reducer
    makes the graph refuse two writes in one step. Every branch of a wave writes the
    identical snapshot, so folding two of them in sequence must not collide and must
    leave the snapshot exactly as written."""
    snapshot = {"review_mode": "light", "skip_ephemeral": "False", "reviewer_model": "m"}
    state = {"id": "T-1"}
    once = apply(state, {"experiment": snapshot})
    twice = apply(once, {"experiment": snapshot})
    assert twice["experiment"] == snapshot


def test_pause_log_round_trips_through_delta_and_apply_with_a_non_empty_before():
    """`pause_log` is `operator.add` like `runs`/`failures`: a branch that already has
    entries, folded with what a new pause adds, round-trips through `delta` and `apply`
    exactly like any other append-only key."""
    before = {"id": "T-1", "pause_log": [{"kind": "escalation", "value": "cap"}]}
    after = {
        "id": "T-1",
        "pause_log": [
            {"kind": "escalation", "value": "cap"},
            {"kind": "resumed"},
            {"kind": "failure", "value": "account_limit", "section": "Developer: api"},
        ],
    }
    patch = delta(before, after)
    assert patch == {
        "pause_log": [
            {"kind": "resumed"},
            {"kind": "failure", "value": "account_limit", "section": "Developer: api"},
        ]
    }
    assert apply(before, patch) == after


def test_design_items_round_trips_through_delta_and_apply_like_pause_log():
    """`design_items` (ADR-0040) is `operator.add` too: a branch that already carries a
    lead's remark, folded with what a later node adds, round-trips through `delta` and
    `apply` exactly like `pause_log` does, with nothing bespoke for this key."""
    before = {
        "id": "T-1",
        "design_items": [
            {"role_slug": "ux_lead", "kind": "observation", "step": "Sign in", "text": "slow"}
        ],
    }
    after = {
        "id": "T-1",
        "design_items": [
            {"role_slug": "ux_lead", "kind": "observation", "step": "Sign in", "text": "slow"},
            {"role_slug": "ui_lead", "kind": "blocking", "step": "Pay", "text": "no button"},
        ],
    }
    patch = delta(before, after)
    assert patch == {
        "design_items": [
            {"role_slug": "ui_lead", "kind": "blocking", "step": "Pay", "text": "no button"},
        ]
    }
    assert apply(before, patch) == after


def test_a_cold_plan_patch_never_introduces_the_solution_interview_section():
    """The ephemeral-arm skip path (issue #28) runs no interview, so it writes no
    interview section: the `sections` merge reducer folds in only the Tech Lead's own
    delivery, and a state that never held the interview key stays without it -- never
    assumed, asserted here at the reducer `plan.py`'s cold path actually calls."""
    from agentic.core.process.domain.roles import SOLUTION_INTERVIEW

    state = {"id": "T-1", "sections": {"PM": "def"}}
    patch = {"sections": {"Tech Lead": "plan text"}}
    merged = apply(state, patch)
    assert SOLUTION_INTERVIEW not in merged["sections"]
    assert merged["sections"] == {"PM": "def", "Tech Lead": "plan text"}


def test_a_carve_out_patch_with_only_issue_url_keeps_data_and_fit():
    """GH-719: the record only grows; the shallow merge keeps what a later patch does not name."""
    record = {"data": {"steps": ["a"]}, "fit": {"fits": False}}
    assert merge(record, {"issue_url": "https://github.com/o/r/issues/9"}) == {
        **record,
        "issue_url": "https://github.com/o/r/issues/9",
    }
    assert apply({"carve_out": record}, {"carve_out": {"issue_url": "u"}})["carve_out"] == {
        "data": {"steps": ["a"]},
        "fit": {"fits": False},
        "issue_url": "u",
    }


def test_a_growing_carve_out_record_round_trips_through_delta_and_apply():
    opened = {"data": {"steps": ["a"]}, "fit": {"fits": False}}
    posted = {**opened, "issue_url": "https://github.com/o/r/issues/9"}
    closed = {**posted, "outcome": "applied"}
    states = [{"id": "T-1", "carve_out": opened}, {"carve_out": posted}, {"carve_out": closed}]
    for before, after in zip(states, states[1:], strict=False):
        before = {"id": "T-1", **before}
        after = {"id": "T-1", **after}
        assert apply(before, delta(before, after)) == after
    first = {"id": "T-1"}
    assert apply(first, delta(first, states[0])) == states[0]


def _escalation(round_: int, numbers: list[int], cause: str = "drawing_departure"):
    return cast(DrawingEscalation, {"round": round_, "numbers": numbers, "cause": cause})


_ESCALATIONS = [
    _escalation(3, [2, 5]),
    _escalation(4, [5, 1], "drawing_cap"),
    _escalation(1, [9]),
    _escalation(4, [], "drawing_departure"),
]


def test_add_drawing_escalation_takes_max_round_sorted_union_and_the_cap():
    merged = add_drawing_escalation(_escalation(3, [5, 2]), _escalation(4, [1, 5], "drawing_cap"))
    assert merged == _escalation(4, [1, 2, 5], "drawing_cap")
    assert add_drawing_escalation(None, _escalation(1, [3, 1])) == _escalation(1, [1, 3])
    assert add_drawing_escalation(_escalation(2, [1], "drawing_cap"), _escalation(1, [1])) == (
        _escalation(2, [1], "drawing_cap")
    )


@pytest.mark.parametrize("a", _ESCALATIONS)
@pytest.mark.parametrize("b", _ESCALATIONS)
def test_add_drawing_escalation_is_equal_in_both_merge_orders_from_the_same_base(a, b):
    base = _escalation(2, [4])
    assert add_drawing_escalation(add_drawing_escalation(base, a), b) == (
        add_drawing_escalation(add_drawing_escalation(base, b), a)
    )


@pytest.mark.parametrize("a", _ESCALATIONS)
@pytest.mark.parametrize("b", _ESCALATIONS)
@pytest.mark.parametrize("c", _ESCALATIONS)
def test_add_drawing_escalation_is_associative(a, b, c):
    assert add_drawing_escalation(add_drawing_escalation(a, b), c) == add_drawing_escalation(
        a, add_drawing_escalation(b, c)
    )


def _staged(round_: int, numbers: list[int], stage: str | None, cause: str = "drawing_departure"):
    record = {"round": round_, "numbers": numbers, "cause": cause}
    return cast(DrawingEscalation, record if stage is None else {**record, "stage": stage})


# Every stage (absent, plan, delivery) at rounds that tie and rounds that do not.
_STAGED = [
    _staged(round_, numbers, stage, cause)
    for round_, numbers, stage, cause in [
        (1, [1], None, "drawing_departure"),
        (1, [2], "plan", "drawing_departure"),
        (1, [3], "delivery", "drawing_cap"),
        (2, [1, 4], None, "drawing_departure"),
        (2, [5], "plan", "drawing_cap"),
        (2, [], "delivery", "drawing_departure"),
        (3, [2], "plan", "drawing_departure"),
    ]
]


@pytest.mark.parametrize("a", _STAGED)
@pytest.mark.parametrize("b", _STAGED)
def test_add_drawing_escalation_with_stage_is_commutative(a, b):
    assert add_drawing_escalation(a, b) == add_drawing_escalation(b, a)


@pytest.mark.parametrize("a", _STAGED)
@pytest.mark.parametrize("b", _STAGED)
@pytest.mark.parametrize("c", _STAGED)
def test_add_drawing_escalation_with_stage_is_associative(a, b, c):
    assert add_drawing_escalation(add_drawing_escalation(a, b), c) == add_drawing_escalation(
        a, add_drawing_escalation(b, c)
    )


@pytest.mark.parametrize("a", _STAGED)
def test_an_empty_side_is_the_identity_of_the_staged_reducer(a):
    empty = cast(DrawingEscalation, {})
    assert add_drawing_escalation(empty, a) == add_drawing_escalation(None, a)
    assert add_drawing_escalation(a, empty) == add_drawing_escalation(empty, a)
    assert add_drawing_escalation(a, empty).get("stage") != "delivery"
    assert add_drawing_escalation(empty, a).get("stage") == (
        "plan" if a.get("stage") == "plan" else None
    )


def test_the_higher_round_decides_the_stage_and_a_tie_goes_to_delivery():
    plan_late = add_drawing_escalation(_staged(1, [1], "delivery"), _staged(2, [2], "plan"))
    assert plan_late == _staged(2, [1, 2], "plan")
    delivery_late = add_drawing_escalation(_staged(1, [1], "plan"), _staged(2, [2], None))
    assert delivery_late == _escalation(2, [1, 2])
    tie = add_drawing_escalation(_staged(2, [1], "plan"), _staged(2, [2], "delivery"))
    assert tie == _escalation(2, [1, 2]) and "stage" not in tie
    assert add_drawing_escalation(_staged(2, [1], "plan"), _staged(2, [2], None)) == tie


def test_the_result_keeps_stage_only_when_plan_even_from_a_single_side():
    assert "stage" not in add_drawing_escalation(None, _staged(1, [1], "delivery"))
    assert add_drawing_escalation(None, _staged(1, [1], "plan"))["stage"] == "plan"
    assert "stage" not in add_drawing_escalation(
        _staged(1, [1], "delivery"), _staged(1, [2], "delivery")
    )


def test_drawing_escalation_round_trips_through_apply_and_delta():
    bare = cast(TaskState, {"id": "T-1"})
    first = cast(TaskState, {"id": "T-1", "drawing_escalation": _escalation(1, [1, 2])})
    assert apply(bare, delta(bare, first)) == first
    assert apply(bare, first) == first
    later = cast(
        TaskState, {"id": "T-1", "drawing_escalation": _escalation(2, [1, 2, 3], "drawing_cap")}
    )
    patch = delta(first, later)
    assert patch["drawing_escalation"] == later["drawing_escalation"]
    assert apply(first, patch) == later
    assert "drawing_escalation" not in delta(later, later)


def test_a_state_without_drawing_escalation_goes_through_apply_and_delta():
    state = cast(TaskState, {"id": "T-1", "cost_usd": 0.5})
    assert apply(state, cast(TaskState, {"cost_usd": 0.25}))["cost_usd"] == 0.75
    assert "drawing_escalation" not in delta(state, state)
    assert "drawing_escalation" not in TaskInput.__annotations__
    assert "drawing_escalation" in TaskState.__optional_keys__


def test_add_drawing_escalation_treats_an_empty_side_as_the_identity():
    # The graph's channel starts from `DrawingEscalation()` and may fold an empty update.
    empty = cast(DrawingEscalation, {})
    held = _escalation(2, [3, 1])
    assert add_drawing_escalation(empty, held) == _escalation(2, [1, 3])
    assert add_drawing_escalation(held, empty) == _escalation(2, [1, 3])
    assert add_drawing_escalation(empty, empty) == {}
    assert add_drawing_escalation(None, empty) == {}


_GRID = [
    _staged(round_, numbers, stage, cause)
    for round_ in (1, 2)
    for numbers in ([], [1], [2, 3])
    for stage in (None, "plan", "delivery")
    for cause in ("drawing_departure", "drawing_cap")
]


def test_staged_escalation_reducer_laws_hold_over_the_whole_grid():
    empty = cast(DrawingEscalation, {})
    for a in _GRID:
        assert add_drawing_escalation(a, empty) == add_drawing_escalation(empty, a)
        for b in [*_GRID, empty]:
            assert add_drawing_escalation(a, b) == add_drawing_escalation(b, a)
            for c in [*_GRID[::5], empty]:
                assert add_drawing_escalation(
                    add_drawing_escalation(a, b), c
                ) == add_drawing_escalation(a, add_drawing_escalation(b, c))
