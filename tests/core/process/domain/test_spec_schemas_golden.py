"""The schemas a delivery without a spec sends the model stay byte for byte what they were
(GH-594 part 2b): the golden fixture was generated before any `*WithAssumptions` class existed,
and is never regenerated. A pydantic upgrade that renders a schema differently turns this red
on purpose."""

import json
from pathlib import Path

import pytest

from agentic.core.process.domain import schemas

GOLDEN = Path(__file__).parent / "fixtures" / "spec_schemas_golden.json"
VOCAB = {"touches_ui": "a", "touches_copy": "b"}
NAMES = [
    "TechPlan",
    "FinalValidation",
    "TechAnswers",
    "PlatformAnswers",
    "Consultation",
    "Delivery",
]


def test_the_golden_covers_exactly_the_six_schemas():
    assert set(json.loads(GOLDEN.read_text())) == set(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_the_live_schema_equals_the_golden(name):
    golden = json.loads(GOLDEN.read_text())
    live = json.dumps(schemas.json_schema(getattr(schemas, name), VOCAB), sort_keys=True)
    assert live == golden[name]
