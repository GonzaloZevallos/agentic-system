import pytest
import yaml

from agentic.core.process.application import resume
from agentic.core.process.domain import document, legacy_names, roles, rules


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
                "main_thread_turns": 45,
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
    # `main_thread_turns` (ADR-0031, issue #117) survives the round trip like every
    # other per-run key added since #24: the forward direction, not only that an old
    # document without it still parses (`test_a_document_written_before_slug_and_
    # timing_existed_still_parses`, extended below).
    assert data["runs"][1]["main_thread_turns"] == 45
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


def test_render_omits_experiment_when_the_snapshot_is_empty():
    # Omit-at-default (Tech Lead plan): a document with the switches at their default
    # is byte-identical to one written before this task.
    with_key = document.render({"id": "T-1", "title": "t", "description": "d", "experiment": {}})
    without_key = document.render({"id": "T-1", "title": "t", "description": "d"})
    assert "experiment" not in with_key
    assert with_key == without_key


def test_render_writes_the_experiment_snapshot_when_non_empty():
    snapshot = {
        "review_mode": "light",
        "skip_ephemeral": "False",
        "reviewer_model": "claude-sonnet-5",
    }
    state = {"id": "T-1", "title": "t", "description": "d", "experiment": snapshot}
    rendered = document.render(state)
    assert "\nexperiment:" in rendered
    assert document.parse(rendered)["experiment"] == snapshot


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


def test_render_writes_system_version_only_when_given_one():
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    without = document.render(state)
    assert "system_version" not in without
    assert "system_version" not in document.parse(without)

    with_one = document.render(state, system_version="abc123+dirty")
    assert "system_version: abc123+dirty" in with_one
    assert document.parse(with_one)["system_version"] == "abc123+dirty"


def test_render_writes_no_system_version_for_an_empty_string_either():
    """The invariant is stated against every writer, not only the producer that never
    returns one (`telemetry.system_version`'s own fallback, `unknown`): an empty string
    is treated the same as no value at all."""
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    rendered = document.render(state, system_version="")
    assert "system_version" not in rendered


def test_render_writes_system_baseline_only_when_given_one():
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    without = document.render(state)
    assert "system_baseline" not in without
    assert "system_baseline" not in document.parse(without)

    with_one = document.render(state, system_baseline="bl-2026-09-23")
    assert "system_baseline: bl-2026-09-23" in with_one
    assert document.parse(with_one)["system_baseline"] == "bl-2026-09-23"


def test_render_writes_no_system_baseline_for_an_empty_string_either():
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    rendered = document.render(state, system_baseline="")
    assert "system_baseline" not in rendered


def test_system_version_and_system_baseline_round_trip_without_swapping():
    """The two keys, at recognisably different shapes (a sha-shaped version, a
    `bl-`-shaped baseline), round-trip to the same key each -- a swapped `render`
    argument would write one under the other's key and this would catch it."""
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    rendered = document.render(
        state, system_version="abc1234+dirty", system_baseline="bl-2026-09-23"
    )
    parsed = document.parse(rendered)
    assert parsed["system_version"] == "abc1234+dirty"
    assert parsed["system_baseline"] == "bl-2026-09-23"


def test_a_document_written_before_system_baseline_existed_parses_with_neither_key():
    """A document from before this task has `system_version` but no `system_baseline`
    at all -- not an empty string, absent -- and still parses (the resume seam is its
    own test in `test_resume.py`)."""
    state = {"id": "T-1", "title": "t", "description": "d", "sections": {}}
    rendered = document.render(state, system_version="abc1234")
    assert "system_baseline" not in rendered
    parsed = document.parse(rendered)
    assert parsed.get("system_baseline") is None
    assert parsed["system_version"] == "abc1234"


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
    .subagent_cost_usd/.max_turns/.denials/.failed_cost_usd/.main_thread_turns` and
    `Failure.reason/.slug/.section/.denials/.main_thread_turns` are new keys inside
    entries `document.py` already rendered and parsed whole: no rename, so a run or a
    failure recorded before this task (or before ADR-0031, issue #117) carries on
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
    assert "tokens" not in run and "calls" not in run and "tool_calls" not in run
    assert "subagent_cost_usd" not in run and "failed_cost_usd" not in run
    assert "subagent_cost_usd" not in run
    assert "max_turns" not in run and "denials" not in run
    assert "main_thread_turns" not in run
    (failure,) = state["failures"]
    assert failure["error"] == "e" and "reason" not in failure and "slug" not in failure
    assert "denials" not in failure
    assert "main_thread_turns" not in failure


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


def test_record_closure_raises_when_the_existing_key_is_an_indentless_block_sequence():
    """`runs` and `failures` render as a block sequence with no indent (`yaml.safe_dump`'s
    own default, pinned by `test_a_document_written_before_slug_and_timing_existed_
    still_parses`): a `- ` item at column 0 must be caught as a continuation too, or
    the first line would be overwritten and the rest left orphaned."""
    raw = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        "runs:\n"
        "- {role: PjM, section: PjM, version: 1, session_id: '', model: m, cost_usd: 0.1,"
        " turns: 1}\n"
        "---\n"
        "\n# t\n\n## Original request\n\nd\n"
    )
    with pytest.raises(ValueError, match="runs"):
        document.record_closure(raw, {"runs": "x"})


def test_a_document_written_after_this_task_seeds_the_same_state_as_one_written_before_it():
    """The closure's four keys and `system_version` are new frontmatter, never new
    `TaskState` fields, so `resume.from_document`'s allow-list filter (`STATE_FIELDS`)
    drops every one of them: a document carrying them seeds the identical state as one
    that does not, inert by construction rather than by a filter this task edits."""
    state = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "status": "ready_for_review",
        "sections": {"Developer": "Built it."},
    }
    before = document.render(state)
    closed = document.record_closure(
        document.render(state, system_version="abc123+dirty"),
        {
            "closed_at": "2026-09-21T10:00:00Z",
            "delivery_outcome": "merged",
            "human_commits": "0",
            "human_lines_changed": "0",
        },
    )
    new_keys = (
        "system_version",
        "closed_at",
        "delivery_outcome",
        "human_commits",
        "human_lines_changed",
    )
    for key in new_keys:
        assert key not in resume.STATE_FIELDS
        assert key in document.parse(closed)  # present in the raw document...

    def seeded(raw: str) -> dict:
        return {k: v for k, v in document.parse(raw).items() if k in resume.STATE_FIELDS}

    assert seeded(before) == seeded(closed)  # ...but filtered out of what resume seeds


def test_record_closure_quotes_values_that_yaml_would_otherwise_retype():
    raw = document.render({"id": "T-1", "title": "t", "description": "d", "sections": {}})
    closed = document.record_closure(raw, {"human_commits": "3", "delivery_outcome": "merged"})
    data = document.parse(closed)
    assert data["human_commits"] == "3"  # a digit string stays a string, not an int
    assert data["delivery_outcome"] == "merged"


def test_a_document_with_old_role_names_parses_under_the_new_ones():
    """2026-10-05 rename: a document written before it names the old role everywhere
    the alias (`legacy_names`) translates; `parse` (via `from_frontmatter`) hands back
    the same sections, pairs and return counts under the new one instead, so a resume
    off this document reads them unchanged (ADR-0040's own role, renamed). The old name
    itself comes from `legacy_names`'s own constants, never typed again here (the
    invariant the whole rename keeps: the literal name lives in one module alone)."""
    old = (
        "---\n"
        "id: T-1\n"
        "title: t\n"
        f"current_role: {legacy_names.OLD_NAME}\n"
        f"returns: {{'{legacy_names.OLD_NAME}→UI Designer': 1}}\n"
        "return_history: "
        f"[{{pair: '{legacy_names.OLD_NAME}→UI Designer', category: definition, reason: r}}]\n"
        f"design_items: [{{role_slug: {legacy_names.OLD_SLUG}, kind: blocking, step: Pay, "
        "text: no button}]\n"
        "---\n"
        f"\n# t\n\n## Original request\n\nd\n\n## {legacy_names.OLD_NAME}\n\napproved\n"
    )
    state = document.parse(old, "T-1")
    assert state["current_role"] == legacy_names.NEW_NAME
    assert state["changes_requested"] == {f"{legacy_names.NEW_NAME}→UI Designer": 1}
    assert state["change_history"][0]["pair"] == f"{legacy_names.NEW_NAME}→UI Designer"
    assert state["design_items"][0]["role_slug"] == legacy_names.NEW_SLUG
    assert set(state["sections"]) == {legacy_names.NEW_NAME}
    assert state["sections"][legacy_names.NEW_NAME] == "approved"


def test_render_never_writes_an_old_role_name():
    """`render` only ever writes new names (Tech Lead plan): a state already carrying
    the new ones (what the domain produces from here on) round-trips with no old name
    reappearing, and `RECORDED_AS` stays the frontmatter-key rename alone."""
    state = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "current_role": legacy_names.NEW_NAME,
        "sections": {legacy_names.NEW_NAME: "approved"},
    }
    rendered = document.render(state)
    assert legacy_names.OLD_NAME not in rendered
    assert legacy_names.NEW_NAME in rendered


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


def test_render_writes_the_playbook_keys_only_when_non_empty():
    base = {"id": "T-1", "title": "t", "description": "d"}
    bare = document.render(base)
    empty = document.render({**base, "playbooks": {}, "playbooks_changed": {}})
    assert "playbooks" not in bare
    assert empty == bare


def test_the_playbook_keys_round_trip():
    state = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "playbooks": {"developer": "abc123"},
        "playbooks_changed": {"developer": "def456"},
    }
    parsed = document.parse(document.render(state))
    assert parsed["playbooks"] == {"developer": "abc123"}
    assert parsed["playbooks_changed"] == {"developer": "def456"}


def test_an_old_document_without_the_playbook_keys_reads_as_empty():
    parsed = document.parse(document.render({"id": "T-1", "title": "t", "description": "d"}))
    assert parsed.get("playbooks", {}) == {}
    assert parsed.get("playbooks_changed", {}) == {}


def test_render_writes_decided_only_when_it_is_true():
    base = {"id": "T-1", "title": "t", "description": "d"}
    bare = document.render(base)
    assert "decided" not in bare
    assert document.render({**base, "decided": False}) == bare
    assert "\ndecided: true\n" in document.render({**base, "decided": True})


def test_decided_round_trips_and_gives_a_one_round_cap():
    state = {"id": "T-1", "title": "t", "description": "## Decided\n- a\n", "decided": True}
    rendered = document.render(state)
    parsed = document.parse(rendered)
    assert parsed["decided"] is True
    assert rules.interview_cap(parsed, roles.INTERVIEW_SOLUTION, 4) == 1
    # What a document records of the request is the demoted heading, readable at level 3.
    assert rules.decided(parsed["description"], level=3)


def test_a_document_without_the_key_parses_without_it_and_keeps_the_configured_cap():
    # The shape of a task that entered before this change, or ran with the cap off:
    # the heading is there, the key is not.
    state = {"id": "T-1", "title": "t", "description": "## Decided\n- a\n"}
    parsed = document.parse(document.render(state))
    assert "decided" not in parsed
    assert rules.decided(parsed["description"], level=3)
    assert rules.interview_cap(parsed, roles.INTERVIEW_SOLUTION, 4) == 4


def test_an_approval_settlement_survives_the_document_and_keeps_the_row_fixed():
    """GH-201: `approval_settled` is a `TaskState` field, so it is restored through
    `STATE_FIELDS`, and the fold over what was read back still closes the row."""
    records = rules.finding_records(
        "api",
        1,
        [
            {
                "location": "x.py:1",
                "claim": "double count",
                "evidence": "$ pytest\nF",
                "severity": "blocking",
                "scope": "subtask",
                "category": "spec-behaviour",
            }
        ],
    )
    state = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "findings": records,
        "approval_settled": [
            {"subtask": "api", "location": "x.py:1", "claim": "double count", "version": 2}
        ],
    }
    assert "approval_settled" in resume.STATE_FIELDS
    back = {
        k: v for k, v in document.parse(document.render(state)).items() if k in resume.STATE_FIELDS
    }
    assert back["approval_settled"] == state["approval_settled"]
    assert [row["status"] for row in rules.findings_rows(back)] == ["fixed"]
    assert rules.open_findings(back) == []


def test_a_state_without_a_settlement_renders_no_key_for_it():
    """Written only when non-empty: a document without it is byte-identical to one written
    before GH-201, whether the key is absent or empty."""
    state = {"id": "T-1", "title": "t", "description": "d"}
    plain = document.render(state)
    assert "approval_settled" not in plain
    assert document.render({**state, "approval_settled": []}) == plain
    assert "approval_settled" not in document.parse(plain)


def test_replaced_remote_tip_round_trips_and_is_absent_when_unset():
    """GH-204: the sha a replace overwrote is a `TaskState` field, so a resume restores
    it through `STATE_FIELDS`; a document without one is byte-identical to today's."""
    base = {"id": "T-1", "title": "t", "description": "d"}
    bare = document.render(base)
    assert "replaced_remote_tip" not in bare
    assert document.render({**base, "replaced_remote_tip": None}) == bare
    parsed = document.parse(document.render({**base, "replaced_remote_tip": "abc123"}))
    assert parsed["replaced_remote_tip"] == "abc123"
    assert "replaced_remote_tip" in resume.STATE_FIELDS
    assert "replaced_remote_tip" not in document.parse(bare)


_LANDED = [{"head": "a" * 40, "step": "fix", "refusal": "open_findings_blocking", "at": "t"}]


def _bare(**more):
    return {"id": "T-1", "title": "t", "description": "d", "sections": {}, **more}


def test_a_document_without_the_landing_keys_renders_byte_identical():
    assert document.render(_bare()) == document.render(
        _bare(landing=[], auto_merge_guarded=[], landing_restarts=0, landing_cost_carried=0.0)
    )
    assert "landing" not in document.render(_bare())


def test_the_landing_keys_render_and_parse_back_under_their_own_names():
    text = document.render(
        _bare(
            landing=_LANDED,
            auto_merge_guarded=[".agentic/stack.yaml"],
            landing_restarts=1,
            landing_cost_carried=2.5,
        )
    )
    state = document.parse(text)
    assert state["landing"] == _LANDED
    assert state["auto_merge_guarded"] == [".agentic/stack.yaml"]
    assert (state["landing_restarts"], state["landing_cost_carried"]) == (1, 2.5)


def test_record_closure_writes_a_list_on_one_line_and_replaces_it_in_place():
    raw = document.render(_bare())
    once = document.record_closure(raw, {"landing": _LANDED})
    twice = document.record_closure(once, {"landing": [*_LANDED, {**_LANDED[0], "step": "rerun"}]})
    assert document.parse(once)["landing"] == _LANDED
    assert [e["step"] for e in document.parse(twice)["landing"]] == ["fix", "rerun"]
    assert twice.count("\nlanding:") == 1


def test_record_closure_still_refuses_to_overwrite_a_block_key_with_a_list():
    raw = document.render(_bare(changes_requested={"PM→Product Analyst": 1}))
    with pytest.raises(ValueError, match="returns"):
        document.record_closure(raw, {"returns": ["x"]})


def _lines_but(text, key):
    return [ln for ln in text.split("\n") if not ln.startswith(f"{key}:")]


def test_render_writes_landing_as_one_flow_line_that_record_closure_replaces():
    """GH-242: `render` and `record_closure` write `landing` in one shape, so the one
    writes what the other can replace."""
    longer = [*_LANDED, {**_LANDED[0], "step": "rerun", "cost_base": 1.5}]
    raw = document.render(_bare(landing=_LANDED))
    assert [ln for ln in raw.split("\n") if ln.startswith("landing:")] == [
        document._scalar_line("landing", _LANDED)
    ]
    after = document.record_closure(raw, {"landing": longer})
    assert document.parse(after)["landing"] == longer
    assert _lines_but(after, "landing") == _lines_but(raw, "landing")
    assert after.count("\nlanding:") == 1


_BLOCK_LANDING = (
    "landing:\n"
    f"- head: {'e6a9fbde' + '0' * 32}\n"
    "  step: re_review\n"
    "  refusal: head_not_reviewed\n"
    "  at: '2026-10-14T12:23:27+00:00'\n"
    "  cost_base: 27.9541\n"
)
_BODY_LOOKALIKES = "intro\n\n- landing: a bullet\n\n    indented: code\nlanding: a body line\n"


@pytest.mark.parametrize(
    ("anchor", "injected", "next_line"),
    [
        ("\nsubtasks:", "\n{block}subtasks:", "subtasks:"),
        ("\nruns:", "\n{block}runs:", "runs:"),
        ("\n---\n", "\n{block}---\n", "---"),
    ],
    ids=["before_subtasks", "before_runs", "last_frontmatter_key"],
)
def test_a_block_landing_in_the_gh_227_shape_is_replaced_by_one_flow_line(
    anchor, injected, next_line
):
    run = {"role": "PjM", "section": "PjM", "version": 1, "session_id": "", "model": "m"}
    run.update(cost_usd=0.1, turns=1)
    failure = {"role": "PjM", "cost_usd": 0.1, "reason": "x"}
    sections = {"PM": _BODY_LOOKALIKES}
    rendered = document.render(_bare(runs=[run], failures=[failure], sections=sections))
    assert "\nlanding:" not in rendered.split("\n---\n")[0]
    raw = rendered.replace(anchor, injected.format(block=_BLOCK_LANDING), 1)
    assert raw.count(_BLOCK_LANDING) == 1
    after_block = raw.split(_BLOCK_LANDING)[1].split("\n")[0]
    assert after_block.startswith(next_line)
    block = document.parse(raw)["landing"]
    assert block[0]["step"] == "re_review" and block[0]["cost_base"] == 27.9541
    marked = [{**block[0], "resume_spawns": 1}]

    after = document.record_closure(raw, {"landing": marked})

    flow = document._scalar_line("landing", marked)
    assert after == raw.replace(_BLOCK_LANDING, flow + "\n")
    frontmatter = after.split("\n---\n")[0].split("\n")
    landing_lines = [ln for ln in frontmatter if ln.startswith("landing:")]
    assert len(landing_lines) == 1
    assert yaml.safe_load(landing_lines[0]) == {"landing": marked}
    assert document.parse(after)["landing"] == marked


def test_a_flow_and_a_block_landing_parse_to_the_same_list():
    flow = document.render(_bare(landing=_LANDED))
    block = flow.replace(
        document._scalar_line("landing", _LANDED),
        "landing:\n- {head: " + "a" * 40 + ", step: fix, refusal: open_findings_blocking, at: t}",
    )
    assert block != flow
    assert document.parse(block)["landing"] == document.parse(flow)["landing"] == _LANDED


@pytest.mark.parametrize("key", ["runs", "failures"])
def test_record_closure_still_refuses_to_replace_the_runs_and_failures_blocks(key):
    run = {"role": "PjM", "section": "PjM", "version": 1, "session_id": "", "model": "m"}
    run.update(cost_usd=0.1, turns=1)
    failure = {"role": "PjM", "cost_usd": 0.1, "reason": "x"}
    raw = document.render(_bare(runs=[run], failures=[failure]))
    assert f"\n{key}:\n- " in raw
    with pytest.raises(ValueError, match=key):
        document.record_closure(raw, {key: [{"x": 1}]})


def test_reviewed_head_carried_from_renders_and_parses_back_on_its_subtask():
    unit = {
        "id": "only",
        "status": "approved",
        "reviewed_head": "bbb",
        "reviewed_head_carried_from": "aaa",
    }
    state = document.parse(document.render(_bare(subtasks={"only": unit})))
    assert state["subtasks"]["only"]["reviewed_head_carried_from"] == "aaa"
    plain = dict(unit)
    del plain["reviewed_head_carried_from"]
    without = document.render(_bare(subtasks={"only": plain}))
    assert "reviewed_head_carried_from" not in without
