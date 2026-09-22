"""Characterisation of the context seam: one golden string per role, byte for byte,
over a fixture task document with every section a task can hold (the PRD and
solution interview transcripts, a multi-subtask plan, the findings table, one
subtask's delivery and review, and the human's decisions).

This is the baseline `plan-trim` (issue #71) diffs against: its acceptance is that
these goldens move only for `DEVELOPER` and `TECH_LEAD_REVIEWER`, byte for byte,
everyone else's staying exactly what is pinned here. No production code changes in
this file — only the fixture and the pins.
"""

from __future__ import annotations

from agentic.core.process.application.run_role import context_for, preamble, with_context
from agentic.core.process.domain.document import HUMAN
from agentic.core.process.domain.roles import (
    DEVELOPER,
    DEVELOPER_INTEGRATOR,
    FINDINGS,
    PJM,
    PM,
    PRD_INTERVIEW,
    PRODUCT_ANALYST,
    SOLUTION_INTERVIEW,
    TECH_LEAD,
    TECH_LEAD_EPHEMERAL,
    TECH_LEAD_PLATFORM,
    TECH_LEAD_REVIEWER,
)
from tests.fakes import MemoryKnowledge, memory_ports

# One subtask's own delivery and its review, the shape `develop`/`review` pass as
# `extra_sections` (`rules.developer_section`/`.review_section`, hardcoded here like
# `test_run_role.py` already does for the same two names).
DEVELOPER_SECTION = "Developer: api"
REVIEW_SECTION = "Tech Lead: review api"
EXTRA_SECTIONS = (DEVELOPER_SECTION, REVIEW_SECTION)

TITLE = "Context diet per role"
# A nested heading, like `test_run_role.py`'s own fixture: `demote()` must still push
# it one level down inside the built `## Original request` section.
DESCRIPTION = (
    "Developers read the whole plan today, sisters included (README §6).\n\n"
    "## Why it matters\n\nThe cache makes it cheap, not free."
)

PJM_SECTION_TEXT = "Clear signal: a feature, PM decides next."
PRD_TRANSCRIPT_TEXT = (
    "**Product Analyst**: What triggers the export?\n\n**PM**: A button on the audit list."
)
PM_SECTION_TEXT = (
    "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet."
)
SOLUTION_TRANSCRIPT_TEXT = (
    "**Tech Lead efímero**: Where does the context rule live?\n\n"
    "**Tech Lead**: `run_role.context_for`, one seam for both prompts."
)
# The plan: three `#### ` briefs with different heading shapes, so a reader of this
# fixture cannot depend on one particular shape (acceptance of this subtask).
TECH_LEAD_SECTION_TEXT = """### Solution

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
# The plan's structured record behind `TECH_LEAD_SECTION_TEXT`'s three briefs (issue
# #71, `plan-trim`): ids deliberately unlike any of the three heading shapes above, so
# the trim cannot pass by accidentally keying off one of them (invariant 3).
PLAN_SUBTASKS = {
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
# `TECH_LEAD_SECTION_TEXT` as the Developer/reviewer of `api` reads it: the
# `### Subtasks` block cut to its own brief, rendered from `PLAN_SUBTASKS["api"]`, plus
# the sister index; every other heading byte-for-byte (`plan_view.for_reader`).
TECH_LEAD_SECTION_TEXT_FOR_API = """### Solution

Trim the plan section per reader, at `context_for`.

### Standards

Hexagonal architecture; the trim is a pure function.

### Subtasks

#### API

Build the read endpoint.

Seams: `adapters.http.routes.get_x`.

Depends on: nothing.

The other subtasks, for context only, not yours to build:

- `sql` — SQL adapter
- `wire` — Wire the endpoint

### Invariants

1. Every role but a Developer or a subtask reviewer keeps its byte-identical context.

### Integration

Merge in dependency order; no integrator needed for two subtasks.

### For the next role

Read your own brief under `### Subtasks`; the others are for context only."""
FINDINGS_SECTION_TEXT = (
    "| subtask | v | location | claim | severity | status |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| api | 1 | run_role.py:170 | needs a golden before the trim | important | open |"
)
DEVELOPER_API_SECTION_TEXT = (
    "### Changes\n\n- `run_role.py`: no change, characterisation only.\n\n"
    "### Tests\n\nGolden strings pin today's context.\n\n"
    "### Decisions and assumptions\n\nNone.\n\n"
    "### For the Tech Lead\n\nNothing to flag."
)
REVIEW_API_SECTION_TEXT = "### Findings\n\nNone. The goldens match what `context_for` builds today."
HUMAN_SECTION_TEXT = (
    "### Decision — where the trim lives\n\n"
    "Inside `context_for`, never in `develop.py` or `review.py`."
)

KNOWLEDGE_TEXT = (
    "## Product knowledge\n\nCSV export ships weekly; the audit list is the only entry point."
)
STANDARDS_TEXT = (
    "## Engineering standards\n\nHexagonal architecture; tests at two seams; strict typing."
)

STATE = {
    "title": TITLE,
    "description": DESCRIPTION,
    "subtasks": PLAN_SUBTASKS,
    "sections": {
        "PjM": PJM_SECTION_TEXT,
        PRD_INTERVIEW: PRD_TRANSCRIPT_TEXT,
        "PM": PM_SECTION_TEXT,
        SOLUTION_INTERVIEW: SOLUTION_TRANSCRIPT_TEXT,
        "Tech Lead": TECH_LEAD_SECTION_TEXT,
        FINDINGS: FINDINGS_SECTION_TEXT,
        DEVELOPER_SECTION: DEVELOPER_API_SECTION_TEXT,
        REVIEW_SECTION: REVIEW_API_SECTION_TEXT,
        "Human": HUMAN_SECTION_TEXT,
    },
}

PORTS = memory_ports(knowledge=MemoryKnowledge(text=KNOWLEDGE_TEXT, standards=STANDARDS_TEXT))


def _context(role, extra_sections: tuple[str, ...] = (), subtask_id: str | None = None) -> str:
    standards, knowledge = preamble(role, PORTS)
    return context_for(STATE, role, knowledge, standards, extra_sections, subtask_id)


def test_pjm_context_is_pinned():
    assert _context(PJM) == (
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_pm_context_is_pinned():
    assert _context(PM) == (
        "## Product knowledge\n\n"
        "CSV export ships weekly; the audit list is the only entry point.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## Interview: Product Analyst→PM\n\n"
        "**Product Analyst**: What triggers the export?\n\n**PM**: A button on the audit list.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_product_analyst_context_is_pinned():
    assert _context(PRODUCT_ANALYST) == (
        "## Product knowledge\n\n"
        "CSV export ships weekly; the audit list is the only entry point.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PjM\n\nClear signal: a feature, PM decides next.\n\n"
        "## Interview: Product Analyst→PM\n\n"
        "**Product Analyst**: What triggers the export?\n\n**PM**: A button on the audit list.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_tech_lead_ephemeral_context_is_pinned():
    assert _context(TECH_LEAD_EPHEMERAL) == (
        "## Engineering standards\n\n"
        "Hexagonal architecture; tests at two seams; strict typing.\n\n"
        "## Product knowledge\n\n"
        "CSV export ships weekly; the audit list is the only entry point.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_tech_lead_context_is_pinned():
    assert _context(TECH_LEAD) == (
        "## Engineering standards\n\n"
        "Hexagonal architecture; tests at two seams; strict typing.\n\n"
        "## Product knowledge\n\n"
        "CSV export ships weekly; the audit list is the only entry point.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        "## Interview: Tech Lead efímero→Tech Lead\n\n"
        "**Tech Lead efímero**: Where does the context rule live?\n\n"
        "**Tech Lead**: `run_role.context_for`, one seam for both prompts.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_tech_lead_platform_context_is_pinned():
    # The platform twin reads `PjM` where `TECH_LEAD` reads `PM`: there is no PM in
    # the loop on this path (ADR-0017).
    assert _context(TECH_LEAD_PLATFORM) == (
        "## Engineering standards\n\n"
        "Hexagonal architecture; tests at two seams; strict typing.\n\n"
        "## Product knowledge\n\n"
        "CSV export ships weekly; the audit list is the only entry point.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PjM\n\nClear signal: a feature, PM decides next.\n\n"
        "## Interview: Tech Lead efímero→Tech Lead\n\n"
        "**Tech Lead efímero**: Where does the context rule live?\n\n"
        "**Tech Lead**: `run_role.context_for`, one seam for both prompts.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_tech_lead_reviewer_context_is_pinned():
    # `plan-trim` (issue #71): the reviewer gets the same projection as the Developer,
    # its own brief plus a sister index instead of every subtask's (`plan_view`).
    assert _context(TECH_LEAD_REVIEWER, EXTRA_SECTIONS, subtask_id="api") == (
        "## Engineering standards\n\n"
        "Hexagonal architecture; tests at two seams; strict typing.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        f"## Tech Lead\n\n{TECH_LEAD_SECTION_TEXT_FOR_API}\n\n"
        "## Tech Lead: findings\n\n"
        "| subtask | v | location | claim | severity | status |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| api | 1 | run_role.py:170 | needs a golden before the trim | important | open |\n\n"
        "## Developer: api\n\n"
        "### Changes\n\n- `run_role.py`: no change, characterisation only.\n\n"
        "### Tests\n\nGolden strings pin today's context.\n\n"
        "### Decisions and assumptions\n\nNone.\n\n"
        "### For the Tech Lead\n\nNothing to flag.\n\n"
        "## Tech Lead: review api\n\n"
        "### Findings\n\nNone. The goldens match what `context_for` builds today.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_developer_context_is_pinned():
    # `plan-trim` (issue #71): this is the golden that moves. The Developer of `api`
    # reads its own brief, not `sql`'s or `wire`'s, under the same `### Subtasks`
    # heading; everything else in this section, and every other role's golden in this
    # file, is untouched.
    assert _context(DEVELOPER, EXTRA_SECTIONS, subtask_id="api") == (
        "## Engineering standards\n\n"
        "Hexagonal architecture; tests at two seams; strict typing.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        f"## Tech Lead\n\n{TECH_LEAD_SECTION_TEXT_FOR_API}\n\n"
        "## Tech Lead: findings\n\n"
        "| subtask | v | location | claim | severity | status |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| api | 1 | run_role.py:170 | needs a golden before the trim | important | open |\n\n"
        "## Developer: api\n\n"
        "### Changes\n\n- `run_role.py`: no change, characterisation only.\n\n"
        "### Tests\n\nGolden strings pin today's context.\n\n"
        "### Decisions and assumptions\n\nNone.\n\n"
        "### For the Tech Lead\n\nNothing to flag.\n\n"
        "## Tech Lead: review api\n\n"
        "### Findings\n\nNone. The goldens match what `context_for` builds today.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_developer_integrator_context_is_pinned():
    # Same `reads` and the same `extra_sections` shape as `DEVELOPER` today, so the
    # bytes below happen to match `test_developer_context_is_pinned`'s — but pinned
    # inline, on the integrator's own call, not by comparing it to another call of the
    # function under test: `plan-trim` must not be able to trim this role and still
    # pass by moving both sides of an equality together.
    assert _context(DEVELOPER_INTEGRATOR, EXTRA_SECTIONS) == (
        "## Engineering standards\n\n"
        "Hexagonal architecture; tests at two seams; strict typing.\n\n"
        "## Original request\n\n"
        "Context diet per role\n\n"
        "Developers read the whole plan today, sisters included (README §6).\n\n"
        "### Why it matters\n\nThe cache makes it cheap, not free.\n\n"
        "## PM\n\n"
        "### Verdict\n\napproved\n\n### For the next role\n\nCSV export only, no filters yet.\n\n"
        f"## Tech Lead\n\n{TECH_LEAD_SECTION_TEXT}\n\n"
        "## Tech Lead: findings\n\n"
        "| subtask | v | location | claim | severity | status |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| api | 1 | run_role.py:170 | needs a golden before the trim | important | open |\n\n"
        "## Developer: api\n\n"
        "### Changes\n\n- `run_role.py`: no change, characterisation only.\n\n"
        "### Tests\n\nGolden strings pin today's context.\n\n"
        "### Decisions and assumptions\n\nNone.\n\n"
        "### For the Tech Lead\n\nNothing to flag.\n\n"
        "## Tech Lead: review api\n\n"
        "### Findings\n\nNone. The goldens match what `context_for` builds today.\n\n"
        "## Human\n\n"
        "### Decision — where the trim lives\n\n"
        "Inside `context_for`, never in `develop.py` or `review.py`."
    )


def test_with_context_developer_cold_prompt_carries_the_same_pinned_context():
    # `with_context` is the seam a returned Developer's cold fallback uses when its
    # resumed session turns out stale (ADR-0004, sixth addendum): the full reading
    # `context_for` builds, plus whatever the caller's own delta text adds on top.
    delta = "## Delta\n\nThe branch moved to `head3` since your last delivery."
    actual = with_context(STATE, DEVELOPER, PORTS, None, delta, extra_sections=EXTRA_SECTIONS)
    assert actual == f"{_context(DEVELOPER, EXTRA_SECTIONS)}\n\n{delta}"


def test_with_context_cold_prompt_carries_the_same_trimmed_plan_as_the_ordinary_prompt():
    # `plan-trim` (issue #71): the cold path (ADR-0004, sixth addendum) must not
    # silently restore the sisters' briefs when `context_for`'s ordinary path trims
    # them — `with_context` threads `subtask_id` to the same seam.
    delta = "## Delta\n\nThe branch moved to `head3` since your last delivery."
    actual = with_context(
        STATE, DEVELOPER, PORTS, None, delta, extra_sections=EXTRA_SECTIONS, subtask_id="api"
    )
    assert actual == f"{_context(DEVELOPER, EXTRA_SECTIONS, subtask_id='api')}\n\n{delta}"
    assert "Persist behind the port the domain declares." not in actual  # not `sql`'s brief


def test_context_for_degrades_to_the_full_plan_for_the_integrator():
    # Invariant 4: `integration` is the fan-in's own subtask, not one of the plan's, so
    # it still reads every sister's brief, even though it *is* a key in `subtasks`
    # once the fan-in has added it (`rules.integration_subtask`).
    with_integration_entry = {**PLAN_SUBTASKS, "integration": {"id": "integration"}}
    state = {**STATE, "subtasks": with_integration_entry}
    standards, knowledge = preamble(DEVELOPER_INTEGRATOR, PORTS)
    text = context_for(
        state, DEVELOPER_INTEGRATOR, knowledge, standards, EXTRA_SECTIONS, "integration"
    )
    assert f"## Tech Lead\n\n{TECH_LEAD_SECTION_TEXT}\n\n" in text


def test_every_role_reads_the_human_last_and_it_is_not_declared():
    # Not a golden, but what makes the goldens above trustworthy: `HUMAN` is never one
    # of `role.reads` (`run_role.context_for`'s own docstring), so every golden above
    # ends with it by the same rule, not by a fixture accident.
    for role in (PJM, PM, PRODUCT_ANALYST, TECH_LEAD_EPHEMERAL, TECH_LEAD, TECH_LEAD_PLATFORM):
        assert HUMAN not in role.reads
