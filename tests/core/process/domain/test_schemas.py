import pytest

from agentic.core.process.domain import schemas
from agentic.core.process.domain.schemas import (
    Answers,
    Classification,
    Definition,
    TechAnswers,
    TechPlan,
    Verdict,
)

VOCAB = {"touches_ui": "UI", "touches_copy": "copy"}


def test_json_schema_closes_flags_over_vocabulary():
    out = schemas.json_schema(Definition, VOCAB)
    flags = out["properties"]["flags"]
    assert set(flags["properties"]) == set(VOCAB)
    assert flags["required"] == list(VOCAB)
    assert flags["additionalProperties"] is False
    # Schemas without flags are untouched.
    assert "flags" not in schemas.json_schema(Classification, VOCAB)["properties"]


def test_validate_accepts_exact_vocabulary():
    data = schemas.validate(
        Definition, {"markdown": "x", "flags": {"touches_ui": True, "touches_copy": False}}, VOCAB
    )
    assert data["flags"] == {"touches_ui": True, "touches_copy": False}


def test_validate_rejects_invented_or_missing_flags():
    match = "unknown: \\['ui_change'\\], missing: \\['touches_copy'\\]"
    with pytest.raises(ValueError, match=match):
        schemas.validate(
            Definition, {"markdown": "x", "flags": {"touches_ui": True, "ui_change": True}}, VOCAB
        )


def test_verdict_carries_the_consolidation():
    out = schemas.json_schema(Verdict, VOCAB)
    assert {"definitions", "glossary"} <= set(out["required"])

    data = schemas.validate(
        Verdict,
        {
            "markdown": "x",
            "verdict": "approved",
            "reason": "r",
            "flags": {"touches_ui": True, "touches_copy": False},
            "definitions": [{"capability": "metrics", "content": "# Metrics"}],
            "glossary": [{"term": "Churn", "definition": "Members who leave.", "avoid": ""}],
        },
        VOCAB,
    )
    assert data["definitions"][0]["capability"] == "metrics"
    assert data["glossary"][0]["term"] == "Churn"


def test_top_level_string_lists_reach_the_model_as_lines():
    # In GH-12 every unparseable structured output came from a role whose schema had
    # a top-level `list[str]`: the model wrote the items as bare text or `<item>` tags
    # instead of a JSON array (issue #8). So the model gets one string, a line per
    # item, and the system still gets the list.
    out = schemas.json_schema(TechAnswers, VOCAB)
    field = out["properties"]["questions_for_pm"]
    assert field["type"] == "string" and "items" not in field
    assert "One per line" in field["description"] and "empty" in field["description"].lower()
    # Nested lists stay arrays: the plan's `seams` never failed.
    plan = schemas.json_schema(TechPlan, VOCAB)
    assert plan["$defs"]["Subtask"]["properties"]["seams"]["type"] == "array"

    raw = {"markdown": "x", "questions_for_pm": "Which day counts?\n\n  Is null a value? \n"}
    assert schemas.validate(TechAnswers, raw, VOCAB)["questions_for_pm"] == [
        "Which day counts?",
        "Is null a value?",
    ]
    assert (
        schemas.validate(TechAnswers, {"markdown": "x", "questions_for_pm": ""}, VOCAB)[
            "questions_for_pm"
        ]
        == []
    )
    # A list, as the fakes and an older run send it, still validates.
    raw = {"markdown": "x", "questions_for_pm": ["a", "b"]}
    assert schemas.validate(TechAnswers, raw, VOCAB)["questions_for_pm"] == ["a", "b"]


def test_validate_rejects_bad_type_as_value_error():
    with pytest.raises(ValueError):
        raw = {"markdown": "x", "type": "chore", "confidence": "high", "reason": "r"}
        schemas.validate(Classification, raw, VOCAB)


def test_withdrawn_may_be_left_out():
    # Most rounds have nothing with the human, so a model that skips the field instead
    # of writing `[]` is not a role failure (issue #12); the numbers stay an array.
    raw = {"markdown": "x", "questions_for_human": ""}
    assert schemas.validate(Answers, raw, VOCAB)["withdrawn"] == []
    raw = {"markdown": "x", "questions_for_human": "", "withdrawn": [2, 1]}
    assert schemas.validate(Answers, raw, VOCAB)["withdrawn"] == [2, 1]
    assert schemas.json_schema(Answers, VOCAB)["properties"]["withdrawn"]["type"] == "array"


def finding(severity="blocking", **more) -> dict:
    return {
        "location": "src/x.py:12",
        "claim": "returns None for an empty list",
        "evidence": "$ pytest tests/test_x.py::test_empty\nFAILED ... AssertionError",
        "severity": severity,
        "scope": "subtask",
        "category": "spec-behaviour",
        **more,
    }


def test_review_findings_are_typed_and_the_categories_closed():
    out = schemas.json_schema(schemas.Review, VOCAB)
    assert "verdict" not in out["properties"]  # derived from the findings (ADR-0016)
    assert out["properties"]["findings"]["type"] == "array"
    data = schemas.validate(
        schemas.Review, {"markdown": "x", "reason": "r", "findings": [finding()]}, VOCAB
    )
    assert data["findings"][0]["category"] == "spec-behaviour"
    with pytest.raises(ValueError):
        schemas.validate(
            schemas.Review,
            {"markdown": "x", "reason": "r", "findings": [finding(category="style")]},
            VOCAB,
        )
    assert set(schemas.CATEGORIES) == {
        "standard-architecture",
        "standard-typing-silenced",
        "standard-seams",
        "standard-other",
        "spec-behaviour",
        "spec-missing-test",
        "spec-scope",
        "process-uncommitted",
        "process-suite-red",
        "plan-calibration",
        "plan-invariant",
        "integration-crosscut",
    }
