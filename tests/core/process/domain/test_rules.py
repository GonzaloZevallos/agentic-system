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
    assert rules.after_validate({"status": "definition_ready"}) is None
    assert rules.after_validate({"status": "returned", "escalated": "x"}) is None
