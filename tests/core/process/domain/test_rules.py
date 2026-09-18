from agentic.core.process.domain import rules, task
from agentic.core.process.domain.denials import denial_for
from agentic.core.process.domain.roles import FINDINGS, PAIR_PM_PA, PJM, PRODUCT_ANALYST_BUG
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


def test_denial_note_names_the_count_and_the_role_rule():
    # Glossary: Negación — one line, system-written, with how many and the rule.
    note = rules.denial_note(PRODUCT_ANALYST_BUG, 8)
    assert note == f"_The system denied 8 write attempts: {denial_for(PRODUCT_ANALYST_BUG)}_"


def test_denial_note_uses_the_singular_for_one():
    note = rules.denial_note(PJM, 1)
    assert "1 write attempt:" in note
    assert "1 write attempts:" not in note
