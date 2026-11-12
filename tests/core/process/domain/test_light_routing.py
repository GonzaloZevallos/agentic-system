"""The light path in `rules` (GH-591, part 2): where a light feature or bug goes, the three
verifier predicates under light, the promotion's way back to the full path's first role, and
the plan checks. A state without `path` reads as `full`, so every function is also compared
against the same state with `path: full` (the golden test)."""

import pytest

from agentic.core.process.domain import cost_history, rules, schemas

LIGHT = {"path": "light"}
FULL = {"path": "full"}
AT = "2026-11-12T10:00:00+00:00"
BEFORE = "2026-11-12T09:00:00+00:00"
AFTER = "2026-11-12T11:00:00+00:00"


def sub(status: str, deps: tuple[str, ...] = ()) -> dict:
    return {"id": "x", "status": status, "depends_on": list(deps)}


def run(role: str, *, section: str | None = None, started_at: str = BEFORE) -> dict:
    return {"role": role, "section": section or role, "started_at": started_at}


# --- routing ----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["feature", "bug"])
def test_light_classify_goes_straight_to_plan(kind):
    assert rules.after_classify({"type": kind, **LIGHT}) == "plan"


@pytest.mark.parametrize(
    ("kind", "first"),
    [("feature", "define"), ("bug", "diagnose"), ("platform_enhancement", "plan")],
)
def test_full_classify_is_unchanged(kind, first):
    assert rules.after_classify({"type": kind, **FULL}) == first
    assert rules.after_classify({"type": kind}) == first


@pytest.mark.parametrize("kind", ["feature", "bug"])
def test_light_validate_and_consolidate_go_to_plan_past_the_design_ladder(kind):
    state = {"type": kind, "status": "definition_ready", "flags": {"touches_ui": True}, **LIGHT}
    assert rules.after_validate(state) == "plan"
    assert rules.after_consolidate(state) == "plan"


def test_full_validate_still_goes_through_the_design_ladder():
    state = {"type": "feature", "status": "definition_ready", "flags": {"touches_ui": True}}
    assert rules.after_validate(state) != "plan"
    assert rules.after_validate({**state, **FULL}) == rules.after_validate(state)


def test_light_validate_keeps_a_discarded_bug_and_a_return():
    assert rules.after_validate({"type": "bug", "status": "discarded", **LIGHT}) == "discard"
    assert rules.after_validate({"type": "bug", "status": "returned", **LIGHT}) == "diagnose"


@pytest.mark.parametrize(("kind", "first"), [("feature", "define"), ("bug", "diagnose")])
def test_the_resume_router_sends_a_light_open_task_to_plan(kind, first):
    state = {"type": kind, "status": "open"}
    assert rules.resume_target({**state, **LIGHT}) == "plan"
    assert rules.resume_target({**state, **FULL}) == first
    assert rules.resume_target(state) == first
    ready = {"type": kind, "status": "definition_ready", "flags": {"touches_ui": True}}
    assert rules.resume_target({**ready, **LIGHT}) == "plan"


def test_the_resume_router_keeps_a_light_platform_task_at_plan_and_classify_for_no_type():
    assert (
        rules.resume_target({"type": "platform_enhancement", "status": "open", **LIGHT}) == "plan"
    )
    assert rules.resume_target({"status": "open", **LIGHT}) == "classify"


def test_an_escalated_light_task_still_ends():
    assert rules.after_classify({"type": "feature", "escalated": True, **LIGHT}) is None


# --- verifier predicates -------------------------------------------------------------------


def approved_unit(**extra) -> dict:
    return {
        "type": "feature",
        "subtasks": {"a": {**sub("approved"), "reviewed_head": "abc"}},
        "runs": [run("Tech Lead", section="Tech Lead: review a", started_at=BEFORE)],
        **extra,
    }


def test_the_predicates_are_true_under_full_and_false_under_light():
    state = approved_unit(flags={"touches_ui": True, "touches_copy": True}, performance=[{"n": 1}])
    assert rules.final_validation_due(state)
    assert rules.copy_review_due(state)
    assert rules.qa_performance_due(state)
    light = {**state, **LIGHT}
    assert not rules.final_validation_due(light)
    assert not rules.copy_review_due(light)
    assert not rules.qa_performance_due(light)
    assert rules.final_validation_due({**state, **FULL})
    assert rules.copy_review_due({**state, **FULL})
    assert rules.qa_performance_due({**state, **FULL})


# --- promotion -----------------------------------------------------------------------------


def promoted(kind: str = "feature", *, runs: list | None = None, **extra) -> dict:
    return {
        "type": kind,
        "path": "full",
        "promotion": {"from_role": "Tech Lead", "kind": "role", "text": None, "at": AT},
        "runs": runs or [],
        **extra,
    }


@pytest.mark.parametrize(("kind", "first"), [("feature", "define"), ("bug", "diagnose")])
def test_a_promoted_task_goes_to_the_first_full_path_role(kind, first):
    state = promoted(kind)
    assert rules.promotion_pending(state)
    assert rules.promotion_route(state) == first


def test_a_promoted_task_is_not_pending_once_the_first_role_ran_after_it():
    ran = promoted(runs=[run("Product Analyst", started_at=AFTER)])
    assert not rules.promotion_pending(ran)
    assert rules.promotion_route(ran) is None


def test_a_run_before_the_promotion_does_not_settle_it():
    before = promoted(runs=[run("Product Analyst", started_at=BEFORE)])
    assert rules.promotion_pending(before)


def test_an_unrelated_role_after_the_promotion_does_not_settle_it():
    other = promoted(runs=[run("Tech Lead", started_at=AFTER)])
    assert rules.promotion_pending(other)


def test_a_platform_task_is_never_pending():
    state = promoted("platform_enhancement")
    assert not rules.promotion_pending(state)
    assert rules.promotion_route(state) is None


def test_no_promotion_is_never_pending():
    assert not rules.promotion_pending({"type": "feature", **LIGHT})
    assert rules.promotion_route({"type": "feature"}) is None


def two_parallel(**extra) -> dict:
    return {
        "subtasks": {"a": sub("approved"), "b": sub("returned"), "c": sub("pending")},
        **extra,
    }


def test_dispatch_stops_while_a_promotion_is_pending():
    assert rules.dispatched_subtasks(two_parallel()) == ["b", "c"]
    assert rules.dispatched_subtasks(two_parallel(**promoted())) == []


def test_dispatch_resumes_once_the_first_role_ran():
    ran = promoted(runs=[run("Product Analyst", started_at=AFTER)])
    assert rules.dispatched_subtasks(two_parallel(**ran)) == ["b", "c"]


def test_after_plan_and_the_fan_in_route_a_promotion_first():
    state = two_parallel(**promoted())
    assert rules.after_plan(state) == "define"
    assert rules.after_integrate(state) == "define"
    assert rules.after_plan(promoted("bug", subtasks=state["subtasks"])) == "diagnose"


def test_the_fan_in_of_an_all_approved_promoted_task_still_goes_back():
    state = {**promoted(), "subtasks": {"a": sub("approved")}}
    assert rules.after_integrate(state) == "define"


def test_the_resume_router_sends_a_promoted_task_to_the_first_role():
    state = {**promoted(), "subtasks": {"a": sub("approved")}, "status": "definition_ready"}
    assert rules.resume_target(state) == "define"
    assert rules.resume_target({**state, "type": "bug"}) == "diagnose"


def test_the_resume_router_after_the_first_role_ran_goes_on_to_the_fan_in():
    ran = promoted(runs=[run("Product Analyst", started_at=AFTER)])
    state = {**ran, "subtasks": {"a": sub("approved")}}
    assert rules.resume_target(state) == "integrate"


# --- golden: path full == no path ----------------------------------------------------------

STATES = [
    {"type": kind, "status": status, "flags": flags, **extra}
    for kind in ("feature", "bug", "platform_enhancement", "")
    for status in ("open", "returned", "definition_ready", "defined", "discarded")
    for flags in ({}, {"touches_ui": True}, {"touches_copy": True})
    for extra in (
        {},
        {"escalated": True},
        {"subtasks": {"a": sub("approved")}},
        {"subtasks": {"a": sub("pending"), "b": sub("approved")}},
        {"parts": [{"title": "p"}]},
        {"performance": [{"n": 1}], "subtasks": {"a": sub("approved")}},
    )
]


@pytest.mark.parametrize(
    "name",
    [
        "after_classify",
        "after_validate",
        "after_consolidate",
        "after_plan",
        "after_integrate",
        "resume_target",
        "dispatched_subtasks",
        "promotion_pending",
        "promotion_route",
        "final_validation_due",
        "copy_review_due",
        "qa_performance_due",
    ],
)
def test_full_path_matches_a_document_without_path(name):
    function = getattr(rules, name)
    for state in STATES:
        assert function({**state, **FULL}) == function(state), state


# --- plan checks ---------------------------------------------------------------------------


def plan_data(subtasks: int = 1, **extra) -> dict:
    return {
        "subtasks": [
            {"id": f"s{n}", "title": "t", "brief": "b", "seams": ["x"], "depends_on": []}
            for n in range(subtasks)
        ],
        "estimated_files": 3,
        "estimated_cost_usd": 5.0,
        "performance": [],
        **extra,
    }


def check(**kwargs):
    return schemas.plan_check_for(cost_history.build([]), 0.0, **kwargs)


def test_a_light_plan_that_convenes_performance_is_refused():
    data = plan_data(performance=[{"name": "p"}])
    with pytest.raises(ValueError, match="performance"):
        check(path="light")(data)
    check(path="full")(data)
    check()(data)


def test_the_owner_label_light_refuses_more_than_two_subtasks():
    with pytest.raises(ValueError, match="owner label path:light: at most 2 subtasks"):
        check(path="light", path_label="light")(plan_data(3))


def test_the_owner_label_light_accepts_two_subtasks():
    check(path="light", path_label="light")(plan_data(2))


def test_a_light_plan_over_the_cap_without_the_label_is_not_refused():
    check(path="light", path_label="none")(plan_data(3))
    check(path="light")(plan_data(4))


def test_a_light_plan_over_the_cap_with_performance_is_left_to_the_promotion():
    check(path="light", path_label="none")(plan_data(3, performance=[{"name": "p"}]))
    with pytest.raises(ValueError, match="performance"):
        check(path="light", path_label="light")(plan_data(3, performance=[{"name": "p"}]))


def test_a_full_plan_over_the_cap_is_not_refused():
    check(path="full", path_label="light")(plan_data(3))
    check()(plan_data(3))


def test_over_cap_decision_is_a_request_from_the_system():
    over = {
        "path": "light",
        "path_label": "none",
        "subtasks": {f"s{n}": sub("pending") for n in range(3)},
    }
    assert rules.plan_over_cap_request(over) == {
        "kind": "plan_over_cap",
        "from_role": "system",
        "text": None,
    }
    assert rules.plan_over_cap_request({**over, "path_label": "light"}) is None
    assert rules.plan_over_cap_request({**over, "path": "full"}) is None
    assert rules.plan_over_cap_request({**over, "subtasks": {"a": sub("pending")}}) is None
    assert rules.plan_over_cap_request({"subtasks": over["subtasks"]}) is None
