"""Unit tests of `plan_view.for_reader`, `subtasks_block` and `with_subtasks_block`,
pure: no state, no ports, no prompt seam.

The fixture section reuses the three-brief, three-heading-shape plan
(`#### api`, `#### api — SQL adapter`, `#### 2. api`) `context-golden` (issue #71)
introduced, so this module is proven never to key off any one of those shapes."""

from agentic.core.process.domain.plan_view import (
    NOTE_SHORT,
    NOTE_WRITTEN,
    for_reader,
    subtasks_block,
    with_subtasks_block,
)
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


def test_adds_the_subtasks_block_when_the_heading_is_missing():
    # Inverted and renamed (was `test_degrades_to_the_full_section_with_no_subtasks_
    # heading`): a plan whose Tech Lead used its own headings instead of `### Subtasks`
    # (every `claude-opus-5-5` plan, GH-134 on) no longer leaves the reader with
    # nothing of their own -- the block is appended, not skipped (issue #139).
    # `with_subtasks_block` repairs a *new* plan's markdown before anyone reads it;
    # this is what an old one, already on disk, still gets when it is resumed.
    no_heading = "### Solution\n\nOne subtask only, no plural heading needed."
    trimmed = for_reader(no_heading, "api", SUBTASKS)
    assert trimmed.startswith(no_heading)
    assert (
        trimmed == f"{no_heading}\n\n### Subtasks\n\n{_brief_text('api')}\n\n{_index_text('api')}"
    )


def _brief_text(subtask_id: str) -> str:
    sub = SUBTASKS[subtask_id]
    depends_on = ", ".join(f"`{d}`" for d in sub["depends_on"]) or "nothing"
    return (
        f"#### {sub['title']}\n\n{sub['brief']}\n\n"
        f"Seams: `{sub['seams'][0]}`.\n\nDepends on: {depends_on}."
    )


def _index_text(subtask_id: str) -> str:
    others = [(sid, sub["title"]) for sid, sub in SUBTASKS.items() if sid != subtask_id]
    lines = "\n".join(f"- `{sid}` — {title}" for sid, title in others)
    return f"The other subtasks, for context only, not yours to build:\n\n{lines}"


def test_a_subtasks_block_that_is_the_last_heading_leaves_no_dangling_blank_lines():
    section = "### Solution\n\nOne module.\n\n### Subtasks\n\n#### api\n\nDo it.\n\nSeams: `x`."
    trimmed = for_reader(section, "api", SUBTASKS)
    assert trimmed.startswith("### Solution\n\nOne module.\n\n### Subtasks\n\n")
    assert not trimmed.endswith("\n\n")


def test_a_subtasks_heading_inside_a_fence_is_ignored():
    section = (
        "### Solution\n\nSee the example below.\n\n"
        "```markdown\n### Subtasks\n\nNot a real heading.\n```\n\n"
        "### Invariants\n\n1. x."
    )
    assert subtasks_block(section) is None
    assert for_reader(section, "api", SUBTASKS) == f"{section}\n\n### Subtasks\n\n" + (
        f"{_brief_text('api')}\n\n{_index_text('api')}"
    )


def test_a_top_heading_inside_a_fence_mid_block_does_not_cut_it():
    section = (
        "### Solution\n\nx.\n\n### Subtasks\n\n#### api\n\nDo it.\n\nSeams: `x`.\n\n"
        "```\n### looks like a heading but is fenced\n```\n\nMore brief text.\n\n"
        "### Invariants\n\n1. y."
    )
    start, end = subtasks_block(section)
    block = section[start:end]
    assert "looks like a heading but is fenced" in block
    assert "More brief text." in block
    assert section[end:].startswith("### Invariants")


def test_subtasks_with_a_colon_or_at_the_wrong_level_do_not_count_as_the_heading():
    assert subtasks_block("### Solution\n\nx.\n\n### Subtasks:\n\n#### api\n\nDo it.") is None
    assert subtasks_block("### Solution\n\nx.\n\n#### Subtasks\n\n#### api\n\nDo it.") is None


def test_an_unclosed_fence_before_the_real_heading_gives_none():
    # A document whose heading only exists past an unclosed fence (a shape
    # `with_subtasks_block` repairs for any *new* plan before anyone reads it)
    # degrades the same as no heading at all: the block is appended, carrying the
    # reader's brief twice, which is the documented, tested trade-off (Tech Lead's
    # "Known risks").
    section = "### Solution\n\n```\nunterminated\n\n### Subtasks\n\n#### api\n\nDo it."
    assert subtasks_block(section) is None
    # `for_reader` then appends the block: the brief appears twice (once inside the
    # unclosed fence's text, once as the appended block), the trade-off named above.
    trimmed = for_reader(section, "api", SUBTASKS)
    assert trimmed == f"{section}\n\n### Subtasks\n\n{_brief_text('api')}\n\n{_index_text('api')}"


def test_with_subtasks_block_writes_the_missing_heading_from_the_structured_plan():
    # The issue's own case (GH-55): a valid structured `subtasks` list, no `###
    # Subtasks` line anywhere. The earlier text survives byte for byte, then the
    # system's own block, one `####` per subtask in plan order, with `NOTE_WRITTEN`.
    section = "### Solution\n\nHex.\n\n### Invariants\n\n1. x."
    subs = [SUBTASKS["api"], SUBTASKS["sql"], SUBTASKS["wire"]]
    briefs = "\n\n".join(_brief_text(s["id"]) for s in subs)
    assert with_subtasks_block(section, subs) == (
        f"{section}\n\n### Subtasks\n\n{NOTE_WRITTEN}\n\n{briefs}"
    )


def test_with_subtasks_block_leaves_a_complete_block_unchanged():
    # SECTION already has one `####` per subtask (three), regardless of their text
    # (invariant 3: heading text is never compared with ids).
    assert with_subtasks_block(SECTION, list(SUBTASKS.values())) == SECTION


def test_with_subtasks_block_treats_a_fenced_heading_as_missing():
    section = (
        "### Solution\n\nSee the example below.\n\n"
        "```markdown\n### Subtasks\n\nNot a real heading.\n```\n\n"
        "### Invariants\n\n1. x."
    )
    assert with_subtasks_block(section, [SUBTASKS["api"]]) == (
        f"{section}\n\n### Subtasks\n\n{NOTE_WRITTEN}\n\n{_brief_text('api')}"
    )


def test_with_subtasks_block_keeps_the_models_prose_and_adds_the_short_note():
    section = "### Solution\n\nx.\n\n### Subtasks\n\n#### a\n\nbrief a\n\n### Invariants\n\n1. y."
    note = NOTE_SHORT.format(n=2)
    assert with_subtasks_block(section, [SUBTASKS["api"], SUBTASKS["sql"]]) == (
        f"### Solution\n\nx.\n\n### Subtasks\n\n{note}\n\n"
        "#### a\n\nbrief a\n\n### Invariants\n\n1. y."
    )


def test_with_subtasks_block_appends_after_a_near_miss_heading_without_naming_it():
    # `### Subtasks:` is not the exact heading (`subtasks_block` already proves this
    # elsewhere), so it is treated as missing: the near-miss text, with its own `####
    # a`, survives byte for byte, and the system's block is appended after it. Nothing
    # about the near miss is detected or named.
    section = "### Solution\n\nx.\n\n### Subtasks:\n\n#### a\n\nbrief a"
    assert with_subtasks_block(section, [SUBTASKS["api"]]) == (
        f"{section}\n\n### Subtasks\n\n{NOTE_WRITTEN}\n\n{_brief_text('api')}"
    )
