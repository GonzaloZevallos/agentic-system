"""Unit tests of `plan_view.for_reader`, pure: no state, no ports, no prompt seam.

The fixture section reuses the three-brief, three-heading-shape plan
(`#### api`, `#### api — SQL adapter`, `#### 2. api`) `context-golden` (issue #71)
introduced, so this module is proven never to key off any one of those shapes."""

from agentic.core.process.domain.plan_view import for_reader
from agentic.core.process.domain.task import INTEGRATION

SECTION = """### Solution

Trim the plan section per reader, at `context_for`.

### Standards

Hexagonal architecture; the trim is a pure function.

### Subtasks

#### api

Build the read endpoint.

Seams: `adapters.http.routes.get_x`.

Depends on: nothing.

#### api — SQL adapter

Persist behind the port the domain declares.

Seams: `adapters.sql.repository.ApiRepository`.

Depends on: `api`.

#### 2. api

Wire the endpoint to the adapter and cut over.

Seams: `application.use_cases.get_x`.

Depends on: `api`, `api — SQL adapter`.

### Invariants

1. Every role but a Developer or a subtask reviewer keeps its byte-identical context.

### Integration

Merge in dependency order; no integrator needed for two subtasks.

### For the next role

Read your own brief under `### Subtasks`; the others are for context only."""

SUBTASKS = {
    "api": {
        "id": "api",
        "title": "API",
        "brief": "Build the read endpoint.",
        "seams": ["adapters.http.routes.get_x"],
        "depends_on": [],
    },
    "sql": {
        "id": "sql",
        "title": "SQL adapter",
        "brief": "Persist behind the port the domain declares.",
        "seams": ["adapters.sql.repository.ApiRepository"],
        "depends_on": ["api"],
    },
    "wire": {
        "id": "wire",
        "title": "Wire the endpoint",
        "brief": "Wire the endpoint to the adapter and cut over.",
        "seams": ["application.use_cases.get_x"],
        "depends_on": ["api", "sql"],
    },
}

BEFORE = """### Solution

Trim the plan section per reader, at `context_for`.

### Standards

Hexagonal architecture; the trim is a pure function.

"""
AFTER = """### Invariants

1. Every role but a Developer or a subtask reviewer keeps its byte-identical context.

### Integration

Merge in dependency order; no integrator needed for two subtasks.

### For the next role

Read your own brief under `### Subtasks`; the others are for context only."""


def test_replaces_the_subtasks_block_with_the_readers_own_brief():
    trimmed = for_reader(SECTION, "sql", SUBTASKS)
    assert trimmed.startswith(BEFORE)
    assert trimmed.endswith(f"\n\n{AFTER}")
    assert "Persist behind the port the domain declares." in trimmed
    assert "adapters.sql.repository.ApiRepository" in trimmed
    # The sisters' own brief text is gone, kept only as a name-and-title index.
    assert "Build the read endpoint." not in trimmed
    assert "Wire the endpoint to the adapter and cut over." not in trimmed
    assert "`api` — API" in trimmed
    assert "`wire` — Wire the endpoint" in trimmed
    assert "`sql` — SQL adapter" not in trimmed  # not its own sister


def test_everything_outside_the_subtasks_block_survives_byte_for_byte():
    trimmed = for_reader(SECTION, "api", SUBTASKS)
    assert trimmed.startswith(BEFORE)
    assert trimmed.endswith(f"\n\n{AFTER}")


def test_degrades_to_the_full_section_with_no_reader_id():
    assert for_reader(SECTION, None, SUBTASKS) == SECTION


def test_degrades_to_the_full_section_when_the_id_is_absent_from_the_plan():
    assert for_reader(SECTION, "ghost", SUBTASKS) == SECTION


def test_degrades_to_the_full_section_for_the_integrator():
    # Even when the fan-in already added an `integration` entry to `subtasks`
    # (`rules.integration_subtask`): its job is the seams between subtasks, not one of
    # them (ADR-0015), so it still reads the whole plan.
    with_integration = {**SUBTASKS, INTEGRATION: {"id": INTEGRATION, "title": "Integration"}}
    assert for_reader(SECTION, INTEGRATION, with_integration) == SECTION


def test_degrades_to_the_full_section_with_no_subtasks_heading():
    no_heading = "### Solution\n\nOne subtask only, no plural heading needed."
    assert for_reader(no_heading, "api", SUBTASKS) == no_heading


def test_a_subtasks_block_that_is_the_last_heading_leaves_no_dangling_blank_lines():
    section = "### Solution\n\nOne module.\n\n### Subtasks\n\n#### api\n\nDo it.\n\nSeams: `x`."
    trimmed = for_reader(section, "api", SUBTASKS)
    assert trimmed.startswith("### Solution\n\nOne module.\n\n### Subtasks\n\n")
    assert not trimmed.endswith("\n\n")
