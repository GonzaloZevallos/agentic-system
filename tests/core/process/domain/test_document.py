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
        "failures": [
            {
                "role": "PM",
                "attempt": 1,
                "error": "x",
                "cost_usd": 0.01,
                "reason": "schema",
                "slug": "pm",
                "section": "PM",
                "denials": 3,
            }
        ],
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
                "max_turns": 4,
                "tokens": {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40},
                "calls": 3,
                "tool_calls": {"Read": 2, "Bash": 1},
                "subagent_cost_usd": 0.05000001,
                "failed_cost_usd": 0.02000001,
                "slug": "product_analyst",
                "started_at": "2026-09-18T10:00:00.000Z",
                "ended_at": "2026-09-18T10:00:05.000Z",
                "queue_wait_s": 1.5,
                "denials": 2,
            },
        ],
        "escalated": "PjM stopped by the task cap: 50.00 of 50.00 USD spent",
        "escalation_class": "cap",
        "proposed_invariants": ["Every subscription query folds duplicates."],
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
    # Tokens, calls, tool calls and the sub-agent split survive the round trip too
    # (issue #24): the forward direction, not only that an old document still parses.
    assert data["runs"][1]["tokens"] == {
        "input": 10,
        "output": 20,
        "cache_read": 30,
        "cache_write": 40,
    }
    assert data["runs"][1]["calls"] == 3
    assert data["runs"][1]["tool_calls"] == {"Read": 2, "Bash": 1}
    assert data["runs"][1]["subagent_cost_usd"] == 0.05  # rounded like cost_by_model
    assert data["runs"][1]["failed_cost_usd"] == 0.02  # rounded like the other money fields
    assert data["runs"][1]["max_turns"] == 4
    # The spine's own new per-run keys survive the round trip too (issue #24): the
    # forward direction, not only that a document written before they existed parses.
    assert data["runs"][1]["slug"] == "product_analyst"
    assert data["runs"][1]["started_at"] == "2026-09-18T10:00:00.000Z"
    assert data["runs"][1]["ended_at"] == "2026-09-18T10:00:05.000Z"
    assert data["runs"][1]["queue_wait_s"] == 1.5
    assert data["runs"][1]["denials"] == 2
    assert data["failures"][0]["reason"] == "schema"
    assert data["failures"][0]["slug"] == "pm"
    assert data["failures"][0]["section"] == "PM"
    assert data["failures"][0]["denials"] == 3
    assert data["cost_usd"] == 0.1235
    assert data["prompts"] == {"pjm": "abcd1234"}
    assert data["description"] == "Users want CSV."
    assert data["sections"] == state["sections"]
    assert data["escalated"] == state["escalated"]
    assert data["escalation_class"] == "cap"
    assert data["proposed_invariants"] == ["Every subscription query folds duplicates."]


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


def test_a_document_written_before_proposed_invariants_existed_still_parses():
    """A document from before this task has no `proposed_invariants` line at all (issue
    #42): `plan.py`'s migration branch reads that absence, not an empty list, so parsing
    an old document must not manufacture the key."""
    old = "---\nid: T-1\ntitle: t\n---\n\n# t\n\n## Original request\n\nd\n"
    state = document.parse(old)
    assert "proposed_invariants" not in state


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
        "proposed_invariants": ["Every subscription query folds duplicates."],
    }
    back = document.parse(document.render(state))
    for field in (
        "invariants",
        "integration_brief",
        "findings",
        "settled_findings",
        "proposed_invariants",
    ):
        assert field in resume.STATE_FIELDS, field
        assert back[field] == state[field], field


def test_a_document_written_before_slug_and_timing_existed_still_parses():
    """`RoleRun.slug/.started_at/.ended_at/.queue_wait_s/.tokens/.calls/.tool_calls/
    .subagent_cost_usd/.max_turns/.denials/.failed_cost_usd` and
    `Failure.reason/.slug/.section/.denials` are new keys inside entries `document.py`
    already rendered and parsed whole: no rename, so a run or a failure recorded before
    this task carries on without them, exactly as written."""
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
    assert "tokens" not in run and "calls" not in run and "tool_calls" not in run
    assert "subagent_cost_usd" not in run and "failed_cost_usd" not in run
    assert "subagent_cost_usd" not in run
    assert "max_turns" not in run and "denials" not in run
    (failure,) = state["failures"]
    assert failure["error"] == "e" and "reason" not in failure and "slug" not in failure
    assert "denials" not in failure


def test_record_closure_replaces_an_existing_scalar_key_leaving_everything_else_untouched():
    raw = document.render(
        {
            "id": "T-1",
            "title": "t",
            "description": "d",
            "status": "ready_for_review",
            "sections": {"Developer": "Built it."},
        },
        "abc123",
    )
    closed = document.record_closure(raw, {"closed_at": "2026-09-21T10:00:00Z"})
    # Only one line was inserted, right before the closing `---`: the rest is
    # byte-identical, body included.
    before = raw.splitlines()
    after = closed.splitlines()
    closing = before.index("---", 1)
    assert after == [*before[:closing], "closed_at: '2026-09-21T10:00:00Z'", *before[closing:]]
    assert document.parse(closed) == {**document.parse(raw), "closed_at": "2026-09-21T10:00:00Z"}


def test_record_closure_replaces_the_key_in_place_when_it_already_exists():
    raw = document.render({"id": "T-1", "title": "t", "description": "d", "sections": {}})
    once = document.record_closure(raw, {"closed_at": "2026-09-21T10:00:00Z"})
    twice = document.record_closure(once, {"closed_at": "2026-09-21T11:00:00Z"})
    assert twice.count("closed_at:") == 1
    assert "2026-09-21T11:00:00Z" in twice
    assert document.parse(twice)["closed_at"] == "2026-09-21T11:00:00Z"


def test_record_closure_raises_when_there_is_no_frontmatter():
    with pytest.raises(ValueError, match="missing frontmatter"):
        document.record_closure("# no frontmatter\n", {"closed_at": "x"})


def test_record_closure_raises_when_the_existing_key_is_not_a_single_scalar_line():
    raw = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        "returns:\n"
        "  PM→Product Analyst: 1\n"
        "---\n"
        "\n# t\n\n## Original request\n\nd\n"
    )
    with pytest.raises(ValueError, match="returns"):
        document.record_closure(raw, {"returns": "x"})


def test_record_closure_quotes_values_that_yaml_would_otherwise_retype():
    raw = document.render({"id": "T-1", "title": "t", "description": "d", "sections": {}})
    closed = document.record_closure(raw, {"human_commits": "3", "delivery_outcome": "merged"})
    data = document.parse(closed)
    assert data["human_commits"] == "3"  # a digit string stays a string, not an int
    assert data["delivery_outcome"] == "merged"


def test_a_document_written_before_escalation_class_existed_still_parses():
    """`escalation_class` is a new key inside the frontmatter `document.py` already
    rendered and parsed: no rename, so a document paused before this task carries on
    without it, and `rules.returns_pair`'s text fallback (ADR-0003, addendum) is what
    a resume of it still reads."""
    old = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        "escalated: 'PM→Product Analyst hit 2 returns: gap'\n"
        "---\n"
        "\n# t\n\n## Original request\n\nd\n"
    )
    state = document.parse(old)
    assert state["escalated"] == "PM→Product Analyst hit 2 returns: gap"
    assert "escalation_class" not in state
    assert rules.returns_pair(state) == "PM→Product Analyst"
