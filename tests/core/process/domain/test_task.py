import pytest

from agentic.core.process.domain.task import (
    REDUCERS,
    PullRequest,
    PullRequestCommit,
    add,
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
        "settled_findings",
        "approval_settled",
        "status",
        "current_role",
        "escalated",
        "escalation_class",
        "proposed_invariants",
        "experiment",
        "pause_log",
        "design_items",
    }
    assert REDUCERS["cost_usd"] is add and REDUCERS["sections"] is merge
    assert REDUCERS["experiment"] is last


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
