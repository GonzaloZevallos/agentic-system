"""The escalation comment's one-word block (GH-593): `application.escalate.comment` puts one
question, a recommended answer and the options right under the reason, from the table the domain
closes by (class, cause). It is presentation only: the rest of the comment is what it was."""

import pytest

from agentic.core.process.application import escalate
from agentic.core.process.domain import rules, task, tracker_items
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
