"""The design roles, their schemas and prompts, the plan's `screens` field and the
Developer's screens notice (ADR-0040, issue #143, `design-roles`). No routing here:
`rules.design_step`, the nodes and the resume logic are another subtask's."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import plan_view, rules, schemas
from agentic.core.process.domain.roles import (
    DESIGN_LEAD,
    DESIGN_LEAD_FINAL,
    PM,
    PM_CONSOLIDATOR,
    PM_FINAL,
    PROMPTS_DIR,
    READ_CODE,
    READ_CODE_RULES,
    ROLES,
    UI_DESIGNER,
    UX_DESIGNER,
    UX_LEAD,
    UX_LEAD_FINAL,
)
from agentic.core.process.domain.task import Subtask


def test_the_design_roles_are_all_read_only_and_registered_for_preflight():
    design_roles = (
        UX_DESIGNER,
        UX_LEAD,
        UI_DESIGNER,
        DESIGN_LEAD,
        PM_CONSOLIDATOR,
        UX_LEAD_FINAL,
        DESIGN_LEAD_FINAL,
        PM_FINAL,
    )
    for role in design_roles:
        assert role in ROLES
        assert (PROMPTS_DIR / f"{role.slug}.md").exists()
        assert not role.executes
        assert not role.denied

    for role in (UX_DESIGNER, UI_DESIGNER):
        assert role.kind == "actor"
        assert role.tools == READ_CODE and role.allowed == READ_CODE_RULES
        assert role.reads_knowledge

    for role in (UX_LEAD, DESIGN_LEAD, UX_LEAD_FINAL, DESIGN_LEAD_FINAL):
        assert role.kind == "verifier"
        assert role.tools == READ_CODE and role.allowed == READ_CODE_RULES

    for role in (PM_CONSOLIDATOR, PM_FINAL):
        assert role.kind == PM.kind == "verifier"


def test_the_final_roles_are_their_own_slugs_and_sections_not_edits():
    # `replace(...)` only: the base lead/PM tuples are untouched, each final variant is
    # its own `Role`, with its own section title and its own prompt file.
    assert UX_LEAD_FINAL.slug != UX_LEAD.slug and UX_LEAD_FINAL.name != UX_LEAD.name
    assert DESIGN_LEAD_FINAL.slug != DESIGN_LEAD.slug
    assert PM_FINAL.slug != PM.slug != PM_CONSOLIDATOR.slug
    assert "Tech Lead" in UX_LEAD_FINAL.reads and "Tech Lead" in DESIGN_LEAD_FINAL.reads
    assert "Tech Lead" in PM_FINAL.reads
    # The thinker/lead each returns-to reads its own section and the thinker's, for a
    # resumed round after a return.
    assert "UX Designer" in UX_LEAD.reads and "UX Lead" in UX_LEAD.reads
    assert "UI Designer" in DESIGN_LEAD.reads and "Design Lead" in DESIGN_LEAD.reads


def test_turn_limits_are_12_for_thinkers_and_leads_20_for_pm_modes():
    for role in (UX_DESIGNER, UX_LEAD, UI_DESIGNER, DESIGN_LEAD, UX_LEAD_FINAL, DESIGN_LEAD_FINAL):
        assert role.max_turns == 12
    for role in (PM_CONSOLIDATOR, PM_FINAL):
        assert role.max_turns == 20


def test_no_new_prompt_names_a_concrete_tool_framework_path_or_command():
    # The plan's own invariant: design data reaches roles as data in the turn message,
    # never a path or a command spelled out in the prompt itself.
    banned = ("claude design", ".html", "playwright", "selenium", "docs/product/design-system")
    for slug in (
        "ux_designer",
        "ux_lead",
        "ui_designer",
        "design_lead",
        "pm_consolidator",
        "ux_lead_final",
        "design_lead_final",
        "pm_final",
    ):
        text = (PROMPTS_DIR / f"{slug}.md").read_text().lower()
        for term in banned:
            assert term not in text, (slug, term)


@pytest.mark.parametrize(
    "schema",
    [schemas.DesignDraft, schemas.DesignReview, schemas.Consolidation, schemas.FinalValidation],
)
def test_json_schema_is_pinned_for_every_new_output(schema):
    out = schemas.json_schema(schema, {})
    assert out["properties"]["markdown"]["type"] == "string"


def test_design_review_rejects_a_kind_outside_observation_or_blocking():
    # The enforcement: `DesignItem.kind` is a closed `Literal`, not free text, so a
    # model-shaped payload with an invalid kind is a role failure, not silently kept.
    with pytest.raises(ValueError):
        schemas.validate(
            schemas.DesignReview,
            {
                "markdown": "x",
                "verdict": "approved",
                "reason": "ok",
                "items": [{"kind": "suggestion", "step": "s1", "text": "t"}],
            },
            {},
        )


def test_design_review_accepts_a_blocking_item():
    data = schemas.validate(
        schemas.DesignReview,
        {
            "markdown": "x",
            "verdict": "returned",
            "reason": "missing screen",
            "items": [{"kind": "blocking", "step": "checkout", "text": "no recovery"}],
        },
        {},
    )
    assert data["items"][0]["kind"] == "blocking"


def test_consolidation_line_lists_round_trip_as_text():
    # `questions_for_human` is a top-level `list[str]`: the model writes it as lines of
    # text (issue #8), and `validate` turns it back into a list.
    data = schemas.validate(
        schemas.Consolidation,
        {
            "markdown": "x",
            "verdict": "approved",
            "reason": "ok",
            "questions_for_human": "First question\nSecond question",
            "observations": [],
        },
        {},
    )
    assert data["questions_for_human"] == ["First question", "Second question"]


def test_plan_view_brief_projects_screens_only_when_non_empty():
    with_screens: Subtask = {
        "id": "front-sales",
        "title": "Front: sales",
        "brief": "Build the sales screen.",
        "seams": ["SalesPage"],
        "depends_on": [],
        "screens": ["Ventas", "NuevaVenta"],
        "status": "pending",
    }
    without_screens: Subtask = {
        "id": "domain",
        "title": "Domain model",
        "brief": "Build the domain.",
        "seams": ["Sale"],
        "depends_on": [],
        "status": "pending",
    }
    section = "### Subtasks\n\nold block\n\n### Next"
    rendered = plan_view.for_reader(section, "front-sales", {"front-sales": with_screens})
    assert "Screens: `Ventas`, `NuevaVenta`." in rendered

    rendered_plain = plan_view.for_reader(section, "domain", {"domain": without_screens})
    assert "Screens:" not in rendered_plain


def test_screens_notice_names_the_declared_command_when_there_is_one():
    notice = rules.screens_notice(["Ventas", "VentaDetalle"], "scripts/compare.sh")
    assert "`Ventas`" in notice and "`VentaDetalle`" in notice
    assert "scripts/compare.sh" in notice


def test_screens_notice_says_there_is_none_without_a_declared_command():
    notice = rules.screens_notice(["Ventas"], None)
    assert "no `design` block" in notice
    assert "`Ventas`" in notice
