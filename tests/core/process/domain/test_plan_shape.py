"""The `plan_shape: single` arm (GH-471) at the pure seams: the declaration and its
assignment, `plan_shape_of`, the cost history and the plan check."""

import pytest

from agentic.core.process.domain import cost_history, document, experiment, schemas
from agentic.core.process.domain.schemas import PLAN_SHAPE_REFUSAL
from agentic.shared.configuration import Configuration, arm_input, experiment_snapshot

DECLARATION = {
    "id": "plan-shape",
    "assignment": "alternate",
    "arms": [
        {"name": "control", "share": 0.5, "switches": {}},
        {"name": "single", "share": 0.5, "switches": {"plan_shape": "single"}},
    ],
}
SINGLE_LABEL = "review_mode=full,skip_ephemeral=False,plan_review=interview,plan_shape=single"


def cfg(**fields):
    return Configuration(product_dir="/tmp/p", **fields)


def assigned(arm_name):
    declaration = experiment.parse_experiment(DECLARATION, "experiment.yaml")
    arm = next(a for a in declaration.arms if a.name == arm_name)
    return experiment.assignment_of(declaration, arm)


# -- the declaration, the assignment and the document ------------------------------------


def test_an_assignment_to_the_single_arm_reaches_the_configuration_and_the_label():
    from agentic.core.measurement.domain import metrics

    assignment = assigned("single")
    assert assignment["switches"] == {"plan_shape": "single"}
    state = {"id": "GH-9", "title": "t", "description": "d", "assignment": assignment}
    parsed = document.parse(document.render(state))
    assert metrics.arm_of(parsed) == SINGLE_LABEL
    assert experiment.plan_shape_of(parsed) == "single"
    arm_cfg = experiment.with_switches(cfg(), assignment["switches"])
    assert arm_cfg.plan_shape == "single"
    assert (arm_cfg.review_mode, arm_cfg.skip_ephemeral) == ("full", False)
    assert metrics.arm_label(arm_input(arm_cfg)) == SINGLE_LABEL


def test_the_control_arm_of_that_declaration_is_the_default_document():
    from agentic.core.measurement.domain import metrics

    assignment = assigned("control")
    assert assignment["switches"] == {}
    state = {"id": "GH-9", "title": "Golden", "description": "d", "assignment": assignment}
    assert "plan_shape" not in document.render(state)
    assert metrics.arm_of(state) == "default"
    assert experiment.plan_shape_of(state) == "free"


def test_a_snapshot_of_the_single_arm_is_written_and_read_back_in_order():
    snapshot = experiment_snapshot(cfg(plan_shape="single"), "m")
    rendered = document.render(
        {"id": "T-1", "title": "t", "description": "d", "experiment": snapshot}
    )
    assert "  reviewer_model: m\n  plan_shape: single\n" in rendered
    assert document.parse(rendered)["experiment"] == snapshot


def test_a_declaration_refuses_a_plan_shape_that_is_neither_free_nor_single():
    bad = {**DECLARATION, "arms": [{"name": "x", "share": 1, "switches": {"plan_shape": "two"}}]}
    with pytest.raises(experiment.InvalidExperiment, match="plan_shape must be one of"):
        experiment.parse_experiment(bad, "experiment.yaml")


def test_plan_shape_of_reads_the_assignment_then_the_snapshot_then_the_default():
    assert experiment.plan_shape_of({}) == "free"
    assert experiment.plan_shape_of({"experiment": {}}) == "free"
    assert experiment.plan_shape_of({"experiment": {"review_mode": "light"}}) == "free"
    assert experiment.plan_shape_of({"experiment": {"plan_shape": "single"}}) == "single"
    by_assignment = {
        "assignment": {"experiment": "e", "arm": "s", "switches": {"plan_shape": "single"}}
    }
    assert experiment.plan_shape_of(by_assignment) == "single"
    # The assignment is what the poller decided; it wins over a snapshot that says otherwise.
    control = {"assignment": {"experiment": "e", "arm": "c", "switches": {}}}
    assert experiment.plan_shape_of({**control, "experiment": {"plan_shape": "single"}}) == "free"


# -- the cost history never sees a single-shape document ---------------------------------


def doc(n_subtasks, cost, **more):
    subtasks = {f"s{i}": {"id": f"s{i}"} for i in range(n_subtasks)}
    return {"id": f"T-{cost}", "subtasks": subtasks, "cost_usd": cost, "closed_at": "x", **more}


def by_assignment():
    return {
        "assignment": {"experiment": "e", "arm": "single", "switches": {"plan_shape": "single"}}
    }


def by_snapshot():
    return {
        "experiment": {"review_mode": "full", "skip_ephemeral": "False", "plan_shape": "single"}
    }


@pytest.mark.parametrize("marker", [by_assignment, by_snapshot])
def test_single_documents_do_not_enter_the_buckets_medians_p90_or_pairs(marker):
    small = [doc(1, c, estimate={"cost_usd": c, "files": 2}) for c in (4.0, 5.0, 6.0, 7.0, 8.0)]
    costly = [
        doc(1, 60.0 + i, estimate={"cost_usd": 70.0, "files": 9}, **marker()) for i in range(10)
    ]
    alone = cost_history.build(small)
    mixed = cost_history.build([*small[:2], *costly, *small[2:]])
    assert mixed == alone
    assert cost_history.reference_usd(mixed, 1) == cost_history.reference_usd(alone, 1) == 8.0
    assert cost_history.bucket_median(mixed, 1) == cost_history.bucket_median(alone, 1) == 6.0
    assert cost_history.render(mixed) == cost_history.render(alone)
    assert mixed.pairs == alone.pairs


def test_a_control_document_of_an_experiment_still_counts():
    control = {"assignment": {"experiment": "e", "arm": "control", "switches": {}}}
    assert cost_history.build([doc(1, 5.0, **control)]).buckets[1].n == 1


# -- the plan check ----------------------------------------------------------------------


def plan(*ids, integration="", cost=20.0, files=3):
    return {
        "subtasks": [
            {"id": i, "title": i, "brief": "b", "seams": ["x"], "depends_on": []} for i in ids
        ],
        "markdown": "### Solution",
        "ci_required": [],
        "ci_none_reason": "The issue requires no external check.",
        "estimated_cost_usd": cost,
        "estimated_files": files,
        "integration": integration,
    }


def split(cost=200.0, files=50):
    parts = [
        {"title": "One", "body": "First slice.", "depends_on": []},
        {"title": "Two", "body": "Second slice.", "depends_on": [1]},
    ]
    return {**plan("a", cost=cost, files=files), "subtasks": [], "parts": parts}


def check(plan_shape, spent=0.0, part_of=None):
    return schemas.plan_check_for(
        cost_history.build([]), spent, plan_shape=plan_shape, part_of=part_of
    )


def test_the_refusals_start_with_the_one_constant():
    assert PLAN_SHAPE_REFUSAL == "plan_shape single:"


def test_single_refuses_two_subtasks_saying_how_many_were_written():
    with pytest.raises(ValueError) as refusal:
        check("single")(plan("a", "b"))
    assert str(refusal.value).startswith(PLAN_SHAPE_REFUSAL)
    assert "2 subtasks" in str(refusal.value)
    assert "dependency order" in str(refusal.value)


def test_single_refuses_an_integration_beside_its_one_subtask():
    with pytest.raises(ValueError) as refusal:
        check("single")(plan("a", integration="wire the CLI"))
    assert str(refusal.value).startswith(PLAN_SHAPE_REFUSAL)
    assert "integration" in str(refusal.value) and "brief" in str(refusal.value)


def test_single_accepts_one_subtask_with_a_blank_integration():
    check("single")(plan("a"))
    check("single")(plan("a", integration="  \n"))


def test_free_is_what_it_was():
    check("free")(plan("a", "b", "c"))
    check("free")(plan("a", integration="wire the CLI"))
    schemas.plan_check_for(cost_history.build([]), 0.0)(plan("a", "b"))


def test_single_never_refuses_a_plan_with_parts_or_a_tracker_only_plan():
    check("single")(split())
    tracker = {
        **plan(),
        "subtasks": [],
        "tracker_checklist": ["Close issue 12"],
        "estimated_files": 0,
        "estimated_cost_usd": 1.0,
    }
    check("single")(tracker)


@pytest.mark.parametrize("plan_shape", ["free", "single"])
@pytest.mark.parametrize("cost,files", [(151.0, 5), (20.0, 41)])
def test_a_plan_over_the_line_is_refused_toward_parts_in_both_arms(plan_shape, cost, files):
    with pytest.raises(ValueError, match="does not fit.*parts"):
        check(plan_shape)(plan("a", cost=cost, files=files))
    check(plan_shape)(split(cost, files))
