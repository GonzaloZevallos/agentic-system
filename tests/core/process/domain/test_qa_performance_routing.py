"""QA Performance's place in the routing (GH-318, ADR-0060): the role and its pair, the
predicate that says the node is due, the order after the fan-in and the one function that
names whose section carries a returned subtask's delta."""

import pytest

from agentic.core.process.domain import roles, rules
from agentic.core.process.domain.schemas import Review
from agentic.core.process.domain.task import ReturnedBy

REVIEW = rules.review_section("api")
SCENARIO = {
    "name": "sales-list",
    "volume": 100000,
    "volume_unit": "rows",
    "basis": "a year of sales",
    "budgets": [{"metric": "time", "limit": 200, "unit": "ms", "source": "issue", "basis": "x"}],
}


def run(role, section=None, **more):
    return {"role": role, "section": section or role, "cost_usd": 0.1, **more}


def qa(verdict, head="a" * 40):
    return run(roles.QA_PERFORMANCE.name, perf_verdict=verdict, perf_head=head)


def convened(*runs, performance=(SCENARIO,), **more):
    return {
        "type": "feature",
        "flags": {},
        "performance": list(performance),
        "subtasks": {"api": {"status": "approved"}},
        "runs": [run("Tech Lead", REVIEW), *runs],
        **more,
    }


def test_the_role_reads_the_code_and_has_no_bash_at_all():
    role = roles.QA_PERFORMANCE
    assert role.name == "QA Performance" and role.kind == "verifier"
    assert "Bash" not in role.tools and not any("Bash" in rule for rule in role.allowed)
    assert not {"Write", "Edit"} & set(role.tools)
    assert role in roles.ROLES
    assert roles.PAIR_QA_PERFORMANCE_TECH_LEAD.by is roles.QA_PERFORMANCE
    assert roles.PAIR_QA_PERFORMANCE_TECH_LEAD.key == "QA Performance→Tech Lead"


def test_it_is_due_once_a_convened_plan_is_all_approved():
    assert rules.qa_performance_due(convened())
    assert rules.after_integrate(convened()) == "qa_performance"


@pytest.mark.parametrize("performance", [(), None])
def test_it_is_never_due_when_the_plan_convened_nothing(performance):
    state = convened()
    state.pop("performance")
    if performance is not None:
        state["performance"] = list(performance)
    assert not rules.qa_performance_due(state)
    assert rules.after_integrate(state) == "deliver"


def test_it_is_not_due_while_a_subtask_is_still_open_or_with_no_subtask():
    state = convened()
    state["subtasks"] = {"api": {"status": "returned"}}
    assert not rules.qa_performance_due(state)
    assert not rules.qa_performance_due({**convened(), "subtasks": {}})


@pytest.mark.parametrize("verdict", ["approved", "unverified"])
def test_an_approving_or_unverified_run_newer_than_the_review_settles_it(verdict):
    state = convened(qa(verdict))
    assert not rules.qa_performance_due(state)
    assert rules.after_integrate(state) == "deliver"


def test_a_returned_run_does_not_settle_it():
    assert rules.qa_performance_due(convened(qa("returned")))


def test_a_review_newer_than_an_approved_run_makes_it_due_again():
    state = convened(qa("approved"), run("Tech Lead", REVIEW))
    assert rules.qa_performance_due(state)


def test_it_reads_the_integration_review_when_there_is_an_integration():
    integration = rules.review_section("integration")
    state = convened(
        run("Tech Lead", integration),
        qa("approved"),
        subtasks={"api": {"status": "approved"}, "integration": {"status": "approved"}},
    )
    assert not rules.qa_performance_due(state)
    state["runs"].append(run("Tech Lead", integration))
    assert rules.qa_performance_due(state)


def test_the_order_is_copy_then_final_validation_then_qa_performance_then_deliver():
    state = {
        "type": "feature",
        "flags": {"touches_ui": True, "touches_copy": True},
        "performance": [SCENARIO],
        "subtasks": {"api": {"status": "approved"}},
        "runs": [run("Tech Lead", REVIEW)],
    }
    assert rules.after_integrate(state) == "copy_review"
    state["runs"].append(run(roles.TECH_WRITER_REVIEW.name))
    assert rules.after_integrate(state) == "final_validation"
    state["runs"].append(run(roles.PM_FINAL.name))
    assert rules.after_integrate(state) == "qa_performance"
    state["runs"].append(qa("approved"))
    assert rules.after_integrate(state) == "deliver"


def test_a_pending_wave_goes_to_the_pipeline_before_the_node():
    state = convened()
    state["subtasks"] = {"api": {"status": "returned"}}
    assert rules.after_integrate(state) == "pipeline"


def test_after_qa_performance_goes_back_to_the_fan_in_unless_escalated():
    assert rules.after_qa_performance(convened()) == "integrate"
    assert rules.after_qa_performance({**convened(), "escalated": True}) is None


def test_return_section_names_whose_delta_a_returned_subtask_reads():
    def returned(by):
        return {"subtasks": {"api": {"status": "returned", "returned_by": by}}}

    assert rules.return_section(returned("review"), "api") == REVIEW
    assert rules.return_section(returned("final_validation"), "api") == roles.PM_FINAL.name
    assert rules.return_section(returned("qa_performance"), "api") == "QA Performance"
    assert rules.return_section({"subtasks": {"api": {"status": "returned"}}}, "api") == REVIEW


def test_return_section_refuses_a_value_outside_the_closed_set():
    """The total match has no fallthrough: a new `ReturnedBy` value that nobody mapped is
    an error, never silently the review's section."""
    bad = {"subtasks": {"api": {"status": "returned", "returned_by": "somebody_else"}}}
    with pytest.raises(AssertionError):
        rules.return_section(bad, "api")
    assert set(ReturnedBy.__args__) == {"review", "final_validation", "qa_performance"}


def test_the_review_schema_is_unchanged_by_the_new_pair():
    assert roles.QA_PERFORMANCE.schema is Review
