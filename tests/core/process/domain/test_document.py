import pytest

from agentic.core.process.application import resume
from agentic.core.process.domain import document, rules


def test_render_then_parse_roundtrip():
    state = {
        "id": "T-1",
        "title": "Export report",
        "description": "Users want CSV.",
        "type": "feature",
        "status": "definition_ready",
        "current_role": "PM",
        "flags": {"touches_ui": False},
        "changes_requested": {"PM→Product Analyst": 1},
        "failures": [{"role": "PM", "attempt": 1, "error": "x", "cost_usd": 0.01}],
        "runs": [
            {
                "role": "PjM",
                "version": 1,
                "session_id": "s1",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            },
            {
                "role": "Product Analyst",
                "version": 1,
                "session_id": "s2",
                "model": "m",
                "cost_usd": 0.2,
                "cost_by_model": {"m": 0.15, "sub": 0.05000001},
                "turns": 2,
            },
        ],
        "escalated": None,
        "cost_usd": 0.12345,
        "prompts": {"pjm": "abcd1234"},
        "sections": {
            "PjM": "Clear feature.",
            # Sub-headings inside a section must not split it.
            "Product Analyst": "### Competitors\n\nNone.\n\n### For the next role\n\nValidate.",
        },
    }
    data = document.parse(document.render(state, "abc123"))
    assert data["id"] == "T-1"
    assert data["type"] == "feature"
    assert data["system_commit"] == "abc123"
    assert data["changes_requested"] == {"PM→Product Analyst": 1}
    assert data["failures"] == state["failures"]
    assert data["runs"][0] == state["runs"][0]
    assert data["runs"][1]["cost_by_model"] == {"m": 0.15, "sub": 0.05}  # rounded like costs
    assert data["cost_usd"] == 0.1235
    assert data["prompts"] == {"pjm": "abcd1234"}
    assert data["description"] == "Users want CSV."
    assert data["sections"] == state["sections"]


def test_interview_stats_never_reaches_the_document():
    """Measurement-only, like `subtask_id`: `interview()`'s own patch key
    (`InterviewStats`) never reaches the document, so a resumed task carries none of
    it and a panel reading `agentic.interview.*` is the only place it shows."""
    state = {
        "id": "T-1",
        "title": "Plan it",
        "description": "x.",
        "interview_stats": {
            "interview": "solution",
            "rounds": 2,
            "questions_to_pm": 1,
            "questions_to_human": 0,
            "invariants_proposed": 1,
        },
    }
    rendered = document.render(state)
    assert "interview_stats" not in rendered
    assert "questions_to_pm" not in rendered
    assert "interview_stats" not in document.parse(rendered)


def test_superseded_sections_are_written_before_the_current_one():
    state = {
        "id": "T-2",
        "title": "t",
        "description": "d",
        "sections": {
            "PjM": "classified",
            "Product Analyst": "v3 current",
            "PM": "approved",
            # Insertion order is the merge order; the writer must not rely on it.
            "Product Analyst (v2)": "second",
            "Product Analyst (v1)": "first",
            "PM (v1)": "returned",
        },
    }
    text = document.render(state)
    order = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    assert order == [
        "Original request",
        "PjM",
        "Product Analyst (v1)",
        "Product Analyst (v2)",
        "Product Analyst",
        "PM (v1)",
        "PM",
    ]
    assert document.parse(text)["sections"] == document.ordered(state["sections"])


def test_request_headings_cannot_open_sections():
    """An issue body brings its own `##`; in the document they go one level down."""
    state = {
        "id": "T-3",
        "title": "t",
        "description": "## Current behaviour\n\nNone.\n\n### Notes\n\n#not a heading",
        "sections": {"PjM": "ok"},
    }
    data = document.parse(document.render(state))
    assert data["description"] == "### Current behaviour\n\nNone.\n\n#### Notes\n\n#not a heading"
    assert list(data["sections"]) == ["PjM"]


def test_parse_rejects_missing_frontmatter():
    with pytest.raises(ValueError, match="bad.md: missing frontmatter"):
        document.parse("# no frontmatter\n", "bad.md")


def test_relative_path():
    assert str(document.relative_path("GH-12")) == ".agentic/tasks/GH-12.md"


def test_the_frontmatter_keeps_the_names_the_metrics_keep():
    """A written document is data (issue #53): its keys do not follow the domain's
    rename, or a task paused before it would resume with an empty count."""
    text = document.render(
        {
            "id": "T-1",
            "title": "t",
            "description": "d",
            "changes_requested": {"PM→Product Analyst": 1},
            "change_history": [
                {"pair": "PM→Product Analyst", "category": "definition", "reason": "r"}
            ],
            "subtasks": {"api": {"id": "api", "change_category": "spec-behaviour"}},
        }
    )
    assert "\nreturns:" in text and "\nreturn_history:" in text
    assert "return_category: spec-behaviour" in text
    assert "changes_requested" not in text and "change_category" not in text


def test_a_document_written_before_the_rename_still_parses_into_the_state():
    old = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        "returns: {'PM→Product Analyst': 2}\n"
        "return_history: [{pair: 'PM→Product Analyst', category: definition, reason: r}]\n"
        "subtasks: {api: {id: api, return_category: spec-behaviour}}\n"
        "---\n"
        "\n# t\n\n## Original request\n\nd\n"
    )
    state = document.parse(old)
    assert state["changes_requested"] == {"PM→Product Analyst": 2}
    assert state["change_history"][0]["category"] == "definition"
    assert state["subtasks"]["api"]["change_category"] == "spec-behaviour"
    assert "returns" not in state and "return_history" not in state


def test_the_channel_survives_the_document():
    """The document is the rescue path (`resume.from_document`): what is not in the
    frontmatter is lost when the checkpoint is, silently and with every test green."""
    findings = rules.finding_records(
        "api",
        2,
        [
            {
                "location": "x.py:1",
                "claim": "counts twice",
                "evidence": "$ pytest\nFAILED",
                "severity": "important",
                "scope": "plan",
                "category": "spec-behaviour",
            }
        ],
    )
    state = {
        "id": "GH-1",
        "title": "T",
        "description": "d",
        "invariants": ["Every subscription query folds duplicates."],
        "integration_brief": "Wire the two ends.",
        "findings": findings,
        "settled_findings": ["y.py:3"],
    }
    back = document.parse(document.render(state))
    for field in ("invariants", "integration_brief", "findings", "settled_findings"):
        assert field in resume.STATE_FIELDS, field
        assert back[field] == state[field], field


def test_a_document_written_before_slug_and_timing_existed_still_parses():
    """`RoleRun.slug/.started_at/.ended_at/.queue_wait_s` and `Failure.reason/.slug/
    .section` are new keys inside entries `document.py` already rendered and parsed
    whole: no rename, so a run or a failure recorded before this task carries on
    without them, exactly as written."""
    old = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        "runs:\n"
        "- {role: PjM, section: PjM, version: 1, session_id: '', model: m, cost_usd: 0.1,"
        " turns: 1}\n"
        "failures:\n"
        "- {role: PM, attempt: 1, error: e, cost_usd: 0.05, session_id: ''}\n"
        "---\n"
        "\n# t\n\n## Original request\n\nd\n"
    )
    state = document.parse(old)
    (run,) = state["runs"]
    assert run == {
        "role": "PjM",
        "section": "PjM",
        "version": 1,
        "session_id": "",
        "model": "m",
        "cost_usd": 0.1,
        "turns": 1,
    }
    assert "slug" not in run and "started_at" not in run and "queue_wait_s" not in run
    (failure,) = state["failures"]
    assert failure["error"] == "e" and "reason" not in failure and "slug" not in failure
