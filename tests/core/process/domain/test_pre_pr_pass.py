"""The important pass before the pull request opens (GH-354, ADR-0053 amendment):
`rules.important_pass_spent` answers whether the budget is spent, `rules.pre_pr_important_due`
whether the final review approved with `owned`/`plan` importants still open, and
`rules.after_integrate` routes to `fix_important` before `full_checks`/`deliver`."""

from agentic.core.process.domain import landing, returns, rules

REVIEW = {"role": "Tech Lead", "section": rules.review_section("integration")}
SINGLE_REVIEW = {"role": "Tech Lead", "section": rules.review_section("only")}


def _row(scope="subtask", who="integration", severity="important", **extra):
    return {
        "subtask": who,
        "version": 1,
        "location": f"{scope}.py:1",
        "claim": f"{scope} {who} {severity} {sorted(extra)}",
        "severity": severity,
        "scope": scope,
        "category": "spec-behaviour",
        "status": "open",
        **extra,
    }


def _state(*rows, landing_entries=(), pull_request=None, single=False, runs=None):
    units = {"only": {"status": "approved"}}
    if not single:
        units = {"api": {"status": "approved"}, "integration": {"status": "approved"}}
    state = {
        "id": "GH-1",
        "subtasks": units,
        "findings": list(rows),
        "runs": [SINGLE_REVIEW if single else REVIEW] if runs is None else runs,
        "landing": list(landing_entries),
    }
    if pull_request:
        state["pull_request"] = pull_request
    return state


def _fix(**extra):
    return {"step": "fix_important", "head": "h", "refusal": "open_findings", **extra}


# --- important_pass_spent ---------------------------------------------------------------


def test_the_pass_is_spent_by_a_fix_important_entry_before_or_after_the_pull_request():
    assert rules.important_pass_spent(_state()) is False
    assert rules.important_pass_spent(_state(landing_entries=[{"step": "fix"}])) is False
    assert rules.important_pass_spent(_state(landing_entries=[_fix()])) is True
    assert rules.important_pass_spent(_state(landing_entries=[_fix(before_pr=True)])) is True


def test_only_a_resolved_entry_is_a_pass_done_when_the_caller_asks_for_it():
    open_pass = _state(landing_entries=[_fix()])
    done = _state(landing_entries=[_fix(resolved_at="2026-10-27T00:00:00+00:00")])
    assert rules.important_pass_spent(open_pass, resolved_only=True) is False
    assert rules.important_pass_spent(done, resolved_only=True) is True
    assert rules.important_pass_spent({"id": "x"}, resolved_only=True) is False


def test_the_budget_is_the_landings_constant():
    assert landing.LANDING_IMPORTANT_PASSES_MAX == rules.LANDING_IMPORTANT_PASSES_MAX == 1


# --- pre_pr_important_due ---------------------------------------------------------------


def test_due_when_the_final_review_approved_with_an_owned_important_row_open():
    assert rules.pre_pr_important_due(_state(_row("subtask", "api"))) is True


def test_due_for_a_plan_row():
    assert rules.pre_pr_important_due(_state(_row("plan", "api"))) is True


def test_due_for_the_single_subtask_when_there_is_no_integration():
    assert rules.pre_pr_important_due(_state(_row("subtask", "only"), single=True)) is True


def test_not_due_with_no_important_row():
    assert rules.pre_pr_important_due(_state()) is False
    assert rules.pre_pr_important_due(_state(_row(severity="observation"))) is False


def test_not_due_for_a_lowered_row():
    lowered = _row("subtask", "api", reported_severity="blocking")
    assert rules.pre_pr_important_due(_state(lowered)) is False


def test_a_lowered_row_beside_a_real_one_does_not_hide_it():
    lowered = _row("subtask", "api", reported_severity="blocking")
    assert rules.pre_pr_important_due(_state(lowered, _row("plan", "api"))) is True


def test_not_due_when_only_outside_rows_are_open():
    assert rules.pre_pr_important_due(_state(_row("outside", "api"))) is False


def test_not_due_once_the_pass_is_spent():
    spent = _state(_row("subtask", "api"), landing_entries=[_fix(before_pr=True)])
    assert rules.pre_pr_important_due(spent) is False


def test_not_due_while_a_unit_is_not_approved():
    state = _state(_row("subtask", "api"))
    state["subtasks"]["api"]["status"] = "returned"
    assert rules.pre_pr_important_due(state) is False


def test_not_due_without_a_review_of_the_final_unit():
    assert rules.pre_pr_important_due(_state(_row("subtask", "api"), runs=[])) is False


def test_not_due_once_the_pull_request_exists():
    """A landing `fix` that reopens a delivered task comes back through the same fan-in:
    the pre-PR pass is never run under a pull request."""
    state = _state(_row("subtask", "api"), pull_request="https://x/pull/1")
    assert rules.pre_pr_important_due(state) is False


# --- after_integrate --------------------------------------------------------------------


def test_the_fan_in_routes_to_fix_important_before_full_checks_and_deliver():
    state = {**_state(_row("subtask", "api")), "task_head": "h"}
    assert rules.full_checks_due(state) is True
    assert rules.after_integrate(state) == "fix_important"


def test_a_task_with_no_such_row_goes_the_route_it_went_before():
    clean = {**_state(), "task_head": "h"}
    assert rules.after_integrate(clean) == "full_checks"
    assert rules.after_integrate(_state()) == "deliver"
    assert rules.after_integrate(_state(_row("outside", "api"))) == "deliver"
    lowered = _state(_row("subtask", "api", reported_severity="blocking"))
    assert rules.after_integrate(lowered) == "deliver"


def test_after_the_pass_the_route_goes_on_to_deliver():
    done = _state(
        _row("subtask", "api"),
        landing_entries=[_fix(before_pr=True, resolved_at="2026-10-27T00:00:00+00:00")],
    )
    assert rules.after_integrate(done) == "deliver"


def test_a_returned_unit_is_dispatched_before_anything_else():
    state = _state(_row("subtask", "api"))
    state["subtasks"]["api"]["status"] = "returned"
    assert rules.after_integrate(state) == "pipeline"


# --- the readers of `landing` that assume a pull request ---------------------------------


def test_a_pre_pr_entry_is_marked_by_before_pr_alone():
    assert returns.before_pr({"step": "fix_important", "before_pr": True}) is True
    assert returns.before_pr({"step": "fix_important"}) is False


def test_an_open_pre_pr_entry_is_no_landing_pass_under_a_pull_request():
    """`landing_open` is the reopened-under-its-PR reading: an entry written before any PR
    existed does not make a later, ordinary delivery look reopened."""
    state = _state(landing_entries=[_fix(before_pr=True)])
    state["subtasks"]["api"]["status"] = "returned"
    state["pull_request"] = "https://x/pull/1"
    assert rules.landing_open(state) is False
    state["landing"] = [_fix()]
    assert rules.landing_open(state) is True


def test_the_landing_keeps_the_before_pr_key_when_it_rewrites_an_entry():
    assert landing.entry_of(_fix(before_pr=True))["before_pr"] is True
    assert "before_pr" not in landing.entry_of(_fix(before_pr="yes"))
    assert "before_pr" not in landing.entry_of(_fix())


def test_no_resume_child_is_owed_for_a_pass_written_before_the_pull_request():
    from datetime import UTC, datetime

    entry = _fix(before_pr=True, resume_spawned_at="2026-10-27T00:00:00+00:00", resume_spawns=1)
    raw = {"landing": [entry], "subtasks": {"api": {"status": "returned"}}}
    now = datetime(2026, 10, 27, 5, tzinfo=UTC)
    assert landing.resume_owed(raw, now) is False
    del entry["before_pr"]
    assert landing.resume_owed(raw, now) is True
