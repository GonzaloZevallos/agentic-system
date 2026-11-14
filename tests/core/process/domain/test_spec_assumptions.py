import json
from pathlib import Path

import pytest

from agentic.core.process.domain import schemas
from agentic.core.process.domain.schemas import (
    Consultation,
    ConsultationWithAssumptions,
    Delivery,
    FinalValidation,
    FinalValidationWithAssumptions,
    PlanSize,
    PlatformAnswers,
    PlatformAnswersWithAssumptions,
    TechAnswers,
    TechAnswersWithAssumptions,
    TechPlan,
    TechPlanWithAssumptions,
)
from agentic.core.process.domain.spec_assumptions import (
    ESCALATE,
    SPEC_SCHEMAS,
    assumption_rows,
    conflict_decided,
    conflict_escalation,
    conflict_question,
    conflict_reason,
    conflict_step,
    interview_doubts,
    schema_for,
)

VOCAB = {"touches_ui": "a", "touches_copy": "b"}
GOLDEN = Path(__file__).parent / "fixtures" / "spec_schemas_golden.json"

BASE_INPUT = {
    TechPlan: {
        "estimated_cost_usd": 1.0,
        "estimated_files": 1,
        "hand_files": 1,
        "hand_lines": 1,
        "test_lines": 1,
        "markdown": "m",
        "invariants": [],
        "integration": "",
        "ci_required": [],
        "ci_none_reason": "The issue requires no external check.",
        "api_break": "",
    },
    FinalValidation: {
        "markdown": "m",
        "verdict": "approved",
        "reason": "r",
        "blocking": [],
        "definitions": [],
    },
    TechAnswers: {"markdown": "m", "questions_for_pm": []},
    PlatformAnswers: {"markdown": "m", "questions_for_human": []},
    Consultation: {
        "markdown": "m",
        "questions_for_human": [],
        "definitions": [],
        "glossary": [],
    },
}
NEW_FIELDS = {
    TechPlan: {"assumptions", "spec_conflict"},
    FinalValidation: {"assumptions"},
    TechAnswers: {"assumptions", "spec_conflict"},
    PlatformAnswers: {"assumptions", "spec_conflict"},
    Consultation: {"assumptions"},
}
BASES = list(SPEC_SCHEMAS)


def test_the_spec_schemas_and_delivery_are_the_golden_keys():
    names = {cls.__name__ for cls in SPEC_SCHEMAS} | {Delivery.__name__}
    assert names == set(json.loads(GOLDEN.read_text()))


def test_the_map_is_closed_over_the_five_bases():
    assert SPEC_SCHEMAS == {
        TechPlan: TechPlanWithAssumptions,
        FinalValidation: FinalValidationWithAssumptions,
        TechAnswers: TechAnswersWithAssumptions,
        PlatformAnswers: PlatformAnswersWithAssumptions,
        Consultation: ConsultationWithAssumptions,
    }


@pytest.mark.parametrize("base", BASES)
def test_without_a_spec_the_schema_is_the_same_object(base):
    assert schema_for(base, False) is base


@pytest.mark.parametrize("base", BASES)
def test_with_a_spec_each_base_maps_to_its_subclass(base):
    assert schema_for(base, True) is SPEC_SCHEMAS[base]
    assert issubclass(SPEC_SCHEMAS[base], base)


@pytest.mark.parametrize("other", [Delivery, PlanSize])
def test_a_class_with_no_subclass_is_returned_unchanged_in_both_modes(other):
    assert schema_for(other, True) is other
    assert schema_for(other, False) is other


@pytest.mark.parametrize("base", BASES)
def test_a_subclass_validates_the_base_input_without_the_new_fields(base):
    sub = SPEC_SCHEMAS[base]
    data = schemas.validate(sub, BASE_INPUT[base], VOCAB)
    assert data["assumptions"] == []
    if "spec_conflict" in NEW_FIELDS[base]:
        assert data["spec_conflict"] is None


@pytest.mark.parametrize("base", BASES)
def test_the_assumptions_arrive_as_lines_and_validate_as_a_list(base):
    sub = SPEC_SCHEMAS[base]
    data = schemas.validate(sub, {**BASE_INPUT[base], "assumptions": "a\nb"}, VOCAB)
    assert data["assumptions"] == ["a", "b"]
    assert schemas.json_schema(sub, VOCAB)["properties"]["assumptions"]["type"] == "string"


def test_spec_conflict_is_closed_over_the_three_causes():
    base = {"markdown": "m", "questions_for_pm": []}
    ok = schemas.validate(TechAnswersWithAssumptions, {**base, "spec_conflict": "data_loss"}, VOCAB)
    assert ok["spec_conflict"] == "data_loss"
    with pytest.raises(ValueError):
        schemas.validate(TechAnswersWithAssumptions, {**base, "spec_conflict": "other"}, VOCAB)


@pytest.mark.parametrize("base", BASES)
def test_a_subclass_schema_is_the_base_schema_plus_its_new_fields(base):
    # The top-level title and description differ by construction (class name and docstring);
    # a redeclared questions_for_human differs in its description only.
    sub_schema = schemas.json_schema(SPEC_SCHEMAS[base], VOCAB)
    base_schema = schemas.json_schema(base, VOCAB)
    sub_props = dict(sub_schema["properties"])
    for name in NEW_FIELDS[base]:
        del sub_props[name]
    base_props = dict(base_schema["properties"])
    for props in (sub_props, base_props):
        if "questions_for_human" in props:
            props["questions_for_human"] = {
                k: v for k, v in props["questions_for_human"].items() if k != "description"
            }
    assert set(sub_schema["properties"]) == set(base_schema["properties"]) | NEW_FIELDS[base]
    assert sub_props == base_props
    assert sub_schema.get("required", []) == base_schema.get("required", [])
    assert sub_schema.get("$defs", {}) == base_schema.get("$defs", {})


@pytest.mark.parametrize("sub", [PlatformAnswersWithAssumptions, ConsultationWithAssumptions])
def test_a_redeclared_questions_field_says_a_doubt_is_an_assumption(sub):
    description = schemas.json_schema(sub, VOCAB)["properties"]["questions_for_human"][
        "description"
    ]
    assert "assumption" in description


def test_platform_answers_redeclares_the_agreed_description():
    props = schemas.json_schema(PlatformAnswersWithAssumptions, VOCAB)["properties"]
    # `json_schema` adds the line-list suffix to every `list[str]` description.
    assert props["questions_for_human"]["description"].startswith(
        "In a spec delivery a behaviour doubt is recorded as an assumption, not asked; "
        "only spec_conflict escalates"
    )


def test_consultation_has_no_spec_conflict():
    assert "spec_conflict" not in ConsultationWithAssumptions.model_fields


def test_assumption_rows_keeps_the_canonical_text_and_drops_what_the_rule_refuses():
    data = {"assumptions": ["  keep   this ", "", "   ", "x" * 241, "two\nlines", "ok"]}
    assert assumption_rows("Developer", data, True) == [
        {"role": "Developer", "text": "keep this"},
        {"role": "Developer", "text": "ok"},
    ]


def test_assumption_rows_keeps_a_row_of_exactly_240_characters():
    assert assumption_rows("R", {"assumptions": ["x" * 240]}, True) == [
        {"role": "R", "text": "x" * 240}
    ]


def test_assumption_rows_is_empty_without_a_spec():
    assert assumption_rows("R", {"assumptions": ["a"]}, False) == []


@pytest.mark.parametrize("data", [None, "text", ["a"], 3, {}, {"assumptions": "a"}])
def test_assumption_rows_never_raises_on_a_shape_it_does_not_know(data):
    assert assumption_rows("R", data, True) == []


def test_assumption_rows_skips_an_item_that_is_not_a_string():
    assert assumption_rows("R", {"assumptions": [1, None, ["a"], "b"]}, True) == [
        {"role": "R", "text": "b"}
    ]


def test_interview_doubts_asks_every_doubt_without_a_spec():
    questions = ["q1", "q2"]
    to_human, texts = interview_doubts(questions, False)
    assert (to_human, texts) == (["q1", "q2"], [])
    assert to_human is not questions


def test_interview_doubts_records_every_doubt_as_an_assumption_with_a_spec():
    assert interview_doubts(["q1", "q2"], True) == ([], ["q1", "q2"])
    assert interview_doubts([], True) == ([], [])


def esc(value):
    return {"kind": "escalation", "value": value}


RESUMED = {"kind": "resumed"}
FAILURE = {"kind": "failure", "value": "rate_limit", "section": "s"}


@pytest.mark.parametrize(
    ("log", "decided"),
    [
        ([], False),
        ([esc("questions")], False),
        ([esc("questions"), RESUMED], True),
        ([esc("questions"), RESUMED, FAILURE, esc("cap")], True),
        ([esc("ci"), RESUMED], False),
        ([RESUMED, esc("questions")], False),
        ([esc("questions"), RESUMED, esc("questions")], True),
        ([esc("questions"), FAILURE, {"kind": "lapse", "value": "x: E"}, RESUMED], True),
    ],
)
def test_conflict_decided_reads_a_questions_escalation_followed_by_a_resume(log, decided):
    assert conflict_decided(log, "contradiction") is decided


def test_conflict_decided_counts_a_legacy_questions_escalation_from_any_raiser():
    # An entry with no spec_cause keeps the old reading: any resumed `questions` pause counts.
    log = [{**esc("questions"), "cause": "no_verdict"}, RESUMED]
    assert conflict_decided(log, "data_loss") is True


def spec_esc(cause):
    return {**esc("questions"), "spec_cause": cause}


C, D, K = spec_esc("contradiction"), spec_esc("data_loss"), spec_esc("conflict")
LEGACY = esc("questions")


@pytest.mark.parametrize(
    ("log", "cause", "decided"),
    [
        ([C, RESUMED, D], "data_loss", False),
        ([C, RESUMED, D], "contradiction", True),
        ([C, RESUMED, C], "contradiction", False),
        ([C, RESUMED, C, RESUMED], "contradiction", True),
        ([D, RESUMED, C], "contradiction", False),
        ([LEGACY, RESUMED], "contradiction", True),
        ([LEGACY, RESUMED], "conflict", True),
        ([LEGACY], "data_loss", False),
        ([], "data_loss", False),
        ([LEGACY, RESUMED, D], "data_loss", False),
        ([K, RESUMED, LEGACY], "conflict", True),  # entries carrying the cause alone decide
        ([K, LEGACY, RESUMED], "conflict", True),
        ([K, RESUMED, K, LEGACY, RESUMED], "conflict", True),
        ([LEGACY, RESUMED, K], "conflict", False),
    ],
)
def test_conflict_decided_is_keyed_on_the_cause(log, cause, decided):
    assert conflict_decided(log, cause) is decided


def test_conflict_step_escalates_until_the_owner_decided_and_then_names_the_cause():
    undecided = {"pause_log": [C]}
    decided = {"pause_log": [C, RESUMED]}
    assert conflict_step(undecided, "contradiction") == ESCALATE == "escalate"
    assert (
        conflict_step(decided, "contradiction")
        == "spec conflict (contradiction) settled by the owner's decision"
    )
    assert conflict_step(decided, "data_loss") == ESCALATE
    assert conflict_step({}, "conflict") == ESCALATE


@pytest.mark.parametrize("cause", ["contradiction", "conflict", "data_loss"])
@pytest.mark.parametrize("where", ["plan", "interview"])
def test_conflict_question_is_one_fixed_line_naming_the_cause(cause, where):
    line = conflict_question(cause, where)
    assert line.startswith(f"Spec {cause}: ")
    assert len(line) <= 240
    assert "\n" not in line
    pointer = (
        "see the Tech Lead section of the task document"
        if where == "plan"
        else "see the solution interview in the task document"
    )
    assert line.endswith(f"({pointer})")


def test_conflict_escalation_writes_the_cause_the_status_and_one_question_line():
    state = {"spec": {"path": "spec.md"}}
    patch = conflict_escalation(state, "data_loss", "why", "plan", "2026-11-13T00:00:00Z")
    assert patch["spec_cause"] == "data_loss"
    assert patch["escalation_class"] == "questions"
    assert patch["status"] == "waiting_on_human"
    assert patch["questions_for_human"] == [conflict_question("data_loss", "plan")]
    assert patch["pause_log"] == [
        {
            "kind": "escalation",
            "value": "questions",
            "at": "2026-11-13T00:00:00Z",
            "spec_cause": "data_loss",
        }
    ]


def test_conflict_escalation_needs_a_spec():
    with pytest.raises(KeyError):
        conflict_escalation({}, "conflict", "why", "plan", "2026-11-13T00:00:00Z")


def test_conflict_reason_is_one_line_naming_the_cause_and_the_round():
    reason = conflict_reason("data_loss", 3)
    assert "data_loss" in reason
    assert "3" in reason
    assert "\n" not in reason
    assert "\r" not in reason
