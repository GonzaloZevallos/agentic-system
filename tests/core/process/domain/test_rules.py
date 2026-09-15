from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import PAIR_PM_PA

PAIR = PAIR_PM_PA
KEY = PAIR.key


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
