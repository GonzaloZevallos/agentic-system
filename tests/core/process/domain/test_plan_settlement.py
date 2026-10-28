"""The settlement of `PM: final validation` rows (GH-374, ADR-0015 addendum): who writes it
(`plan_settlement`), how the fold applies it (`rules.findings_rows`), the one reader of "PM's
approval is live" (`rules.pm_final_approved_version`) and the GH-374 ledger it was made for."""

from agentic.core.process.domain import plan_settlement, rules
from agentic.core.process.domain.roles import PAIR_PM_TECH_LEAD, PM_FINAL
from agentic.core.process.domain.task import PlanSettledRow

PM = PM_FINAL.name
HEAD = "e9c47772" + "0" * 32


def rec(location, claim, version, *, subtask="integration", severity="blocking", scope="plan"):
    return {
        "subtask": subtask,
        "version": version,
        "location": location,
        "claim": claim,
        "severity": severity,
        "scope": scope,
        "category": "implementation",
        "status": "open",
    }


def pm_run(version):
    return {"role": "PM", "section": PM, "version": version}


def pm_return():
    return {"pair": PAIR_PM_TECH_LEAD.key, "category": "implementation", "reason": "x"}


def state(**more):
    base = {
        "type": "feature",
        "flags": {},
        "subtasks": {"integration": {"status": "approved", "reviewed_head": HEAD}},
        "runs": [],
        "change_history": [],
        "findings": [],
    }
    return {**base, **more}


def row(location, claim, version, by="approved") -> PlanSettledRow:
    return {"location": location, "claim": claim, "version": version, "by": by}


def keys(rows):
    return {(r["claim"], r["version"], r["by"]) for r in rows}


# --- the one reader of "PM's approval is live" ------------------------------------------------


def test_the_approved_version_is_the_latest_pm_run_while_no_return_matches_it():
    approving = state(runs=[pm_run(1), pm_run(2)], change_history=[pm_return()])
    assert rules.pm_final_approved_version(approving) == 2


def test_the_approved_version_is_zero_without_a_run_or_when_a_return_matches_every_run():
    assert rules.pm_final_approved_version(state()) == 0
    returned = state(runs=[pm_run(1)], change_history=[pm_return()])
    assert rules.pm_final_approved_version(returned) == 0


def test_a_return_of_another_pair_is_not_a_pm_return():
    other = {"pair": "someone_else", "category": "implementation", "reason": "x"}
    assert rules.pm_final_approved_version(state(runs=[pm_run(1)], change_history=[other])) == 1


# --- on_approval -------------------------------------------------------------------------------


def test_an_approval_settles_each_earlier_open_key_once_as_approved():
    s = state(
        findings=[
            rec(PM, "Phone captures exist", 1),
            rec(PM, "phone  captures EXIST", 2),
            rec(PM, "Other criterion", 1),
        ]
    )
    got = plan_settlement.on_approval(s, 3)
    assert keys(got) == {
        ("Phone captures exist", 3, "approved"),
        ("Other criterion", 3, "approved"),
    }


def test_an_approval_leaves_a_record_at_or_above_its_version_open():
    s = state(findings=[rec(PM, "late", 3), rec(PM, "later", 4)])
    assert plan_settlement.on_approval(s, 3) == []


def test_an_approval_never_touches_another_location():
    s = state(
        findings=[
            rec("QA Performance", "slow", 1),
            rec("full_checks:tests", "red", 1),
            rec("agentic/x.py", "bug", 1, scope="subtask", subtask="api"),
            rec(PM, "criterion", 1),
        ]
    )
    assert keys(plan_settlement.on_approval(s, 2)) == {("criterion", 2, "approved")}


def test_a_dismissed_record_is_not_settled_again():
    s = state(findings=[{**rec(PM, "noise", 1), "status": "dismissed"}])
    assert plan_settlement.on_approval(s, 2) == []


# --- on_return ---------------------------------------------------------------------------------


def test_a_return_supersedes_only_earlier_keys_it_does_not_raise_again():
    s = state(
        findings=[
            rec(PM, "kept", 1),
            rec(PM, "dropped", 1),
            rec(PM, "Kept", 2),
        ]
    )
    got = plan_settlement.on_return(s, 2, ["KEPT"])
    assert keys(got) == {("dropped", 2, "superseded")}


def test_a_return_leaves_its_own_records_and_other_locations_open():
    s = state(findings=[rec(PM, "own", 2), rec("QA Performance", "slow", 1)])
    assert plan_settlement.on_return(s, 2, []) == []


# --- pending -----------------------------------------------------------------------------------


def test_pending_is_empty_without_a_live_approval():
    s = state(findings=[rec(PM, "criterion", 1)])
    assert plan_settlement.pending(s) == []
    returned = state(
        findings=[rec(PM, "criterion", 1)], runs=[pm_run(1)], change_history=[pm_return()]
    )
    assert plan_settlement.pending(returned) == []


def test_pending_is_what_the_live_approval_would_settle_and_nothing_twice():
    s = state(
        findings=[rec(PM, "a", 1), rec(PM, "b", 2)],
        runs=[pm_run(1), pm_run(2), pm_run(3)],
        change_history=[pm_return(), pm_return()],
    )
    assert keys(plan_settlement.pending(s)) == {("a", 3, "approved"), ("b", 3, "approved")}
    applied = {**s, "plan_settled": plan_settlement.pending(s)}
    assert plan_settlement.pending(applied) == []


def test_pending_still_offers_a_record_a_settled_row_does_not_reach():
    s = state(
        findings=[rec(PM, "a", 1), rec(PM, "a", 3)],
        runs=[pm_run(1), pm_run(2), pm_run(4)],
        change_history=[pm_return(), pm_return()],
        plan_settled=[row(PM, "a", 2)],
    )
    assert keys(plan_settlement.pending(s)) == {("a", 4, "approved")}


# --- the fold ----------------------------------------------------------------------------------


def statuses(s):
    return {r["claim"]: r["status"] for r in rules.findings_rows(s)}


def test_the_fold_closes_a_record_below_a_settled_row_and_keeps_one_above_it():
    s = state(
        findings=[rec(PM, "a", 1), rec(PM, "b", 3)],
        plan_settled=[row(PM, "A ", 2), row(PM, "b", 2)],
    )
    assert statuses(s) == {"a": "fixed", "b": "open"}


def test_the_fold_closes_record_by_record_so_a_later_reraise_reopens_the_key():
    s = state(findings=[rec(PM, "a", 1), rec(PM, "a", 3)], plan_settled=[row(PM, "a", 2)])
    assert statuses(s) == {"a": "open"}


def test_the_fold_ignores_a_settled_row_of_another_location_or_claim():
    s = state(
        findings=[rec("QA Performance", "slow", 1), rec(PM, "a", 1)],
        plan_settled=[row(PM, "slow", 2), row("QA Performance", "a", 2)],
    )
    assert statuses(s) == {"slow": "open", "a": "open"}


def test_a_state_without_plan_settled_folds_as_before():
    findings = [rec(PM, "a", 1), rec("agentic/x.py", "b", 1, scope="subtask", subtask="api")]
    plain = state(findings=findings)
    assert rules.findings_rows(plain) == rules.findings_rows({**plain, "plan_settled": []})
    assert statuses(plain) == {"a": "open", "b": "open"}


def test_a_tech_lead_approval_never_settles_a_pm_final_row():
    s = state(
        findings=[rec(PM, "a", 1)],
        subtasks={"integration": {"status": "approved"}},
        approval_settled=[{"subtask": "integration", "location": PM, "claim": "a", "version": 5}],
        runs=[{"role": "Tech Lead", "section": "Tech Lead", "version": 5}],
    )
    assert plan_settlement.on_approval(s, 1) == []
    assert statuses(s) == {"a": "open"}


# --- GH-374 ------------------------------------------------------------------------------------


def gh374_ledger():
    """Three PM final rounds (return, return, approve) with the criteria reworded each round,
    20 blocking plan records over versions 1-2, two of them at version 2 with different keys."""
    findings = [rec(PM, f"Criterion {i} (round 1 wording)", 1) for i in range(10)]
    findings += [rec(PM, f"Criterion {i} reworded in round 2", 2) for i in range(10)]
    findings += [rec("agentic/x.py", "still open and unrelated", 1, subtask="api", scope="subtask")]
    return state(
        findings=findings,
        subtasks={
            "integration": {"status": "approved", "reviewed_head": HEAD},
            "api": {"status": "approved"},
        },
        runs=[pm_run(1), pm_run(2), pm_run(3)],
        change_history=[pm_return(), pm_return()],
    )


def merge_facts(s):
    return rules.MergeFacts(
        task_id="GH-374",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=sum(1 for r in rules.open_findings(s) if r["severity"] == "blocking"),
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=("agentic/x.py",),
        attempts=1,
        checks_head=HEAD,
        checks_master="m" * 40,
        checks_passed=True,
        current_master="m" * 40,
    )


def test_the_gh374_ledger_refuses_the_merge_until_pending_is_applied():
    s = gh374_ledger()
    before = rules.auto_merge_decision(merge_facts(s))
    assert (before.kind, before.reason) == ("refuse", "open_findings_blocking")


def test_the_gh374_ledger_settled_by_pending_leaves_no_pm_blocker_and_passes_the_rule():
    s = gh374_ledger()
    s = {**s, "plan_settled": plan_settlement.pending(s)}
    assert len(s["plan_settled"]) == 20
    assert {r["by"] for r in s["plan_settled"]} == {"approved"}
    blockers = [r for r in rules.open_findings(s) if r["severity"] == "blocking"]
    # The unrelated subtask row is not the PM's to close; the rest of the ledger is settled.
    assert [r["location"] for r in blockers] == ["agentic/x.py"]
    decision = rules.auto_merge_decision(merge_facts(replace_findings(s)))
    assert decision.reason != "open_findings_blocking"
    assert decision.kind == "merge"


def replace_findings(s):
    """The same ledger without the unrelated subtask row, which the PM settlement never owns."""
    return {**s, "findings": [r for r in s["findings"] if r["location"] == PM]}
