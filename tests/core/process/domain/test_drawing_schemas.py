"""The drawing schema classes (GH-766 parts 1b and 3a): they validate on their own, and only
`Answers`, `Consultation` and their subclasses carry them, `DefinitionTurn` the `visible` field."""

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

DRAWING_DEFS = {"DrawingDeparture", "DrawingCheck"}
FORBIDDEN_DEFS = DRAWING_DEFS | {"DefinitionTurn"}


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


def _with_field(name: str) -> set[type[BaseModel]]:
    return {c for c in _subclasses(RoleOutput) | {RoleOutput} if name in c.model_fields}


def test_visible_exists_only_in_the_definition_turn():
    assert _with_field("visible") == {DefinitionTurn}


def test_drawing_checks_and_departures_exist_only_in_answers_consultation_and_subclasses():
    owners = {s.Answers, s.Consultation} | _subclasses(s.Answers) | _subclasses(s.Consultation)
    assert owners >= {s.Answers, s.Consultation, s.ConsultationWithAssumptions}
    # The final review is the one other owner (a role gets it in 5.2); DesignReview stays clean.
    owners = owners | {FinalDesignReview}
    assert _with_field("drawing_checks") == owners
    assert _with_field("departures") == owners


@pytest.mark.parametrize("model", _role_outputs(), ids=lambda m: m.__name__)
def test_a_role_output_reaches_the_drawing_schemas_only_through_those_fields(
    model: type[BaseModel],
):
    titles, _ = _reachable(model)
    carries = bool({"drawing_checks", "departures"} & set(model.model_fields))
    assert bool(DRAWING_DEFS & titles) == carries


def test_the_empty_default_keeps_the_new_fields_out_of_an_existing_payload():
    turn = s.Answers(markdown="m", questions_for_human=[])
    assert turn.drawing_checks == [] and turn.departures == []
    consultation = s.Consultation(markdown="m", questions_for_human=[], definitions=[], glossary=[])
    assert consultation.drawing_checks == [] and consultation.departures == []


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
