from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast, get_args

import pytest

from agentic.core.process.application.ports import BranchPullRequest
from agentic.core.process.domain import ci, document, roles, rules, task
from agentic.core.process.domain.adr_numbers import Renaming
from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import (
    DEVELOPER,
    FINDINGS,
    INTERVIEW_DEFINITION,
    INTERVIEW_SOLUTION,
    PAIR_PM_PA,
    PAIR_UI_LEAD,
    PAIR_UX_LEAD,
    PJM,
    PRODUCT_ANALYST_BUG,
    TECH_LEAD_EPHEMERAL,
)
from agentic.core.process.domain.schemas import TaskType
from agentic.core.process.domain.task import INTEGRATION, Carried

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


def test_decided_is_true_for_a_level_two_heading_however_it_is_cased_or_punctuated():
    assert rules.decided("Intro\n\n## Decided\n- one\n")
    assert rules.decided("## Decided:\n")
    assert rules.decided("## DECIDED  \n")
    assert rules.decided("## decided")


def test_decided_rejects_what_is_not_a_level_two_heading_outside_a_fence():
    assert not rules.decided("### Decided\n")
    assert not rules.decided("# Decided\n")
    assert not rules.decided("## Decided later\n")
    assert not rules.decided("## Decided::\n")
    assert not rules.decided("We Decided this in a grill.\n## Undecided\n")
    assert not rules.decided("```\n## Decided\n```\n")
    assert not rules.decided("~~~md\n## Decided\n~~~\n")
    assert not rules.decided("")


def test_decided_reads_the_heading_again_once_a_fence_closes_and_at_the_level_asked():
    assert rules.decided("```\nx\n```\n## Decided\n")
    assert rules.decided("### Decided\n", level=3)
    assert not rules.decided("## Decided\n", level=3)


def test_decided_reads_the_demoted_request_back_at_level_three():
    assert rules.decided(document.demote("## Decided\n- a\n"), level=3)


def test_entering_marks_a_decided_request_only_when_the_cap_is_on():
    task = {"id": "GH-1", "title": "t", "description": "Body\n\n## Decided\n- a\n"}
    assert rules.entering(task, cap=True) == {**task, "decided": True}
    assert "decided" not in task
    assert rules.entering(task, cap=False) == task
    assert "decided" not in rules.entering(task, cap=False)
    plain = {"id": "GH-1", "title": "t", "description": "Body\n"}
    assert "decided" not in rules.entering(plain, cap=True)


def test_interview_cap_is_one_only_for_a_decided_state_at_a_stage_in_the_set():
    decided = {"decided": True}
    assert rules.interview_cap(decided, INTERVIEW_DEFINITION, 4) == 1
    assert rules.interview_cap(decided, INTERVIEW_SOLUTION, 4) == 1
    assert rules.interview_cap({}, INTERVIEW_SOLUTION, 4) == 4
    assert rules.interview_cap({"decided": False}, INTERVIEW_SOLUTION, 4) == 4
    assert rules.interview_cap(decided, INTERVIEW_SOLUTION, 1) == 1
    # A configured cap below one stays: the cap never raises.
    assert rules.interview_cap(decided, INTERVIEW_SOLUTION, 0) == 0


def test_interview_cap_ignores_a_stage_that_is_not_in_the_set(monkeypatch):
    monkeypatch.setattr(rules, "DECIDED_CAP_STAGES", frozenset({"definition"}))
    assert rules.interview_cap({"decided": True}, INTERVIEW_SOLUTION, 4) == 4
    assert rules.interview_cap({"decided": True}, INTERVIEW_DEFINITION, 4) == 1


def test_a_state_with_no_decided_key_keeps_the_configured_cap_whatever_its_request_holds():
    state = {"description": "Body\n\n### Decided\n- a\n"}
    assert rules.interview_cap(state, INTERVIEW_SOLUTION, 4) == 4
    assert rules.interview_progress(state, INTERVIEW_SOLUTION, 4).rounds_left == 4


def test_interview_progress_for_a_cap_of_two_or_more_is_what_it_always_was():
    section = INTERVIEW_DEFINITION.section
    runs = [
        {"role": "Product Analyst", "section": section, "session_id": f"q{i}"} for i in range(4)
    ]
    for cap, done, left in [(4, 0, 4), (4, 4, 2), (3, 3, 2), (2, 2, 2), (2, 0, 2)]:
        progress = rules.interview_progress({"runs": runs[:done]}, INTERVIEW_DEFINITION, cap)
        assert (progress.rounds_done, progress.rounds_left) == (done, left)


def test_interview_progress_of_a_decided_state_gives_a_retake_one_round():
    section = INTERVIEW_DEFINITION.section
    runs = [
        {"role": "Product Analyst", "section": section, "session_id": f"q{i}"} for i in range(2)
    ]
    for done in (0, 1, 2):
        state = {"decided": True, "runs": runs[:done]}
        progress = rules.interview_progress(state, INTERVIEW_DEFINITION, 4)
        assert (progress.rounds_done, progress.rounds_left) == (done, 1)


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

    state["runs"] = [*state["runs"], run("UI Lead")]  # approved
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
    `ui_screens` even though UI Lead had already approved that too."""
    runs = [
        run("Product Analyst"),  # PRD v1
        run("UX Designer"),
        run("UX Lead"),  # approved
        run("UI Designer"),
        run("UI Lead"),  # approved
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
    # UI's own step is now stale: UX Designer is newer than UI Lead's approval.
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


def test_final_validation_due_rejects_a_plain_feature_and_an_unapproved_one():
    """The guard's own rejecting inputs: no design branch at all, and a design-branch
    task not every subtask of which is approved yet -- neither is ever due, whatever
    `runs` holds."""
    plain = {"type": "feature", "flags": {}, "subtasks": {"api": {"status": "approved"}}}
    assert rules.final_validation_due(plain) is False
    pending = {**UX_FLAGS, "subtasks": {"api": {"status": "pending"}}}
    assert rules.final_validation_due(pending) is False


def test_final_validation_due_table_covers_the_single_subtask_case_and_a_pm_return():
    """The accepting case -- due once the single subtask's own review approved and PM
    never validated it -- and the table's own hard case: PM's return is matched by a
    newer, approving review before this reads due again (ADR-0040's own ladder)."""
    review = rules.review_section("api")
    state = {
        **UX_FLAGS,
        "subtasks": {"api": {"status": "approved"}},
        "runs": [run("Tech Lead", review)],
    }
    assert rules.final_validation_due(state) is True

    # PM's own approving run now covers this review: not due again.
    state["runs"] = [*state["runs"], run(roles.PM_FINAL.name)]
    assert rules.final_validation_due(state) is False

    # PM returns the subtask; the Developer and a fresh, approving review follow.
    state["change_history"] = [
        rules.change_entry(roles.PAIR_PM_TECH_LEAD, "spec-behaviour", "fix the gap")
    ]
    state["runs"] = [*state["runs"], run("Tech Lead", review)]
    assert rules.final_validation_due(state) is True


def test_a_task_escalated_in_deliver_after_final_validation_resumes_with_no_rerun():
    """An escalation past final validation (`deliver`'s own `task_checkout` failing, for
    instance) resumes through `integrate`, the same road every other subtask-based
    resume takes; `after_integrate` reads the same approving run `final_validation_due`
    already does and falls straight through to `deliver`, never back to
    `final_validation`."""
    review = rules.review_section("api")
    state = {
        **UX_FLAGS,
        "subtasks": {"api": {"status": "approved"}},
        "runs": [run("Tech Lead", review), run(roles.PM_FINAL.name)],
        **rules.escalation("failure", "deliver's own checkout failed"),
    }
    resumed = task.apply(state, rules.resumed(state, "fixed"))
    assert rules.resume_target(resumed) == "integrate"
    assert rules.after_integrate(resumed) == "deliver"


def test_return_section_reads_pm_final_validation_only_when_it_is_the_open_return():
    plain = {"subtasks": {"api": {"status": "returned"}}}
    assert rules.return_section(plain, "api") == rules.review_section("api")
    final = {"subtasks": {"api": {"status": "returned", "returned_by": "final_validation"}}}
    assert rules.return_section(final, "api") == roles.PM_FINAL.name
    # Absent entirely -- a document from before this key existed -- the same default.
    assert rules.return_section({"subtasks": {}}, "api") == rules.review_section("api")


def test_design_effect_by_role_counts_lead_returns_and_blocking_items_per_role():

    state = {
        "change_history": [
            rules.change_entry(PAIR_UX_LEAD, rules.PRD_RETURN, "x"),
            rules.change_entry(PAIR_UI_LEAD, rules.PRD_RETURN, "y"),
            rules.change_entry(PAIR_PM_PA, rules.PRD_RETURN, "z"),  # not a design pair
        ],
        "design_items": [
            {"role_slug": "ux_lead", "kind": "blocking", "step": "Sign in", "text": "dead end"},
            {"role_slug": "ux_lead", "kind": "observation", "step": "Pay", "text": "slow"},
            {"role_slug": "ui_lead", "kind": "blocking", "step": "Cart", "text": "no state"},
        ],
    }
    assert rules.design_effect_by_role(state) == {"ux_lead": 2, "ui_lead": 2}
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


# The task cap and the phases (ADR-0004, 2026-10-05 addendum).


def subtasks(*ids):
    return {"subtasks": {sid: {"id": sid, "status": "pending"} for sid in ids}}


def test_the_task_cap_is_the_flat_ceiling_which_an_override_raises_and_never_lowers():
    assert rules.task_cap(250, None) == 250
    assert rules.task_cap(250, 400) == 400
    assert rules.task_cap(250, 120) == 250  # rejected: an override below the ceiling
    assert rules.task_cap(0.7, None) == 0.7


def test_a_cap_escalation_is_recognised_and_proposes_one_more_attempt_cap():
    reason = rules.cap_reached("Developer", 106.27, 100)
    assert reason == "Developer stopped by the task cap: 106.27 of 100.00 USD spent"
    assert rules.stopped_by_cap({"escalated": reason})
    assert not rules.stopped_by_cap({"escalated": "PM hit 2 returns: x"})
    assert rules.suggested_budget({"cost_usd": 106.27}, 10) == 117


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


def test_run_ended_is_true_only_for_ready_for_review_discarded_and_split():
    ended = {"ready_for_review", "discarded", "split"}
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
    # Numbered, in the order the state holds them: the coverage map refers to them as
    # `invariant N` (GH-174), and every reader numbers them through this one renderer.
    assert rules.invariants_markdown(state) == "1. fold duplicates\n2. no relative imports"
    assert rules.invariants_markdown({}) == "- none"
    assert rules.invariants_markdown({"invariants": []}) == "- none"


def _returned_state(**more) -> dict:
    return {
        "subtasks": {"api": {"status": "returned", "reason": "half the brief"}},
        "sections": {"Review of api": "Missing the docs."},
        **more,
    }


def test_the_returned_delta_asks_for_the_whole_map_again_only_with_invariants():
    plain = rules.returned_developer_delta(_returned_state(), "api", "agentic/GH-1.api", "abc")
    empty = rules.returned_developer_delta(
        _returned_state(invariants=[]), "api", "agentic/GH-1.api", "abc"
    )
    assert plain == empty
    assert "coverage map" not in plain
    with_invariants = rules.returned_developer_delta(
        _returned_state(invariants=["fold duplicates"]), "api", "agentic/GH-1.api", "abc"
    )
    assert with_invariants.startswith(plain)
    tail = with_invariants[len(plain) :]
    assert "coverage map" in tail and "current head" in tail and "full" in tail


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


def test_a_stall_is_a_reason_of_its_own_and_never_external():
    assert "stall" not in rules.EXTERNAL_REASONS


def test_auto_resumable_is_none_for_an_epoch_whose_last_failure_is_a_stall():
    # Rejected input: a stalled role escalated as `failure`, and the poller must not
    # retake it on its own the way it does an account limit (ADR-0057).
    alone = {"pause_log": [_failure_entry("stall"), _escalation_entry("failure")]}
    after_external = {
        "pause_log": [
            _failure_entry("account_limit"),
            _failure_entry("stall"),
            _escalation_entry("failure"),
        ]
    }
    assert rules.auto_resumable(alone) is None
    assert rules.auto_resumable(after_external) is None


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
    state = {"pause_log": [_failure_entry("upstream", section=section)]}
    assert rules.auto_resume_limit(state, "upstream") == 1


def test_auto_resume_limit_is_six_outside_an_interview_section():
    state = {"pause_log": [_failure_entry("upstream", section="Developer: api")]}
    assert rules.auto_resume_limit(state, "upstream") == 6


def test_auto_resume_limit_of_an_account_limit_is_the_whole_window_calendar_in_an_interview():
    state = {"pause_log": [_failure_entry("account_limit", section=INTERVIEW_SOLUTION.section)]}
    assert rules.auto_resume_limit(state, "account_limit") == 24


def test_auto_resume_limit_of_an_account_limit_is_the_whole_window_calendar_outside_one():
    state = {"pause_log": [_failure_entry("account_limit", section="Developer: api")]}
    assert rules.auto_resume_limit(state, "account_limit") == 24


def test_next_attempt_at_is_none_before_its_own_slot_elapses():
    since = datetime(2026, 10, 3, 19, 30)
    now = datetime(2026, 10, 3, 19, 40)  # 10 of the first slot's 15 minutes
    assert (
        rules.next_attempt_at(since, prior=0, limit=6, now=now, schedule=rules.RETAKE_SCHEDULE)
        is None
    )


def test_next_attempt_at_is_due_once_its_own_slot_elapses():
    since = datetime(2026, 10, 3, 19, 30)
    now = datetime(2026, 10, 3, 19, 46)
    due = rules.next_attempt_at(since, prior=0, limit=6, now=now, schedule=rules.RETAKE_SCHEDULE)
    assert due == datetime(2026, 10, 3, 19, 45)


def test_next_attempt_at_is_none_once_the_calendar_is_spent():
    since = datetime(2026, 10, 3, 19, 30)
    now = datetime(2026, 10, 4, 19, 30)  # a full day later, no slot left to run out
    assert (
        rules.next_attempt_at(since, prior=1, limit=1, now=now, schedule=rules.RETAKE_SCHEDULE)
        is None
    )


def test_launch_schedule_keeps_the_growing_calendar():
    assert rules.LAUNCH_SCHEDULE == (15, 30, 60, 120, 120, 120)


def test_poll_plan_does_not_launch_a_late_attempt_before_its_growing_slot():
    # prior=5 is the sixth slot, 120 minutes: 20 minutes after the last attempt is far
    # from it, so the launch calendar is never the flat 15-minute one.
    late = rules.LaunchCandidate(
        issue=9, blocked=False, since=NOW - timedelta(minutes=20), prior=5, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[late], available_slots=1, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_launches_a_late_attempt_once_its_growing_slot_elapses():
    late = rules.LaunchCandidate(
        issue=9, blocked=False, since=NOW - timedelta(minutes=121), prior=5, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[late], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("launch", 9)]


ACCOUNT = rules.ACCOUNT_LIMIT_SCHEDULE
T0 = datetime(2026, 10, 5, 7, 40)


def test_next_attempt_at_on_the_account_limit_calendar_is_flat_fifteen_minutes():
    for k in (0, 12, 23):
        due = rules.next_attempt_at(
            T0, prior=k, limit=24, now=T0 + timedelta(minutes=15), schedule=ACCOUNT
        )
        assert due == T0 + timedelta(minutes=15)


def test_next_attempt_at_on_the_account_limit_calendar_is_none_a_second_early():
    now = T0 + timedelta(minutes=14, seconds=59)
    assert rules.next_attempt_at(T0, prior=23, limit=24, now=now, schedule=ACCOUNT) is None


def test_next_attempt_at_on_the_account_limit_calendar_is_none_after_the_last_slot():
    now = T0 + timedelta(days=1)
    assert rules.next_attempt_at(T0, prior=24, limit=24, now=now, schedule=ACCOUNT) is None
    assert rules._schedule_exhausted(24, 24, schedule=ACCOUNT)
    assert not rules._schedule_exhausted(23, 24, schedule=ACCOUNT)


def test_the_account_limit_calendar_covers_a_whole_five_hour_window():
    assert rules.ACCOUNT_LIMIT_SCHEDULE == (15,) * 24
    assert sum(rules.ACCOUNT_LIMIT_SCHEDULE) >= 5 * 60


def test_next_attempt_at_on_the_growing_calendar_is_none_past_its_six_slots():
    now = T0 + timedelta(days=1)
    assert (
        rules.next_attempt_at(T0, prior=10, limit=6, now=now, schedule=rules.RETAKE_SCHEDULE)
        is None
    )


def test_schedule_for_gives_the_flat_calendar_to_an_account_limit_only():
    assert rules.schedule_for("account_limit") == rules.ACCOUNT_LIMIT_SCHEDULE
    assert rules.schedule_for("upstream") == rules.RETAKE_SCHEDULE
    assert rules.schedule_for("network") == rules.RETAKE_SCHEDULE


def test_a_mixed_epoch_takes_the_calendar_of_its_last_failure():
    account_then_upstream = {
        "pause_log": [_failure_entry("account_limit"), _failure_entry("upstream")]
    }
    upstream_then_account = {
        "pause_log": [_failure_entry("upstream"), _failure_entry("account_limit")]
    }
    reason = rules.auto_resumable(account_then_upstream)
    assert reason is not None and rules.schedule_for(reason) == rules.RETAKE_SCHEDULE
    reason = rules.auto_resumable(upstream_then_account)
    assert reason is not None and rules.schedule_for(reason) == rules.ACCOUNT_LIMIT_SCHEDULE


def test_account_limit_hourly_covers_a_week_of_sixty_minute_slots():
    assert rules.ACCOUNT_LIMIT_HOURLY == (60,) * 168
    assert sum(rules.ACCOUNT_LIMIT_HOURLY) == 7 * 24 * 60


def test_schedule_for_follows_the_hourly_calendar_only_for_an_account_limit_past_the_chase_max():
    past = rules.ACCOUNT_LIMIT_CHASE_MAX
    assert rules.schedule_for("account_limit", past - 1) == rules.ACCOUNT_LIMIT_SCHEDULE
    assert rules.schedule_for("account_limit", past) == rules.ACCOUNT_LIMIT_HOURLY
    # Rejected input: no other reason ever gets the hourly calendar, however long the chase.
    for reason in ("upstream", "network"):
        assert rules.schedule_for(reason, past + 50) == rules.RETAKE_SCHEDULE


def test_the_hourly_calendar_is_indexed_from_the_chase_max_and_spent_after_168_slots():
    past = rules.ACCOUNT_LIMIT_CHASE_MAX
    base = rules.ResumeCandidate(5, "account_limit", T0, prior=3, limit=24, chase_prior=past)
    assert not rules.resume_exhausted(base)
    # Slot 0 of the hourly calendar is 60 minutes after the pause, not 15.
    assert rules.retake_due(base, T0 + timedelta(minutes=59)) is None
    assert rules.retake_due(base, T0 + timedelta(minutes=60)) == T0 + timedelta(minutes=60)
    last = replace(base, chase_prior=past + 167)
    assert not rules.resume_exhausted(last)
    assert rules.retake_due(last, T0 + timedelta(minutes=60)) is not None
    spent = replace(base, chase_prior=past + 168)
    assert rules.resume_exhausted(spent)
    assert rules.retake_due(spent, T0 + timedelta(days=30)) is None


def test_only_an_account_limit_that_spent_the_hourly_calendar_persisted():
    past = rules.ACCOUNT_LIMIT_CHASE_MAX
    spent = rules.ResumeCandidate(5, "account_limit", T0, 0, 24, chase_prior=past + 168)
    assert rules.account_limit_persisted(spent)
    assert not rules.account_limit_persisted(replace(spent, chase_prior=past + 167))
    # Rejected input: an upstream chase this long is exhausted, but is not this escalation.
    assert not rules.account_limit_persisted(replace(spent, reason="upstream"))


def test_the_persisted_escalation_is_a_failure_class_pause_auto_resumable_does_not_retake():
    patch = rules.account_limit_persisted_patch()
    assert patch["escalated"] == "account limit persisted 7 days"
    assert patch["escalation_class"] == "failure"
    state = {
        "pause_log": [_failure_entry("account_limit"), {"kind": "escalation", "value": "failure"}]
    }
    assert rules.auto_resumable(state) == "account_limit"
    assert rules.auto_resumable(task.apply(state, patch)) is None


def test_low_confidence_type_resolves_every_task_type():
    # `platform_enhancement` has no path of its own to guess into: it is a feature.
    assert rules.low_confidence_type("platform_enhancement") == "feature"
    assert rules.low_confidence_type("bug") == "bug"
    assert rules.low_confidence_type("feature") == "feature"
    for guess in get_args(TaskType):
        assert rules.low_confidence_type(guess) in get_args(TaskType)


def test_the_chase_backstop_is_inert_for_an_upstream_whose_schedule_is_spent():
    spent = rules.ResumeCandidate(5, "upstream", T0, prior=6, limit=6, chase_prior=6)
    assert rules.resume_exhausted(spent)
    live = rules.ResumeCandidate(5, "upstream", T0, prior=5, limit=6, chase_prior=5)
    assert not rules.resume_exhausted(live)


def test_resume_exhausted_by_the_interview_ceiling_but_no_longer_by_the_chase_max():
    # GH-239 edits the case that asserted `chase_prior=96` handed the task to the human:
    # past `ACCOUNT_LIMIT_CHASE_MAX` the hourly calendar takes over.
    base = rules.ResumeCandidate(5, "account_limit", T0, prior=2, limit=24, chase_prior=95)
    assert not rules.resume_exhausted(base)
    assert not rules.resume_exhausted(replace(base, chase_prior=96))
    assert rules.resume_exhausted(replace(base, ceiling_spent=True))


def test_window_probes_counts_a_comment_inside_the_clock_tolerance_of_the_last_run_end():
    end = datetime(2026, 10, 5, 8, 0)
    inside = end - timedelta(seconds=119)
    outside = end - timedelta(seconds=121)
    assert rules.window_probes([inside], end) == 1
    assert rules.window_probes([outside], end) == 0


def test_window_probes_counts_everything_when_no_run_ended():
    times = [T0, T0 + timedelta(minutes=16), T0 + timedelta(minutes=32)]
    assert rules.window_probes(times, None) == 3


def _starts(*offsets_s: int) -> list[datetime]:
    return [T0 + timedelta(seconds=s) for s in offsets_s]


def test_productive_retakes_counts_a_round_just_before_its_comment():
    retakes = [T0 + timedelta(minutes=15)]
    assert rules.productive_retakes(retakes, [retakes[0] - timedelta(seconds=1)]) == 1


def test_productive_retakes_counts_a_round_inside_the_tolerance_before_its_comment():
    retakes = [T0 + timedelta(minutes=15)]
    assert rules.productive_retakes(retakes, [retakes[0] - timedelta(seconds=119)]) == 1


def test_productive_retakes_does_not_count_a_round_older_than_the_tolerance():
    retakes = [T0 + timedelta(minutes=15)]
    assert rules.productive_retakes(retakes, [retakes[0] - timedelta(seconds=121)]) == 0


def test_productive_retakes_attributes_a_round_to_the_retake_whose_window_holds_it():
    first, second = T0 + timedelta(minutes=15), T0 + timedelta(minutes=31)
    # One round, in the second window: only the second retake ran something.
    assert rules.productive_retakes([first, second], [second + timedelta(minutes=1)]) == 1
    # A round in each window: both were productive.
    both = [first + timedelta(minutes=1), second + timedelta(minutes=1)]
    assert rules.productive_retakes([first, second], both) == 2


def test_productive_retakes_is_zero_when_no_retake_ran_a_round():
    retakes = [T0 + timedelta(minutes=m) for m in (15, 31, 47)]
    assert rules.productive_retakes(retakes, []) == 0


def test_productive_retakes_fails_closed_when_the_round_starts_are_unreadable():
    assert rules.productive_retakes([T0], None) == 1


def test_interview_rounds_is_the_interviewers_own_runs_of_the_section():
    section = INTERVIEW_DEFINITION.section
    runs = [
        {"role": "Product Analyst", "section": section},
        {"role": "PM", "section": section},
        {"role": "Product Analyst", "section": "PM"},
    ]
    assert rules.interview_rounds({"runs": runs}, INTERVIEW_DEFINITION) == [runs[0]]
    assert rules.interview_rounds({}, INTERVIEW_DEFINITION) == []


def test_round_starts_parses_every_started_at():
    rounds = [{"started_at": "2026-10-05T08:00:00.000+00:00"}]
    assert rules.round_starts(rounds) == [datetime(2026, 10, 5, 8, 0, tzinfo=UTC)]
    assert rules.round_starts([]) == []


def test_round_starts_is_none_when_any_start_is_missing_or_unparseable():
    good = {"started_at": "2026-10-05T08:00:00.000+00:00"}
    assert rules.round_starts([good, {}]) is None
    assert rules.round_starts([good, {"started_at": ""}]) is None
    assert rules.round_starts([good, {"started_at": "not a time"}]) is None
    # A start with no offset cannot be compared with GitHub's own aware timestamps.
    assert rules.round_starts([{"started_at": "2026-10-05T08:00:00"}]) is None


def test_run_ends_is_none_when_any_end_is_missing_or_unparseable():
    good = {"ended_at": "2026-10-05T08:00:00.000+00:00"}
    assert rules.run_ends({"runs": [good]}) == [datetime(2026, 10, 5, 8, 0, tzinfo=UTC)]
    assert rules.run_ends({}) == []
    assert rules.run_ends({"runs": [good, {"role": "PM"}]}) is None
    assert rules.run_ends({"runs": [good, {"ended_at": "garbage"}]}) is None


def test_interview_for_section_finds_the_interview_of_a_section_only():
    assert rules.interview_for_section(INTERVIEW_SOLUTION.section) == INTERVIEW_SOLUTION
    assert rules.interview_for_section("Developer: api") is None


def test_interview_ceiling_is_spent_by_a_productive_account_limit_interview_retake():
    state = {"pause_log": [_failure_entry("account_limit", section=INTERVIEW_SOLUTION.section)]}
    assert rules.interview_ceiling_spent(state, "account_limit", 1) is True
    assert rules.interview_ceiling_spent(state, "account_limit", 0) is False


def test_interview_ceiling_is_never_spent_for_upstream_or_outside_an_interview():
    interview = {"pause_log": [_failure_entry("upstream", section=INTERVIEW_SOLUTION.section)]}
    assert rules.interview_ceiling_spent(interview, "upstream", 3) is False
    outside = {"pause_log": [_failure_entry("account_limit", section="Developer: api")]}
    assert rules.interview_ceiling_spent(outside, "account_limit", 3) is False


def test_blocker_resolved_not_planned_resolves_without_a_merge():
    assert rules.blocker_resolved("NOT_PLANNED", merged_into_checkout=False) is True


def test_blocker_resolved_completed_needs_the_merge_in_the_checkout():
    assert rules.blocker_resolved("COMPLETED", merged_into_checkout=True) is True
    assert rules.blocker_resolved("COMPLETED", merged_into_checkout=False) is False


def test_blocker_resolved_rejects_open_and_unknown_state_reasons():
    assert rules.blocker_resolved("OPEN", merged_into_checkout=True) is False
    assert rules.blocker_resolved(None, merged_into_checkout=True) is False
    assert rules.blocker_resolved("SOME_FUTURE_REASON", merged_into_checkout=True) is False


# GH-142 (apostrophe #8): an empty native list is `blockedBy.nodes: []`, GitHub's own
# shape for "no native dependency", indistinguishable from a field GitHub never
# exposed at all -- so `blocker_numbers` falls back to the body for both, the one
# place in the product that branches on `blocked_by` being `None` or `()`.


def test_blocker_numbers_falls_back_to_the_body_when_native_is_empty():
    assert rules.blocker_numbers((), lambda: "Blocked by: #7") == (7,)


def test_blocker_numbers_falls_back_to_the_body_when_native_is_none():
    assert rules.blocker_numbers(None, lambda: "Blocked by: #7") == (7,)


def test_blocker_numbers_is_empty_when_native_is_empty_and_the_body_names_none():
    assert rules.blocker_numbers((), lambda: "just a description, no blockers") == ()


def test_blocker_numbers_never_calls_the_body_thunk_when_native_is_not_empty():
    def _raising() -> str:
        raise AssertionError("body must not be read when native blockers exist")

    assert rules.blocker_numbers((3,), _raising) == (3,)


def test_blocker_numbers_reads_only_the_first_non_empty_line_of_the_body():
    assert rules.blocker_numbers((), lambda: "intro\n\nBlocked by: #7") == ()


def test_blocker_numbers_reads_several_blockers_off_one_line():
    assert rules.blocker_numbers((), lambda: "Blocked by: #7, #9") == (7, 9)


NOW = datetime(2026, 10, 3, 20, 0)


def test_poll_plan_closes_everything_lowest_first_and_never_spends_a_slot():
    actions = rules.poll_plan(
        closes=[9, 3], resumes=[], launches=[], available_slots=0, now=NOW
    ).actions
    assert actions == [rules.PollAction("close", 3), rules.PollAction("close", 9)]


def test_poll_plan_resumes_a_due_account_limit_pause():
    due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW.replace(hour=0), prior=0, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[due], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("resume", 5)]


def test_poll_plan_does_not_resume_a_pause_still_waiting_out_its_own_calendar():
    # Escalated one minute ago: its own 15-minute first slot has not elapsed yet.
    not_due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(minutes=1), prior=0, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[not_due], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_caps_resumes_at_available_slots_lowest_issue_first():
    since = NOW.replace(hour=0)
    a = rules.ResumeCandidate(issue=20, reason="upstream", since=since, prior=0, limit=6)
    b = rules.ResumeCandidate(issue=5, reason="upstream", since=since, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[a, b], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("resume", 5)]


def test_poll_plan_launches_the_lowest_unblocked_issue():
    blocked = rules.LaunchCandidate(issue=4, blocked=True, since=None, prior=0, limit=6)
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[blocked, ready], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("launch", 9)]


def test_poll_plan_does_not_launch_an_issue_with_an_open_blocker():
    # Rejected input: the one candidate there is stays blocked -- nothing launches.
    blocked = rules.LaunchCandidate(issue=4, blocked=True, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[blocked], available_slots=1, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_launches_nothing_when_the_cap_is_full():
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[ready], available_slots=0, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_does_not_launch_an_issue_whose_own_wait_has_not_elapsed():
    # Rejected input: the only launch candidate there is was attempted a minute ago,
    # its own first 15-minute slot has not elapsed yet -- nothing launches this round.
    not_due = rules.LaunchCandidate(
        issue=9, blocked=False, since=NOW - timedelta(minutes=1), prior=0, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[not_due], available_slots=1, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_does_not_launch_an_issue_whose_own_calendar_is_spent():
    # Rejected input: every slot of this issue's own calendar already ran out
    # (`prior == limit`) -- excluded until a human acts, per "al llegar al límite".
    exhausted = rules.LaunchCandidate(
        issue=9, blocked=False, since=NOW - timedelta(days=1), prior=6, limit=6
    )
    actions = rules.poll_plan(
        closes=[], resumes=[], launches=[exhausted], available_slots=1, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_launches_even_though_an_exhausted_resume_elsewhere_cannot_retry():
    # A resume whose own calendar is spent (`prior >= limit`) is not "an external wait
    # not yet elapsed": it needs a human now, like any other escalation, and must not
    # freeze every launch in the product until one acts.
    exhausted = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(days=1), prior=24, limit=24
    )
    ready = rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], resumes=[exhausted], launches=[ready], available_slots=1, now=NOW
    ).actions
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
    ).actions
    assert actions == []


def test_poll_plan_called_twice_with_the_same_closes_never_doubles_the_action():
    # `poll_plan` is pure and stateless: not relaunching an already-registered issue is
    # the caller's own bookkeeping (the launches ledger), never this function's memory.
    once = rules.poll_plan(closes=[7], resumes=[], launches=[], available_slots=1, now=NOW).actions
    twice = rules.poll_plan(closes=[7], resumes=[], launches=[], available_slots=1, now=NOW).actions
    assert once == twice == [rules.PollAction("close", 7)]


def _ready_launch() -> rules.LaunchCandidate:
    return rules.LaunchCandidate(issue=9, blocked=False, since=None, prior=0, limit=6)


def test_poll_plan_blocks_a_launch_while_the_last_account_limit_slot_still_waits():
    waiting = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(minutes=1), prior=23, limit=24
    )
    actions = rules.poll_plan(
        closes=[], resumes=[waiting], launches=[_ready_launch()], available_slots=1, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_retakes_an_account_limit_pause_on_the_flat_calendar():
    due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(minutes=16), prior=12, limit=24
    )
    actions = rules.poll_plan(
        closes=[], resumes=[due], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("resume", 5)]


def test_poll_plan_retakes_hourly_once_the_chase_max_is_reached():
    # GH-239 edits the case that asserted nothing was retaken at `chase_prior=96`.
    since = NOW - timedelta(minutes=16)
    over = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=since, prior=2, limit=24, chase_prior=96
    )
    # The hourly slot is 60 minutes after the pause: at 16 it is not due, and the
    # waiting pause still blocks the launch.
    actions = rules.poll_plan(
        closes=[], resumes=[over], launches=[_ready_launch()], available_slots=1, now=NOW
    ).actions
    assert actions == []
    due = replace(over, since=NOW - timedelta(minutes=61))
    actions = rules.poll_plan(
        closes=[], resumes=[due], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("resume", 5)]
    spent = replace(over, chase_prior=rules.ACCOUNT_LIMIT_CHASE_MAX + 168)
    actions = rules.poll_plan(
        closes=[], resumes=[spent], launches=[_ready_launch()], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("launch", 9)]


def test_poll_plan_does_not_retake_nor_block_once_the_interview_ceiling_is_spent():
    since = NOW - timedelta(minutes=16)
    spent = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=since, prior=1, limit=24, ceiling_spent=True
    )
    actions = rules.poll_plan(
        closes=[], resumes=[spent], launches=[_ready_launch()], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("launch", 9)]
    open_ceiling = replace(spent, ceiling_spent=False)
    actions = rules.poll_plan(
        closes=[], resumes=[open_ceiling], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("resume", 5)]


def test_poll_plan_treats_a_zero_limit_as_exhausted_never_as_a_flag():
    for prior in (0, 3):
        zero = rules.ResumeCandidate(
            issue=5, reason="account_limit", since=NOW - timedelta(days=1), prior=prior, limit=0
        )
        assert (
            rules.next_attempt_at(zero.since, zero.prior, zero.limit, NOW, schedule=ACCOUNT) is None
        )
        actions = rules.poll_plan(
            closes=[], resumes=[zero], launches=[_ready_launch()], available_slots=1, now=NOW
        ).actions
        assert actions == [rules.PollAction("launch", 9)]


# --- interrupted_run: the pure decision to retake a run a reboot cut (GH-176) ---


def _interrupted_task(**more) -> dict:
    """An open task a run is in the middle of: no `closed_at`, not escalated, no pull
    request, status `developing`. `more` overrides or adds document keys."""
    return {"status": "developing", **more}


PREVIOUS_BOOT = rules.LockFacts(other_boot=True, alive=False)
SAME_BOOT = rules.LockFacts(other_boot=False, alive=False)
UNKNOWN_BOOT = rules.LockFacts(other_boot=None, alive=False)


def test_a_dead_lock_from_a_previous_boot_is_retaken():
    assert rules.interrupted_run(_interrupted_task(), PREVIOUS_BOOT, 0) == "retake"


def test_a_dead_lock_from_the_same_boot_is_left_to_the_human():
    assert rules.interrupted_run(_interrupted_task(), SAME_BOOT, 0) == "human"


def test_a_dead_lock_with_no_boot_id_is_never_retaken():
    assert rules.interrupted_run(_interrupted_task(), UNKNOWN_BOOT, 0) == "human"


def test_a_lock_whose_process_is_alive_is_running():
    alive = rules.LockFacts(other_boot=False, alive=True)
    assert rules.interrupted_run(_interrupted_task(), alive, 0) == "running"


def test_no_lock_record_means_there_is_nothing_to_retake():
    assert rules.interrupted_run(_interrupted_task(), None, 0) == "none"


def test_a_task_with_any_of_these_fields_is_not_an_interrupted_run():
    for extra in (
        {"closed_at": "2026-10-05T10:00:00.000Z"},
        {"escalated": "needs a human"},
        {"pull_request": "https://github.com/o/r/pull/3"},
    ):
        task_doc = _interrupted_task(**extra)
        assert rules.interrupted_run(task_doc, PREVIOUS_BOOT, 0) == "none", extra


def test_a_task_in_a_finished_or_waiting_status_is_not_an_interrupted_run():
    for status in ("ready_for_review", "discarded", "split", "waiting_on_human"):
        assert rules.interrupted_run({"status": status}, PREVIOUS_BOOT, 0) == "none", status


def test_a_falsy_closed_at_escalated_or_pull_request_does_not_stop_a_retake():
    task_doc = _interrupted_task(closed_at=None, escalated="", pull_request=None)
    assert rules.interrupted_run(task_doc, PREVIOUS_BOOT, 0) == "retake"


def test_a_document_with_no_status_goes_to_the_human():
    assert rules.interrupted_run({}, PREVIOUS_BOOT, 0) == "human"


def test_retakes_stop_at_the_cap_and_the_task_goes_to_the_human():
    under = rules.RETAKE_AFTER_REBOOT_MAX - 1
    assert rules.interrupted_run(_interrupted_task(), PREVIOUS_BOOT, under) == "retake"
    spent = rules.RETAKE_AFTER_REBOOT_MAX
    assert rules.interrupted_run(_interrupted_task(), PREVIOUS_BOOT, spent) == "human"


def test_a_live_process_stays_running_even_when_the_boot_is_another():
    # A record from another boot whose pid `liveness` still reads as alive: not ours to
    # retake, whatever the caller derived `other_boot` as.
    alive = rules.LockFacts(other_boot=True, alive=True)
    assert rules.interrupted_run(_interrupted_task(), alive, 0) == "running"


def test_poll_plan_orders_closes_then_retakes_then_resumes_then_a_launch():
    due = rules.ResumeCandidate(
        issue=2, reason="account_limit", since=NOW.replace(hour=0), prior=0, limit=6
    )
    actions = rules.poll_plan(
        closes=[8],
        retakes=[6, 4],
        resumes=[due],
        launches=[_ready_launch()],
        available_slots=3,
        now=NOW,
    ).actions
    assert actions == [
        rules.PollAction("close", 8),
        rules.PollAction("retake", 4),
        rules.PollAction("retake", 6),
        rules.PollAction("resume", 2),
    ]


def test_poll_plan_launches_after_a_retake_only_when_a_slot_remains():
    plan = dict(closes=[], resumes=[], launches=[_ready_launch()], now=NOW)
    assert rules.poll_plan(retakes=[4], available_slots=2, **plan).actions == [
        rules.PollAction("retake", 4),
        rules.PollAction("launch", 9),
    ]
    assert rules.poll_plan(retakes=[4], available_slots=1, **plan).actions == [
        rules.PollAction("retake", 4)
    ]


def test_poll_plan_never_spends_more_slots_than_available_on_retakes_resumes_and_a_launch():
    since = NOW.replace(hour=0)
    resumes = [
        rules.ResumeCandidate(issue=n, reason="upstream", since=since, prior=0, limit=6)
        for n in (1, 2)
    ]
    for slots in range(0, 6):
        actions = rules.poll_plan(
            closes=[],
            retakes=[10, 11, 12],
            resumes=resumes,
            launches=[_ready_launch()],
            available_slots=slots,
            now=NOW,
        ).actions
        spent = [a for a in actions if a.kind != "close"]
        assert len(spent) == min(slots, 6), slots


def test_poll_plan_gives_retakes_the_slots_before_resumes():
    since = NOW.replace(hour=0)
    resume = rules.ResumeCandidate(issue=1, reason="upstream", since=since, prior=0, limit=6)
    actions = rules.poll_plan(
        closes=[], retakes=[9], resumes=[resume], launches=[], available_slots=1, now=NOW
    ).actions
    assert actions == [rules.PollAction("retake", 9)]


def test_poll_plan_retakes_nothing_when_the_cap_is_full():
    actions = rules.poll_plan(
        closes=[], retakes=[9], resumes=[], launches=[], available_slots=0, now=NOW
    ).actions
    assert actions == []


def test_poll_plan_retakes_do_not_unblock_a_launch_while_a_resume_still_waits():
    not_due = rules.ResumeCandidate(
        issue=5, reason="account_limit", since=NOW - timedelta(minutes=1), prior=0, limit=6
    )
    actions = rules.poll_plan(
        closes=[],
        retakes=[4],
        resumes=[not_due],
        launches=[_ready_launch()],
        available_slots=3,
        now=NOW,
    ).actions
    assert actions == [rules.PollAction("retake", 4)]


# An approval settles its own subtask's earlier blocking records (GH-201, ADR-0015 addendum).


def _approved(state, subtask="api", version=2, findings=()):
    """`state` after `subtask`'s review at `version` approved: the settlement the approval
    appends, plus that review's own findings, exactly as `review.py` appends them."""
    settled = rules.settled_on_approval(state, subtask, version, list(findings))
    return {
        **state,
        "findings": [
            *state.get("findings", []),
            *rules.finding_records(subtask, version, findings),
        ],
        "approval_settled": [*state.get("approval_settled", []), *settled],
    }


def _statuses(state):
    return {row["claim"]: row["status"] for row in rules.findings_rows(state)}


def test_an_approval_closes_its_own_subtasks_earlier_blocking_row():
    """The apostrophe GH-107 shape: a v1 blocking, a v2 approval with nothing to settle."""
    state = {"findings": rules.finding_records("api", 1, [found()])}
    settled = rules.settled_on_approval(state, "api", 2, [])
    assert settled == [
        {"subtask": "api", "location": "x.py:1", "claim": "double count", "version": 2}
    ]
    approved = _approved(state)
    assert _statuses(approved) == {"double count": "fixed"}
    assert rules.open_findings(approved) == []


def test_an_approval_never_settles_what_is_not_its_subtasks_blocking_record():
    """Each input here must leave the row open: approval is a verdict on this subtask's own
    earlier blocking records and nothing else."""
    rejected = {
        "its own important": rules.finding_records("api", 1, [found(severity="important")]),
        "a plan-scope row": rules.finding_records(
            "api", 1, [found(scope="plan", severity="important")]
        ),
        "a blocking lowered to important": rules.finding_records(
            "api", 1, rules.lower_unbacked_blocks([found(scope="plan")])[0]
        ),
        "a sister's blocking at the same key": rules.finding_records("domain", 1, [found()]),
        "its own observation": rules.finding_records("api", 1, [found(severity="observation")]),
    }
    for name, records in rejected.items():
        state = {"findings": records}
        assert rules.settled_on_approval(state, "api", 2, []) == [], name
        assert {r["status"] for r in rules.findings_rows(_approved(state))} == {"open"}, name


def test_a_key_raised_by_both_the_approved_subtask_and_a_sister_stays_open():
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records("domain", 1, [found()]),
        ]
    }
    (row,) = rules.findings_rows(_approved(state))
    assert row["status"] == "open" and row["subtask"] == "api, domain"


def test_a_sisters_row_at_the_same_location_with_another_claim_stays_open():
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records("domain", 1, [found(claim="something else")]),
        ]
    }
    assert _statuses(_approved(state)) == {"double count": "fixed", "something else": "open"}


def test_a_same_key_important_keeps_the_row_open_after_the_approval():
    """The blocking is covered, the important beside it is not: the row is one key."""
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records("api", 1, [found(severity="important")]),
        ]
    }
    assert rules.settled_on_approval(state, "api", 2, []) != []
    assert _statuses(_approved(state)) == {"double count": "open"}


def test_an_approval_that_raises_the_key_again_settles_nothing():
    state = {"findings": rules.finding_records("api", 1, [found()])}
    again = [found(severity="important")]
    assert rules.settled_on_approval(state, "api", 2, again) == []
    assert _statuses(_approved(state, findings=again)) == {"double count": "open"}


def test_an_approval_at_the_first_version_settles_nothing():
    state = {"findings": rules.finding_records("api", 1, [found()])}
    assert rules.settled_on_approval(state, "api", 1, []) == []
    assert _statuses(_approved(state, version=1)) == {"double count": "open"}


def test_an_approval_covers_every_earlier_version_even_with_gaps():
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records("api", 3, [found()]),
        ]
    }
    assert len(rules.settled_on_approval(state, "api", 5, [])) == 1
    assert _statuses(_approved(state, version=5)) == {"double count": "fixed"}


def test_a_later_regression_reopens_the_row_an_approval_closed():
    """Coverage is per record, never per key: a v3 blocking at the same key is not covered."""
    approved = _approved({"findings": rules.finding_records("api", 1, [found()])})
    assert _statuses(approved) == {"double count": "fixed"}
    regressed = {
        **approved,
        "findings": [*approved["findings"], *rules.finding_records("api", 3, [found()])],
    }
    assert _statuses(regressed) == {"double count": "open"}


def test_a_late_sister_re_raise_stays_open_in_either_append_order():
    state = {"findings": rules.finding_records("api", 1, [found()])}
    approved = _approved(state)
    sister = rules.finding_records("domain", 2, [found()])
    after = {**approved, "findings": [*approved["findings"], *sister]}
    before = {**approved, "findings": [*sister, *approved["findings"]]}
    assert _statuses(after) == {"double count": "open"}
    assert _statuses(before) == {"double count": "open"}


def test_the_reviewers_settled_runs_first_and_is_never_reopened_by_approval_coverage():
    """A location in `settled_findings` closes the row for good, even beside a later record
    no settlement covers."""
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records("domain", 2, [found()]),
        ],
        "settled_findings": ["x.py:1"],
    }
    assert _statuses(state) == {"double count": "fixed"}
    assert _statuses(_approved(state)) == {"double count": "fixed"}


def test_an_approval_does_not_close_a_key_the_integration_also_raised():
    """`api` and `integration` both raised it; `api` approving at v2 leaves it open until
    the integration review settles it."""
    state = {
        "findings": [
            *rules.finding_records("api", 1, [found()]),
            *rules.finding_records(INTEGRATION, 1, [found()]),
        ]
    }
    assert _statuses(_approved(state)) == {"double count": "open"}
    settled = {**_approved(state), "settled_findings": ["x.py:1"]}
    assert _statuses(settled) == {"double count": "fixed"}


# --- GH-204: what delivery does with a remote task branch that has diverged ----------


def _pr(number, state):
    return BranchPullRequest(number, state, f"https://github.com/o/r/pull/{number}")


def _plan(remote="abc123", in_head=False, prs=(), in_archive=False):
    return rules.push_plan(remote, in_head, list(prs), in_archive)


def test_push_plan_pushes_when_the_remote_has_no_branch_or_is_inside_head():
    assert _plan(remote=None).kind == "push"
    assert _plan(in_head=True).kind == "push"
    # Pull requests are never consulted for a remote that fast-forwards: even an open
    # one does not matter.
    assert _plan(in_head=True, prs=[_pr(1, "OPEN")]).kind == "push"


def test_push_plan_refuses_a_diverged_branch_with_an_open_pull_request():
    plan = _plan(prs=[_pr(1, "CLOSED"), _pr(2, "OPEN")], in_archive=True)
    # A refusal still carries the observed remote sha, so its reason can name it.
    assert (plan.kind, plan.reason, plan.old_sha) == ("refuse", "open_pull_request", "abc123")


def test_push_plan_refuses_a_merged_pull_request_and_open_wins_over_merged():
    merged = _plan(prs=[_pr(1, "CLOSED"), _pr(3, "MERGED")], in_archive=True)
    assert (merged.kind, merged.reason) == ("refuse", "merged_pull_request")
    both = _plan(prs=[_pr(3, "MERGED"), _pr(4, "OPEN")])
    assert both.reason == "open_pull_request"


def test_push_plan_replaces_when_every_pull_request_is_closed():
    plan = _plan(prs=[_pr(1, "CLOSED"), _pr(2, "CLOSED")])
    assert (plan.kind, plan.old_sha, plan.reason) == ("replace", "abc123", None)


def test_push_plan_with_no_pull_request_replaces_only_its_own_archive():
    assert _plan(in_archive=True).kind == "replace"
    assert _plan(in_archive=True).old_sha == "abc123"
    refused = _plan(in_archive=False)
    assert (refused.kind, refused.reason) == ("refuse", "unowned_divergence")


def test_archived_task_branches_are_this_tasks_own_in_both_spellings():
    refs = [
        "archive/run1-abc1234/agentic/GH-1",
        "archive/run2-unknown/agentic/GH-1",
        "archive/run3-04abd2c/GH-1",
        "archive/run1-abc1234/agentic/GH-12",
        "archive/run1-abc1234/GH-12",
        "archive/run1-abc1234/agentic/GH-1.s2",
        "archive/run1-abc1234/GH-1.s2",
        "agentic/GH-1",
        "archive/other/agentic/GH-1",
    ]
    assert rules.archived_task_branches(refs, "GH-1") == [
        "archive/run1-abc1234/agentic/GH-1",
        "archive/run2-unknown/agentic/GH-1",
        "archive/run3-04abd2c/GH-1",
    ]
    assert rules.archived_task_branches(refs, "GH-12") == [
        "archive/run1-abc1234/agentic/GH-12",
        "archive/run1-abc1234/GH-12",
    ]


def test_replace_record_sets_on_a_replace_and_clears_on_a_refusal():
    replace_plan = _plan(prs=[_pr(1, "CLOSED")])
    assert rules.replace_record(None, replace_plan, False) == "abc123"
    assert rules.replace_record("old", replace_plan, True) == "abc123"
    refuse = _plan(prs=[_pr(1, "OPEN")])
    assert rules.replace_record("old", refuse, False) is None


def test_replace_record_clears_after_a_lost_lease_and_keeps_after_a_landed_replace():
    push = _plan(remote="new", in_head=True)
    # Lost lease: the remote is still (or contains) the recorded sha.
    assert rules.replace_record("old", push, recorded_in_remote=True) is None
    # Landed replace: the remote is our pushed head, which does not contain `old`.
    assert rules.replace_record("old", push, recorded_in_remote=False) == "old"
    # Nothing recorded, nothing to keep.
    assert rules.replace_record(None, push, recorded_in_remote=False) is None
    # An absent remote with a recorded sha: the remote no longer contains it either.
    assert rules.replace_record("old", _plan(remote=None), False) == "old"


def test_push_refused_reason_names_the_branch_the_sha_and_the_pull_requests():
    plan = _plan(prs=[_pr(1, "CLOSED"), _pr(2, "OPEN")])
    reason = rules.push_refused_reason(plan, [_pr(1, "CLOSED"), _pr(2, "OPEN")], "agentic/GH-9")
    assert "agentic/GH-9" in reason and "abc123" in reason and "#2" in reason
    assert "open" in reason.lower()
    merged = rules.push_plan("abc123", False, [_pr(5, "MERGED")], False)
    assert "merged" in rules.push_refused_reason(merged, [_pr(5, "MERGED")], "agentic/GH-9").lower()
    unowned = rules.push_plan("abc123", False, [], False)
    text = rules.push_refused_reason(unowned, [], "agentic/GH-9")
    assert "abc123" in text and "archive" in text.lower()


def test_replaced_note_names_the_sha_and_how_to_recover_it():
    note = rules.replaced_note("abc123", [_pr(1, "CLOSED"), _pr(2, "CLOSED")], "agentic/GH-9")
    assert "`agentic/GH-9`" in note and "`abc123`" in note
    assert "#1" in note and "#2" in note
    assert "git push origin abc123:refs/heads/" in note
    bare = rules.replaced_note("abc123", [], "agentic/GH-9")
    assert "`abc123`" in bare and "#" not in bare


def test_entering_carries_the_landing_restart_and_adds_nothing_when_there_is_nothing():
    task = {"id": "GH-1", "title": "t", "description": "plain"}
    assert rules.entering(task, cap=False, carried=Carried(1, 2.5, "", "", ())) == {
        **task,
        "landing_restarts": 1,
        "landing_cost_carried": 2.5,
    }
    assert rules.entering(task, cap=False, carried=Carried(0, 0.0, "", "", ())) == task
    assert rules.entering(task, cap=False, carried=None) == task


HUMAN_TEXT = (
    "### Decision 1\n\nEscalated: the plan did not fit\n\nSplit it in two.\n\n"
    "### Decision 2\n\nKeep the CLI as is."
)


def test_entering_seeds_the_human_section_verbatim_and_the_next_decision_is_numbered_three():
    task = {"id": "GH-1", "title": "t", "description": "plain"}
    entered = rules.entering(task, cap=False, carried=Carried(0, 0.0, "", HUMAN_TEXT, ()))
    assert entered == {**task, "sections": {"Human": HUMAN_TEXT}}
    assert "landing_restarts" not in entered and "landing_cost_carried" not in entered
    grown = rules.human_section(cast(Any, entered), None, "Another call.")
    assert "### Decision 3\n\nAnother call." in grown
    assert grown.startswith(HUMAN_TEXT)


def test_entering_with_a_restart_and_a_human_section_carries_both():
    task = {"id": "GH-1", "title": "t", "description": "plain"}
    entered = rules.entering(task, cap=False, carried=Carried(1, 2.5, "diag", HUMAN_TEXT, ()))
    assert entered["sections"] == {"Human": HUMAN_TEXT}
    assert (entered["landing_restarts"], entered["landing_cost_carried"]) == (1, 2.5)
    assert "diag" not in str(entered["sections"])


def test_entered_human_section_reaches_the_prompt_a_role_of_the_fresh_run_renders():
    task = {"id": "GH-1", "title": "t", "description": "plain"}
    entered = rules.entering(task, cap=False, carried=Carried(0, 0.0, "", HUMAN_TEXT, ()))
    delta = rules.interview_retake_delta(
        cast(Any, entered), INTERVIEW_DEFINITION, "agentic/GH-1", "abc"
    )
    assert f"## Human\n\n{HUMAN_TEXT}" in delta


# --- GH-257: `retake_calendar` is `_calendar`'s body, asked by the landing too ----------------


@pytest.mark.parametrize(
    ("reason", "prior", "chase_prior"),
    [
        ("account_limit", 3, 3),
        ("account_limit", 0, rules.ACCOUNT_LIMIT_CHASE_MAX),
        ("account_limit", 5, rules.ACCOUNT_LIMIT_CHASE_MAX + 4),
        ("upstream", 2, 2),
        ("network", 1, rules.ACCOUNT_LIMIT_CHASE_MAX + 4),
    ],
)
def test_retake_calendar_is_what_calendar_gives_for_the_same_fields(reason, prior, chase_prior):
    candidate = rules.ResumeCandidate(
        7, reason, NOW, prior, len(rules.RETAKE_SCHEDULE), chase_prior
    )
    assert rules.retake_calendar(reason, prior, chase_prior, len(rules.RETAKE_SCHEDULE)) == (
        rules._calendar(candidate)
    )


def test_retake_calendar_names_the_flat_hourly_and_growing_calendars():
    limit = len(rules.RETAKE_SCHEDULE)
    flat = rules.retake_calendar("account_limit", 2, 2, limit)
    assert flat == (rules.ACCOUNT_LIMIT_SCHEDULE, 2, limit)
    past = rules.ACCOUNT_LIMIT_CHASE_MAX
    assert rules.retake_calendar("account_limit", 2, past + 3, limit) == (
        rules.ACCOUNT_LIMIT_HOURLY,
        3,
        len(rules.ACCOUNT_LIMIT_HOURLY),
    )
    assert rules.retake_calendar("upstream", 2, past, limit) == (rules.RETAKE_SCHEDULE, 2, limit)


@pytest.mark.parametrize(
    ("kind", "counts"),
    [("role", True), ("land_model", True), ("merge", False), ("land_checks", False)],
)
def test_only_the_children_that_can_spend_a_model_count_against_the_cap(kind, counts):
    assert rules.counts_against_cap(kind) is counts


def test_counts_against_cap_decides_every_run_kind():
    for kind in get_args(rules.RunKind):
        assert isinstance(rules.counts_against_cap(kind), bool)


# `adr_probe_wanted` is the one answer to "should the ADR-collision read run", derived from the
# order of `auto_merge_decision` the way `conflict_probe_wanted` is.

PROBE_HEAD = "a" * 40


def _probe_facts(**over) -> rules.MergeFacts:
    base = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=PROBE_HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=PROBE_HEAD,
        in_eval_set=False,
        touched_paths=("docs/adr/0004-x.md",),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=None,
    )
    return replace(base, **over)


def test_the_adr_probe_is_wanted_for_a_ready_task_with_an_open_pull_request():
    assert rules.adr_probe_wanted(_probe_facts()) is True


def test_the_adr_probe_is_not_wanted_for_a_task_that_is_not_ready():
    assert rules.adr_probe_wanted(_probe_facts(status="developing")) is False


def test_the_adr_probe_is_not_wanted_for_a_pull_request_that_is_not_open():
    assert rules.adr_probe_wanted(_probe_facts(pr_state="CLOSED")) is False
    assert rules.adr_probe_wanted(_probe_facts(pr_state=None)) is False


def test_the_adr_probe_is_wanted_for_an_unapproved_task_because_the_collision_comes_first():
    """The order is the decision's, not a list kept beside it: `adr_number_taken` is ahead of
    `not_approved`, so an unapproved head with a collision is a renumber, not a re-review."""
    facts = _probe_facts(reviewed_unit_approved=False)
    assert rules.auto_merge_decision(facts) == rules.MergeDecision("refuse", "not_approved")
    assert rules.adr_probe_wanted(facts) is True


def test_the_adr_probe_gate_ignores_the_collision_fact_it_is_asked_to_derive():
    renaming = Renaming((("docs/adr/0004-x.md", "docs/adr/0010-x.md"),), {})
    for known in (None, renaming):
        assert rules.adr_probe_wanted(replace(_probe_facts(), adr_collision=known)) is True


# The estimate's one home (invariants 1-3, 6): the thresholds, the factor, the decision
# and the reading of the estimate in force live only in `rules`.


def test_estimate_fits_at_the_limits_and_not_one_past():
    assert rules.estimate_fits(150, 40, None)
    assert not rules.estimate_fits(150.01, 40, None)
    assert not rules.estimate_fits(150, 41, None)


def test_estimate_fits_decides_on_the_larger_of_the_estimate_and_the_history_reference():
    # A declared 100 that the bucket's p90 says is 200 does not fit: never the estimate alone.
    assert not rules.estimate_fits(100, 10, 200.0)
    # A reference below the estimate changes nothing.
    assert rules.estimate_fits(100, 10, 50.0)
    assert not rules.estimate_fits(160, 10, 50.0)


def test_estimate_cap_is_twice_the_estimate():
    assert rules.estimate_cap(110.0) == 220.0


def test_estimate_in_force_is_the_last_revision_else_the_plans_estimate_else_none():
    assert rules.estimate_in_force({}) is None
    plan = {"estimate": {"cost_usd": 110.0, "files": 35}}
    assert rules.estimate_in_force(plan) == 110.0
    revised = {
        **plan,
        "estimate_revisions": [
            {"cost_usd": 150.0, "source": "human"},
            {"cost_usd": 190.0, "source": "human"},
        ],
    }
    assert rules.estimate_in_force(revised) == 190.0
    # An empty list of revisions falls back to the plan's estimate, never to zero.
    assert rules.estimate_in_force({**plan, "estimate_revisions": []}) == 110.0


def test_delivered_files_is_the_patch_that_records_the_actual_files():
    assert rules.delivered_files(12) == {"actual_files": 12}


def test_auto_resumable_never_retakes_an_estimate_escalation():
    # Rejected input: an `estimate` pause, even with an external failure before it.
    state = {"pause_log": [_failure_entry("upstream"), _escalation_entry("estimate")]}
    assert rules.auto_resumable(state) is None
    assert rules.auto_resumable({"pause_log": [_escalation_entry("estimate")]}) is None


# --- The split (ADR-0054): a plan that does not fit ends in `split`, not in a build ---

PARTS = [{"title": "First", "body": "Do it.", "depends_on": []}]


def test_after_plan_routes_a_plan_with_parts_to_split_and_one_without_to_integrate():
    assert rules.after_plan({"status": "planned", "parts": PARTS}) == "split"
    assert rules.after_plan({"status": "planned", "parts": []}) == "integrate"
    assert rules.after_plan({"status": "planned"}) == "integrate"
    assert rules.after_plan({"status": "planned", "parts": PARTS, "escalated": "x"}) is None


def test_the_split_marker_round_trips_and_names_its_task_and_part():
    marker = rules.split_marker("GH-12", 3)
    assert marker == "<!-- agentic-split: GH-12 part 3 -->"
    assert rules.split_part_of(f"{marker}\nBody") == ("GH-12", 3)
    assert rules.split_part_of(f"Blocked by: #4\n\ntext\n{marker}") == ("GH-12", 3)


def test_split_part_of_reads_no_marker_from_text_that_only_resembles_one():
    assert rules.split_part_of("") is None
    assert rules.split_part_of("A plain description of a task.") is None
    assert rules.split_part_of("<!-- agentic-split: GH-12 -->") is None
    assert rules.split_part_of("<!-- agentic:stack-review anchor=abc -->") is None
    assert rules.split_part_of("agentic-split: GH-12 part 3") is None


def test_split_is_an_end_with_nothing_to_resume():
    done = {"status": "split", "type": "feature", "parts": PARTS}
    assert rules.resumed_status(done) == "split"
    assert rules.resume_target({**done, **rules.resumed(done, None)}) is None


def test_a_task_paused_while_splitting_resumes_at_split_without_planning_again():
    paused = {
        "status": "waiting_on_human",
        "type": "feature",
        "parts": PARTS,
        "split_issues": {"1": "https://github.com/o/r/issues/5"},
        **rules.escalation("failure", "gh failed"),
    }
    resumed = task.apply(paused, rules.resumed(paused, None))
    assert rules.resume_target(resumed) == "split"


def test_resuming_an_estimate_escalation_of_a_part_waives_the_fit_and_replans():
    paused = {
        "status": "waiting_on_human",
        "type": "platform_enhancement",
        "parts": PARTS,
        "estimate": {"cost_usd": 400.0, "files": 90},
        **rules.escalation("estimate", "a part of a split does not fit"),
    }
    patch = rules.resumed(paused, "Build it as one.")
    assert patch["fit_waived"] is True
    assert patch["parts"] == []
    resumed = task.apply(paused, patch)
    assert rules.resume_target(resumed) == "plan"
    # Nothing here decides the estimate in force: only the cap's own resumption does.
    assert "estimate_revisions" not in patch and "estimate" not in patch


def test_resuming_any_other_escalation_leaves_the_fit_alone():
    paused = {"status": "waiting_on_human", "parts": PARTS, **rules.escalation("failure", "x")}
    patch = rules.resumed(paused, None)
    assert "fit_waived" not in patch and "parts" not in patch


# GH-294 pin: with no `important` open the decision is what it was before the final
# important pass existed. This table is written first and never edited by that change.

PIN_HEAD = "a" * 40
PIN_MASTER = "b" * 40


def _pin_facts(**over) -> rules.MergeFacts:
    base = rules.MergeFacts(
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=PIN_HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=PIN_HEAD,
        in_eval_set=False,
        touched_paths=("agentic/x.py",),
        attempts=1,
        checks_head=PIN_HEAD,
        checks_master=PIN_MASTER,
        checks_passed=True,
        current_master=PIN_MASTER,
    )
    return replace(base, **over)


_PIN_RED = ci.CiVerdict("red", ("t",), ())
_PIN_PENDING = ci.CiVerdict("pending", (), ("t",))
_PIN_CI = {"ci_required": ("t",), "ci_head": PIN_HEAD}

NO_IMPORTANT_DECISIONS = [
    ("no findings", {}, ("merge", None)),
    ("only blocking", {"open_blocking": 2}, ("refuse", "open_findings_blocking")),
    ("not ready", {"status": "developing"}, ("refuse", "not_ready")),
    ("not approved", {"reviewed_unit_approved": False}, ("refuse", "not_approved")),
    ("ci red", {**_PIN_CI, "ci_verdict": _PIN_RED}, ("refuse", "ci_failed")),
    (
        "ci pending, spent",
        {**_PIN_CI, "ci_verdict": _PIN_PENDING, "ci_waited_s": ci.CI_WAIT_MAX_S},
        ("refuse", "ci_missing"),
    ),
    (
        "ci unreadable, spent",
        {
            **_PIN_CI,
            "ci_unreadable": ci.CiUnreadable("gh_unavailable"),
            "ci_waited_s": ci.CI_WAIT_MAX_S,
        },
        ("refuse", "ci_unreadable"),
    ),
    ("checks failed", {"checks_passed": False}, ("refuse", "checks_failed")),
    (
        "checks failed on a conflict",
        {"checks_passed": False, "failed_step": "conflict"},
        ("refuse", "checks_failed"),
    ),
    ("merge conflict", {"conflicts": True}, ("refuse", "merge_conflict")),
    (
        "adr number taken",
        {"adr_collision": Renaming((("docs/adr/0000-x.md", "docs/adr/0001-x.md"),), {})},
        ("refuse", "adr_number_taken"),
    ),
    (
        "attempts exhausted",
        {"checks_master": "d" * 40, "attempts": rules.MERGE_ATTEMPTS_MAX},
        ("refuse", "attempts_exhausted"),
    ),
    (
        "merge failed",
        {"merge_failures": rules.MERGE_CALLS_MAX},
        ("refuse", "merge_failed"),
    ),
    ("checks to run", {"checks_passed": None, "attempts": 0}, ("run_checks", None)),
]


@pytest.mark.parametrize(
    ("over", "expected"),
    [(o, e) for _, o, e in NO_IMPORTANT_DECISIONS],
    ids=[n for n, _, _ in NO_IMPORTANT_DECISIONS],
)
def test_with_no_important_open_the_merge_decision_is_what_it_was(over, expected):
    assert rules.auto_merge_decision(_pin_facts(**over)) == rules.MergeDecision(*expected)


# GH-294: `reported_severity` and the partition of the open `important` findings.


def test_a_blocking_lowered_for_no_evidence_keeps_what_the_reviewer_reported():
    lowered, _ = rules.lower_unbacked_blocks([found(evidence="  ")])
    (record,) = rules.finding_records("api", 1, lowered)
    assert record["severity"] == "observation"
    assert record["reported_severity"] == "blocking"


def test_a_blocking_of_plan_scope_lowered_to_important_keeps_what_the_reviewer_reported():
    lowered, _ = rules.lower_unbacked_blocks([found(scope="plan")])
    (record,) = rules.finding_records("api", 1, lowered)
    assert (record["severity"], record["reported_severity"]) == ("important", "blocking")


def test_a_finding_the_system_did_not_lower_carries_no_reported_severity():
    kept = [
        found(),
        found(claim="b", severity="important"),
        found(claim="c", severity="important", scope="outside"),
    ]
    lowered, _ = rules.lower_unbacked_blocks(kept)
    assert all("reported_severity" not in r for r in rules.finding_records("api", 1, lowered))
    assert all("reported_severity" not in r for r in rules.finding_records("api", 1, kept))


def _part(subtasks, *records):
    """A document with `subtasks` (name -> status) and the raw records `(subtask, claim,
    severity, scope)`, all open."""
    return {
        "subtasks": {name: {"status": status} for name, status in subtasks.items()},
        "findings": [
            {
                "subtask": who,
                "version": 1,
                "location": "x.py:1",
                "claim": claim,
                "severity": severity,
                "scope": scope,
                "category": "spec-behaviour",
                "status": "open",
            }
            for who, claim, severity, scope in records
        ],
    }


def _claims(rows):
    return [row["claim"] for row in rows]


def test_the_partition_sorts_important_rows_by_scope_and_owner():
    state = _part(
        {"domain": "approved", "api": "approved", INTEGRATION: "approved"},
        ("domain", "d", "important", "subtask"),
        ("api, domain", "shared", "important", "subtask"),
        ("api", "p", "important", "plan"),
        ("api", "o", "important", "outside"),
        ("api", "blocking one", "blocking", "subtask"),
        ("api", "a note", "observation", "subtask"),
    )
    part = rules.important_partition(state)
    assert {name: _claims(rows) for name, rows in part.owned.items()} == {
        "domain": ["d", "shared"],
        "api": ["shared"],
    }
    assert _claims(part.plan) == ["p"]
    assert _claims(part.outside) == ["o"]


def test_an_outside_row_is_never_owned_nor_plan():
    state = _part(
        {"api": "approved"},
        ("api", "o", "important", "outside"),
        ("zzz", "o2", "important", "outside"),
    )
    part = rules.important_partition(state)
    assert part.owned == {} and part.plan == ()
    assert _claims(part.outside) == ["o", "o2"]


def test_a_row_whose_name_is_no_subtask_goes_to_the_reviewed_unit():
    state = _part(
        {"api": "approved", INTEGRATION: "approved"}, ("ghost", "g", "important", "subtask")
    )
    assert {n: _claims(r) for n, r in rules.important_partition(state).owned.items()} == {
        INTEGRATION: ["g"]
    }
    single = _part({"api": "approved"}, ("ghost", "g", "important", "subtask"))
    assert list(rules.important_partition(single).owned) == ["api"]


def test_the_partition_reads_open_rows_only():
    state = _part({"api": "approved"}, ("api", "d", "important", "subtask"))
    state["settled_findings"] = ["x.py:1"]
    part = rules.important_partition(state)
    assert part.owned == {} and part.plan == () and part.outside == ()


def test_a_document_with_an_outside_record_parses_and_folds():
    state = _part({"api": "approved"}, ("api", "o", "important", "outside"))
    parsed = document.parse(
        document.render({**state, "id": "GH-1", "title": "t", "description": "d"})
    )
    assert _claims(rules.findings_rows(parsed)) == ["o"]
    assert _claims(rules.open_findings(parsed)) == ["o"]


def test_the_task_s_own_important_refuses_until_the_pass_or_the_follow_up():
    refused = rules.MergeDecision("refuse", "open_findings")
    merged = rules.MergeDecision("merge", None)
    assert rules.auto_merge_decision(_pin_facts(open_important=1)) == refused
    assert (
        rules.auto_merge_decision(_pin_facts(open_important=1, important_pass_done=True)) == merged
    )
    # A document the landing closed before the pass existed lands as it did.
    assert rules.auto_merge_decision(_pin_facts(open_important=1, followups_filed=True)) == merged


def test_an_outside_row_refuses_until_the_follow_up_and_a_done_pass_does_not_cover_it():
    refused = rules.MergeDecision("refuse", "open_findings")
    assert rules.auto_merge_decision(_pin_facts(open_outside=1)) == refused
    assert (
        rules.auto_merge_decision(_pin_facts(open_outside=1, important_pass_done=True)) == refused
    )
    assert rules.auto_merge_decision(_pin_facts(open_outside=1, followups_filed=True)) == (
        rules.MergeDecision("merge", None)
    )


def test_the_blocking_refusal_still_comes_before_the_important_ones():
    facts = _pin_facts(open_blocking=1, open_important=1, open_outside=1)
    assert rules.auto_merge_decision(facts) == rules.MergeDecision(
        "refuse", "open_findings_blocking"
    )


def test_ending_a_task_without_a_delivery_stamps_finished_at_and_nothing_else_does():
    assert rules.finished("discarded", "2026-10-22T10:00:00+00:00") == {
        "status": "discarded",
        "finished_at": "2026-10-22T10:00:00+00:00",
    }
    assert rules.finished("split", "2026-10-22T10:00:00+00:00")["finished_at"]
    with pytest.raises(ValueError, match="ready_for_review"):
        rules.finished("ready_for_review", "2026-10-22T10:00:00+00:00")
    # The document writes the key only when set: one written before it is byte-identical.
    base = {"id": "T-1", "title": "t", "description": "d", "status": "discarded"}
    assert "finished_at" not in document.render(base)
    stamped = document.render({**base, "finished_at": "2026-10-22T10:00:00+00:00"})
    assert "finished_at: '2026-10-22T10:00:00+00:00'" in stamped


# --- crash retake: a run that raised under the graph (GH-308) ---

CRASHED = rules.LockFacts(other_boot=False, alive=False, crashed_version="abc123")
CRASH_PREFIX = "Retaken after crash: "


def _crash_verdict(lock=CRASHED, at_version=0, total=0, key="def456", prior=0):
    return rules.interrupted_run(
        _interrupted_task(),
        lock,
        prior,
        crash_prior_at_version=at_version,
        crash_prior_total=total,
        version_key=key,
    )


def test_crash_version_key_strips_dirty_and_refuses_unknown():
    assert rules.crash_version_key("abc123") == "abc123"
    assert rules.crash_version_key("abc123+dirty") == "abc123"
    assert rules.crash_version_key("unknown") is None
    assert rules.crash_version_key(None) is None
    assert rules.crash_version_key("") is None


def test_the_crash_comment_names_both_versions_and_the_count():
    assert (
        rules.crash_retake_comment("abc+dirty", "def", 2, 2)
        == "run raised under abc+dirty; resuming under def (retake 2 of 2)"
    )


def test_crash_retake_counts_read_what_the_formatter_wrote():
    mine = CRASH_PREFIX + rules.crash_retake_comment("a", "def456", 1, 2)
    other_version = CRASH_PREFIX + rules.crash_retake_comment("a", "def4567", 1, 2)
    prefix_of_mine = CRASH_PREFIX + rules.crash_retake_comment("a", "def45", 1, 2)
    unrelated = "Auto-resumed: resuming under def456 (retake 1 of 2)"
    bodies = [mine, other_version, prefix_of_mine, unrelated, "a human said so"]
    assert rules.crash_retake_counts(bodies, "def456", CRASH_PREFIX) == (1, 3)
    assert rules.crash_retake_counts([], "def456", CRASH_PREFIX) == (0, 0)


def test_a_crash_mark_under_both_caps_is_a_crash_retake():
    assert _crash_verdict() == "retake_crash"
    one_short = rules.CRASH_RETAKE_PER_VERSION_MAX - 1
    assert _crash_verdict(at_version=one_short) == "retake_crash"


def test_the_per_version_cap_spent_leaves_the_task_to_the_human():
    assert _crash_verdict(at_version=rules.CRASH_RETAKE_PER_VERSION_MAX) == "human"


def test_the_total_cap_spent_leaves_the_task_to_the_human_even_with_a_fresh_version():
    assert _crash_verdict(total=rules.CRASH_RETAKE_TOTAL_MAX, at_version=0) == "human"
    assert _crash_verdict(total=rules.CRASH_RETAKE_TOTAL_MAX - 1) == "retake_crash"


def test_an_unreadable_version_key_never_retakes_a_crash():
    assert _crash_verdict(key=None) == "human"


def test_a_crash_mark_with_the_caps_spent_falls_to_the_reboot_branch_on_another_boot():
    lock = rules.LockFacts(other_boot=True, alive=False, crashed_version="abc123")
    spent = rules.CRASH_RETAKE_PER_VERSION_MAX
    assert _crash_verdict(lock, at_version=spent) == "retake"
    assert _crash_verdict(lock, at_version=spent, prior=rules.RETAKE_AFTER_REBOOT_MAX) == "human"
    assert _crash_verdict(lock, key=None) == "retake"


def test_a_crash_mark_proves_the_process_ended_even_when_the_pid_reads_alive():
    lock = rules.LockFacts(other_boot=False, alive=True, crashed_version="abc123")
    assert _crash_verdict(lock) == "retake_crash"
    spent = rules.CRASH_RETAKE_TOTAL_MAX
    assert _crash_verdict(lock, total=spent) == "human"


def test_a_crash_mark_does_not_override_the_document_checks():
    for task_doc in (
        {"status": "developing", "escalated": "needs a human"},
        {"status": "ready_for_review"},
    ):
        assert rules.interrupted_run(task_doc, CRASHED, 0, version_key="def456") == "none", task_doc
    assert rules.interrupted_run({}, CRASHED, 0, version_key="def456") == "human"


def test_a_lock_without_a_crash_mark_ignores_the_crash_arguments():
    assert _crash_verdict(SAME_BOOT) == "human"
    assert _crash_verdict(PREVIOUS_BOOT) == "retake"


def test_poll_plan_crash_retakes_share_the_slots_with_reboot_retakes_lowest_first():
    plan = dict(closes=[], resumes=[], launches=[], now=NOW)
    actions = rules.poll_plan(retakes=[6], crash_retakes=[4, 9], available_slots=2, **plan).actions
    assert actions == [
        rules.PollAction("retake", 4, reason="crash"),
        rules.PollAction("retake", 6),
    ]
    assert rules.PollAction("retake", 6).reason == "reboot"


def test_poll_plan_crash_retakes_take_a_slot_from_resumes_and_launches():
    plan = dict(closes=[], resumes=[], launches=[_ready_launch()], now=NOW)
    only_retake = rules.poll_plan(crash_retakes=[4], available_slots=1, **plan).actions
    assert only_retake == [rules.PollAction("retake", 4, reason="crash")]
    with_launch = rules.poll_plan(crash_retakes=[4], available_slots=2, **plan).actions
    assert with_launch == [
        rules.PollAction("retake", 4, reason="crash"),
        rules.PollAction("launch", 9),
    ]
    assert rules.poll_plan(crash_retakes=[4], available_slots=0, **plan).actions == []
