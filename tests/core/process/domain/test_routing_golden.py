"""A golden table of today's routing (GH-999): what `rules.after_validate` and
`rules.resume_target` answer over states built from tuples. The early size check is not wired into
either yet, so every row stays as it is until the part that wires it changes one on purpose."""

from typing import Any, get_args

import pytest

from agentic.core.process.domain import rules
from agentic.core.process.domain.task import EscalationClass, Status

AT = "2026-11-19T10:00:00Z"
PM_RUN = {"role": "PM", "section": "PM", "version": 1, "at": AT}
PROMOTED = {"at": "2026-11-19T11:00:00Z"}
PR = "https://example.test/pr/1"
UI = {"touches_ui": True, "touches_copy": False}
COPY = {"touches_ui": False, "touches_copy": True}
PART = [{"title": "a"}]
PENDING = {"s": {"status": "pending"}}
APPROVED = {"s": {"status": "approved"}}


def feature(**facts: Any) -> Any:
    """A feature on the full path with PM's run, `definition_ready` unless `facts` say otherwise."""
    return {
        "type": "feature",
        "path": "full",
        "status": "definition_ready",
        "description": "x",
        "runs": [PM_RUN],
        **facts,
    }


# (status, after_validate, resume_target) of a full-path feature in each status.
BY_STATUS = [
    ("open", None, "define"),
    ("defined", None, "validate"),
    ("returned", "define", "define"),
    ("definition_ready", "early_size", "early_size"),
    ("planned", None, None),
    ("developing", None, None),
    ("ready_for_review", None, None),
    ("waiting_on_human", None, None),
    ("discarded", "discard", None),
    ("split", None, None),
    ("tracker_only", None, None),
    ("evaluated", None, None),
]

# (label, state facts as pairs, after_validate, resume_target).
ROWS: list[tuple[str, tuple[tuple[str, Any], ...], str | None, str | None]] = [
    ("pull request, stage open", (("status", "developing"), ("pull_request", PR)), None, None),
    ("pull request, definition_ready", (("pull_request", PR),), "early_size", None),
    ("plan due, definition_ready", (("plan_due", True),), "plan", "plan"),
    ("plan due, developing", (("status", "developing"), ("plan_due", True)), None, "plan"),
    ("parts without subtasks", (("status", "planned"), ("parts", PART)), None, "split"),
    (
        "parts with subtasks",
        (("status", "planned"), ("parts", PART), ("subtasks", PENDING)),
        None,
        "integrate",
    ),
    ("subtasks pending", (("status", "developing"), ("subtasks", PENDING)), None, "integrate"),
    ("subtasks approved", (("status", "developing"), ("subtasks", APPROVED)), None, "integrate"),
    (
        "promotion owed, feature",
        (("status", "open"), ("path", "light"), ("promotion", PROMOTED)),
        None,
        "define",
    ),
    (
        "promotion owed, bug",
        (("status", "open"), ("type", "bug"), ("promotion", PROMOTED)),
        None,
        "diagnose",
    ),
    ("feature touches_ui, no design run", (("flags", UI),), "early_size", "early_size"),
    ("feature touches_copy only", (("flags", COPY),), "early_size", "early_size"),
    ("bug definition_ready", (("type", "bug"),), "plan", "plan"),
    ("bug returned", (("type", "bug"), ("status", "returned")), "diagnose", "diagnose"),
    ("platform definition_ready", (("type", "platform"),), "plan", "plan"),
    ("platform open", (("type", "platform"), ("status", "open")), None, "classify"),
    ("light feature definition_ready", (("path", "light"),), "plan", "plan"),
    ("light feature returned", (("path", "light"), ("status", "returned")), "define", "define"),
    ("light feature open", (("path", "light"), ("status", "open")), None, "plan"),
    ("spec feature definition_ready", (("spec", {"slice": "s"}),), "plan", "plan"),
    (
        "spec feature returned",
        (("spec", {"slice": "s"}), ("status", "returned")),
        "plan",
        "plan",
    ),
]


def test_the_table_covers_every_status_and_escalation_class():
    assert [status for status, _, _ in BY_STATUS] == list(get_args(Status))
    assert len(BY_STATUS) + len(get_args(EscalationClass)) + len(ROWS) >= 40


@pytest.mark.parametrize(("status", "validated", "resumed"), BY_STATUS)
def test_every_status_of_a_full_path_feature_routes_as_it_does_today(status, validated, resumed):
    state = feature(status=status)
    assert rules.after_validate(state) == validated
    assert rules.resume_target(state) == resumed


@pytest.mark.parametrize("cls", get_args(EscalationClass))
def test_an_escalated_task_goes_nowhere_whatever_the_class(cls):
    state = feature(**rules.escalation(cls, "reason"))
    assert rules.after_validate(state) is None
    assert rules.resume_target(state) is None


@pytest.mark.parametrize(("label", "facts", "validated", "resumed"), ROWS, ids=[r[0] for r in ROWS])
def test_a_mixed_state_routes_as_it_does_today(label, facts, validated, resumed):
    state = feature(**dict(facts))
    assert rules.after_validate(state) == validated, label
    assert rules.resume_target(state) == resumed, label


def test_a_task_with_no_type_that_is_open_goes_to_classify():
    assert rules.after_validate({"status": "open"}) is None
    assert rules.resume_target({"status": "open"}) == "classify"


# The rows whose output changed, with the early check answered: today's output, unchanged.
MEASURED = [
    (("pull_request", PR),),
    (("flags", UI),),
    (("flags", COPY),),
    (),
]
TODAY = [("plan", None), ("ux_flow", "ux_flow"), ("write_copy", "write_copy"), ("plan", "plan")]


@pytest.mark.parametrize(("facts", "today"), zip(MEASURED, TODAY, strict=True))
def test_a_measured_task_routes_exactly_as_before_the_early_check(facts, today):
    state = feature(**dict(facts))
    state["early_split"] = [{"verdict": "go", "cause": "close", "at": "2026-01-01T00:00:00+00:00"}]
    assert (rules.after_validate(state), rules.resume_target(state)) == today


def test_a_definition_ready_task_with_parts_and_no_subtasks_resumes_to_split():
    assert rules.resume_target(feature(parts=PART)) == "split"


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (rules.escalation("cap", "r"), None),
        ((("parts", PART),), "split"),
        ((("parts", PART), ("subtasks", PENDING)), "plan"),
        ((("flags", UI),), "ux_flow"),
        ((), "plan"),
        ((("path", "light"), ("parts", PART)), "split"),
    ],
)
def test_after_early_size_splits_or_falls_through_to_the_design_step_or_the_plan(facts, expected):
    state = feature(**(facts if isinstance(facts, dict) else dict(facts)))
    assert rules.after_early_size(state) == expected
