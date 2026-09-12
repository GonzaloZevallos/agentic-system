import pytest

from agentic import schemas
from agentic.schemas import Classification, Definition

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


def test_validate_rejects_bad_type():
    with pytest.raises(ValueError):
        raw = {"markdown": "x", "type": "chore", "confidence": "high", "reason": "r"}
        schemas.validate(Classification, raw, VOCAB)
