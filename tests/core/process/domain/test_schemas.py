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


# The estimate check (ADR-0054): `plan_check_for(history, spent_usd)` is the plan check
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


# The two shapes of a plan (ADR-0054): (A) subtasks and a fitting estimate, (B) parts and an
# estimate that does not fit. `rules.estimate_fits` decides; the check only applies it.


def _split(cost, files, parts=None, **more):
    two = [
        {"title": "One", "body": "First slice.", "depends_on": []},
        {"title": "Two", "body": "Second slice.", "depends_on": [1]},
    ]
    data = _plan("### Solution", "a")
    return {
        **data,
        "estimated_cost_usd": cost,
        "estimated_files": files,
        "subtasks": [],
        "parts": two if parts is None else parts,
        **more,
    }


def _part(depends_on=(), title="T", body="B"):
    return {"title": title, "body": body, "depends_on": list(depends_on)}


def test_a_plan_that_does_not_fit_and_builds_whole_is_refused_with_the_split_reason():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="does not fit.*parts"):
        check(_estimated(151.0, 3))
    with pytest.raises(ValueError, match="does not fit.*parts"):
        check(_estimated(20.0, 41))
    check(_estimated(150.0, 40))  # the thresholds themselves fit


def test_the_bucket_p90_makes_a_declared_fit_not_fit():
    check = schemas.plan_check_for(_history(1, [200] * 5), 0.0)
    with pytest.raises(ValueError, match="does not fit"):
        check(_estimated(100.0, 3))


def test_a_waived_fit_lets_a_plan_that_does_not_fit_build_whole_and_refuses_parts():
    check = schemas.plan_check_for(_history(1, []), 0.0, fit_waived=True)
    check(_estimated(400.0, 90))
    with pytest.raises(ValueError, match="waived"):
        check(_split(400.0, 90))


def test_a_plan_with_parts_and_an_estimate_that_does_not_fit_is_accepted():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    check(_split(400.0, 90))
    check(_split(100.0, 41))


def test_the_split_shape_skips_the_half_median_floor():
    # 5 tasks of 400 USD: the bucket median is 400, half of it 200; a split estimate of 160
    # would be under the floor in the build shape.
    check = schemas.plan_check_for(_history(2, [400] * 5), 0.0)
    check(_split(160.0, 3))


def test_a_split_that_fits_is_refused_because_nothing_needs_splitting():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="fits"):
        check(_split(20.0, 3))


def test_a_split_is_judged_on_the_declared_estimate_when_the_bucket_is_too_thin():
    # No bucket with enough tasks: only the declared estimate speaks.
    check = schemas.plan_check_for(_history(2, [10] * 4), 0.0)
    with pytest.raises(ValueError, match="fits"):
        check(_split(100.0, 3))


def test_a_plan_with_both_subtasks_and_parts_is_refused():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="subtasks or parts, never both"):
        check({**_split(400.0, 90), "subtasks": _plan("x", "a")["subtasks"]})


def test_a_split_needs_at_least_two_parts():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="at least two parts"):
        check(_split(400.0, 90, [_part()]))


@pytest.mark.parametrize(
    ("parts", "reason"),
    [
        ([_part(), _part([3])], "unknown part 3"),
        ([_part(), _part([0])], "unknown part 0"),
        ([_part(), _part([2])], "earlier"),
        ([_part([2]), _part()], "earlier"),
        ([_part(), _part([1, 1])], "twice"),
        ([_part(title=" "), _part()], "title"),
        ([_part(), _part(body="")], "body"),
    ],
)
def test_every_violation_of_a_split_has_its_own_reason(parts, reason):
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match=reason):
        check(_split(400.0, 90, parts))


def test_a_split_still_answers_for_the_files_and_the_spend():
    with pytest.raises(ValueError, match="at least one file"):
        schemas.plan_check_for(_history(1, []), 0.0)(_split(400.0, 0))
    with pytest.raises(ValueError, match="already spent"):
        schemas.plan_check_for(_history(1, []), 500.0)(_split(400.0, 90))


def test_the_plan_schema_leaves_subtasks_and_parts_optional_for_the_model():
    plan = schemas.json_schema(schemas.TechPlan, VOCAB)
    assert "parts" in plan["properties"]
    assert "subtasks" not in plan.get("required", []) and "parts" not in plan.get("required", [])


# --- The third shape: work that only touches the tracker (GH-336) ----------------------

CHECKLIST = ["Post the verdict table on #307", "Close the follow-ups that are done"]


def _tracker_only(files=0, cost=2.0, checklist=None, **more):
    return {
        "markdown": "### Solution",
        "estimated_cost_usd": cost,
        "estimated_files": files,
        "subtasks": [],
        "parts": [],
        "tracker_checklist": CHECKLIST if checklist is None else checklist,
        **more,
    }


def test_a_plan_whose_only_work_is_on_the_tracker_is_accepted_without_the_estimate_checks():
    # A bucket with a high median and a reference that would refuse a tiny estimate: none of
    # the fit, the reference or the median is consulted for the zero-file shape.
    check = schemas.plan_check_for(_history(1, [400] * 5), 0.0)
    check(_tracker_only(cost=0.5))  # does not raise


def test_the_tracker_shape_refuses_a_positive_file_count():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="tracker.*estimated_files must be 0"):
        check(_tracker_only(files=2))


def test_the_tracker_shape_still_answers_for_what_the_task_already_spent():
    with pytest.raises(ValueError, match="already spent"):
        schemas.plan_check_for(_history(1, []), 5.0)(_tracker_only(cost=2.0))


def test_a_checklist_next_to_parts_is_refused():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="parts.*plans itself|each part plans itself"):
        check(_split(400.0, 90, tracker_checklist=CHECKLIST))


def test_a_plan_with_no_subtasks_no_parts_and_no_checklist_is_still_refused():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="at least one subtask"):
        check(_tracker_only(checklist=[]))
    with pytest.raises(ValueError, match="at least one subtask"):
        check({**_tracker_only(), "tracker_checklist": None})


def test_zero_files_stay_refused_for_a_plan_with_subtasks_even_with_a_checklist():
    check = schemas.plan_check_for(_history(1, []), 0.0)
    with pytest.raises(ValueError, match="at least one file"):
        check({**_estimated(20.0, 0), "tracker_checklist": CHECKLIST})
    check({**_estimated(20.0, 3), "tracker_checklist": CHECKLIST})  # does not raise


def test_a_checklist_item_must_be_one_line():
    for bad in ("", "two\nlines"):
        with pytest.raises(ValueError, match="one non-empty line"):
            schemas.TechPlan.model_validate(
                {
                    "markdown": "m",
                    "estimated_cost_usd": 1,
                    "estimated_files": 0,
                    "tracker_checklist": [bad],
                }
            )


def test_the_plan_schema_leaves_the_checklist_optional_for_the_model():
    plan = schemas.json_schema(schemas.TechPlan, VOCAB)
    assert "tracker_checklist" in plan["properties"]
    assert "tracker_checklist" not in plan.get("required", [])


# --- QA Performance convening (GH-318, ADR-0060) ---------------------------------------


def _scenario(**over) -> dict:
    return {
        "name": "list",
        "volume": 50000,
        "volume_unit": "rows",
        "basis": "the issue's largest account",
        **over,
        "budgets": over.get(
            "budgets",
            [
                {
                    "metric": "list_ms",
                    "limit": 300,
                    "unit": "ms",
                    "source": "issue",
                    "basis": "the issue says 300 ms",
                },
                {
                    "metric": "list_queries",
                    "limit": 3,
                    "unit": "queries",
                    "source": "proposed",
                    "basis": "one query per level plus the page",
                },
            ],
        ),
    }


def test_an_old_plan_without_performance_parses_with_it_empty():
    old = _plan("m", "api")
    assert "performance" not in old
    assert (
        schemas.validate(TechPlan, {**old, "estimated_cost_usd": 3, "estimated_files": 4}, {})[
            "performance"
        ]
        == []
    )


def test_an_old_task_document_parses_with_its_runs_unchanged():
    from pathlib import Path

    from agentic.core.process.domain import document

    raw = (Path(__file__).parents[3] / "fixtures" / "pre_performance_document.md").read_text()
    state = document.parse(raw)
    assert "performance" not in state
    assert state["runs"] and all(
        "perf_verdict" not in run and "perf_head" not in run for run in state["runs"]
    )
    assert document.parse(document.render(state))["runs"] == state["runs"]


def test_a_convened_plan_round_trips_through_validate():
    raw = {
        **_plan("m", "perf"),
        "estimated_cost_usd": 3,
        "estimated_files": 4,
        "performance": [_scenario()],
    }
    data = schemas.validate(TechPlan, raw, {})
    assert data["performance"] == raw["performance"]
    assert schemas.validate(TechPlan, {**raw, "performance": data["performance"]}, {}) == data


@pytest.mark.parametrize(
    "bad",
    [
        {"volume_unit": "weeks"},
        {
            "budgets": [
                {"metric": "m_ms", "limit": 1, "unit": "seconds", "source": "issue", "basis": "b"}
            ]
        },
        {
            "budgets": [
                {"metric": "m_ms", "limit": 1, "unit": "ms", "source": "guess", "basis": "b"}
            ]
        },
        {"budgets": []},
        {"volume": 0},
        {"volume": float("nan")},
        {"basis": "two\nlines"},
    ],
)
def test_a_scenario_with_a_bad_unit_source_volume_or_basis_is_refused(bad):
    raw = {**_plan("m", "perf"), "estimated_cost_usd": 3, "estimated_files": 4}
    with pytest.raises(ValueError):
        schemas.validate(TechPlan, {**raw, "performance": [_scenario(**bad)]}, {})


def test_a_plan_cannot_name_a_scenario_or_a_metric_twice():
    raw = {**_plan("m", "perf"), "estimated_cost_usd": 3, "estimated_files": 4}
    with pytest.raises(ValueError, match="twice"):
        schemas.validate(TechPlan, {**raw, "performance": [_scenario(), _scenario()]}, {})
    budget = _scenario()["budgets"][0]
    with pytest.raises(ValueError, match="twice"):
        schemas.validate(
            TechPlan, {**raw, "performance": [_scenario(budgets=[budget, budget])]}, {}
        )


def test_plan_limits_gives_the_volume_the_unit_and_the_limits_the_measurement_uses():
    from agentic.core.process.domain.performance import Limit

    scenario = schemas.PerfScenario.model_validate(_scenario())
    assert schemas.plan_limits(scenario) == (
        50000.0,
        "rows",
        {"list_ms": Limit(300.0, "ms"), "list_queries": Limit(3.0, "queries")},
    )


def _budget(**over) -> dict:
    return {
        "metric": "list_ms",
        "limit": 300,
        "unit": "ms",
        "source": "issue",
        "basis": "the issue says 300 ms",
        **over,
    }


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (_scenario(budgets=[_budget(unit="queries")]), "must end with"),
        (_scenario(budgets=[_budget(metric="list_queries")]), "must end with"),
        (_scenario(budgets=[_budget(limit=-1)]), "finite number, 0 or more"),
        (_scenario(budgets=[_budget(limit=float("inf"))]), "finite number, 0 or more"),
        (_scenario(budgets=[_budget(limit=float("nan"))]), "finite number, 0 or more"),
        (_scenario(budgets=[_budget(basis="one\ntwo")]), "one non-empty line"),
        (_scenario(budgets=[_budget(basis="  ")]), "one non-empty line"),
        (_scenario(name="List Page"), "slug"),
        (_scenario(volume=float("inf")), "positive finite"),
        (_scenario(volume=-5), "positive finite"),
    ],
)
def test_the_budget_and_scenario_checks_refuse_what_they_guard(scenario, message):
    raw = {**_plan("m", "perf"), "estimated_cost_usd": 3, "estimated_files": 4}
    with pytest.raises(ValueError, match=message):
        schemas.validate(TechPlan, {**raw, "performance": [scenario]}, {})


def test_a_convened_plan_reaches_the_document_and_a_plan_without_one_leaves_no_trace():
    from pathlib import Path

    from agentic.core.process.domain import document

    fixture = Path(__file__).parents[3] / "fixtures" / "pre_performance_document.md"
    state = document.parse(fixture.read_text())
    plan = schemas.validate(
        TechPlan,
        {
            **_plan("m", "perf"),
            "estimated_cost_usd": 3,
            "estimated_files": 4,
            "performance": [_scenario()],
        },
        {},
    )
    convened = document.parse(document.render({**state, "performance": plan["performance"]}))
    assert convened["performance"] == plan["performance"]
    assert "performance" not in document.render(state)
    assert "performance" not in document.render({**state, "performance": []})


# A task that is itself a part (GH-338, GH-346): never split again, so `parts` is refused
# first with the rule and no remedy that names parts, and an over-the-line subtask plan is
# left to the plan stage, which asks the human once.

PART_OF = ("GH-316", 2)
# Median 10, p90 200: a declared 20 USD is over the line by the reference alone.
HIGH_REFERENCE = [10, 10, 10, 10, 200, 200, 200]


def test_a_part_that_writes_parts_and_a_checklist_is_told_to_write_subtasks_only():
    # The GH-346 shape: the checklist refusal is never reached.
    check = schemas.plan_check_for(_history(1, []), 0.0, part_of=PART_OF)
    with pytest.raises(ValueError, match="part 2 of GH-316.*never split again.*subtasks only") as e:
        check(_split(400.0, 90, tracker_checklist=CHECKLIST))
    assert "write parts" not in str(e.value)
    with pytest.raises(ValueError, match="subtasks only"):
        check(_split(20.0, 3))  # parts that would fit too: refused all the same
    with pytest.raises(ValueError, match="subtasks only"):
        check({**_estimated(20.0, 3), "parts": _split(20.0, 3)["parts"]})  # both shapes


def test_a_part_with_subtasks_over_the_line_by_the_reference_alone_passes_the_check():
    # The GH-338 shape: 20 USD, 4 files, refused only by the history's reference.
    history = _history(1, HIGH_REFERENCE)
    plan = _estimated(20.0, 4)
    with pytest.raises(ValueError, match="does not fit.*write parts"):
        schemas.plan_check_for(history, 0.0)(plan)  # today's text, with part_of=None
    schemas.plan_check_for(history, 0.0, part_of=PART_OF)(plan)  # does not raise
    # A cost of 400 USD and 90 files pass on a part as well: the stage asks, the check does not.
    schemas.plan_check_for(history, 0.0, part_of=PART_OF)(_estimated(400.0, 90))


def test_a_part_keeps_every_other_refusal():
    check = schemas.plan_check_for(_history(1, HIGH_REFERENCE), 30.0, part_of=PART_OF)
    with pytest.raises(ValueError, match="already spent"):
        check(_estimated(20.0, 4))
    with pytest.raises(ValueError, match="median"):
        schemas.plan_check_for(_history(1, [400] * 5), 0.0, part_of=PART_OF)(_estimated(20.0, 4))
    with pytest.raises(ValueError, match="at least one file"):
        schemas.plan_check_for(_history(1, []), 0.0, part_of=PART_OF)(_estimated(20.0, 0))


def test_plan_fits_carries_the_figures_it_decided_on():
    history = _history(1, HIGH_REFERENCE)
    fit = schemas.plan_fits(_estimated(20.0, 4), history)
    assert fit == schemas.FitDecision(False, 20.0, 4, 200.0)
    own = schemas.plan_fits(_estimated(20.0, 4), _history(1, []))
    assert own == schemas.FitDecision(True, 20.0, 4, None)
    assert schemas.plan_fits(_estimated(20.0, 41), _history(1, [])).fits is False
    # The size is the number of parts when the plan has them.
    assert schemas.plan_fits(_split(400.0, 90), _history(2, [10] * 5)).reference_usd == 10.0
