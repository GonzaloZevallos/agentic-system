"""The escalation comment's one-word block (GH-593): `application.escalate.comment` puts one
question, a recommended answer and the options right under the reason, from the table the domain
closes by (class, cause). It is presentation only: the rest of the comment is what it was."""

from typing import get_args

import pytest

from agentic.core.process.application import escalate
from agentic.core.process.domain import escalation_prompts, rate_limit, rules, task, tracker_items
from agentic.core.process.domain.spec_delivery import SpecRouteCause
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def state(reason, cls, **extra):
    return {"id": "GH-12", "escalated": reason, "escalation_class": cls, **extra}


def test_a_returns_escalation_opens_with_the_reason_then_asks_one_question(cfg):
    text = escalate.comment(state("the pair kept returning", "returns"), cfg, memory_ports())

    question, recommended, options = rules.ESCALATION_PROMPTS[("returns", None)]
    lines = text.splitlines()
    # The comment still starts with the marker the poller and the resume look for.
    assert lines[0] == "Escalated: the pair kept returning"
    assert f"Question: {question}" in lines
    assert f"Recommended: {recommended.format(issue=12)}" in lines
    for option in options:
        shown = option.format(issue=12)
        assert f"- {shown}" in lines or f"- `{shown}`" in lines
    # The block sits between the reason and the footer, which does not change.
    assert lines.index(f"Question: {question}") < lines.index("Options:")
    assert text.endswith("then run `agentic resume --issue 12`.")


def test_a_spent_mechanical_ci_escalation_asks_to_try_again(cfg):
    spent = state("CI gave no verdict", "ci", escalation_cause="ci_gh_unavailable")
    text = escalate.comment(spent, cfg, memory_ports())

    question, recommended, _ = rules.ESCALATION_PROMPTS[("ci", "ci_gh_unavailable")]
    assert f"Question: {question}" in text
    assert f"Recommended: `{recommended.format(issue=12)}`" in text
    assert "- `gh issue close 12 --reason 'not planned'`" in text
    assert "Question: Which type" not in text


def test_the_questions_class_lists_the_roles_questions_one_per_line(cfg):
    asked = state(
        "the Product Analyst has questions",
        "questions",
        questions_for_human=["Who is the user?", "Which currency?"],
    )
    text = escalate.comment(asked, cfg, memory_ports())

    lines = text.splitlines()
    assert "1. Who is the user?" in lines and "2. Which currency?" in lines
    assert lines.index("1. Who is the user?") + 1 == lines.index("2. Which currency?")
    # The questions are listed once, inside the block.
    assert text.count("Who is the user?") == 1
    assert "Options:" in lines


def test_a_restarted_attempt_that_returned_again_asks_for_a_change_to_the_brief(cfg):
    again = state(
        "Tech Lead→Developer (a) hit 2 returns: x",
        "returns",
        restarts={"Tech Lead→Developer (a)": 1},
    )
    text = escalate.comment(again, cfg, memory_ports())

    assert "Which change to the brief should it try?" in text


def test_another_pair_that_never_restarted_keeps_the_plain_returns_question(cfg):
    # The input the prompt must not misread: some other pair restarted, this one did not.
    other = state(
        "Tech Lead→Developer (b) hit 2 returns: x",
        "returns",
        restarts={"Tech Lead→Developer (a)": 1},
    )
    text = escalate.comment(other, cfg, memory_ports())

    assert "Which change to the brief" not in text
    assert "The pair kept returning the same work. What should change?" in text


def test_a_state_with_no_class_keeps_the_comment_it_had(cfg):
    legacy = {"id": "GH-12", "escalated": "old reason"}
    text = escalate.comment(legacy, cfg, memory_ports())

    assert "Question:" not in text
    assert text.splitlines()[0] == "Escalated: old reason"


ITEM = {"kind": "human", "question": "Ship it?", "recommendation": "Yes, ship."}
AT = "2026-11-13T10:00:00+00:00"


def raised(**extra):
    base = {"id": "GH-12", "tracker_items": [ITEM], **extra}
    return task.apply(base, tracker_items.escalate_human(base, AT))


@pytest.mark.anyio
async def test_a_human_item_escalates_through_the_node_with_needs_human_and_no_close(cfg):
    ports = memory_ports()
    state = raised()

    await escalate.escalate(state, cfg, ports)

    assert ports.tracker.labels == [("GH-12", "needs-human")]
    [(_, text)] = ports.tracker.comments
    assert "Ship it? — Recommended: Yes, ship." in text
    assert "Decision:" in text
    assert "gh issue close" not in text
    assert "Decide each item above; each recommendation is the plan's." in text


def test_a_role_question_and_a_pending_item_show_once_each_in_that_order(cfg):
    state = {**raised(), "questions_for_human": ["Which currency?"]}
    text = escalate.comment(state, cfg, memory_ports())

    line = tracker_items.question_line(ITEM)
    assert text.count("Which currency?") == 1 and text.count(line) == 1
    assert text.index("Which currency?") < text.index(line)


def test_a_ci_escalation_after_the_items_shows_each_item_once(cfg):
    state = raised()
    ci = rules.logged(rules.escalation("ci", "red", cause="ci_gh_unavailable"), at=AT)
    text = escalate.comment(task.apply(state, ci), cfg, memory_ports())

    question, _, _ = rules.ESCALATION_PROMPTS[("ci", "ci_gh_unavailable")]
    assert f"Question: {question}" in text
    assert text.count(tracker_items.question_line(ITEM)) == 1


def test_a_ci_escalation_lists_pending_items_not_in_the_questions_under_its_own_prompt(cfg):
    state = {**raised(), "questions_for_human": []}
    ci = rules.logged(rules.escalation("ci", "red", cause="ci_gh_unavailable"), at=AT)
    text = escalate.comment(task.apply(state, ci), cfg, memory_ports())

    assert "Your reply also decides these items:" in text
    assert text.count(tracker_items.question_line(ITEM)) == 1


def test_without_pending_items_the_comment_has_no_item_block(cfg):
    text = escalate.comment(
        state("x", "ci", escalation_cause="ci_gh_unavailable"), cfg, memory_ports()
    )
    assert "also decides" not in text


def test_only_the_human_items_pending_are_listed_though_a_system_item_shares_the_pause(
    cfg, monkeypatch
):
    """GH-625: the listing reads the items through `human_items`, so a system item whose hash is
    pending too is not asked of the owner."""
    monkeypatch.setattr(tracker_items, "KINDS", frozenset({"human", "comment"}))
    comment = {"kind": "comment", "text": "Superseded by the newer item."}
    pending = [tracker_items.item_hash(ITEM), tracker_items.item_hash(comment)]
    state = {
        "id": "GH-12",
        "escalated": "the plan leaves 1 decision(s) to the owner",
        "escalation_class": "questions",
        "tracker_items": [ITEM, comment],
        "tracker_human": pending,
    }
    text = escalate.comment(state, cfg, memory_ports())

    assert tracker_items.question_line(ITEM) in text and "Superseded by the newer item." not in text


@pytest.mark.parametrize("cause", get_args(SpecRouteCause))
def test_a_refused_spec_asks_its_own_question_not_which_type(cfg, cause):
    refused = state(
        "The spec is refused.",
        "classification",
        spec_refusal={"cause": cause, "detail": []},
    )
    text = escalate.comment(refused, cfg, memory_ports())

    assert "Which type is this task?" not in text
    question, recommended, _ = rules.ESCALATION_PROMPTS[("classification", cause)]
    assert f"Question: {question}" in text
    shown = recommended.format(issue=12)
    assert f"Recommended: {shown}" in text or f"Recommended: `{shown}`" in text
    if cause == "spec_type":
        assert "Remove the line, or decide that it is a feature?" in text
    else:
        assert f"({cause}, see the reason above)" in text


def test_a_classification_pause_without_a_refusal_still_asks_which_type(cfg):
    text = escalate.comment(state("unclear", "classification"), cfg, memory_ports())

    assert "Question: Which type is this task?" in text


def _drawing_state(cause="drawing_departure", **extra):
    from agentic.core.process.domain import drawing, drawing_answer

    base = {"id": "GH-12"}
    departures = [
        drawing.Departure("design/home.png", e, "blue", "green", "keep", "approved", "dev")
        for e in ("hero", "footer")
    ]
    patch = drawing_answer.escalation_patch(base, departures, "T0")
    patch["drawing_escalation"] = {**patch["drawing_escalation"], "cause": cause}
    return {**base, **patch, **extra}


def test_a_drawing_pause_shows_the_numbers_and_both_commands_per_number(cfg):
    asked = _drawing_state(questions_for_human=["1. stray question"])
    text = escalate.comment(asked, cfg, memory_ports())
    assert "Departure #1:" in text and "Departure #2:" in text
    for n in (1, 2):
        for verdict in ("accept", "reject"):
            assert f"agentic resume --issue 12 --drawing {n}={verdict}" in text
    assert "\n1. " not in text and "stray question" not in text
    assert not [line for line in text.splitlines() if "agentic resume" in line and "<n>" in line]


def test_a_drawing_cap_pause_keeps_the_close_option(cfg):
    text = escalate.comment(_drawing_state("drawing_cap"), cfg, memory_ports())
    assert "gh issue close 12 --reason 'not planned'" in text
    assert "--drawing 2=reject" in text


def test_a_drawing_pause_with_no_issue_number_shows_only_prose():
    from tests.fakes import memory_ports as ports_of

    ports = ports_of()
    ports.inbox.issue_of = lambda task_id: None
    text = escalate.comment(_drawing_state(), Configuration(product_dir="/tmp/p"), ports)
    assert "--drawing" not in text.replace("`Drawing <n>", "")
    assert "agentic resume" not in text.split("Options:")[0]


# --- GH-989: the GitHub rate limit pauses without telling the tracker -------------------------

CLOCK = "2026-11-19T00:00:00+00:00"
NOW = "2026-11-19T01:00:00+00:00"


def limited(**extra):
    base = {"id": "GH-12", "status": "developing", "rate_limit_paused_at": CLOCK, **extra}
    return task.apply(base, rate_limit.rate_limit_pause_patch(base, resets_at=None, at=NOW))


@pytest.mark.anyio
async def test_a_github_rate_limit_pause_saves_the_document_and_writes_nothing_to_the_tracker(cfg):
    ports = memory_ports()
    paused = limited()

    patch = await escalate.escalate(paused, cfg, ports)

    assert ports.tracker.labels == [] and ports.tracker.comments == []
    assert ports.documents.last["rate_limit_paused_at"] == NOW
    assert patch == {}


@pytest.mark.anyio
async def test_the_persisted_escalation_also_writes_nothing_from_the_node(cfg):
    ports = memory_ports()
    persisted = task.apply(
        limited(), rate_limit.rate_limit_persisted_patch(at="2026-11-19T13:00:00+00:00")
    )

    await escalate.escalate(persisted, cfg, ports)

    assert ports.tracker.labels == [] and ports.tracker.comments == []
    assert ports.documents.last["escalated"] == rate_limit.RATE_LIMIT_PERSISTED


@pytest.mark.anyio
async def test_an_ordinary_escalation_still_labels_and_comments_and_clears_the_clock(cfg):
    ports = memory_ports()
    ordinary = {**raised(), "rate_limit_paused_at": CLOCK, "rate_limit_noticed_at": NOW}

    patch = await escalate.escalate(ordinary, cfg, ports)

    assert ports.tracker.labels == [("GH-12", "needs-human")]
    assert len(ports.tracker.comments) == 1
    assert patch == {"rate_limit_paused_at": None, "rate_limit_noticed_at": None}
    assert ports.documents.last.get("rate_limit_paused_at") is None
    assert ports.documents.last.get("rate_limit_noticed_at") is None


def _format_state(**extra):
    log = [
        {"kind": "failure", "value": "schema", "section": "S", "rule": "schema_invalid"},
        {"kind": "escalation", "value": "failure"},
    ]
    return state("Tech Lead failed", "failure", pause_log=log, **extra)


def test_a_format_refusal_asks_to_fix_the_rule_not_to_try_again(cfg):
    text = escalate.comment(_format_state(), cfg, memory_ports())

    question, recommended, _ = rules.ESCALATION_PROMPTS[("failure", "format_refusal")]
    assert f"Question: {question}" in text
    assert f"Recommended: `{recommended.format(issue=12)}`" in text
    assert "Should the task try again now?" not in text


def test_a_held_format_task_asks_about_the_hold(cfg):
    held = _format_state(format_hold={"rule": "schema_invalid", "cause": "rule_spent", "at": "x"})
    text = escalate.comment(held, cfg, memory_ports())

    question, _, _ = rules.ESCALATION_PROMPTS[("failure", "format_hold")]
    assert f"Question: {question}" in text
    assert "Should the task try again now?" not in text


def test_a_failure_that_is_not_a_format_epoch_keeps_the_plain_question(cfg):
    for log in (
        [
            {"kind": "failure", "value": "schema", "section": "S"},  # no rule
            {"kind": "escalation", "value": "failure"},
        ],
        [
            {"kind": "failure", "value": "schema", "section": "S", "rule": "schema_invalid"},
            {"kind": "failure", "value": "rate_limit", "section": "T"},
            {"kind": "escalation", "value": "failure"},
        ],
    ):
        text = escalate.comment(state("x", "failure", pause_log=log), cfg, memory_ports())
        assert "Question: Should the task try again now?" in text


def _stalled_failure(**extra):
    return {
        "reason": "stall",
        "error": "stalled",
        "stall": {"seconds": 1801, "last_event": "system", "in_hook": True},
        **extra,
    }


def _escalated_by(failures, cls="failure"):
    """A state built the way the graph builds it: the real `rules.escalation` patch composed
    through `rules.logged` over `failures`, folded in by `task.apply`."""
    patch = rules.logged(
        {"failures": failures, **rules.escalation(cls, "Tech Lead failed")},
        failures,
        at="2026-11-27T00:00:00+00:00",
    )
    return task.apply({"id": "GH-12", "pause_log": []}, patch)


def test_two_stalls_add_the_suffix_and_ask_the_session_stalled_question(cfg):
    stalled = _escalated_by([_stalled_failure(), _stalled_failure()])
    text = escalate.comment(stalled, cfg, memory_ports())

    lines = text.splitlines()
    question, recommended, options = escalation_prompts.SESSION_STALLED_PROMPT
    assert lines[0] == "Escalated: Tech Lead failed (session_stalled)"
    assert f"Question: {question}" in lines
    assert f"Recommended: `{recommended.format(issue=12)}`" in lines
    assert text.count(question) == 1
    # Never stored: the state keeps its reason and its (absent) cause.
    assert stalled["escalated"] == "Tech Lead failed"
    assert stalled["escalation_cause"] is None
    assert (
        rules.auto_resumable(stalled) is None and rules.pause_kind(stalled["pause_log"]) == "human"
    )


def test_a_single_stall_or_a_stall_then_a_schema_failure_keeps_the_plain_retry(cfg):
    retry = escalation_prompts.ESCALATION_PROMPTS[("failure", None)][0]
    schema = {"reason": "schema", "error": "bad"}
    for failures in ([_stalled_failure()], [_stalled_failure(), schema]):
        text = escalate.comment(_escalated_by(failures), cfg, memory_ports())
        assert text.splitlines()[0] == "Escalated: Tech Lead failed"
        assert f"Question: {retry}" in text and "session_stalled" not in text


def test_a_format_failure_keeps_its_format_row_and_no_suffix(cfg):
    text = escalate.comment(_format_state(), cfg, memory_ports())

    assert "session_stalled" not in text.splitlines()[0]
    assert (
        f"Question: {escalation_prompts.ESCALATION_PROMPTS[('failure', 'format_refusal')][0]}"
        in text
    )


def test_another_class_never_picks_the_stall_row(cfg):
    stalled = _escalated_by([_stalled_failure(), _stalled_failure()], cls="cap")
    text = escalate.comment(stalled, cfg, memory_ports())

    assert "session_stalled" not in text
    assert escalation_prompts.SESSION_STALLED_PROMPT[0] not in text


def test_the_picker_runs_once_per_comment(cfg, monkeypatch):
    calls = []
    real = escalation_prompts.stalled_batch
    monkeypatch.setattr(
        escalation_prompts, "stalled_batch", lambda log: calls.append(1) or real(log)
    )
    escalate.comment(_escalated_by([_stalled_failure(), _stalled_failure()]), cfg, memory_ports())
    assert calls == [1]
