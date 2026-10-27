"""The `fix_important` pre-PR system node (GH-354): what it reopens, what it records, what
it never touches, and when the fan-in closes the entry."""

import asyncio
from types import SimpleNamespace

from agentic.core.process.application import pre_pr_fix
from agentic.core.process.domain import returns, rules
from agentic.core.process.domain.task import apply

NOW = "2026-10-27T10:00:00+00:00"


def _row(who, scope="subtask", severity="important"):
    return {
        "subtask": who,
        "version": 1,
        "location": f"{who}.py:1",
        "claim": f"{who} {scope}",
        "severity": severity,
        "scope": scope,
        "category": "spec-scope",
        "status": "open",
    }


def _state(*rows):
    return {
        "id": "GH-1",
        "status": "developing",
        "cost_usd": 3.0,
        "task_head": "abc123",
        "subtasks": {
            "api": {"status": "approved", "branch": "b.api"},
            "web": {"status": "approved", "branch": "b.web"},
            "integration": {"status": "approved", "branch": "b"},
        },
        "findings": list(rows),
        "runs": [{"role": "Tech Lead", "section": rules.review_section("integration")}],
        "changes_requested": {"Tech Lead→Developer:api": 1},
        "change_history": [{"pair": "Tech Lead→Developer:api", "category": "spec-scope"}],
    }


def _run(state):
    saved = []
    ports = SimpleNamespace(documents=SimpleNamespace(save=saved.append))
    patch = asyncio.run(pre_pr_fix.fix_important(state, None, ports))
    return patch, saved


def test_the_owning_unit_is_reopened_and_the_others_are_left_alone():
    state = _state(_row("api"))
    patch, saved = _run(state)
    after = apply(state, patch)

    assert after["subtasks"]["api"]["status"] == "returned"
    assert after["subtasks"]["web"]["status"] == "approved"
    # The integration goes back to `pending`: it is reviewed again after the unit.
    assert after["subtasks"]["integration"]["status"] == "pending"
    assert "api.py:1" in after["subtasks"]["api"]["reason"]
    assert after["subtasks"]["api"]["change_category"] == "spec-scope"
    assert saved == [after] and after["status"] == "developing"


def test_it_is_no_return_and_the_pair_counter_is_never_written():
    state = _state(_row("api"))
    patch, _ = _run(state)
    after = apply(state, patch)

    assert "changes_requested" not in patch
    assert after["changes_requested"] == state["changes_requested"]
    new = after["change_history"][len(state["change_history"]) :]
    assert [returns.is_return(entry) for entry in new] == [False]
    assert len(after["change_history"]) == len(state["change_history"]) + 1


def test_it_records_the_pass_before_the_pull_request_and_spends_the_budget():
    state = _state(_row("api"))
    patch, _ = _run(state)
    [entry] = patch["landing"]

    assert entry["step"] == "fix_important" and entry["before_pr"] is True
    assert entry["head"] == "abc123" and entry["cost_base"] == 3.0
    assert "resolved_at" not in entry
    assert rules.important_pass_spent(apply(state, patch)) is True


def test_a_plan_row_reopens_the_reviewed_unit():
    state = _state(_row("api", "plan"))
    patch, _ = _run(state)
    assert apply(state, patch)["subtasks"]["integration"]["status"] == "returned"


def test_the_fan_in_closes_the_entry_only_when_every_unit_is_approved_again():
    state = _state(_row("api"))
    patch, _ = _run(state)
    open_pass = apply(state, patch)

    assert pre_pr_fix.close_pass(open_pass, NOW) is None
    approved = {
        **open_pass,
        "subtasks": {k: {**v, "status": "approved"} for k, v in open_pass["subtasks"].items()},
        "cost_usd": 4.5,
    }
    [closed] = pre_pr_fix.close_pass(approved, NOW)
    assert closed["resolved_at"] == NOW and closed["cost_usd"] == 1.5
    assert closed["before_pr"] is True
    assert pre_pr_fix.close_pass({**approved, "landing": [closed]}, NOW) is None


def test_it_closes_nothing_for_an_entry_the_landing_wrote_under_a_pull_request():
    state = {
        **_state(),
        "landing": [{"step": "fix_important", "head": "h", "refusal": "open_findings"}],
    }
    assert pre_pr_fix.close_pass(state, NOW) is None
