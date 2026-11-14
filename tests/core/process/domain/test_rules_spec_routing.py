"""A spec delivery in `rules` (GH-594, part 4b-2): `state["spec"]` non-empty skips the definition
chain and the design ladder, a promotion never goes back to the definition, and the four
verifiers keep following the flags derived from the slice's screens."""

import pytest

from agentic.core.process.domain import rules

SPEC = {"slice": "a slice"}
AT = "2026-11-12T10:00:00+00:00"
BEFORE = "2026-11-12T09:00:00+00:00"
FLAGS = {"touches_ui": True, "touches_copy": True}


def feature(**extra) -> dict:
    """A feature on the full path with screens and copy, carrying a spec unless told not to."""
    return {"type": "feature", "path": "full", "flags": dict(FLAGS), "spec": SPEC, **extra}


@pytest.fixture
def ladder_open(monkeypatch):
    """The design ladder says `ux_flow`: any approval that reaches it would not go to `plan`."""
    monkeypatch.setattr(rules, "design_step", lambda state: "ux_flow")


def test_after_classify_goes_to_plan_under_a_spec():
    assert rules.after_classify(feature()) == "plan"
    assert rules.after_classify(feature(spec={})) == "define"


def test_after_validate_returned_goes_to_plan_under_a_spec():
    assert rules.after_validate(feature(status="returned")) == "plan"
    assert rules.after_validate(feature(status="returned", spec={})) == "define"


def test_after_validate_approval_goes_to_plan_past_the_ladder(ladder_open):
    assert rules.after_validate(feature(status="definition_ready")) == "plan"
    assert rules.after_validate(feature(status="definition_ready", spec={})) == "ux_flow"


def test_after_consolidate_returned_goes_to_plan_under_a_spec():
    assert rules.after_consolidate(feature(status="returned")) == "plan"
    assert rules.after_consolidate(feature(status="returned", spec={})) == "define"


def test_after_consolidate_approval_goes_to_plan_past_the_ladder(ladder_open):
    assert rules.after_consolidate(feature(status="approved")) == "plan"
    assert rules.after_consolidate(feature(status="approved", spec={})) == "ux_flow"


def test_design_step_is_none_under_a_spec():
    assert rules.design_step(feature(spec={})) == "ux_flow"
    assert rules.design_step(feature()) is None


def test_resume_open_after_a_questions_pause_goes_to_plan_under_a_spec():
    state = feature(status="open")
    assert rules.resume_target(state) == "plan"
    assert rules.resume_target({**state, "spec": {}}) == "define"


def test_resume_open_with_a_spec_but_no_type_goes_back_to_classify():
    state = {"status": "open", "spec": SPEC}
    assert rules.resume_target(state) == "classify"


def test_resume_returned_goes_to_plan_under_a_spec():
    state = feature(status="returned")
    assert rules.resume_target(state) == "plan"
    assert rules.resume_target({**state, "spec": {}}) == "define"


def test_resume_definition_ready_goes_to_plan_past_the_ladder(ladder_open):
    state = feature(status="definition_ready")
    assert rules.resume_target(state) == "plan"
    assert rules.resume_target({**state, "spec": {}}) == "ux_flow"


def test_a_promotion_under_a_spec_owes_nothing_and_never_goes_back():
    promoted = feature(status="open", promotion={"at": AT, "kind": "needs_design"})
    assert not rules.promotion_pending(promoted)
    assert rules.promotion_route(promoted) is None
    owed = {**promoted, "spec": {}}
    assert rules.promotion_pending(owed)
    assert rules.promotion_route(owed) == "define"


# --- verifiers: the same value with and without a spec ----------------------------------------


def approved_unit(**extra) -> dict:
    return {
        "subtasks": {
            "a": {"id": "x", "status": "approved", "depends_on": [], "reviewed_head": "h"}
        },
        "runs": [{"role": "Tech Lead", "section": "Tech Lead: review a", "started_at": BEFORE}],
        "performance": [{"n": 1}],
        "task_head": "h",
        **feature(**extra),
    }


@pytest.mark.parametrize(
    "verifier",
    [
        rules.final_validation_due,
        rules.copy_review_due,
        rules.qa_performance_due,
        rules.full_checks_due,
    ],
)
def test_a_verifier_is_unchanged_by_a_spec(verifier):
    with_spec = approved_unit()
    without = approved_unit(spec={})
    assert verifier(with_spec) is verifier(without) is True
    assert verifier({**with_spec, "flags": {}}) is verifier({**without, "flags": {}})
