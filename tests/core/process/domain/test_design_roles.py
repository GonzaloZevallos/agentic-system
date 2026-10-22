"""The design roles, their schemas and prompts, the plan's `screens` field and the
Developer's screens notice (ADR-0040, issue #143, `design-roles`). No routing here:
`rules.design_step`, the nodes and the resume logic are another subtask's."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import plan_view, rules, schemas
from agentic.core.process.domain.roles import (
    PM,
    PM_CONSOLIDATOR,
    PM_FINAL,
    PROMPTS_DIR,
    READ_CODE,
    READ_CODE_RULES,
    ROLES,
    UI_DESIGNER,
    UI_LEAD,
    UI_LEAD_FINAL,
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
        UI_LEAD,
        PM_CONSOLIDATOR,
        UX_LEAD_FINAL,
        UI_LEAD_FINAL,
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
        assert "product" in role.criteria

    for role in (UX_LEAD, UI_LEAD, UX_LEAD_FINAL, UI_LEAD_FINAL):
        assert role.kind == "verifier"
        assert role.tools == READ_CODE and role.allowed == READ_CODE_RULES

    for role in (PM_CONSOLIDATOR, PM_FINAL):
        assert role.kind == PM.kind == "verifier"


def test_the_four_design_roles_read_the_design_standards_whole_and_the_leads_no_product():
    from agentic.core.process.domain.roles import whole_for

    for role in (UX_DESIGNER, UX_LEAD, UI_DESIGNER, UI_LEAD):
        assert "design" in role.criteria and whole_for(role, "design"), role.slug
    for role in (UX_LEAD, UI_LEAD):
        assert "product" not in role.criteria, role.slug
    # Rejected: no other family of roles reads the design standards.
    assert {r.slug for r in ROLES if "design" in r.criteria} == {
        "ux_designer",
        "ux_lead",
        "ui_designer",
        "ui_lead",
        "ux_lead_final",
        "ui_lead_final",
    }


def test_the_final_roles_are_their_own_slugs_and_sections_not_edits():
    # `replace(...)` only: the base lead/PM tuples are untouched, each final variant is
    # its own `Role`, with its own section title and its own prompt file.
    assert UX_LEAD_FINAL.slug != UX_LEAD.slug and UX_LEAD_FINAL.name != UX_LEAD.name
    assert UI_LEAD_FINAL.slug != UI_LEAD.slug
    assert PM_FINAL.slug != PM.slug != PM_CONSOLIDATOR.slug
    assert "Tech Lead" in UX_LEAD_FINAL.reads and "Tech Lead" in UI_LEAD_FINAL.reads
    assert "Tech Lead" in PM_FINAL.reads
    # The thinker/lead each returns-to reads its own section and the thinker's, for a
    # resumed round after a return.
    assert "UX Designer" in UX_LEAD.reads and "UX Lead" in UX_LEAD.reads
    assert "UI Designer" in UI_LEAD.reads and "UI Lead" in UI_LEAD.reads


def test_turn_limits_are_12_for_thinkers_and_leads_20_for_pm_modes():
    for role in (UX_DESIGNER, UX_LEAD, UI_DESIGNER, UI_LEAD, UX_LEAD_FINAL, UI_LEAD_FINAL):
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
        "ui_lead",
        "pm_consolidator",
        "ux_lead_final",
        "ui_lead_final",
        "pm_final",
    ):
        text = (PROMPTS_DIR / f"{slug}.md").read_text().lower()
        for term in banned:
            assert term not in text, (slug, term)


# Shared by every new output: `RoleOutput.markdown` itself, unchanged by any of them.
_MARKDOWN = {
    "description": "The role's Markdown section, free text, `###` headings",
    "title": "Markdown",
    "type": "string",
}

# `DesignItem` is nested under both `DesignReview.items` and `Consolidation.observations`
# (schemas.py): Pydantic lifts it into `$defs` once and refs it from both, so this exact
# block is what a dropped `verdict`, a renamed field or a widened `kind` enum would move.
_DESIGN_ITEM_DEF = {
    "description": (
        "One remark from a lead (UX Lead or UI Lead) or from PM's consolidation (ADR-0040),\n"
        "the unit `design_items` is made of. `blocking` means the job cannot be completed as\n"
        "the design stands; anything else is `observation`, friction that does not pause the\n"
        "task (glossary: Observación al diseño). Never written from section prose: only\n"
        "from this field, structured."
    ),
    "properties": {
        "kind": {"enum": ["observation", "blocking"], "title": "Kind", "type": "string"},
        "step": {
            "description": "The flow step or screen this remark is about",
            "title": "Step",
            "type": "string",
        },
        "text": {
            "description": "The remark itself, one or two sentences",
            "title": "Text",
            "type": "string",
        },
    },
    "required": ["kind", "step", "text"],
    "title": "DesignItem",
    "type": "object",
}


def test_design_draft_json_schema_is_pinned():
    # The thinker output (UX Designer, UI Designer): `markdown` alone, the usual fields
    # and nothing else -- a field added here would be structure the next role cannot see
    # (the content is the section's prose, read by name, not as data).
    assert schemas.json_schema(schemas.DesignDraft, {}) == {
        "description": (
            "UX Designer's flow or UI Designer's screens design (ADR-0040): the content is\n"
            "the `markdown` itself, nothing structured beyond the usual fields -- the next role\n"
            "reads it as a section, not as data."
        ),
        "properties": {"markdown": _MARKDOWN},
        "required": ["markdown"],
        "title": "DesignDraft",
        "type": "object",
    }


def test_design_review_json_schema_is_pinned():
    # The lead output (UX Lead, UI Lead, and their final-validation counterparts):
    # a `verdict` that gates the design step, a `reason` and the structured `items`.
    assert schemas.json_schema(schemas.DesignReview, {}) == {
        "$defs": {"DesignItem": _DESIGN_ITEM_DEF},
        "description": (
            "UX Lead's walk or UI Lead's comparison judgement (ADR-0040), and the same\n"
            "shape for their final-validation counterparts: a verdict the design step\n"
            "(`rules.design_step`) reads, with the remarks that back it. Leads never return to\n"
            "a Developer (README.md, 4): a `returned` verdict here sends the thinker back, never\n"
            "the code."
        ),
        "properties": {
            "markdown": _MARKDOWN,
            "verdict": {"enum": ["approved", "returned"], "title": "Verdict", "type": "string"},
            "reason": {
                "description": "One line. On `returned`, what the thinker must fix",
                "title": "Reason",
                "type": "string",
            },
            "items": {
                "description": (
                    "Every remark, blocking or observation; empty only when there is none"
                ),
                "items": {"$ref": "#/$defs/DesignItem"},
                "title": "Items",
                "type": "array",
            },
        },
        "required": ["markdown", "verdict", "reason", "items"],
        "title": "DesignReview",
        "type": "object",
    }


def test_consolidation_json_schema_is_pinned():
    # PM's consolidation: a verdict, the two routed piles (`questions_for_human` as a
    # line-list, `observations` as structured `DesignItem`s), on top of the usual fields.
    assert schemas.json_schema(schemas.Consolidation, {}) == {
        "$defs": {"DesignItem": _DESIGN_ITEM_DEF},
        "description": (
            "PM's consolidation of the validated flow and screens design into the PRD\n"
            "(ADR-0040): `markdown` is the consolidated PRD section. What UX marked as\n"
            "impeding the job becomes a question for the human; the rest of the friction\n"
            "becomes an observation."
        ),
        "properties": {
            "markdown": _MARKDOWN,
            "verdict": {"enum": ["approved", "returned"], "title": "Verdict", "type": "string"},
            "reason": {
                "description": "One line. On `returned`, what Product Analyst must fix",
                "title": "Reason",
                "type": "string",
            },
            "questions_for_human": {
                "type": "string",
                "description": (
                    "What UX or UI marked as impeding the job, written for a person who has not "
                    "read the task. Empty if none. One per line; an empty string if none"
                ),
            },
            "observations": {
                "description": (
                    "Friction that does not impede the job (kind is always `observation` here): "
                    "it travels to the pull request, never pauses the task. Empty if none"
                ),
                "items": {"$ref": "#/$defs/DesignItem"},
                "title": "Observations",
                "type": "array",
            },
        },
        "required": ["markdown", "verdict", "reason", "questions_for_human", "observations"],
        "title": "Consolidation",
        "type": "object",
    }


def test_final_validation_json_schema_is_pinned():
    # PM's early final validation: a verdict, a reason and `blocking` as the one-line
    # claims the integration review must settle -- a line-list like `questions_for_human`.
    assert schemas.json_schema(schemas.FinalValidation, {}) == {
        "description": (
            "PM's early, UI-only final validation (ADR-0040): the verdict over UX Lead's\n"
            "final walk and UI Lead's final comparisons, before Tech Lead's own close."
        ),
        "properties": {
            "markdown": _MARKDOWN,
            "verdict": {"enum": ["approved", "returned"], "title": "Verdict", "type": "string"},
            "reason": {
                "description": "One line. On `returned`, what Tech Lead must fix first",
                "title": "Reason",
                "type": "string",
            },
            "blocking": {
                "type": "string",
                "description": (
                    "On `returned`, one-line claims the integration review must settle, each "
                    "checkable on the diff alone. Empty on `approved`. One per line; an empty "
                    "string if none"
                ),
            },
        },
        "required": ["markdown", "verdict", "reason", "blocking"],
        "title": "FinalValidation",
        "type": "object",
    }


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
