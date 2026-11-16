"""The drawing schema classes nothing consumes yet (GH-766 part 1b): they validate on
their own and no schema a role receives reaches them, so no prompt or role output moves."""

from collections.abc import Iterable
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from agentic.core.process.domain import roles
from agentic.core.process.domain import schemas as s
from agentic.core.process.domain.roles import Role
from agentic.core.process.domain.schemas import (
    DefinitionTurn,
    DrawingCheck,
    DrawingDeparture,
    FinalDesignReview,
    RoleOutput,
)

FORBIDDEN_FIELDS = {"drawing_checks", "departures", "drawing_departures", "visible"}
FORBIDDEN_DEFS = {"DrawingDeparture", "DrawingCheck", "DefinitionTurn"}

# The base schemas a role receives today. A base class added later and left out of this
# list is not covered by this check; the walk over every Role output below is what
# catches a new one a role actually uses.
BASE_SCHEMAS = [
    s.TechPlan,
    s.Consultation,
    s.ConsultationWithAssumptions,
    s.TechAnswers,
    s.PlatformAnswers,
    s.FinalValidation,
    s.Delivery,
    s.DesignReview,
    s.InterviewTurn,
    s.Answers,
]


def _departure(**over: str) -> dict[str, str]:
    base = {
        "drawing": "design/home.png",
        "element": "Save button",
        "shows": "a blue button",
        "would_ship": "a green button",
        "recommendation": "ship the blue one",
        "reason": "the drawing is approved",
        "raised_by": "developer",
    }
    return {**base, **over}


def test_a_check_unverified_validates_without_a_drawing_or_an_element():
    check = DrawingCheck(subject="screen", ref="home", verdict="unverified")
    assert check.drawing is None and check.element is None


@pytest.mark.parametrize("verdict", ["matches", "departs"])
def test_a_check_that_says_matches_or_departs_needs_the_path_and_the_element(verdict: str):
    with pytest.raises(ValidationError):
        DrawingCheck(subject="screen", ref="home", verdict=verdict)
    with pytest.raises(ValidationError):
        DrawingCheck(subject="screen", ref="home", verdict=verdict, drawing="a.png")
    with pytest.raises(ValidationError):
        DrawingCheck(subject="screen", ref="home", verdict=verdict, element="Save")
    ok = DrawingCheck(
        subject="question", ref="q1", verdict=verdict, drawing="./a.png", element="Save"
    )
    assert ok.drawing == "./a.png"


@pytest.mark.parametrize("path", ["../a.png", "/a.png", "a:b.png", "a.png:3", " "])
def test_a_check_with_an_invalid_drawing_path_is_rejected(path: str):
    with pytest.raises(ValidationError):
        DrawingCheck(subject="screen", ref="h", verdict="departs", drawing=path, element="x")


def test_a_check_with_an_invalid_path_is_rejected_even_when_unverified():
    with pytest.raises(ValidationError):
        DrawingCheck(subject="screen", ref="h", verdict="unverified", drawing="a:b", element="x")


def test_the_final_design_review_has_the_two_drawing_fields_and_accepts_empty_lists():
    assert {"drawing_checks", "departures"} <= set(FinalDesignReview.model_fields)
    assert issubclass(FinalDesignReview, s.DesignReview)
    review = FinalDesignReview(
        markdown="m", verdict="approved", reason="ok", items=[], drawing_checks=[], departures=[]
    )
    assert review.drawing_checks == [] and review.departures == []
    with pytest.raises(ValidationError):
        FinalDesignReview(markdown="m", verdict="approved", reason="ok", items=[], departures=[])
    with pytest.raises(ValidationError):
        FinalDesignReview(
            markdown="m", verdict="approved", reason="ok", items=[], drawing_checks=[]
        )


def test_the_final_design_review_validates_its_rows():
    ok = FinalDesignReview(
        markdown="m",
        verdict="returned",
        reason="r",
        items=[],
        drawing_checks=[
            DrawingCheck(
                subject="screen", ref="home", verdict="departs", drawing="a.png", element="x"
            )
        ],
        departures=[DrawingDeparture(**_departure())],
    )
    assert len(ok.drawing_checks) == 1 and len(ok.departures) == 1
    with pytest.raises(ValidationError):
        FinalDesignReview(
            markdown="m",
            verdict="approved",
            reason="r",
            items=[],
            drawing_checks=[{"subject": "screen", "ref": "h", "verdict": "departs"}],
            departures=[],
        )


def test_a_definition_turn_validates_without_visible():
    turn = DefinitionTurn(done=True, markdown="m")
    assert turn.visible == []
    assert DefinitionTurn(done=True, markdown="m", visible=[1, 2]).visible == [1, 2]


def test_a_departure_keeps_the_drawing_as_given_and_rejects_a_colon_in_it():
    assert DrawingDeparture(**_departure(drawing="./design/home.png")).drawing == (
        "./design/home.png"
    )
    with pytest.raises(ValidationError):
        DrawingDeparture(**_departure(drawing="a:b"))


@pytest.mark.parametrize("over", [{"drawing": "../x.png"}, {"element": "  "}])
def test_a_departure_with_an_unusable_path_or_element_is_rejected(over: dict[str, str]):
    with pytest.raises(ValidationError):
        DrawingDeparture(**_departure(**over))


@pytest.mark.parametrize("model", BASE_SCHEMAS, ids=lambda m: m.__name__)
def test_no_base_schema_a_role_receives_has_a_drawing_field(model: type[BaseModel]):
    assert not _forbidden_properties(model)


def _forbidden_properties(model: type[BaseModel]) -> set[str]:
    return FORBIDDEN_FIELDS & set(model.model_json_schema()["properties"])


@pytest.mark.parametrize("model", [DefinitionTurn], ids=lambda m: m.__name__)
def test_the_base_schema_check_catches_a_model_with_a_forbidden_property(model: type[BaseModel]):
    assert _forbidden_properties(model) == {"visible"}


def _walk(node: Any, root: dict[str, Any], seen: set[str], titles: set[str], names: set[str]):
    if isinstance(node, list):
        for item in node:
            _walk(item, root, seen, titles, names)
        return
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and ref not in seen:
        seen.add(ref)
        target: Any = root
        for part in ref.removeprefix("#/").split("/"):
            target = target[part]
        _walk(target, root, seen, titles, names)
    if "title" in node and "properties" in node:
        titles.add(node["title"])
    names.update(node.get("properties", {}))
    for key in ("properties", "$defs"):
        for value in node.get(key, {}).values():
            _walk(value, root, seen, titles, names)
    for key in ("items", "anyOf", "allOf", "oneOf", "additionalProperties"):
        _walk(node.get(key), root, seen, titles, names)


def _reachable(model: type[BaseModel]) -> tuple[set[str], set[str]]:
    schema = model.model_json_schema()
    titles: set[str] = set()
    names: set[str] = set()
    _walk(schema, schema, set(), titles, names)
    return titles, names


def _role_outputs() -> list[type[BaseModel]]:
    found = [v.schema for v in vars(roles).values() if isinstance(v, Role)]
    return list({m: None for m in found})


def test_the_walk_sees_a_drawing_schema_when_one_is_reachable():
    class Holder(BaseModel):
        checks: list[DrawingCheck]
        turn: DefinitionTurn | None = None
        departures: list[DrawingDeparture] = []

    titles, names = _reachable(Holder)
    assert FORBIDDEN_DEFS <= titles
    assert "visible" in names


def test_there_are_role_outputs_to_walk():
    assert len(_role_outputs()) > 10


@pytest.mark.parametrize("model", _role_outputs(), ids=lambda m: m.__name__)
def test_no_role_output_reaches_a_drawing_schema_or_field(model: type[BaseModel]):
    titles, names = _reachable(model)
    assert not FORBIDDEN_DEFS & titles
    assert not FORBIDDEN_FIELDS & names


def _subclasses(cls: type) -> set[type]:
    out: set[type] = set()
    for sub in cls.__subclasses__():
        out |= {sub} | _subclasses(sub)
    return out


def _writers_of_the_record(classes: Iterable[type[BaseModel]]) -> list[str]:
    return sorted(
        c.__name__ for c in classes if {"drawing_departures", "number"} & set(c.model_fields)
    )


def test_no_role_output_writes_a_departure_number_or_the_record():
    writers = _subclasses(RoleOutput)
    assert len(writers) > 10
    assert _writers_of_the_record(writers) == []


def test_the_writers_check_reports_a_class_with_a_number_or_the_record():
    # Standalone models, not RoleOutput subclasses: those would join the real walk.
    class WithNumber(BaseModel):
        number: int

    class WithRecord(BaseModel):
        drawing_departures: list[str] = []

    class Clean(BaseModel):
        text: str = ""

    assert _writers_of_the_record([WithNumber, WithRecord, Clean]) == ["WithNumber", "WithRecord"]
