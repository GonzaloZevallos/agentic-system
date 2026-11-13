import hashlib
import subprocess
import sys

import pytest

from agentic.core.process.domain import rules, task, tracker_items

ITEM = {"kind": "human", "question": "Ship it?", "recommendation": "Yes, ship."}


def test_parse_accepts_a_valid_item_and_returns_a_new_dict_with_exactly_the_three_keys():
    raw = [{**ITEM, "extra": "dropped"}]
    parsed = tracker_items.parse(raw)
    assert parsed == [ITEM]
    assert parsed[0] is not raw[0]


def test_kinds_holds_only_human():
    assert tracker_items.KINDS == frozenset({"human"})


@pytest.mark.parametrize("kind", ["comment", "open_issue", "", None])
def test_parse_rejects_an_unknown_kind_and_says_to_write_it_as_human(kind):
    with pytest.raises(ValueError, match="human") as error:
        tracker_items.parse([{**ITEM, "kind": kind}])
    assert "{kind: human, question, recommendation}" in str(error.value)
    if kind:
        assert kind in str(error.value)


def test_parse_rejects_a_missing_kind():
    with pytest.raises(ValueError, match="human"):
        tracker_items.parse([{"question": "q", "recommendation": "r"}])


@pytest.mark.parametrize("missing", ["question", "recommendation"])
def test_parse_rejects_a_missing_field(missing):
    item = {k: v for k, v in ITEM.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        tracker_items.parse([item])


@pytest.mark.parametrize("field", ["question", "recommendation"])
@pytest.mark.parametrize("value", ["one\ntwo", "   ", ""])
def test_parse_rejects_a_field_that_is_not_one_line(field, value):
    with pytest.raises(ValueError, match="one non-empty line"):
        tracker_items.parse([{**ITEM, field: value}])


@pytest.mark.parametrize("raw", ["text", {"kind": "human"}, None, 3])
def test_parse_rejects_a_non_list(raw):
    with pytest.raises(ValueError, match="list"):
        tracker_items.parse(raw)


def test_parse_rejects_a_non_mapping_item():
    with pytest.raises(ValueError, match="human"):
        tracker_items.parse(["do the thing"])


def test_parse_of_an_empty_list_is_empty():
    assert tracker_items.parse([]) == []


def test_item_hash_is_pinned_and_covers_the_kind():
    assert tracker_items.item_hash(ITEM) == "28383afe2b80"
    assert tracker_items.item_hash(ITEM) == tracker_items.digest(
        ["human", "Ship it?", "Yes, ship."]
    )


def test_digest_gives_the_bytes_of_the_old_tracker_hash_formula():
    lines = ["  a ", "b", "c d"]
    expected = hashlib.sha256("\n".join(s.strip() for s in lines).encode()).hexdigest()[:12]
    assert tracker_items.digest(lines) == expected


OTHER = {"kind": "human", "question": "Rename it?", "recommendation": "No."}
AT = "2026-11-13T10:00:00+00:00"
H = tracker_items.item_hash(ITEM)
H2 = tracker_items.item_hash(OTHER)


def test_unanswered_counts_no_record_and_a_record_of_only_at_but_not_answered():
    items = [ITEM, OTHER]
    assert tracker_items.unanswered(items, {}) == items
    assert tracker_items.unanswered(items, {H: {"at": "t1"}}) == items
    assert tracker_items.unanswered(items, {H: {"at": "t1", "answered": "t2"}}) == [OTHER]


def test_question_line_is_the_question_a_dash_and_the_recommendation():
    assert tracker_items.question_line(ITEM) == "Ship it? — Recommended: Yes, ship."


def test_escalate_human_returns_nothing_with_no_items_all_answered_or_already_pending():
    assert tracker_items.escalate_human({}, AT) == {}
    answered = {"tracker_items": [ITEM], "tracker_actions_done": {H: {"at": "a", "answered": "b"}}}
    assert tracker_items.escalate_human(answered, AT) == {}
    pending = {"tracker_items": [ITEM], "tracker_human": [H]}
    assert tracker_items.escalate_human(pending, AT) == {}


def test_escalate_human_gives_the_whole_patch_for_one_item():
    patch = tracker_items.escalate_human({"tracker_items": [ITEM]}, AT)
    assert patch == {
        "status": "waiting_on_human",
        "escalated": "the plan leaves 1 decision(s) to the owner",
        "escalation_class": "questions",
        "escalation_cause": None,
        "tracker_human": [H],
        "questions_for_human": ["Ship it? — Recommended: Yes, ship."],
        "tracker_actions_done": {H: {"at": AT}},
        "pause_log": [{"kind": "escalation", "value": "questions", "at": AT}],
    }


def test_escalate_human_adds_only_the_new_item_and_keeps_the_pending_one():
    state = {"tracker_items": [ITEM, OTHER], "tracker_human": [H]}
    patch = tracker_items.escalate_human(state, AT)
    assert patch["tracker_human"] == [H, H2]
    assert patch["questions_for_human"] == [tracker_items.question_line(OTHER)]
    assert patch["tracker_actions_done"] == {H2: {"at": AT}}


def test_a_later_ci_escalation_keeps_the_items_pending_and_the_pause_a_humans():
    state = task.apply(
        {"tracker_items": [ITEM]}, tracker_items.escalate_human({"tracker_items": [ITEM]}, AT)
    )
    ci = rules.logged(rules.escalation("ci", "red", cause="ci_gh_unavailable"), at=AT)
    state = task.apply(state, ci)
    assert state["tracker_human"] == [H]
    assert rules.tracker_item_pause(state)
    assert rules.pause_kind(rules._current_epoch(state)) == "human"
    assert rules.auto_resumable(state) is None
    resumed = rules.resumed(state, "Ship it.", at="2026-11-13T11:00:00+00:00")
    state = task.apply(state, resumed)
    assert state["tracker_actions_done"][H] == {
        "at": AT,
        "answered": "2026-11-13T11:00:00+00:00",
    }
    assert tracker_items.escalate_human(state, AT) == {}


def test_importing_the_module_does_not_import_rules():
    code = (
        "import sys, agentic.core.process.domain.tracker_items;"
        "sys.exit('agentic.core.process.domain.rules' in sys.modules)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
