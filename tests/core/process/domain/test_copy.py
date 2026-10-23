"""The Tech Writer's text findings: identifiers, records, owners, duplicates and the texts
in force (`domain.copy`), and how the findings table reads them (`rules`)."""

from agentic.core.process.domain import copy, rules
from agentic.core.process.domain.schemas import CopyFinding, CopyReview
from agentic.core.process.domain.task import INTEGRATION


def finding(location="a.py:3", claim="Too long", **over):
    base = {
        "location": location,
        "claim": claim,
        "evidence": "Criteria: 40 characters",
        "proposed": "Short",
        "severity": "important",
        "scope": "subtask",
        "category": "copy-criteria-written",
    }
    return CopyFinding(**{**base, **over})


def review(*findings, settled=(), standards=""):
    return CopyReview(
        markdown="m", findings=list(findings), settled=list(settled), proposed_standards=standards
    )


def apply(state, n, rev, owners=None, fallback="api"):
    records, settled = copy.copy_records(state, n, rev, owners or {}, fallback)
    return {
        **state,
        "findings": [*state.get("findings", []), *records],
        "settled_findings": [*state.get("settled_findings", []), *settled],
    }


def state_with(*subtasks):
    return {"subtasks": {name: {"status": "approved"} for name in subtasks}}


def test_a_copy_location_round_trips_and_a_plain_one_is_not_a_copy_location():
    assert copy.copy_location(2, 1, "a.py:3") == "copy:2.1:a.py:3"
    assert copy.copy_location_parts("copy:2.1:a.py:3") == (2, 1, "a.py:3")
    assert copy.copy_location_parts("a.py:3") is None
    assert copy.copy_location_parts("copy:x.y:a.py") is None
    assert copy.copy_location_parts(copy.NO_WRITING_LOCATION) == (0, 0, "WRITING_STANDARDS.md")


def test_a_finding_becomes_a_record_with_its_identifier_pass_and_owners():
    state = state_with("api", "ui")
    records, settled = copy.copy_records(
        state, 3, review(finding("ui.tsx:9")), {"ui.tsx": ["ui", "api"]}, "api"
    )
    assert settled == []
    assert records == [
        {
            "subtask": "ui, api",
            "version": 3,
            "location": "copy:3.1:ui.tsx:9",
            "claim": "Too long",
            "severity": "important",
            "scope": "subtask",
            "category": "copy-criteria-written",
            "status": "open",
            "proposed": "Short",
        }
    ]


def test_a_file_no_subtask_touched_falls_to_the_reviewed_unit():
    state = state_with("api", INTEGRATION)
    records, _ = copy.copy_records(state, 1, review(finding("x.py:1")), {}, INTEGRATION)
    assert records[0]["subtask"] == INTEGRATION


def test_two_findings_on_one_line_settle_one_at_a_time():
    state = state_with("api")
    state = apply(state, 1, review(finding("a.py:3", "one"), finding("a.py:3", "two")))
    state = apply(state, 2, review(settled=["copy:1.1:a.py:3"]))
    open_claims = [row["claim"] for row in rules.open_findings(state)]
    assert open_claims == ["two"]


def test_a_defect_settled_in_pass_one_and_found_again_in_pass_two_stays_open():
    state = state_with("api")
    state = apply(state, 1, review(finding()))
    state = apply(state, 2, review(settled=["copy:1.1:a.py:3"]))
    assert rules.open_findings(state) == []
    state = apply(state, 3, review(finding()))
    part = rules.important_partition(state)
    assert [row["location"] for row in part.owned["api"]] == ["copy:3.1:a.py:3"]


def test_the_same_open_defect_in_two_passes_is_one_row():
    state = state_with("api")
    state = apply(state, 1, review(finding("a.py:3", "Too  long")))
    records, _ = copy.copy_records(state, 2, review(finding("a.py:3", "too long")), {}, "api")
    assert records == []
    assert len(rules.open_findings(state)) == 1


def test_a_settled_entry_that_is_not_an_open_copy_row_closes_nothing():
    state = state_with("api")
    state["findings"] = [
        {
            "subtask": "api",
            "version": 1,
            "location": "a.py:3",
            "claim": "tech lead row",
            "severity": "important",
            "scope": "subtask",
            "category": "standard-other",
            "status": "open",
        }
    ]
    _, settled = copy.copy_records(state, 2, review(settled=["a.py:3", "copy:9.9:a.py:3"]), {}, "a")
    assert settled == []
    state = apply(state, 2, review(settled=["a.py:3"]))
    assert len(rules.open_findings(state)) == 1


def test_a_tech_lead_approval_at_a_higher_version_does_not_cover_a_copy_row():
    state = state_with("api")
    state = apply(state, 1, review(finding()))
    state["approval_settled"] = [
        {"subtask": "api", "location": "copy:1.1:a.py:3", "claim": "Too long", "version": 5}
    ]
    assert len(rules.open_findings(state)) == 1


def test_owners_are_only_the_subtasks_whose_diff_touched_the_file():
    state = state_with("api", "ui", INTEGRATION)
    owners = {"a.py": ["api"], "both.py": ["api", "ui"]}
    state = apply(state, 1, review(finding("a.py:1", "x"), finding("both.py:2", "y")), owners)
    owned = {n: [r["claim"] for r in rows] for n, rows in rules.important_partition(state).owned.items()}
    assert owned == {"api": ["x", "y"], "ui": ["y"]}
    assert INTEGRATION not in owned


def test_missing_standards_make_exactly_one_outside_row_after_two_passes():
    state = state_with("api")
    state = apply(state, 1, review(standards="# Writing\n- short"))
    state = apply(state, 2, review(standards="# Writing\n- short"))
    part = rules.important_partition(state)
    assert [(r["location"], r["claim"]) for r in part.outside] == [
        (copy.NO_WRITING_LOCATION, copy.NO_WRITING_CLAIM)
    ]
    assert part.outside[0]["proposed"] == "# Writing\n- short"
    assert part.outside[0]["scope"] == "outside"


def test_no_standards_row_is_added_when_the_caller_says_the_file_is_missing():
    records, _ = copy.copy_records({}, 1, review(), {}, "api", standards_missing=True)
    assert [r["location"] for r in records] == [copy.NO_WRITING_LOCATION]


def test_a_review_that_settles_and_raises_the_same_row_keeps_the_new_one():
    state = state_with("api")
    state = apply(state, 1, review(finding()))
    records, settled = copy.copy_records(
        state, 2, review(finding(), settled=["copy:1.1:a.py:3"]), {}, "api"
    )
    assert settled == ["copy:1.1:a.py:3"] and len(records) == 1


def test_an_observation_without_evidence_is_dismissed_on_the_spot():
    records, _ = copy.copy_records(
        {}, 1, review(finding(severity="observation", evidence=" ")), {}, "api"
    )
    assert records[0]["status"] == "dismissed"


def test_the_texts_in_force_are_the_proposed_of_settled_rows_latest_per_location():
    state = state_with("api")
    state = apply(state, 1, review(finding(proposed="First")))
    state = apply(state, 2, review(settled=["copy:1.1:a.py:3"]))
    state = apply(state, 3, review(finding(claim="again", proposed="Second")))
    state = apply(state, 4, review(settled=["copy:3.1:a.py:3"]))
    state = apply(state, 5, review(finding("b.py:1", "open one", proposed="Open")))
    assert copy.current_texts(state) == [("a.py:3", "Second")]


def test_the_findings_table_shows_a_copy_row_by_its_real_location_and_others_unchanged():
    state = state_with("api")
    state = apply(state, 1, review(finding("a.py:3")))
    table = rules.findings_markdown(rules.open_findings(state))
    assert "| `a.py:3` |" in table and "copy:" not in table
    other = {
        "subtask": "api",
        "version": 1,
        "location": "x.py:1",
        "claim": "c",
        "severity": "important",
        "scope": "subtask",
        "category": "standard-other",
        "status": "open",
    }
    assert rules.findings_markdown([other]) == (
        "| Severity | Scope | Category | Subtask | Location | Claim |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| important | subtask | standard-other | api | `x.py:1` | c |"
    )


# --- routing -------------------------------------------------------------------------------


def run(role, section=None):
    return {"role": role, **({"section": section} if section else {})}


def feature(*flags, runs=()):
    return {"type": "feature", "flags": {flag: True for flag in flags}, "runs": list(runs)}


def test_copy_applies_only_to_a_feature_that_touches_copy():
    assert rules.copy_applies(feature("touches_copy"))
    assert not rules.copy_applies(feature("touches_ui"))
    assert not rules.copy_applies({"type": "feature"})
    for kind in ("bug", "platform_enhancement"):
        assert not rules.copy_applies({"type": kind, "flags": {"touches_copy": True}})


def test_design_step_without_touches_copy_is_what_it_was():
    assert rules.design_step(feature()) is None
    assert rules.design_step(feature("touches_ui")) == "ux_flow"
    for kind in ("bug", "platform_enhancement"):
        assert rules.design_step({"type": kind, "flags": {"touches_ui": True}}) is None
        assert rules.design_step({"type": kind, "flags": {"touches_copy": True}}) is None
    walked = [
        run("Product Analyst"),
        run("UX Designer"),
        run("UX Lead"),
        run("UI Designer"),
        run("UI Lead"),
    ]
    assert rules.design_step(feature("touches_ui", runs=walked)) == "consolidate"
    done = [*walked, run("PM: consolidation")]
    assert rules.design_step(feature("touches_ui", runs=done)) is None


def test_a_copy_only_feature_writes_copy_then_falls_through_to_plan():
    runs = [run("Product Analyst"), run("PM")]
    state = feature("touches_copy", runs=runs)
    assert rules.design_step(state) == "write_copy"
    state["runs"] = [*runs, run("Tech Writer")]
    assert rules.design_step(state) is None
    assert rules.after_validate({**state, "status": "definition_ready"}) == "plan"


def test_a_newer_prd_makes_the_copy_due_again():
    state = feature("touches_copy", runs=[run("Product Analyst"), run("Tech Writer")])
    assert rules.design_step(state) is None
    state["runs"] = [*state["runs"], run("Product Analyst")]
    assert rules.design_step(state) == "write_copy"


def test_copy_comes_after_the_design_branch_and_its_consolidation():
    runs = [run("Product Analyst"), run("UX Designer"), run("UX Lead")]
    runs += [run("UI Designer"), run("UI Lead")]
    state = feature("touches_ui", "touches_copy", runs=runs)
    assert rules.design_step(state) == "consolidate"
    state["runs"] = [*runs, run("PM: consolidation")]
    assert rules.design_step(state) == "write_copy"
    state["runs"] = [*state["runs"], run("Tech Writer")]
    assert rules.design_step(state) is None
    # A consolidation after the copy makes the copy stale.
    state["runs"] = [*state["runs"], run("PM: consolidation")]
    assert rules.design_step(state) == "write_copy"


def test_resume_at_definition_ready_with_only_touches_copy_lands_on_write_copy():
    state = {**feature("touches_copy", runs=[run("Product Analyst")]), "status": "definition_ready"}
    assert rules.resume_target(state) == "write_copy"
    assert rules.resume_target({**feature(), "status": "definition_ready"}) == "plan"


def approved_state(*flags, review_runs=(), unit="api"):
    return {
        **feature(*flags, runs=review_runs),
        "subtasks": {unit: {"status": "approved"}},
    }


def test_copy_review_is_due_after_the_last_approval_and_before_final_validation():
    review = run("Tech Lead", "Tech Lead: review api")
    state = approved_state("touches_copy", review_runs=[review])
    assert rules.copy_review_due(state)
    assert rules.after_integrate(state) == "copy_review"
    state["runs"] = [*state["runs"], run("Tech Writer: review")]
    assert not rules.copy_review_due(state)
    assert rules.after_integrate(state) == "deliver"
    # A fix and a new approval make it due again.
    state["runs"] = [*state["runs"], review]
    assert rules.copy_review_due(state)


def test_copy_review_goes_before_the_final_validation_of_a_task_with_both_flags():
    review = run("Tech Lead", "Tech Lead: review api")
    state = approved_state("touches_copy", "touches_ui", review_runs=[review])
    assert rules.after_integrate(state) == "copy_review"
    state["runs"] = [*state["runs"], run("Tech Writer: review")]
    assert rules.after_integrate(state) == "final_validation"


def test_copy_review_is_not_due_without_the_flag_or_before_every_subtask_is_approved():
    review = run("Tech Lead", "Tech Lead: review api")
    plain = approved_state(review_runs=[review])
    assert not rules.copy_review_due(plain) and rules.after_integrate(plain) == "deliver"
    pending = approved_state("touches_copy", review_runs=[review])
    pending["subtasks"]["ui"] = {"status": "pending"}
    assert not rules.copy_review_due(pending)


def test_after_copy_review_returns_to_the_fan_in_unless_escalated():
    assert rules.after_copy_review({}) == "integrate"
    assert rules.after_copy_review({"escalated": "x"}) is None
