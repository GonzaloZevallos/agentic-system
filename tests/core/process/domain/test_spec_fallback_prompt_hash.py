"""PM's system prompt and output schema in the states where no draft Spec is due are byte-identical
to the base of GH-594 part 5a (the digests below were computed on that base, by a script that
never imports this part's code), and with a draft due the prompt gains the section and the schema
the field."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.adapters.filesystem.prompts import FilesystemPrompts
from agentic.core.process.application.run_role import load_prompt
from agentic.core.process.application.validate_prd import draft_section, validator_for
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import PM, PM_BUG, PM_CONSOLIDATOR, Role
from agentic.shared.configuration import Configuration

ROOT = Path(__file__).resolve().parents[4]
VOCABULARY = {"touches_ui": "The task adds or changes a screen", "touches_copy": "It changes text"}
# `{{date}}` is today by default and the pinned digests hold the day they were computed.
PLACEHOLDERS = {"remaining_changes": 2, "max_rounds": 4, "round": 1, "date": "2026-11-17"}


class Ports:
    def __init__(self) -> None:
        self.prompts = FilesystemPrompts(ROOT / "no-overlay")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_of(role: Role, tiers: str = "") -> str:
    ports: Any = Ports()
    loaded = load_prompt(
        role,
        Configuration(product_dir=ROOT),
        ports,
        VOCABULARY,
        tiers=tiers,
        **PLACEHOLDERS,
    )
    return loaded.text


def schema_of(role: Role) -> str:
    return json.dumps(schemas.json_schema(role.schema, VOCABULARY), sort_keys=True)


BASE = {
    "pm": (
        "6d05f3605a9ed2c0cfb5bf70347793d70cc9d82d04ba30fd807d9c8d10597e3a",
        "abdf646b7d0277f084da259d3ae6c34e377ad62a1b0e069f045344583fbda0a1",
    ),
    "pm_bug": (
        "501c9bb8ca1da6131b9873d15467534fe27d046207339c0a323270c9475f3064",
        "f114978b2002c333e3fa17d1cbfe3d76cbf73d2e7d9cbf8dd72beb7a017ec228",
    ),
    "pm_consolidator": (
        "b8ebd8732e9605144d1233af9410c0783f2715ee18e856f404d86f2a7eae3960",
        "03c5ef9a57d07c3d356946153ca375479cb3b9e851a851f2e7e9601857876f4c",
    ),
}
FALSE_STATES: dict[str, dict[str, Any]] = {
    "bug": {"type": "bug"},
    "platform": {"type": "platform_enhancement", "path": "full"},
    "light": {"type": "feature", "path": "light"},
    "evaluation": {"type": "feature", "path": "full", "evaluation": {"cohort": "x"}},
    "spec": {"type": "feature", "path": "full", "spec": {"slice": "a slice"}},
}


@pytest.mark.parametrize("role", [PM, PM_BUG, PM_CONSOLIDATOR], ids=lambda role: role.slug)
def test_the_base_prompt_and_schema_are_pinned(role):
    prompt, schema = BASE[role.slug]
    assert digest(prompt_of(role)) == prompt
    assert digest(schema_of(role)) == schema


@pytest.mark.parametrize("state", FALSE_STATES.values(), ids=FALSE_STATES.keys())
def test_without_a_draft_due_the_validator_and_its_section_are_the_base(state):
    role = validator_for(state)
    assert role in (PM, PM_BUG)
    section = draft_section(state, Ports())
    assert section == ""
    prompt, schema = BASE[role.slug]
    assert digest(prompt_of(role, section)) == prompt
    assert digest(schema_of(role)) == schema


def test_a_full_feature_adds_the_section_and_the_field():
    state = {"type": "feature", "path": "full"}
    role = validator_for(state)
    section = draft_section(state, Ports())
    assert section
    assert prompt_of(role, section) == f"{prompt_of(PM)}\n\n{section}"
    properties = schemas.json_schema(role.schema, VOCABULARY)["properties"]
    assert "front matter" in properties["spec_draft"]["description"]
    base_properties = schemas.json_schema(PM.schema, VOCABULARY)["properties"]
    assert set(properties) - set(base_properties) == {"spec_draft"}
    assert "spec_draft" not in prompt_of(PM)


def test_the_draft_block_passes_the_refusals_every_block_passes():
    from agentic.core.process.domain.spec_brief import DRAFT_BLOCK, block_refusal

    text = Ports().prompts.spec_block(DRAFT_BLOCK)
    assert block_refusal(text) is None
    assert block_refusal(text + "\n{{x}}\n") == "template"
