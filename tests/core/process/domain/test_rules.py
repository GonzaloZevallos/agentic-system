from datetime import datetime, timedelta
from typing import get_args

from agentic.core.process.domain import document, roles, rules, task
from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import (
    DEVELOPER,
    FINDINGS,
    INTERVIEW_DEFINITION,
    INTERVIEW_SOLUTION,
    PAIR_DESIGN_LEAD,
    PAIR_PM_PA,
    PAIR_UX_LEAD,
    PJM,
    PRODUCT_ANALYST_BUG,
    TECH_LEAD_EPHEMERAL,
)
from agentic.core.process.domain.task import INTEGRATION

PAIR = PAIR_PM_PA
KEY = PAIR.key
DEF = {"pair": KEY, "category": "definition", "reason": "gap"}


def test_remaining_changes_counts_down_from_the_limit():
    assert rules.remaining_changes({}, PAIR, 2) == 2
    assert rules.remaining_changes({"changes_requested": {KEY: 1}}, PAIR, 2) == 1
    assert rules.remaining_changes({"changes_requested": {KEY: 2}}, PAIR, 2) == 0
    assert rules.remaining_changes({"changes_requested": {"other": 5}}, PAIR, 2) == 2


def test_record_return_adds_one_to_that_pair_only():
    assert rules.record_return({}, PAIR) == {KEY: 1}
    assert rules.record_return({"changes_requested": {KEY: 1, "other": 5}}, PAIR) == {KEY: 2}


def test_limit_reached_names_the_pair_the_limit_and_the_reason():
    assert (
        rules.limit_reached(PAIR, 2, "still no metrics") == f"{KEY} hit 2 returns: still no metrics"
    )


def test_escalation_carries_the_reason_and_its_class_together():
    # The only place either key is written, so a patch cannot set `escalated` without
    # `escalation_class` (issue #24, task-events).
    assert rules.escalation("cap", "PjM stopped by the task cap") == {
        "escalated": "PjM stopped by the task cap",
        "escalation_class": "cap",
    }


def test_returns_pair_reads_the_class_first_and_falls_back_to_the_text():
    at_limit = f"{KEY} hit 2 returns: still no metrics"
    # A document written before `escalation_class` existed: no class, so the text alone
    # decides (ADR-0003, addendum).
    assert rules.returns_pair({"escalated": at_limit}) == KEY
    # The class confirms it.
    assert rules.returns_pair({"escalated": at_limit, "escalation_class": "returns"}) == KEY
    # A class that says this was not a pair at its limit gates the text out, even if it
    # happens to match the pattern.
    assert rules.returns_pair({"escalated": at_limit, "escalation_class": "cap"}) is None
    assert rules.returns_pair({"escalated": "PjM failed twice"}) is None


def test_next_version_counts_that_roles_runs_only():
    runs = [{"role": "PjM"}, {"role": "PM"}, {"role": "PM"}]
    assert rules.next_version({"runs": runs}, "PM") == 3
    assert rules.next_version({"runs": runs}, "Product Analyst") == 1
    assert rules.next_version({}, "PM") == 1


def test_sections_for_keeps_the_superseded_delivery_as_history():
    assert rules.sections_for({}, "PM", "new") == {"PM": "new"}
    state = {"sections": {"PM": "old"}, "runs": [{"role": "PM"}]}
    assert rules.sections_for(state, "PM", "new") == {"PM": "new", "PM (v1)": "old"}


def test_interview_progress_on_a_fresh_interview_gives_the_whole_budget_and_no_sessions():
    assert rules.interview_progress({}, INTERVIEW_DEFINITION, 4) == rules.InterviewProgress(
        rounds_done=0, rounds_left=4, sessions={}
    )


def test_interview_progress_counts_the_interviewers_own_runs_including_the_closing_one():
    # Four interviewer runs under the interview's section: three questions and the
    # closing turn. The interviewee's own runs (answers) do not double the count.
    section = INTERVIEW_DEFINITION.section
    runs = [
        {"role": "Product Analyst", "section": section, "session_id": f"q{i}"} for i in range(1, 5)
    ] + [{"role": "PM", "section": section, "session_id": f"a{i}"} for i in range(1, 4)]
    progress = rules.interview_progress({"runs": runs}, INTERVIEW_DEFINITION, 4)
    assert progress.rounds_done == 4
    # Exactly two more, the retake's own budget, even though the ordinary cap is spent.
    assert progress.rounds_left == 2
    assert progress.sessions == {"product_analyst_interviewer": "q4", "pm_interviewee": "a3"}


def test_interview_progress_resumed_at_the_cap_gets_the_two_it_had_left():
    # Cut at round 2 by a role failure or the task cap: the same formula still gives it
    # exactly the rounds a fresh interview would have left, `INTERVIEW_RETAKE_ROUNDS` as
    # the floor for one that closed already using up the ordinary budget.
    section = INTERVIEW_DEFINITION.section
    runs = [
        {"role": "Product Analyst", "section": section, "session_id": "q1"},
        {"role": "PM", "section": section, "session_id": "a1"},
        {"role": "Product Analyst", "section": section, "session_id": "q2"},
    ]
    progress = rules.interview_progress({"runs": runs}, INTERVIEW_DEFINITION, 4)
    assert progress.rounds_done == 2
    assert progress.rounds_left == 2


def test_interview_progress_seeds_invariants_from_the_state():
    # `state["proposed_invariants"]` is what the whole interview accumulated so far
    # (issue #42): a retake's ephemeral transcript starts from it, never empty.
    state = {"proposed_invariants": ["Every X does Y."]}
    progress = rules.interview_progress(state, INTERVIEW_SOLUTION, 4)
    assert progress.invariants == ["Every X does Y."]
    assert rules.interview_progress({}, INTERVIEW_SOLUTION, 4).invariants == []


def test_interview_progress_ignores_runs_of_other_sections():
    # The consultant's own runs (PM's section, not the interview's) never count as a
    # round, and a seat with no run there is simply absent from `sessions`.
    runs = [
        {"role": "Product Analyst", "section": INTERVIEW_DEFINITION.section, "session_id": "q1"},
        {"role": "PM", "section": "PM", "session_id": "pm-1"},
    ]
    progress = rules.interview_progress({"runs": runs}, INTERVIEW_DEFINITION, 4)
    assert progress.rounds_done == 1
    assert progress.sessions == {"product_analyst_interviewer": "q1"}


def test_interview_retake_delta_names_the_branch_the_head_and_the_edit_rule():
    delta = rules.interview_retake_delta({}, INTERVIEW_DEFINITION, "agentic/GH-1", "abc123")
    assert "`agentic/GH-1`" in delta and "`abc123`" in delta
    assert "edit" in delta.lower() and "never re-create" in delta
    assert "## Human" not in delta  # nothing decided yet: no section to carry


def test_interview_retake_delta_carries_the_human_section_unconditionally():
    state = {"sections": {"Human": "### Decision 1\n\nUSD."}}
    delta = rules.interview_retake_delta(state, INTERVIEW_DEFINITION, "agentic/GH-1", "abc123")
    assert delta.endswith("## Human\n\n### Decision 1\n\nUSD.")


def test_routing_ends_on_escalation_and_loops_on_return():
    assert rules.after_classify({"type": "feature"}) == "define"
    assert rules.after_classify({"escalated": "x"}) is None
    assert rules.after_define({}) == "validate"
    assert rules.after_define({"escalated": "x"}) is None
    assert rules.after_validate({"status": "returned"}) == "define"
    assert rules.after_validate({"status": "definition_ready"}) == "plan"
    assert rules.after_validate({"status": "returned", "escalated": "x"}) is None


def test_a_platform_enhancement_skips_the_definition_stage():
    """The shortest of the three paths (README.md, 2; issue #46): no product behaviour
    to define, so the plan is the first node after PjM."""
    assert rules.after_classify({"type": "platform_enhancement"}) == "plan"


def test_a_bug_is_diagnosed_then_validated_and_may_end_discarded():
    """The bug path (README.md, 2; issue #65): the diagnosis in place of the definition,
    the same PM after it. PM's return goes back to whoever wrote what it validated; an
    approval goes to the plan, unless PM ruled the behaviour expected, which discards."""
    assert rules.after_classify({"type": "bug"}) == "diagnose"
    assert rules.after_diagnose({}) == "validate"
    assert rules.after_diagnose({"escalated": "x"}) is None
    assert rules.after_validate({"status": "returned", "type": "bug"}) == "diagnose"
    assert rules.after_validate({"status": "returned", "type": "feature"}) == "define"
    assert rules.after_validate({"status": "definition_ready", "type": "bug"}) == "plan"
    assert rules.after_validate({"status": "discarded"}) == "discard"
    assert rules.after_discard({}) is None


# The definition-side design branch (ADR-0040).

UX_FLAGS = {"type": "feature", "flags": {"touches_ui": True}}


def test_design_step_is_none_off_the_feature_plus_touches_ui_path():
    # Neither a bug nor a platform enhancement runs this branch, whatever its flags.
    assert rules.design_step({"type": "bug", "flags": {"touches_ui": True}}) is None
    platform = {"type": "platform_enhancement", "flags": {"touches_ui": True}}
    assert rules.design_step(platform) is None
    # A feature PM did not flag `touches_ui` does not either.
    assert rules.design_step({"type": "feature", "flags": {}}) is None
    assert rules.design_step({"type": "feature"}) is None
    # `after_validate`/`resume_target` fall through to `plan` exactly as before this
    # task on every one of these: a bug and a platform enhancement get today's nodes.
    assert rules.after_validate({"status": "definition_ready", "type": "bug"}) == "plan"
    assert rules.resume_target({"status": "definition_ready", "type": "bug"}) == "plan"


def test_design_step_walks_the_five_steps_in_order_then_falls_through_to_plan():
    """A `touches_ui` feature, built up one run at a time: each step is next exactly
    once its own thinker has nothing new to validate, and consolidation is last."""
    state: dict = {**UX_FLAGS, "runs": []}
    assert rules.design_step(state) == "ux_flow"

    state["runs"] = [*state["runs"], run("UX Designer")]
    assert rules.design_step(state) == "ux_walk"

    state["runs"] = [*state["runs"], run("UX Lead")]  # approved: no change_history entry
    assert rules.design_step(state) == "ui_screens"

    state["runs"] = [*state["runs"], run("UI Designer")]
    assert rules.design_step(state) == "design_review"

    state["runs"] = [*state["runs"], run("Design Lead")]  # approved
    assert rules.design_step(state) == "consolidate"

    state["runs"] = [*state["runs"], run("PM: consolidation")]
    assert rules.design_step(state) is None
    # The routing falls through to `plan` exactly like a task this branch does not
    # apply to, once every step is satisfied.
    assert rules.after_consolidate({**state, "status": "definition_ready"}) == "plan"


def test_design_step_never_skips_ux_walk_even_with_a_stray_lead_run_out_of_order():
    """An enforcement check: a UX Lead run recorded with no UX Designer run at all
    (a malformed or out-of-order state) must not read as the UX step satisfied -- the
    thinker is still next, never skipped on the strength of the lead's run alone."""
    state = {**UX_FLAGS, "runs": [run("UX Lead")]}
    assert rules.design_step(state) == "ux_flow"


def test_a_ux_lead_return_sends_the_task_back_to_ux_designer_and_counts_its_pair():

    state = {
        **UX_FLAGS,
        "runs": [run("UX Designer"), run("UX Lead")],
        "change_history": [rules.change_entry(PAIR_UX_LEAD, rules.PRD_RETURN, "dead end")],
    }
    assert rules.design_step(state) == "ux_flow"
    assert rules.after_ux_walk(state) == "ux_flow"
    # A second return escalates, like any other pair at its limit; the design node's
    # own `after_*` stops there, same as every other `after_*` on an escalation.
    assert rules.after_ux_walk({**state, "escalated": "x"}) is None


def test_design_step_table_covers_a_consolidation_return_and_prd_v2_reopening_the_branch():
    """The table's own hard case (the plan's acceptance list): a consolidation return
    to Product Analyst, a new PRD version, which reopens `ux_flow` even though UX Lead
    had already approved; and the flow's own re-walk, which in turn reopens
    `ui_screens` even though Design Lead had already approved that too."""
    runs = [
        run("Product Analyst"),  # PRD v1
        run("UX Designer"),
        run("UX Lead"),  # approved
        run("UI Designer"),
        run("Design Lead"),  # approved
        run("PM: consolidation"),  # returned, below
    ]
    state = {**UX_FLAGS, "runs": runs}
    assert rules.design_step(state) is None  # fully consolidated, falls through to plan

    # PM's consolidation returns to Product Analyst; a new PRD lands.
    state["runs"] = [*state["runs"], run("Product Analyst")]  # PRD v2
    assert rules.design_step(state) == "ux_flow"

    # The flow is rewritten and re-walked, approved again.
    state["runs"] = [*state["runs"], run("UX Designer")]
    assert rules.design_step(state) == "ux_walk"
    state["runs"] = [*state["runs"], run("UX Lead")]
    # UI's own step is now stale: UX Designer is newer than Design Lead's approval.
    assert rules.design_step(state) == "ui_screens"


def test_resume_target_sends_a_ui_feature_back_to_ux_designer_after_a_lead_hits_its_limit():

    state = {
        **UX_FLAGS,
        "status": "definition_ready",
        "runs": [run("Product Analyst"), run("PM"), run("UX Designer"), run("UX Lead")],
        "change_history": [rules.change_entry(PAIR_UX_LEAD, rules.PRD_RETURN, "dead end")],
        "changes_requested": {PAIR_UX_LEAD.key: 2},
        **rules.escalation("returns", rules.limit_reached(PAIR_UX_LEAD, 2, "dead end")),
    }
    resumed = task.apply(state, rules.resumed(state, None))
    assert resumed["changes_requested"][PAIR_UX_LEAD.key] == 0
    assert rules.resume_target(resumed) == "ux_flow"


def test_resume_target_lands_on_ui_screens_after_an_unrelated_failure_there():
    """A UI feature with one UX Lead return (already resolved: UX Designer redid the
    flow, UX Lead approved it), escalated later while at `ui_screens` by something
    unrelated (a role failure, not a pair at its limit): the resume must land back on
    `ui_screens`, not restart the whole branch at `define`."""

    state = {
        **UX_FLAGS,
        "status": "definition_ready",
        "runs": [
            run("Product Analyst"),
            run("PM"),
            run("UX Designer"),
            run("UX Lead"),  # returned
            run("UX Designer"),
            run("UX Lead"),  # approved
        ],
        "change_history": [rules.change_entry(PAIR_UX_LEAD, rules.PRD_RETURN, "dead end")],
        **rules.escalation("failure", "UI Designer failed twice: boom"),
    }
    resumed = task.apply(state, rules.resumed(state, "fixed"))
    assert rules.resume_target(resumed) == "ui_screens"


def test_a_pre_change_document_with_no_design_sections_resumes_into_plan():
    """A document written before this task existed: `definition_ready`, no `flags`
    key at all, so `design_applies` reads `False` and the resume lands exactly where
    it would have before this branch existed."""
    assert rules.resume_target({"status": "definition_ready"}) == "plan"


def test_resume_target_of_a_fresh_touches_ui_task_with_nothing_run_starts_at_ux_flow():
    state = {**UX_FLAGS, "status": "definition_ready"}
    assert rules.resume_target(state) == "ux_flow"


def test_task_cap_adds_one_unit_for_the_design_branch_from_the_start():
    assert rules.work_units(UX_FLAGS) == 1
    assert rules.task_cap(UX_FLAGS, 30, 15, 250) == 45
    # A feature without the flag, a bug, and a platform enhancement: unchanged.
    assert rules.work_units({"type": "feature", "flags": {}}) == 0
    assert rules.work_units({"type": "bug", "flags": {"touches_ui": True}}) == 0
    assert rules.work_units({"type": "platform_enhancement", "flags": {"touches_ui": True}}) == 0
    # Stacks with the subtasks' own units (existing behaviour, untouched): 2 subtasks +
    # 1 integration + 1 design-branch unit + 1 final-validation unit, once there is a
    # plan (ADR-0040, this task's own second unit).
    assert rules.work_units({**UX_FLAGS, **subtasks("api", "domain")}) == 5


def test_design_effect_by_role_counts_lead_returns_and_blocking_items_per_role():

    state = {
        "change_history": [
            rules.change_entry(PAIR_UX_LEAD, rules.PRD_RETURN, "x"),
            rules.change_entry(PAIR_DESIGN_LEAD, rules.PRD_RETURN, "y"),
            rules.change_entry(PAIR_PM_PA, rules.PRD_RETURN, "z"),  # not a design pair
        ],
        "design_items": [
            {"role_slug": "ux_lead", "kind": "blocking", "step": "Sign in", "text": "dead end"},
            {"role_slug": "ux_lead", "kind": "observation", "step": "Pay", "text": "slow"},
            {"role_slug": "design_lead", "kind": "blocking", "step": "Cart", "text": "no state"},
        ],
    }
    assert rules.design_effect_by_role(state) == {"ux_lead": 2, "design_lead": 2}
    assert rules.design_effect_by_role({}) == {}


def test_design_observations_markdown_is_a_table_with_the_cell_escaping_findings_has():
    items = [{"role_slug": "ux_lead", "step": "Pay | Cart", "text": "slow"}]
    rendered = rules.design_observations_markdown(items)
    assert rendered.splitlines()[0] == "| Role | Step | Text |"
    assert "Pay \\| Cart" in rendered


def test_review_kind_is_first_incremental_or_integration():
    # Which review this is (ADR-0016): the integration subtask's is always
    # `integration`; any other is `first` until a return sends it back, then every
    # pass after that is `incremental` (ADR-0015).
    assert rules.review_kind("api", reviewed_before=False) == "first"
    assert rules.review_kind("api", reviewed_before=True) == "incremental"
    assert rules.review_kind(INTEGRATION, reviewed_before=False) == "integration"
    assert rules.review_kind(INTEGRATION, reviewed_before=True) == "integration"


def test_the_verdict_and_the_category_come_from_the_blocking_findings():
    # A review returns only on a blocking finding, and the return takes the category of
    # the first one; anything else approves, with remarks (ADR-0016).
    block = {"severity": "blocking", "category": "spec-behaviour"}
    other = {"severity": "blocking", "category": "standard-seams"}
    note = {"severity": "important", "category": "standard-architecture"}
    assert rules.change_category([]) is None
    assert rules.change_category([note]) is None
    assert rules.change_category([note, block, other]) == "spec-behaviour"


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
    assert rules.change_entry(PAIR, "definition", "no metrics") == {
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
    assert rules.stopped_by_cap({"escalated": reason})
    assert not rules.stopped_by_cap({"escalated": "PM hit 2 returns: x"})
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


def test_run_ended_is_true_only_for_ready_for_review_and_discarded():
    ended = {"ready_for_review", "discarded"}
    for status in get_args(task.Status):
        assert rules.run_ended(status) is (status in ended)


# Pause and resume (ADR-0003, addendum).


def run(role, section=None):
    return {"role": role, "section": section or role, "cost_usd": 0.1}


def test_the_status_before_the_escalation_is_derived_from_the_definition_runs():
    interview = run("PM", "Interview: Product Analyst→PM")
    assert rules.resumed_status({"runs": [run("PjM"), interview]}) == "open"
    assert rules.resumed_status({"runs": [run("Product Analyst")]}) == "defined"
    returned = {"runs": [run("Product Analyst"), run("PM")], "change_history": [DEF]}
    assert rules.resumed_status(returned) == "returned"
    approved = {"runs": [run("Product Analyst"), run("PM")]}
    assert rules.resumed_status(approved) == "definition_ready"
    # The last return, at the limit, is not counted, but the reason says it happened.
    at_limit = {**approved, "escalated": rules.limit_reached(PAIR, 2, "gap")}
    assert rules.resumed_status(at_limit) == "returned"
    # The same, with the class a fresh escalation now carries alongside the text.
    assert rules.resumed_status({**at_limit, "escalation_class": "returns"}) == "returned"
    # A document from before the history has only the counter.
    assert rules.resumed_status({**approved, "changes_requested": {KEY: 1}}) == "returned"
    assert rules.resumed_status(subtasks("api")) == "planned"
    assert rules.resumed_status({"subtasks": {"api": {"status": "delivered"}}}) == "developing"
    # A discard is an end: its runs (a diagnosis, PM's ruling) do not make it a
    # definition ready to plan.
    discarded = {**approved, "status": "discarded", "type": "bug"}
    assert rules.resumed_status(discarded) == "discarded"
    assert rules.resume_target({**discarded, **rules.resumed(discarded, None)}) is None


def test_resuming_clears_the_escalation_resets_the_pair_and_keeps_the_decision():
    state = {
        "status": "waiting_on_human",
        "escalated": "Tech Lead→Developer:api hit 2 returns: still",
        "escalation_class": "returns",
        "questions_for_human": ["Which currency?"],
        "subtasks": {"api": {"id": "api", "status": "returned"}},
    }
    patch = rules.resumed(state, "  Keep USD.\n\n## Why\n\nAudits.  ")
    assert patch["escalated"] is None and patch["status"] == "developing"
    assert patch["escalation_class"] is None
    assert patch["questions_for_human"] == []
    assert patch["changes_requested"] == {"Tech Lead→Developer:api": 0}
    assert patch["sections"]["Human"] == (
        "### Decision 1\n\nEscalated: Tech Lead→Developer:api hit 2 returns: still\n\n"
        "**Questions this answers:**\n\n1. Which currency?\n\n"
        "Keep USD.\n\n### Why\n\nAudits."
    )
    again = rules.human_section({"sections": {"Human": patch["sections"]["Human"]}}, "x", "Go")
    assert again.startswith("### Decision 1") and again.endswith(
        "### Decision 2\n\nEscalated: x\n\nGo"
    )
    assert "changes_requested" not in rules.resumed(
        {"escalated": "PjM stopped by the task cap: 1 of 1"}, None
    )
    assert "sections" not in rules.resumed({}, "   ")


def test_human_section_with_no_questions_is_byte_identical_to_today():
    """The proof nothing observable changed for the ordinary case: a cap escalation and
    a pair-limit escalation never carry `questions_for_human`, so their `## Human` text
    must render exactly as it did before this subtask."""
    state = {"sections": {"Human": "### Decision 1\n\nEscalated: x\n\nGo"}}
    assert rules.human_section(state, "y", "  Do it.  ") == (
        "### Decision 1\n\nEscalated: x\n\nGo\n\n### Decision 2\n\nEscalated: y\n\nDo it."
    )
    assert rules.human_section({}, None, "Do it.") == "### Decision 1\n\nDo it."
    assert rules.human_section({"questions_for_human": []}, None, "Do it.") == (
        "### Decision 1\n\nDo it."
    )


def test_human_section_pairs_each_question_with_the_decision():
    state = {"questions_for_human": ["Which currency?", "Which cap?"]}
    text = rules.human_section(state, "x", "Keep USD.")
    assert text == (
        "### Decision 1\n\nEscalated: x\n\n"
        "**Questions this answers:**\n\n1. Which currency?\n2. Which cap?\n\n"
        "Keep USD."
    )


def test_human_section_demotes_a_question_that_carries_a_heading():
    """A question is system-authored text, rendered through the same `demote()` the
    decision goes through: a `##` line embedded in one, at column 0, must not open a
    section of its own."""
    state = {"questions_for_human": ["Which currency?\n## Follow-up"]}
    text = rules.human_section(state, None, "Go")
    assert "\n### Follow-up" in text
    assert "\n## Follow-up" not in text


def test_human_section_demotes_a_question_that_opens_a_line_with_a_decision_heading():
    """The one shape whose neutralisation actually depends on `demote()` reaching the
    decision count: a question whose second line begins, at column 0, with the literal
    `### Decision `. A quote mid-sentence (below) is already harmless to the
    line-anchored regex without `demote()`'s help; this shape is not — undemoted, it
    would read as a heading of its own and would inflate the next count."""
    state = {"questions_for_human": ["Which cap?\n### Decision 9: already answered?"]}
    text = rules.human_section(state, None, "Go")
    assert "\n#### Decision 9: already answered?" in text
    assert "\n### Decision 9: already answered?" not in text
    again = rules.human_section({"sections": {"Human": text}}, None, "Next")
    assert again.endswith("### Decision 2\n\nNext")


def test_human_section_counts_decisions_with_a_line_anchored_match():
    """A decision (or a question) that merely quotes the literal `### Decision ` inside
    a sentence must not shift the numbering: only a heading at column 0 counts."""
    previous = '### Decision 1\n\nEscalated: x\n\nSee also "### Decision " above.'
    text = rules.human_section({"sections": {"Human": previous}}, None, "Go")
    assert text.endswith("### Decision 2\n\nGo")


def test_human_section_round_trips_through_the_document():
    """Not just a raw-string assertion: a paired-questions decision goes through
    `document.render` and back through `document.parse` and comes back whole, proving
    against the real parser — not a substring check — that the questions block opens no
    section of its own and that a quoted `### Decision ` inside one is not read as a
    heading either."""
    questions = [
        "Which currency?\n## Follow-up",
        'Was it: "### Decision " already answered?',
    ]
    text = rules.human_section({"questions_for_human": questions}, "x", "Keep USD.")

    document_state = {
        "id": "T-1",
        "title": "Pick a currency",
        "description": "Users want a default currency.",
        "sections": {document.HUMAN: text},
    }
    raw = document.render(document_state)
    parsed = document.parse(raw)

    # No stray `##` section: the embedded heading line was demoted before it ever
    # reached the parser, and the quoted "### Decision " never was one.
    assert set(parsed["sections"]) == {document.HUMAN}
    roundtripped = parsed["sections"][document.HUMAN]
    assert roundtripped == text.strip()
    assert "1. Which currency?" in roundtripped
    assert "### Follow-up" in roundtripped
    assert '"### Decision " already answered?' in roundtripped

    # The round-tripped text still counts as exactly one decision: the quoted literal
    # inside it is not mistaken for a second heading by `human_section` either.
    again = rules.human_section({"sections": {document.HUMAN: roundtripped}}, None, "Next")
    assert again.endswith("### Decision 2\n\nNext")


def test_resuming_leaves_the_proposed_invariants_untouched():
    # A resume is not a fresh interview: what the ephemeral proposed across the whole
    # interview (issue #42) survives a resume exactly as it was, for the next retake to
    # seed from; `resumed` never mentions the key, so `apply` keeps it as is.
    assert "proposed_invariants" not in rules.resumed({"proposed_invariants": ["Do X."]}, None)


def test_resuming_leaves_the_integration_to_the_fan_in():
    """Only the fan-in knows what its merges could not settle, so only it may add the
    integration; a resume just points the task at it."""
    state = {"subtasks": {"api": {"status": "approved"}, "domain": {"status": "approved"}}}
    patch = rules.resumed(state, None)
    assert "subtasks" not in patch
    assert rules.resume_target(task.apply(state, patch)) == "integrate"


def test_the_resume_target_follows_the_state():
    assert rules.resume_target({"escalated": "x", "status": "defined"}) is None
    assert rules.resume_target({"status": "open"}) == "classify"
    assert rules.resume_target({"status": "open", "type": "feature"}) == "define"
    # A bug paused in its diagnosis goes back to it (issue #65); a platform enhancement
    # paused in the solution interview with no plan yet goes back to the plan, not to a
    # definition it never had (issue #46).
    assert rules.resume_target({"status": "open", "type": "bug"}) == "diagnose"
    assert rules.resume_target({"status": "open", "type": "platform_enhancement"}) == "plan"
    assert rules.resume_target({"status": "defined"}) == "validate"
    assert rules.resume_target({"status": "returned"}) == "define"
    assert rules.resume_target({"status": "returned", "type": "bug"}) == "diagnose"
    assert rules.resume_target({"status": "definition_ready"}) == "plan"
    # Discarded is an end, like a pull request: nothing left to run.
    assert rules.resume_target({"status": "discarded", "type": "bug"}) is None
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
    assert patch["escalation_class"] == "unmergeable"


def test_conflict_markers_are_found_only_at_the_start_of_a_line():
    assert rules.has_conflict_markers("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> dep\n")
    assert rules.has_conflict_markers("before\n=======\nafter\n")
    assert not rules.has_conflict_markers("no markers here\njust text\n")
    # A marker inside a line, not at its start, is prose (a comment about the format,
    # a shell one-liner), never the leftover git wrote.
    assert not rules.has_conflict_markers("the docs show `<<<<<<< HEAD` as an example\n")


def test_dependency_before_decides_from_the_state_alone_with_no_git():
    contained = task.DependencyState(contained=True, merge_in_progress=False, marked_paths=())
    assert rules.dependency_before(contained) == "skip"
    # A merge in progress wins even when the branch also reads as contained: nothing
    # here calls `merge_keeping_conflicts` on top of an unfinished merge, whatever else
    # is true (this is the resume case: a human's partial fix, or this run's own
    # earlier attempt left the checkout mid-merge).
    resumed = task.DependencyState(contained=True, merge_in_progress=True, marked_paths=("x.py",))
    assert rules.dependency_before(resumed) == "hand_over"
    fresh = task.DependencyState(contained=False, merge_in_progress=False, marked_paths=())
    assert rules.dependency_before(fresh) == "attempt"


def test_dependency_after_attempt_tells_clean_from_conflicted_from_unmergeable():
    clean = task.MergeOutcome(reason=None)
    assert rules.dependency_after_attempt(clean) == "clean"
    conflicted = task.MergeOutcome(reason="CONFLICT (content): x.py", conflicted_paths=("x.py",))
    assert rules.dependency_after_attempt(conflicted) == "hand_over"
    # Refused with nothing for a Developer to resolve (unknown branch, dirty tree, a
    # git error): no conflicted paths at all, so paying an attempt would discover
    # nothing new.
    refused = task.MergeOutcome(reason="fatal: not something we can merge")
    assert rules.dependency_after_attempt(refused) == "unmergeable"


def test_dependency_delivered_needs_ancestry_no_merge_and_no_leftover_markers():
    clean = task.DependencyState(contained=True, merge_in_progress=False, marked_paths=())
    assert rules.dependency_delivered(clean)
    not_landed = task.DependencyState(contained=False, merge_in_progress=False, marked_paths=())
    assert not rules.dependency_delivered(not_landed)
    mid_merge = task.DependencyState(contained=True, merge_in_progress=True, marked_paths=())
    assert not rules.dependency_delivered(mid_merge)
    # Ancestry holds (a `git add` past the marker, then a commit) but the marker is
    # still in the file's own content: not a resolution, whatever git's index says.
    staged_marker = task.DependencyState(
        contained=True, merge_in_progress=False, marked_paths=("x.py",)
    )
    assert not rules.dependency_delivered(staged_marker)


def test_dependency_unresolved_reason_names_which_fact_is_still_unsettled():
    mid_merge = task.DependencyState(contained=False, merge_in_progress=True, marked_paths=())
    assert "in progress" in rules.dependency_unresolved_reason(mid_merge)
    marked = task.DependencyState(contained=True, merge_in_progress=False, marked_paths=("x.py",))
    reason = rules.dependency_unresolved_reason(marked)
    assert "x.py" in reason and "marker" in reason
    missing = task.DependencyState(contained=False, merge_in_progress=False, marked_paths=())
    assert "not" in rules.dependency_unresolved_reason(missing)


def test_merge_notice_names_the_paths_and_forbids_whole_side_strategies():
    notice = rules.merge_notice("domain", "agentic/GH-1.domain", ["pyproject.toml", "x.py"])
    assert "agentic/GH-1.domain" in notice and "domain" in notice
    assert "`pyproject.toml`" in notice and "`x.py`" in notice
    assert "git restore --ours" in notice and "git restore --theirs" in notice
    assert "git checkout --theirs" in notice  # named as denied, never told to widen it
    assert "-s ours" in notice and "-X ours" in notice and "-X theirs" in notice
    assert "checks" in notice and "commit" in notice
    assert "section" in notice  # the conflicted paths and the diff land there


def test_resumed_after_unmergeable_says_why_and_what_may_already_be_done():
    state = {
        "subtasks": {"api": {"conflicted_paths": ["pyproject.toml"]}},
    }
    text = rules.resumed_after_unmergeable(state, "api", "agentic/GH-1.api", "abc123")
    assert "agentic/GH-1.api" in text and "abc123" in text
    assert "pyproject.toml" in text
    assert "human" in text
    assert "do not go looking for work to redo" in text.lower() or "redo" in text


def test_a_setup_step_that_fails_escalates_with_its_own_class():
    from agentic.core.process.domain.roles import DEVELOPER

    patch = rules.setup_unprepared(DEVELOPER, "agentic/GH-1.api", "deps", "uv sync", "boom")
    assert patch["status"] == "waiting_on_human" and patch["current_role"] == "Developer"
    assert "setup step 'deps' failed on 'agentic/GH-1.api'" in patch["escalated"]
    assert "uv sync" in patch["escalated"] and "boom" in patch["escalated"]
    assert patch["escalation_class"] == "setup"


# The findings table (issue #42).


def found(claim="double count", severity="blocking", scope="subtask", evidence="$ pytest\nF"):
    return {
        "location": "x.py:1",
        "claim": claim,
        "evidence": evidence,
        "severity": severity,
        "scope": scope,
        "category": "spec-behaviour",
    }


def test_a_finding_nobody_can_check_is_dismissed_on_the_spot():
    """An observation with no evidence is either the reviewer's hunch or a blocking the
    system lowered for having none. Neither is a live objection for the next role."""
    records = rules.finding_records(
        "api", 1, [found(), found(claim="maybe", severity="observation", evidence="  ")]
    )
    assert [r["status"] for r in records] == ["open", "dismissed"]
    # No evidence on the record: it is the command and its output, it already lives in
    # the review's section, and a copy per finding per pass only grows the document.
    assert records[0] == {
        "subtask": "api",
        "version": 1,
        "location": "x.py:1",
        "claim": "double count",
        "severity": "blocking",
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
    }


def test_the_first_verdict_on_a_finding_is_the_one_that_stands():
    """Same location and same claim is one row. A later review raising what an earlier
    one dismissed does not reopen the argument, whichever subtask raises it."""
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found(severity="observation", evidence="")]),
            *rules.finding_records("domain", 1, [found()]),
        ]
    }
    (row,) = rules.findings_rows(state)
    # One row, and it names every subtask that raised it: the prompts route on that
    # column ("a row about another subtask" vs "a row about yours").
    assert (row["status"], row["subtask"]) == ("dismissed", "api, domain")
    assert rules.open_findings(state) == []


def test_the_same_objection_written_twice_is_one_row():
    """Two reviewer runs do not write the same string. A trailing space or a capital must
    not split the row the whole feature exists to collapse."""
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records(
                "domain", 1, [{**found(), "location": "x.py:1 ", "claim": "Double  count"}]
            ),
        ]
    }
    (row,) = rules.findings_rows(state)
    assert row["subtask"] == "api, domain"


def test_only_a_reviewer_saying_so_closes_a_row():
    """A pass that stops mentioning a row usually means it did not look: every pass after
    the first is incremental and is told not to re-read what it approved (ADR-0015).
    Closing on silence would make the first return of a subtask wipe its carried remarks,
    which is the exact class of thing issue #17 is about."""
    silent = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records("api", 2, [found(claim="something else")]),
        ]
    }
    rows = {row["claim"]: row["status"] for row in rules.findings_rows(silent)}
    assert rows == {"double count": "open", "something else": "open"}

    said = {**silent, "settled_findings": ["x.py:1"]}
    assert {row["status"] for row in rules.findings_rows(said)} == {"fixed"}


def test_the_integration_review_can_close_what_a_subtask_review_could_not():
    """A `plan` row is by definition something no single Developer could fix, so the
    subtask that raised it is never reviewed again. The integration review reads the
    whole branch and is the one that settles it."""
    raised = rules.finding_records("api", 1, [found(scope="plan", severity="important")])
    state = {"findings": raised, "settled_findings": []}
    assert rules.open_findings(state)
    state["settled_findings"] = ["x.py:1"]
    assert rules.open_findings(state) == []


def test_what_the_roles_read_is_the_open_rows_with_the_plan_ones_first():
    """The plan-scoped ones lead: no single Developer can fix one, so they are what the
    next wave and the integrator have to act on. Within a scope, worst first."""
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found(claim="a", severity="important")]),
            *rules.finding_records("api", 1, [found(claim="b")]),
            *rules.finding_records(
                "domain", 1, [found(claim="c", severity="important", scope="plan")]
            ),
            *rules.finding_records("domain", 1, [found(claim="d", severity="observation")]),
        ]
    }
    assert [row["claim"] for row in rules.open_findings(state)] == ["c", "b", "a", "d"]

    table = rules.findings_section(state)[FINDINGS]
    # No status column: every row here is open, and a cell that always says the same
    # thing is context three roles pay for and nobody reads.
    assert table.startswith("| Severity | Scope | Category | Subtask | Location | Claim |")
    assert "| important | plan | spec-behaviour | domain | `x.py:1` | c |" in table
    assert rules.findings_section({})[FINDINGS] == "None open."


def test_a_claim_with_a_pipe_or_a_newline_does_not_break_the_table():
    state = {"findings": rules.finding_records("api", 1, [found(claim="a | b\nand c")])}
    (line,) = rules.findings_section(state)[FINDINGS].splitlines()[2:]
    assert line.endswith(r"| a \| b and c |")


def test_the_integration_runs_a_developer_only_when_there_is_something_to_do():
    """The system merges each approved subtask as it is approved, so joining them is not
    a role's job any more. The integration review of the whole branch always is."""
    plain = rules.integration_subtask()
    assert plain["status"] == "delivered" and plain["conflicts"] == []
    assert rules.integration_subtask(conflicts=["agentic/GH-1.api"])["status"] == "pending"
    declared = rules.integration_subtask("Wire the two ends.")
    assert declared["status"] == "pending" and "Wire the two ends." in declared["brief"]
    both = rules.integration_subtask("Wire it.", ["agentic/GH-1.api"])
    assert "Wire it." in both["brief"] and "`agentic/GH-1.api`" in both["brief"]


def test_the_invariants_reach_a_prompt_as_a_list():
    state = {"invariants": ["fold duplicates", "no relative imports"]}
    assert rules.invariants_markdown(state) == "- fold duplicates\n- no relative imports"
    assert rules.invariants_markdown({}) == "- none"


def test_an_integration_with_nothing_to_do_enters_at_its_review():
    """`delivered` is not a lie: the fan-in delivered it, merge by merge. What matters is
    that the pipeline sends it to `review` and no integrator is paid for a merge already
    done. A conflict, or work the plan declared, sends it to `develop` instead."""
    plain = {INTEGRATION: rules.integration_subtask()}
    assert rules.dispatched_subtasks({"subtasks": plain}) == [INTEGRATION]
    assert rules.pipeline_entry({"subtasks": plain, "subtask_id": INTEGRATION}) == "review"

    work = {INTEGRATION: rules.integration_subtask(conflicts=["agentic/GH-1.api"])}
    assert rules.pipeline_entry({"subtasks": work, "subtask_id": INTEGRATION}) == "develop"


def test_a_returned_integration_stops_saying_there_was_nothing_to_do():
    """The review sent it back, so an integrator runs. A brief still reading "everything
    is already merged" contradicts why it was woken up."""
    nothing = rules.integration_subtask()
    assert "already on the task branch" in nothing["brief"]
    now = rules.integration_work("Wire the two ends.", ["agentic/GH-1.api"])
    assert "Wire the two ends." in now["brief"] and "`agentic/GH-1.api`" in now["brief"]
    assert now["conflicts"] == ["agentic/GH-1.api"]


# The motive of a failed attempt (issue #66, `docs/product/role-failures.md`): a report
# never yields an empty diagnostic, and `success` never names a cause.

LIMIT_PROSE = "API Error: You've hit your session limit · resets 11:20pm"


def test_failure_motive_leads_with_the_result_prose_and_names_neither_success_nor_empty():
    # The 2026-09-17 incident: `subtype="success"`, `errors=[]`, the limit in `result`.
    report = rules.ReportedFailure(subtype="success", errors=(), result=LIMIT_PROSE)
    motive = rules.failure_motive(report)
    assert LIMIT_PROSE in motive
    assert "success" not in motive
    assert motive.strip() and not motive.rstrip().endswith(":")


def test_failure_motive_falls_back_to_the_errors_when_there_is_no_result():
    report = rules.ReportedFailure(subtype="error", errors=("boom", " also this "))
    assert rules.failure_motive(report) == "boom; also this (subtype=error)"


def test_failure_motive_drops_an_errors_entry_that_repeats_the_result_prose():
    report = rules.ReportedFailure(result="API Error: boom", errors=("API Error: boom", "extra"))
    assert rules.failure_motive(report) == "API Error: boom; extra"


def test_failure_motive_a_whitespace_only_result_falls_back_to_errors():
    report = rules.ReportedFailure(result="   ", errors=("real cause",))
    assert rules.failure_motive(report) == "real cause"


def test_failure_motive_appends_metadata_in_order():
    report = rules.ReportedFailure(
        subtype="error_during_execution",
        result="broke",
        terminal_reason="api_error",
        api_error_status=529,
        exit_code=1,
    )
    assert rules.failure_motive(report) == (
        "broke (subtype=error_during_execution, terminal reason: api_error, HTTP 529, exit code 1)"
    )


def test_failure_motive_tier_two_names_the_metadata_when_there_is_no_cause():
    report = rules.ReportedFailure(terminal_reason="api_error", api_error_status=529)
    assert (
        rules.failure_motive(report)
        == "the SDK reported no message (terminal reason: api_error, HTTP 529)"
    )


def test_failure_motive_tier_three_names_the_exit_code_when_that_is_all_there_is():
    with_code = rules.ReportedFailure(exit_code=1)
    assert rules.failure_motive(with_code) == "the SDK ended with exit code 1 and reported no cause"
    # The `ResultMessage` site structurally carries none: no formatted `None`.
    without_code = rules.ReportedFailure()
    assert rules.failure_motive(without_code) == "the SDK reported no cause and no exit code"


def test_max_turns_motive_is_bare_when_the_sdk_reports_nothing_beyond_the_subtype():
    # `subtype` is always `error_max_turns` here — that is how the runner knows to call
    # this — so repeating it would be the same tautology issue #66 fixed for a
    # different field (issues #69, #81): tier 3 of the definition, the fixed sentence
    # standing alone, not "max_turns (N) exhausted...: the SDK reported no cause...".
    report = rules.ReportedFailure(subtype="error_max_turns")
    assert rules.max_turns_motive(30, report) == "max_turns (30) exhausted without a result"


def test_max_turns_motive_carries_the_sdks_prose():
    report = rules.ReportedFailure(subtype="error_max_turns", result="wrote a heredoc; denied")
    assert rules.max_turns_motive(30, report) == (
        "max_turns (30) exhausted without a result: wrote a heredoc; denied"
    )


# `failure_reason` (ADR-0029): pins the pair `(failure_reason(report), failure_motive(report))`
# from one `ReportedFailure` per real payload, read verbatim from the task document's own
# request and from ADR-0029 itself -- `.agentic/tasks/` is untracked in this checkout
# (ADR-0020), so GH-31's and GH-73's log lines come from the original request quoted in this
# task's document, and GH-32's from ADR-0029's own "El clasificador es una función pura del
# dominio" section. `error_max_budget_usd` is the shape `test_runner.py:220` and `:514` raise.


def test_failure_reason_gh31_session_limit_429_is_account_limit():
    # `~/.agentic-runs/GH-31-20260921-172042.log`: "You've hit your session limit ·
    # resets 7:30pm ... (terminal reason: api_error, HTTP 429, exit code 1)". The
    # population `_metadata_core` reports no subtype for is `subtype="success"`
    # (ADR-0029): omitted from the motive's metadata, never a `subtype` clause match.
    report = rules.ReportedFailure(
        subtype="success",
        result="You've hit your session limit · resets 7:30pm",
        terminal_reason="api_error",
        api_error_status=429,
        exit_code=1,
    )
    assert rules.failure_reason(report) == "account_limit"
    assert rules.failure_motive(report) == (
        "You've hit your session limit · resets 7:30pm "
        "(terminal reason: api_error, HTTP 429, exit code 1)"
    )


def test_failure_reason_gh73_529_overloaded_is_upstream():
    # `~/.agentic-runs/GH-73-resume-20260921-220410.log`: "API Error: 529 Overloaded
    # ... (terminal reason: api_error, HTTP 529, exit code 1)".
    report = rules.ReportedFailure(
        subtype="success",
        result="API Error: 529 Overloaded",
        terminal_reason="api_error",
        api_error_status=529,
        exit_code=1,
    )
    assert rules.failure_reason(report) == "upstream"
    assert rules.failure_motive(report) == (
        "API Error: 529 Overloaded (terminal reason: api_error, HTTP 529, exit code 1)"
    )


def test_failure_reason_gh32_structured_output_exhaustion_is_schema():
    # ADR-0029: "Failed to provide valid structured output after 5 attempts
    # (subtype=error_max_structured_output_retries, terminal reason:
    # structured_output_retry_exhausted, exit code 1)" -- a relabelling of what the
    # glossary's Fallo de rol already names, not a new value (`reason` was `sdk`).
    report = rules.ReportedFailure(
        subtype="error_max_structured_output_retries",
        result="Failed to provide valid structured output after 5 attempts",
        terminal_reason="structured_output_retry_exhausted",
        exit_code=1,
    )
    assert rules.failure_reason(report) == "schema"
    assert rules.failure_motive(report) == (
        "Failed to provide valid structured output after 5 attempts "
        "(subtype=error_max_structured_output_retries, "
        "terminal reason: structured_output_retry_exhausted, exit code 1)"
    )


def test_failure_reason_error_max_budget_usd_is_limit():
    # `test_runner.py:220` and `:514`: the two byte-identical assertions this task must
    # not touch. Pinned here too, off the same `ReportedFailure` shape.
    report = rules.ReportedFailure(subtype="error_max_budget_usd", errors=("over budget",))
    assert rules.failure_reason(report) == "limit"
    assert rules.failure_motive(report) == "over budget (subtype=error_max_budget_usd)"


def test_failure_reason_rejects_limit_worded_prose_with_no_status_as_sdk():
    # The account_limit clause reads `api_error_status`, never prose: the same words
    # with no status attached must not classify as a limit.
    report = rules.ReportedFailure(subtype="success", result=LIMIT_PROSE)
    assert rules.failure_reason(report) == "sdk"


def test_failure_reason_rejects_499_as_sdk():
    report = rules.ReportedFailure(api_error_status=499)
    assert rules.failure_reason(report) == "sdk"


def test_failure_reason_rejects_600_as_sdk():
    report = rules.ReportedFailure(api_error_status=600)
    assert rules.failure_reason(report) == "sdk"


def test_failure_reason_null_subtype_with_429_is_account_limit():
    # The latent break ADR-0029 names: `subtype=None` used to raise `TypeError` inside
    # `except ResultError`. `report.subtype or ""` -- the `ResultMessage` site's own
    # guard, verbatim -- makes this classify instead.
    report = rules.ReportedFailure(subtype=None, api_error_status=429)
    assert rules.failure_reason(report) == "account_limit"


def test_failure_reason_null_subtype_with_nothing_is_sdk():
    report = rules.ReportedFailure(subtype=None)
    assert rules.failure_reason(report) == "sdk"


def test_failure_reason_budget_subtype_wins_over_upstream_status():
    # Pins the precedence instead of leaving it a docstring claim: a 529 report that
    # also carries a budget subtype is still `limit`, first.
    report = rules.ReportedFailure(subtype="error_max_budget_usd", api_error_status=529)
    assert rules.failure_reason(report) == "limit"


def test_failure_reason_gh19_api_error_with_no_status_is_network():
    # apostrophe GH-19: "API Error: Can't reach the API server -- check your internet
    # or DNS (EAI_AGAIN)", a `ResultError` with no HTTP status to read at all (issue
    # #137). Never read from the prose: only `terminal_reason` and the absent status.
    report = rules.ReportedFailure(
        subtype="success",
        result="API Error: Can't reach the API server — check your internet or DNS (EAI_AGAIN)",
        terminal_reason="api_error",
        exit_code=1,
    )
    assert rules.failure_reason(report) == "network"


def test_failure_reason_api_error_with_a_status_is_not_network():
    # The declared seam's rejected input: the same `terminal_reason="api_error"` the
    # network clause looks at, but with a status present, must still read as whatever
    # status clause matches -- never fall through to `network`.
    report = rules.ReportedFailure(terminal_reason="api_error", api_error_status=429)
    assert rules.failure_reason(report) == "account_limit"


def test_max_turns_motive_carries_metadata_when_there_is_no_prose():
    report = rules.ReportedFailure(subtype="error_max_turns", terminal_reason="api_error")
    assert rules.max_turns_motive(12, report) == (
        "max_turns (12) exhausted without a result: "
        "the SDK reported no message (terminal reason: api_error)"
    )


def test_exception_motive_reads_the_message_or_names_the_type():
    assert rules.exception_motive(ValueError("be humble")) == "be humble"
    assert rules.exception_motive(RuntimeError()) == "RuntimeError, with no message"
    assert rules.exception_motive(RuntimeError("  ")) == "RuntimeError, with no message"


def test_motive_collapses_crlf_and_drops_control_characters_but_keeps_newline_and_tab():
    raw = "line one\r\nline\x07 two\twith a tab"
    assert rules.motive(raw, "other") == "line one\nline two\twith a tab"


def test_motive_neutralises_a_triple_backtick_so_no_fence_stays_open():
    raw = "before ```danger\ncode``` after"
    result = rules.motive(raw, "other")
    assert "```" not in result
    assert "before" in result and "danger" in result and "after" in result


def test_motive_backstops_an_empty_or_colon_ending_text_keeping_the_prefix():
    assert rules.motive("", "sdk") == "no cause reported (reason: sdk)"
    assert rules.motive("   ", "sdk") == "no cause reported (reason: sdk)"
    assert rules.motive("run failed (success): ", "sdk") == (
        "run failed (success): no cause reported (reason: sdk)"
    )
    assert rules.motive("run failed (success):", "sdk") == (
        "run failed (success): no cause reported (reason: sdk)"
    )
    # A text that already names a cause is left alone.
    assert rules.motive("boom (subtype=error)", "sdk") == "boom (subtype=error)"


def test_motive_caps_a_long_text_at_2000_keeping_head_and_tail():
    text = ("A" * 5000) + "B" * 5000
    capped = rules.motive(text, "other")
    assert len(capped) < len(text)
    assert capped.startswith("A" * 1600)
    assert capped.endswith("B" * 300)
    assert "characters elided" in capped


def test_stopped_by_cap_prefers_the_escalation_class_when_the_state_carries_one():
    reason = rules.cap_reached("Developer", 106.27, 100)
    assert rules.stopped_by_cap({"escalated": reason, "escalation_class": "cap"})


def test_stopped_by_cap_is_false_when_the_class_says_something_else_even_if_the_text_matches():
    reason = rules.cap_reached("Developer", 106.27, 100)
    assert not rules.stopped_by_cap({"escalated": reason, "escalation_class": "returns"})


def test_stopped_by_cap_falls_back_to_the_text_when_the_state_carries_no_class():
    reason = rules.cap_reached("Developer", 106.27, 100)
    assert rules.stopped_by_cap({"escalated": reason})
    assert not rules.stopped_by_cap({"escalated": "PM hit 2 returns: x"})
    assert not rules.stopped_by_cap({})


def test_system_note_wraps_the_text_in_italics():
    # The one helper every system-written line goes through (denial_note's,
    # cold_fallback_note's, and turn_notice.notice_note's).
    assert rules.system_note("x happened") == "_x happened_"


def test_denial_note_names_the_count_and_the_role_rule():
    # Glossary: Negación — one line, system-written, with how many and the rule.
    note = rules.denial_note(PRODUCT_ANALYST_BUG, 8)
    assert note == f"_The system denied 8 write attempts: {denial_for(PRODUCT_ANALYST_BUG)}_"


def test_denial_note_uses_the_singular_for_one():
    note = rules.denial_note(PJM, 1)
    assert "1 write attempt:" in note
    assert "1 write attempts:" not in note


def test_interview_participant_slugs_match_the_interview_specs():
    # The hardcoded set must not drift from the specs it is meant to mirror
    # (`domain/roles.py`'s `Interview` objects): every seat of every interview, by slug.
    specs = roles.INTERVIEWS
    seats = {
        role.slug
        for spec in specs
        for role in (spec.interviewer, spec.interviewee, spec.consultant)
        if role is not None
    }
    assert rules.INTERVIEW_PARTICIPANT_SLUGS == seats


def test_turn_cut_message_orders_the_commit_before_the_delivery_for_an_executing_role():
    message = rules.turn_cut_retry_message(DEVELOPER)
    assert "turn cap ended your previous attempt" in message
    commit_at = message.index("Commit what you have")
    deliver_at = message.index("deliver anyway")
    assert commit_at < deliver_at
    assert "checks are red" in message
    assert "name the gap in `summary`" in message
    # Never a branch or a head: that fact belongs to `returned_developer_delta` alone.
    assert "branch" not in message and "head" not in message


def test_turn_cut_message_tells_an_interview_participant_to_close_the_turn():
    message = rules.turn_cut_retry_message(TECH_LEAD_EPHEMERAL)
    assert "Close this turn with what you have." in message
    assert "Commit" not in message
    assert "verdict" not in message


def test_turn_cut_message_defaults_to_not_delivering_an_unreached_verdict():
    # `product_analyst_bug` is the role this arm exists for
    # (`docs/product/role-failures.md`): a spent budget must never read as a ruling.
    message = rules.turn_cut_retry_message(PRODUCT_ANALYST_BUG)
    assert "Do not deliver a verdict you have not reached." in message
    assert "say in your own output what you could not read" in message
    assert "Commit" not in message
    assert "Close this turn" not in message


def test_turn_cut_message_never_asserts_a_branch_or_a_head():
    for role in (DEVELOPER, TECH_LEAD_EPHEMERAL, PRODUCT_ANALYST_BUG):
        message = rules.turn_cut_retry_message(role)
        assert "branch" not in message
        assert "head" not in message


# Archiving (ADR-0011, sixth addendum): `--fresh` archives a previous run instead of
# building on top of it. `archive_prefix` and `archive_ref` are the naming rule;
# `branches_of_task` is the segment-exact predicate every caller of a `Workspace`
# prefix verb must go through; `next_archive_run` is the three-case rule for `N`.


def test_archive_prefix_names_the_run_and_the_system_commit():
    assert rules.archive_prefix(4, "75de807") == "archive/run4-75de807"


def test_archive_prefix_falls_back_to_the_literal_unknown():
    # A document that could not be read or parsed decides nothing about `N`: it only
    # ever changes this literal (ADR-0011, sixth addendum).
    assert rules.archive_prefix(1, "unknown") == "archive/run1-unknown"


def test_archive_ref_keeps_the_whole_branch_name():
    assert rules.archive_ref("agentic/GH-12", 4, "75de807") == "archive/run4-75de807/agentic/GH-12"
    assert (
        rules.archive_ref("agentic/GH-12.api", 4, "75de807")
        == "archive/run4-75de807/agentic/GH-12.api"
    )


def test_branches_of_task_compares_whole_segments_never_startswith():
    # gym-saas is at GH-88: `agentic/GH-1` and `agentic/GH-12` coexist today, and an
    # unfiltered prefix verb would let `--fresh` on GH-1 rename GH-12's live branch.
    names = ["agentic/GH-1", "agentic/GH-1.api", "agentic/GH-12", "agentic/GH-12.domain"]
    assert rules.branches_of_task(names, "GH-1") == ["agentic/GH-1", "agentic/GH-1.api"]
    assert rules.branches_of_task(names, "GH-12") == ["agentic/GH-12", "agentic/GH-12.domain"]


def test_branches_of_task_ignores_an_unrelated_branch():
    assert rules.branches_of_task(["main", "feature/x"], "GH-1") == []


def test_next_archive_run_is_one_past_the_highest_archived_when_the_task_branch_is_live():
    # The hand-made gym-saas GH-12 refs, without the `agentic/` prefix: the fourth run
    # of GH-12 would be `run4`, exactly as the ADR's own worked example says.
    refs = ["archive/run2-04abd2c/GH-12", "archive/run3-75de807/GH-12"]
    assert rules.next_archive_run(refs, ["agentic/GH-12"], "GH-12") == 4


def test_next_archive_run_recognises_the_prefixed_spelling_too():
    refs = ["archive/run1-abc1234/agentic/GH-12", "archive/run2-def5678/agentic/GH-12.api"]
    assert rules.next_archive_run(refs, ["agentic/GH-12"], "GH-12") == 3


def test_next_archive_run_reuses_n_when_only_a_subtask_branch_is_still_live():
    # An archive cut short after the task branch was renamed but before its subtask
    # branch was: `agentic/<task>` absent, `agentic/<task>.<sub>` still live.
    refs = ["archive/run4-abc1234/agentic/GH-12"]
    live = ["agentic/GH-12.api"]
    assert rules.next_archive_run(refs, live, "GH-12") == 4


def test_next_archive_run_is_none_when_neither_branch_is_live_nor_archived():
    assert rules.next_archive_run([], [], "GH-12") is None
    assert rules.next_archive_run(["archive/run1-x/GH-1"], [], "GH-12") is None


def test_next_archive_run_does_not_confuse_gh_1_with_gh_12():
    # Both the archived refs and the live branches must be filtered by whole segment,
    # not by `startswith`, the same predicate `branches_of_task` enforces elsewhere.
    refs = ["archive/run1-abc1234/GH-12"]
    live = ["agentic/GH-12"]
    assert rules.next_archive_run(refs, live, "GH-1") is None


# The poller's own pure core (issue #137): `rules.logged`, `rules.auto_resumable`,
# `rules.auto_resume_limit`, `rules.next_attempt_at`, `rules.blocker_resolved` and
# `rules.poll_plan`. `rules.escalation` and `rules.unmergeable` are untouched; `logged`
# is the only place a `pause_log` entry is ever appended.


def test_logged_appends_one_escalation_entry_reading_the_patchs_own_class():
    patch = {"status": "waiting_on_human"} | rules.escalation("cap", "PjM stopped: 1 of 1")
    logged = rules.logged(patch)
    assert logged["pause_log"] == [{"kind": "escalation", "value": "cap"}]
    # Everything else in the patch travels untouched.
    assert logged["escalated"] == "PjM stopped: 1 of 1" and logged["status"] == "waiting_on_human"


def test_logged_folds_failures_before_the_escalation_entry_in_order():
    patch = rules.escalation("failure", "Developer failed 2 times: network down")
    failures = [
        {
            "role": "Developer",
            "attempt": 1,
            "error": "x",
            "cost_usd": 0.1,
            "session_id": "",
            "reason": "network",
            "section": "Developer: api",
        },
        {
            "role": "Developer",
            "attempt": 2,
            "error": "y",
            "cost_usd": 0.2,
            "session_id": "",
            "reason": "account_limit",
            "section": "Developer: api",
        },
    ]
    logged = rules.logged(patch, failures)
    assert logged["pause_log"] == [
        {"kind": "failure", "value": "network", "section": "Developer: api"},
        {"kind": "failure", "value": "account_limit", "section": "Developer: api"},
        {"kind": "escalation", "value": "failure"},
    ]


def test_logged_defaults_a_legacy_failure_with_no_reason_to_other():
    failures = [{"role": "PM", "attempt": 1, "error": "x", "cost_usd": 0.0, "session_id": ""}]
    logged = rules.logged(rules.escalation("failure", "PM failed 2 times: x"), failures)
    assert logged["pause_log"][0] == {"kind": "failure", "value": "other", "section": ""}


def test_logged_with_unmergeable_has_no_failures_since_no_role_ran():
    patch = rules.unmergeable("api", "domain", "CONFLICT (content): x.py")
    logged = rules.logged(patch)
    assert logged["pause_log"] == [{"kind": "escalation", "value": "unmergeable"}]


def _failure_entry(reason: str, section: str = "Developer: api") -> dict:
    return {"kind": "failure", "value": reason, "section": section}


def _escalation_entry(cls: str) -> dict:
    return {"kind": "escalation", "value": cls}


def test_auto_resumable_is_the_reason_of_an_account_limit_pause():
    state = {"pause_log": [_failure_entry("account_limit"), _escalation_entry("failure")]}
    assert rules.auto_resumable(state) == "account_limit"


def test_auto_resumable_is_none_for_questions():
    # A `questions`-class escalation never carries a `"failure"` entry at all: no role
    # broke, an interviewee simply ran out of things it could decide.
    state = {"pause_log": [_escalation_entry("questions")]}
    assert rules.auto_resumable(state) is None


def test_auto_resumable_is_none_when_the_class_is_not_failure():
    # Rejected input: a `"failure"` entry sits beside an `"escalation"` entry that is
    # not `"failure"` -- e.g. the task cap landed right after an external failure.
    state = {"pause_log": [_failure_entry("upstream"), _escalation_entry("cap")]}
    assert rules.auto_resumable(state) is None


def test_auto_resumable_is_none_when_a_failure_reason_is_not_external():
    # Rejected input: a genuine `sdk` break alongside an external one in the same
    # epoch must not read as auto-resumable.
    state = {
        "pause_log": [
            _failure_entry("account_limit"),
            _failure_entry("sdk"),
            _escalation_entry("failure"),
        ]
    }
    assert rules.auto_resumable(state) is None


def test_auto_resumable_returns_the_most_recent_failures_reason():
    state = {
        "pause_log": [
            _failure_entry("account_limit"),
            _failure_entry("upstream"),
            _escalation_entry("failure"),
        ]
    }
    assert rules.auto_resumable(state) == "upstream"


def test_auto_resumable_only_reads_the_current_epoch_after_the_last_resumed():
    # An old epoch's external failure must not leak into a fresh one that escalated on
    # `questions` after a human already resumed the task once.
    state = {
        "pause_log": [
            _failure_entry("account_limit"),
            _escalation_entry("failure"),
            {"kind": "resumed"},
            _escalation_entry("questions"),
        ]
    }
    assert rules.auto_resumable(state) is None


def test_auto_resume_limit_is_one_inside_an_interview_section():
    section = INTERVIEW_SOLUTION.section
    state = {"pause_log": [_failure_entry("account_limit", section=section)]}
    assert rules.auto_resume_limit(state) == 1


def test_auto_resume_limit_is_six_outside_an_interview_section():
    state = {"pause_log": [_failure_entry("account_limit", section="Developer: api")]}
    assert rules.auto_resume_limit(state) == 6


def test_next_attempt_at_is_none_before_its_own_slot_elapses():
    since = datetime(2026, 10, 3, 19, 30)
    now = datetime(2026, 10, 3, 19, 40)  # 10 of the first slot's 15 minutes
    assert rules.next_attempt_at(since, prior=0, limit=6, now=now) is None


def test_next_attempt_at_is_due_once_its_own_slot_elapses():
    since = datetime(2026, 10, 3, 19, 30)
    now = datetime(2026, 10, 3, 19, 46)
    assert rules.next_attempt_at(since, prior=0, limit=6, now=now) == datetime(2026, 10, 3, 19, 45)


def test_next_attempt_at_is_none_once_the_calendar_is_spent():
    since = datetime(2026, 10, 3, 19, 30)
    now = datetime(2026, 10, 4, 19, 30)  # a full day later, no slot left to run out
    assert rules.next_attempt_at(since, prior=1, limit=1, now=now) is None


def test_blocker_resolved_not_planned_resolves_without_a_merge():
    assert rules.blocker_resolved("NOT_PLANNED", merged_into_checkout=False) is True


def test_blocker_resolved_completed_needs_the_merge_in_the_checkout():
    assert rules.blocker_resolved("COMPLETED", merged_into_checkout=True) is True
    assert rules.blocker_resolved("COMPLETED", merged_into_checkout=False) is False


def test_blocker_resolved_rejects_open_and_unknown_state_reasons():
    assert rules.blocker_resolved("OPEN", merged_into_checkout=True) is False
    assert rules.blocker_resolved(None, merged_into_checkout=True) is False
    assert rules.blocker_resolved("SOME_FUTURE_REASON", merged_into_checkout=True) is False


NOW = datetime(2026, 10, 3, 20, 0)


def test_poll_plan_closes_everything_lowest_first_and_never_spends_a_slot():
    actions = rules.poll_plan(closes=[9, 3], resumes=[], launches=[], available_slots=0, now=NOW)
    assert actions == [rules.PollAction("close", 3), rules.PollAction("close", 9)]


def test_poll_plan_resumes_a_due_account_limit_pause():
    due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW.replace(hour=0), prior=0, limit=6
    )
    actions = rules.poll_plan(closes=[], resumes=[due], launches=[], available_slots=1, now=NOW)
    assert actions == [rules.PollAction("resume", 5)]


def test_poll_plan_does_not_resume_a_pause_still_waiting_out_its_own_calendar():
    # Escalated one minute ago: its own 15-minute first slot has not elapsed yet.
    not_due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(minutes=1), prior=0, limit=6
    )
    actions = rules.poll_plan(closes=[], resumes=[not_due], launches=[], available_slots=1, now=NOW)
    assert actions == []


def test_poll_plan_caps_resumes_at_available_slots_lowest_issue_first():
    since = NOW.replace(hour=0)
    a = rules.ResumeCandidate(issue=20, reason="upstream", since=since, prior=0, limit=6)
    b = rules.ResumeCandidate(issue=5, reason="upstream", since=since, prior=0, limit=6)
    actions = rules.poll_plan(closes=[], resumes=[a, b], launches=[], available_slots=1, now=NOW)
    assert actions == [rules.PollAction("resume", 5)]


def test_poll_plan_launches_the_lowest_unblocked_issue():
    blocked = rules.LaunchCandidate(issue=4, blocked=True, since=None, prior=0, limit=6)
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[blocked, ready], available_slots=1, now=NOW
    )
    assert actions == [rules.PollAction("launch", 9)]


def test_poll_plan_does_not_launch_an_issue_with_an_open_blocker():
    # Rejected input: the one candidate there is stays blocked -- nothing launches.
    blocked = rules.LaunchCandidate(issue=4, blocked=True, since=None, prior=0, limit=6)
    actions = rules.poll_plan(closes=[], resumes=[], launches=[blocked], available_slots=1, now=NOW)
    assert actions == []


def test_poll_plan_launches_nothing_when_the_cap_is_full():
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(closes=[], resumes=[], launches=[ready], available_slots=0, now=NOW)
    assert actions == []


def test_poll_plan_does_not_launch_an_issue_whose_own_wait_has_not_elapsed():
    # Rejected input: the only launch candidate there is was attempted a minute ago,
    # its own first 15-minute slot has not elapsed yet -- nothing launches this round.
    not_due = rules.LaunchCandidate(
        issue=9, blocked=False, since=NOW - timedelta(minutes=1), prior=0, limit=6
    )
    actions = rules.poll_plan(closes=[], resumes=[], launches=[not_due], available_slots=1, now=NOW)
    assert actions == []


def test_poll_plan_does_not_launch_an_issue_whose_own_calendar_is_spent():
    # Rejected input: every slot of this issue's own calendar already ran out
    # (`prior == limit`) -- excluded until a human acts, per "al llegar al límite".
    exhausted = rules.LaunchCandidate(
        issue=9, blocked=False, since=NOW - timedelta(days=1), prior=6, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[exhausted], available_slots=1, now=NOW
    )
    assert actions == []


def test_poll_plan_launches_even_though_an_exhausted_resume_elsewhere_cannot_retry():
    # A resume whose own calendar is spent (`prior >= limit`) is not "an external wait
    # not yet elapsed": it needs a human now, like any other escalation, and must not
    # freeze every launch in the product until one acts.
    exhausted = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(days=1), prior=6, limit=6
    )
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[exhausted], launches=[ready], available_slots=1, now=NOW
    )
    assert actions == [rules.PollAction("launch", 9)]


def test_poll_plan_launches_nothing_while_a_pause_elsewhere_still_waits():
    # No se lanza nada mientras haya una espera externa sin vencer en el producto,
    # whether or not a slot is free for the launch.
    not_due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(minutes=1), prior=0, limit=6
    )
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[not_due], launches=[ready], available_slots=1, now=NOW
    )
    assert actions == []


def test_poll_plan_called_twice_with_the_same_closes_never_doubles_the_action():
    # `poll_plan` is pure and stateless: not relaunching an already-registered issue is
    # the caller's own bookkeeping (the launches ledger), never this function's memory.
    once = rules.poll_plan(closes=[7], resumes=[], launches=[], available_slots=1, now=NOW)
    twice = rules.poll_plan(closes=[7], resumes=[], launches=[], available_slots=1, now=NOW)
    assert once == twice == [rules.PollAction("close", 7)]
