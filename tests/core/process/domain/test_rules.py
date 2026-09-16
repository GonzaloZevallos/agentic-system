from agentic.core.process.domain import rules, task
from agentic.core.process.domain.roles import PAIR_PM_PA
from agentic.core.process.domain.task import INTEGRATION

PAIR = PAIR_PM_PA
KEY = PAIR.key
DEF = {"pair": KEY, "category": "definition", "reason": "gap"}


def test_remaining_returns_counts_down_from_the_limit():
    assert rules.remaining_returns({}, PAIR, 2) == 2
    assert rules.remaining_returns({"returns": {KEY: 1}}, PAIR, 2) == 1
    assert rules.remaining_returns({"returns": {KEY: 2}}, PAIR, 2) == 0
    assert rules.remaining_returns({"returns": {"other": 5}}, PAIR, 2) == 2


def test_record_return_adds_one_to_that_pair_only():
    assert rules.record_return({}, PAIR) == {KEY: 1}
    assert rules.record_return({"returns": {KEY: 1, "other": 5}}, PAIR) == {KEY: 2}


def test_limit_reached_names_the_pair_the_limit_and_the_reason():
    assert (
        rules.limit_reached(PAIR, 2, "still no metrics") == f"{KEY} hit 2 returns: still no metrics"
    )


def test_next_version_counts_that_roles_runs_only():
    runs = [{"role": "PjM"}, {"role": "PM"}, {"role": "PM"}]
    assert rules.next_version({"runs": runs}, "PM") == 3
    assert rules.next_version({"runs": runs}, "Product Analyst") == 1
    assert rules.next_version({}, "PM") == 1


def test_sections_for_keeps_the_superseded_delivery_as_history():
    assert rules.sections_for({}, "PM", "new") == {"PM": "new"}
    state = {"sections": {"PM": "old"}, "runs": [{"role": "PM"}]}
    assert rules.sections_for(state, "PM", "new") == {"PM": "new", "PM (v1)": "old"}


def test_routing_ends_on_escalation_and_loops_on_return():
    assert rules.after_classify({}) == "define"
    assert rules.after_classify({"escalated": "x"}) is None
    assert rules.after_define({}) == "validate"
    assert rules.after_define({"escalated": "x"}) is None
    assert rules.after_validate({"status": "returned"}) == "define"
    assert rules.after_validate({"status": "definition_ready"}) == "plan"
    assert rules.after_validate({"status": "returned", "escalated": "x"}) is None


def test_the_verdict_and_the_category_come_from_the_blocking_findings():
    # A review returns only on a blocking finding, and the return takes the category of
    # the first one; anything else approves, with remarks (ADR-0016).
    block = {"severity": "blocking", "category": "spec-behaviour"}
    other = {"severity": "blocking", "category": "standard-seams"}
    note = {"severity": "important", "category": "standard-architecture"}
    assert rules.return_category([]) is None
    assert rules.return_category([note]) is None
    assert rules.return_category([note, block, other]) == "spec-behaviour"


def finding(severity="blocking", evidence="$ pytest\nFAILED", scope="subtask", at="x.py:1"):
    return {
        "location": at,
        "claim": "c",
        "evidence": evidence,
        "severity": severity,
        "scope": scope,
        "category": "spec-behaviour",
    }


def test_a_block_without_backing_is_lowered_not_failed():
    # Filling a field badly must not cost a retry, let alone escalate the task: the
    # system lowers the block and says so (ADR-0016). Without evidence it is an
    # observation; of the plan's scope, the Developer cannot fix it here: important.
    kept = finding(at="a.py:1")
    bare = finding(evidence="  ", at="b.py:2")
    planned = finding(scope="plan", at="c.py:3")
    note = finding("observation", evidence="", at="d.py:4")
    findings, lowered = rules.lower_unbacked_blocks([kept, bare, planned, note])
    assert [f["severity"] for f in findings] == [
        "blocking",
        "observation",
        "important",
        "observation",
    ]
    assert findings[0] is kept and bare["severity"] == "blocking"  # the input is untouched
    assert lowered == [
        "`b.py:2` blocking lowered to observation: no evidence",
        "`c.py:3` blocking lowered to important: scope plan, not fixable in this subtask",
    ]
    assert rules.lower_unbacked_blocks([kept]) == ([kept], [])
    # Both wrong: the lower level wins, since without evidence nothing supports it.
    (both,), _ = rules.lower_unbacked_blocks([finding(evidence="", scope="plan")])
    assert both["severity"] == "observation"


def test_the_system_writes_the_verdict_heading_of_a_review():
    # The verdict is derived, so the reviewer never writes it: its section cannot
    # contradict what the system decided (ADR-0016).
    findings = "### Findings\n\n| blocking | ... |"
    assert rules.review_markdown(None, "all good", findings) == (
        f"### Verdict\n\n**approved**: all good\n\n{findings}"
    )
    assert rules.review_markdown("spec-behaviour", "no test", findings).startswith(
        "### Verdict\n\n**returned** (`spec-behaviour`): no test\n\n### Findings"
    )
    lowered = ["`b.py:2` blocking lowered to observation: no evidence"]
    assert rules.review_markdown(None, "ok", findings, lowered) == (
        "### Verdict\n\n**approved**: ok\n\nLowered by the system:\n\n"
        f"- `b.py:2` blocking lowered to observation: no evidence\n\n{findings}"
    )


def test_a_return_is_recorded_with_its_category_for_the_history():
    assert rules.return_entry(PAIR, "definition", "no metrics") == {
        "pair": KEY,
        "category": "definition",
        "reason": "no metrics",
    }


# The task cap and the phases (ADR-0004, fourth addendum).


def subtasks(*ids):
    return {"subtasks": {sid: {"id": sid, "status": "pending"} for sid in ids}}


def test_the_task_cap_is_the_base_until_the_plan_then_grows_with_its_units():
    assert rules.task_cap({}, 30, 15, 250) == 30
    assert rules.task_cap(subtasks("api"), 30, 15, 250) == 45  # one subtask, no integration
    assert rules.work_units(subtasks("api", "domain")) == 3  # the integration counts ahead
    assert rules.work_units(subtasks("api", "domain", INTEGRATION)) == 3  # and only once
    assert rules.task_cap(subtasks(*[f"s{n}" for n in range(12)]), 30, 15, 180) == 180


def test_a_cap_escalation_is_recognised_and_proposes_one_more_unit():
    reason = rules.cap_reached("Developer", 106.27, 100)
    assert reason == "Developer stopped by the task cap: 106.27 of 100.00 USD spent"
    assert rules.stopped_by_cap(reason) and not rules.stopped_by_cap("PM hit 2 returns: x")
    assert rules.suggested_budget({"cost_usd": 106.27}, 15) == 122


def test_spent_by_phase_sums_the_runs_by_section_and_keeps_what_they_miss():
    runs = [
        {"role": "PjM", "section": "PjM", "cost_usd": 0.5},
        {"role": "PM", "section": "PM", "cost_usd": 1.0},
        {"role": "Tech Lead", "section": "Interview: Tech Lead efímero→Tech Lead", "cost_usd": 2},
        {"role": "Tech Lead", "section": "Tech Lead", "cost_usd": 1.0},
        {"role": "Developer", "section": "Developer: api", "cost_usd": 3.0},
        {"role": "Tech Lead", "section": "Tech Lead: review api", "cost_usd": 4.0},
    ]
    assert rules.spent_by_phase({"runs": runs, "cost_usd": 11.5}) == {
        "definition": 1.5,
        "plan": 3.0,
        "development": 3.0,
        "review": 4.0,
    }
    assert rules.spent_by_phase({"runs": runs, "cost_usd": 12.5})["other"] == 1.0


# Pause and resume (ADR-0003, addendum).


def run(role, section=None):
    return {"role": role, "section": section or role, "cost_usd": 0.1}


def test_the_status_before_the_escalation_is_derived_from_the_definition_runs():
    interview = run("PM", "Interview: Product Analyst→PM")
    assert rules.resumed_status({"runs": [run("PjM"), interview]}) == "open"
    assert rules.resumed_status({"runs": [run("Product Analyst")]}) == "defined"
    returned = {"runs": [run("Product Analyst"), run("PM")], "return_history": [DEF]}
    assert rules.resumed_status(returned) == "returned"
    approved = {"runs": [run("Product Analyst"), run("PM")]}
    assert rules.resumed_status(approved) == "definition_ready"
    # The last return, at the limit, is not counted, but the reason says it happened.
    at_limit = {**approved, "escalated": rules.limit_reached(PAIR, 2, "gap")}
    assert rules.resumed_status(at_limit) == "returned"
    # A document from before the history has only the counter.
    assert rules.resumed_status({**approved, "returns": {KEY: 1}}) == "returned"
    assert rules.resumed_status(subtasks("api")) == "planned"
    assert rules.resumed_status({"subtasks": {"api": {"status": "delivered"}}}) == "developing"


def test_resuming_clears_the_escalation_resets_the_pair_and_keeps_the_decision():
    state = {
        "status": "waiting_on_human",
        "escalated": "Tech Lead→Developer:api hit 2 returns: still",
        "questions_for_human": ["Which currency?"],
        "subtasks": {"api": {"id": "api", "status": "returned"}},
    }
    patch = rules.resumed(state, "  Keep USD.\n\n## Why\n\nAudits.  ")
    assert patch["escalated"] is None and patch["status"] == "developing"
    assert patch["questions_for_human"] == []
    assert patch["returns"] == {"Tech Lead→Developer:api": 0}
    assert patch["sections"]["Human"] == (
        "### Decision 1\n\nEscalated: Tech Lead→Developer:api hit 2 returns: still\n\n"
        "Keep USD.\n\n### Why\n\nAudits."
    )
    again = rules.human_section({"sections": {"Human": patch["sections"]["Human"]}}, "x", "Go")
    assert again.startswith("### Decision 1") and again.endswith(
        "### Decision 2\n\nEscalated: x\n\nGo"
    )
    assert "returns" not in rules.resumed(
        {"escalated": "PjM stopped by the task cap: 1 of 1"}, None
    )
    assert "sections" not in rules.resumed({}, "   ")


def test_resuming_adds_the_integration_the_review_did_not_get_to_add():
    state = {"subtasks": {"api": {"status": "approved"}, "domain": {"status": "approved"}}}
    patch = rules.resumed(state, None)
    assert patch["subtasks"] == {INTEGRATION: rules.integration_subtask()}
    assert rules.resume_target(task.apply(state, patch)) == "integrate"


def test_the_resume_target_follows_the_state():
    assert rules.resume_target({"escalated": "x", "status": "defined"}) is None
    assert rules.resume_target({"status": "open"}) == "classify"
    assert rules.resume_target({"status": "open", "type": "bug"}) == "classify"
    assert rules.resume_target({"status": "open", "type": "feature"}) == "define"
    assert rules.resume_target({"status": "defined"}) == "validate"
    assert rules.resume_target({"status": "returned"}) == "define"
    assert rules.resume_target({"status": "definition_ready"}) == "plan"
    # With a plan every road goes through the fan-in, which decides the wave itself.
    both = {"api": {"status": "delivered"}, "domain": {"status": "pending"}}
    assert rules.resume_target({"subtasks": both}) == "integrate"
    assert rules.resume_target({"subtasks": {"api": {"status": "returned"}}}) == "integrate"
    assert rules.resume_target({"subtasks": {"api": {"status": "approved"}}}) == "integrate"
    assert rules.resume_target({"pull_request": "u", "subtasks": both}) is None


def test_the_fan_in_dispatches_a_delivery_before_it_is_built_again():
    """A delivery the review never reached is reviewed, not built twice: where a run the
    cap stopped between the two resumes."""
    both = {"api": {"status": "delivered"}, "domain": {"status": "pending"}}
    assert rules.dispatched_subtasks({"subtasks": both}) == ["domain", "api"]
    assert rules.pipeline_entry({"subtasks": both, "subtask_id": "api"}) == "review"
    assert rules.pipeline_entry({"subtasks": both, "subtask_id": "domain"}) == "develop"


def test_a_plan_that_cannot_move_has_nothing_to_resume_into():
    """Nothing to dispatch and not everything approved: a `depends_on` on a subtask that
    does not exist, or a cycle. The caller says so instead of taking the task out of the
    queue, announcing a resume and running nothing."""
    stuck = {"api": {"status": "pending", "depends_on": ["ghost"]}}
    assert rules.dispatched_subtasks({"subtasks": stuck}) == []
    assert rules.resume_target({"subtasks": stuck}) is None
    cycle = {
        "a": {"status": "pending", "depends_on": ["b"]},
        "b": {"status": "pending", "depends_on": ["a"]},
    }
    assert rules.resume_target({"subtasks": cycle}) is None


def test_a_dependency_that_does_not_merge_escalates_before_the_developer_costs_anything():
    patch = rules.unmergeable("api", "domain", "CONFLICT (content): x.py")
    assert patch["status"] == "waiting_on_human"
    # No role ran, so nothing seeded `current_role`: without this the human reads the
    # name of whoever wrote last.
    assert patch["current_role"] == "Developer"
    assert "subtask 'api' cannot start on 'domain'" in patch["escalated"]
    assert "CONFLICT (content): x.py" in patch["escalated"]
    # An answer alone fixes nothing here, so the escalation says what to do instead.
    assert "by hand" in patch["escalated"]
