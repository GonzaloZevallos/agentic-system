import pytest

from agentic.core.process.domain import schemas
from agentic.core.process.domain.schemas import (
    PRD,
    Answers,
    Classification,
    TechAnswers,
    TechPlan,
    Verdict,
)

VOCAB = {"touches_ui": "UI", "touches_copy": "copy"}


def _plan(markdown: str, *ids: str) -> dict:
    return {
        "subtasks": [
            {"id": i, "title": i, "brief": "b", "seams": ["x"], "depends_on": []} for i in ids
        ],
        "markdown": markdown,
    }


def test_json_schema_closes_flags_over_vocabulary():
    out = schemas.json_schema(PRD, VOCAB)
    flags = out["properties"]["flags"]
    assert set(flags["properties"]) == set(VOCAB)
    assert flags["required"] == list(VOCAB)
    assert flags["additionalProperties"] is False
    # Schemas without flags are untouched.
    assert "flags" not in schemas.json_schema(Classification, VOCAB)["properties"]


def test_prd_json_schema_is_pinned():
    # `json_schema` rewrites only `flags` and the line-list fields (schemas.py), so
    # Pydantic's own `title` -- the class name -- passes through unchanged into the
    # `output_format` the CLI enforces. `prompts.fingerprint` cannot see it, so a
    # rename of the class is the one thing that must be pinned by a test instead
    # (ADR-0026): this is that pin, title included.
    assert schemas.json_schema(PRD, VOCAB) == {
        "properties": {
            "markdown": {
                "description": "The role's Markdown section, free text, `###` headings",
                "title": "Markdown",
                "type": "string",
            },
            "flags": {
                "type": "object",
                "properties": {
                    "touches_ui": {"type": "boolean", "description": "UI"},
                    "touches_copy": {"type": "boolean", "description": "copy"},
                },
                "required": ["touches_ui", "touches_copy"],
                "additionalProperties": False,
            },
        },
        "required": ["markdown", "flags"],
        "title": "PRD",
        "type": "object",
    }


def test_validate_accepts_exact_vocabulary():
    data = schemas.validate(
        PRD, {"markdown": "x", "flags": {"touches_ui": True, "touches_copy": False}}, VOCAB
    )
    assert data["flags"] == {"touches_ui": True, "touches_copy": False}


def test_validate_rejects_invented_or_missing_flags():
    match = "unknown: \\['ui_change'\\], missing: \\['touches_copy'\\]"
    with pytest.raises(ValueError, match=match):
        schemas.validate(
            PRD, {"markdown": "x", "flags": {"touches_ui": True, "ui_change": True}}, VOCAB
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


def test_plan_check_accepts_a_plan_with_no_subtasks_heading():
    # issue #152: the markdown's own shape is no longer `plan_check`'s concern -- the
    # plan stage repairs it afterwards (`plan_view.with_subtasks_block`). This plan is
    # otherwise valid (one slug, one seam, no bad dependency), so nothing is left to
    # reject.
    data = _plan("### Solution\n\nHex.\n\n### Invariants\n\n1. x.", "a")
    schemas.plan_check(data)  # does not raise


def test_plan_check_accepts_a_heading_with_fewer_h4_than_subtasks():
    markdown = "### Solution\n\nHex.\n\n### Subtasks\n\n#### a\n\nbrief a"
    data = _plan(markdown, "a", "b")
    schemas.plan_check(data)  # does not raise


def test_plan_check_accepts_subtasks_colon_as_the_heading():
    markdown = "### Solution\n\nHex.\n\n### Subtasks:\n\n#### a\n\nbrief a"
    data = _plan(markdown, "a")
    schemas.plan_check(data)  # does not raise


def test_plan_check_accepts_the_heading_only_inside_a_fence():
    markdown = "### Solution\n\nHex.\n\n```\n### Subtasks\n\n#### a\n```"
    data = _plan(markdown, "a")
    schemas.plan_check(data)  # does not raise


def test_plan_check_accepts_extra_internal_h4_headings():
    markdown = (
        "### Solution\n\nHex.\n\n### Subtasks\n\n#### a\n\n#### Design\n\nnote\n\n#### b\n\nbrief"
    )
    data = _plan(markdown, "a", "b")
    schemas.plan_check(data)  # does not raise


# Every structural rule still rejects, with today's exact message, regardless of the
# markdown's own shape (issue #152: only the heading stopped being a `plan_check`
# motive) -- each built with NO `### Subtasks` heading, to prove the structural rule
# alone is what raises.
_NO_HEADING = "### Solution\n\nHex, no heading here."


def test_plan_check_rejects_an_empty_subtasks_list():
    data = _plan(_NO_HEADING)
    with pytest.raises(ValueError, match="a plan needs at least one subtask"):
        schemas.plan_check(data)


def test_plan_check_rejects_a_non_slug_id():
    data = _plan(_NO_HEADING, "Not A Slug")
    with pytest.raises(ValueError, match=r"must be a slug \(lower-case, hyphens\)"):
        schemas.plan_check(data)


def test_plan_check_rejects_integration_as_an_id():
    data = _plan(_NO_HEADING, "integration")
    with pytest.raises(ValueError, match="is reserved for the integration"):
        schemas.plan_check(data)


def test_plan_check_rejects_a_subtask_with_no_seams():
    data = _plan(_NO_HEADING, "a")
    data["subtasks"][0]["seams"] = []
    with pytest.raises(ValueError, match="declares no seams; tests need them"):
        schemas.plan_check(data)


def test_plan_check_rejects_an_unknown_dependency():
    data = _plan(_NO_HEADING, "a")
    data["subtasks"][0]["depends_on"] = ["ghost"]
    with pytest.raises(ValueError, match="depends on unknown subtask 'ghost'"):
        schemas.plan_check(data)


def test_plan_check_rejects_duplicate_ids():
    data = _plan(_NO_HEADING, "a", "a")
    with pytest.raises(ValueError, match="subtask ids must be unique"):
        schemas.plan_check(data)


def test_delivery_carries_a_coverage_map_that_reaches_the_model_as_lines():
    out = schemas.json_schema(schemas.Delivery, VOCAB)
    field = out["properties"]["coverage"]
    assert field["type"] == "string" and "items" not in field
    assert "lines_required" not in field
    # The one line-list field that is never "none" when there are invariants: its suffix
    # does not invite an empty string.
    assert field["description"].endswith(". One per line")
    assert "invariant N: <path>:<line>" in field["description"]
    assert "unsatisfiable, see Decisions" in field["description"]
    assert "not touched by this subtask" in field["description"]
    assert "brief: <item> -> <path>:<line>" in field["description"]
    assert "integration" in field["description"]


def test_every_other_line_list_keeps_its_suffix_byte_for_byte():
    out = schemas.json_schema(schemas.Delivery, VOCAB)
    assert out["properties"]["assumptions"] == {
        "type": "string",
        "description": "Decisions taken where the brief was silent. "
        "One per line; an empty string if none",
    }
    assert out["properties"]["summary"] == {
        "type": "string",
        "title": "Summary",
        "description": "What changed, in a few lines, for the reviewer",
    }
    review = schemas.json_schema(schemas.Review, VOCAB)["properties"]["settled"]["description"]
    assert review.endswith("One per line; an empty string if none")
    answers = schemas.json_schema(TechAnswers, VOCAB)["properties"]["questions_for_pm"]
    assert answers["description"].endswith(". One per line; an empty string if none")


def test_a_delivery_written_before_the_coverage_field_still_validates():
    data = schemas.validate(
        schemas.Delivery, {"markdown": "x", "summary": "s", "assumptions": []}, VOCAB
    )
    assert data["coverage"] == []


def test_a_coverage_string_validates_as_its_lines():
    raw = {
        "markdown": "x",
        "summary": "s",
        "assumptions": "",
        "coverage": "invariant 1: a.py:1\n\n brief: x -> b.py:2 \n",
    }
    assert schemas.validate(schemas.Delivery, raw, VOCAB)["coverage"] == [
        "invariant 1: a.py:1",
        "brief: x -> b.py:2",
    ]


def test_the_coverage_description_quotes_the_forms_from_the_one_module_byte_for_byte():
    from agentic.core.process.domain import coverage

    description = schemas.json_schema(schemas.Delivery, VOCAB)["properties"]["coverage"][
        "description"
    ]
    for form in (
        coverage.MAPPED_FORM,
        coverage.UNSATISFIABLE_FORM,
        coverage.NOT_TOUCHED_FORM,
        coverage.BRIEF_FORM,
    ):
        assert f"`{form}`" in description
    # The forms use the typographic ellipsis the prompts quote, never an ASCII stand-in.
    assert "..." not in description
    assert "…" in coverage.MAPPED_FORM and "…" in coverage.BRIEF_FORM


# The estimate check (ADR-0053): `plan_check_for(history, spent_usd)` is the plan check
# plus three refusals that read the cost history and what the task already spent.


def _estimated(cost, files, *subtask_ids):
    data = _plan("### Solution", *(subtask_ids or ("a",)))
    return {**data, "estimated_cost_usd": cost, "estimated_files": files}


def _history(n_subtasks, costs):
    from agentic.core.process.domain import cost_history

    return cost_history.build(
        [
            {"cost_usd": c, "subtasks": {f"s{i}": {} for i in range(n_subtasks)}, "closed_at": "x"}
            for c in costs
        ]
    )


def test_plan_check_for_accepts_a_plan_that_clears_the_floors():
    check = schemas.plan_check_for(_history(1, []), 5.0)
    check(_estimated(20.0, 3))  # does not raise


def test_plan_check_for_still_applies_what_plan_check_validates():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="at least one"):
        check({**_estimated(20.0, 3), "subtasks": []})


def test_plan_check_for_rejects_less_than_one_file():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="at least one file"):
        check(_estimated(20.0, 0))


def test_plan_check_for_rejects_an_estimate_below_what_was_already_spent():
    check = schemas.plan_check_for(_history(1, []), 30.0)
    with pytest.raises(ValueError, match="already spent 30.00"):
        check(_estimated(20.0, 3))


def test_plan_check_for_rejects_half_the_median_of_a_bucket_of_five():
    check = schemas.plan_check_for(_history(1, [40, 40, 40, 40, 40]), 0.0)
    with pytest.raises(ValueError, match="median"):
        check(_estimated(19.0, 3))
    check(_estimated(20.0, 3))  # exactly half: allowed


def test_plan_check_for_does_not_apply_the_floor_to_a_bucket_below_five():
    check = schemas.plan_check_for(_history(1, [40, 40, 40, 40]), 0.0)
    check(_estimated(1.0, 3))  # does not raise


def test_plan_check_for_looks_at_the_bucket_of_the_plans_own_subtask_count():
    check = schemas.plan_check_for(_history(2, [40] * 5), 0.0)
    check(_estimated(1.0, 3, "a"))  # a different bucket: no floor
    with pytest.raises(ValueError, match="median"):
        check(_estimated(1.0, 3, "a", "b"))
