"""`rules.escalation` and `rules.resumed` with a spec in the state (GH-594): a `questions`
pause of a spec delivery carries a closed `spec_cause`, and without a spec nothing changes."""

from __future__ import annotations

from typing import cast, get_args

import pytest

from agentic.core.process.domain import document, rules
from agentic.core.process.domain.spec_delivery import SpecContract
from agentic.core.process.domain.task import EscalationClass, TaskState

SPEC: SpecContract = {
    "path": "docs/specs/a.md",
    "slice": "s1",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "criteria": [],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}


@pytest.mark.parametrize("cls", get_args(EscalationClass))
def test_without_a_spec_the_patch_is_the_three_keys_of_today(cls: EscalationClass):
    assert rules.escalation(cls, "why") == {
        "escalated": "why",
        "escalation_class": cls,
        "escalation_cause": None,
    }


@pytest.mark.parametrize("cls", [c for c in get_args(EscalationClass) if c != "questions"])
def test_with_a_spec_every_class_but_questions_writes_a_none_cause(cls: EscalationClass):
    assert rules.escalation(cls, "why", spec=SPEC)["spec_cause"] is None


def test_a_spec_questions_without_a_cause_is_refused():
    with pytest.raises(ValueError, match="needs a spec_cause"):
        rules.escalation("questions", "why", spec=SPEC)


def test_a_spec_questions_writes_its_cause():
    patch = rules.escalation("questions", "why", spec=SPEC, spec_cause="contradiction")
    assert patch["spec_cause"] == "contradiction"
    assert patch["escalation_class"] == "questions"


def test_a_cause_with_another_class_is_refused():
    with pytest.raises(ValueError, match="questions escalation"):
        rules.escalation("failure", "why", spec=SPEC, spec_cause="conflict")


def test_a_cause_with_a_mechanical_cause_is_refused():
    with pytest.raises(ValueError, match="mechanical"):
        rules.escalation(
            "questions", "why", cause="delivery_transient", spec=SPEC, spec_cause="data_loss"
        )


def test_a_cause_without_a_spec_is_refused():
    with pytest.raises(ValueError, match="no spec"):
        rules.escalation("questions", "why", spec_cause="conflict")


def test_resuming_a_spec_task_clears_the_cause():
    state: TaskState = {"spec": SPEC, "escalated": "x", "spec_cause": "conflict"}
    assert rules.resumed(state, None, at="2026-11-13T00:00:00Z")["spec_cause"] is None


def test_resuming_a_task_without_a_spec_writes_no_cause():
    assert "spec_cause" not in rules.resumed({"escalated": "x"}, None, at="2026-11-13T00:00:00Z")


def test_the_graphs_empty_default_spec_is_no_spec():
    empty = cast(SpecContract, {})
    assert rules.escalation("questions", "why", spec=empty) == rules.escalation("questions", "why")
    state: TaskState = {"spec": empty, "escalated": "x"}
    assert "spec_cause" not in rules.resumed(state, None, at="2026-11-13T00:00:00Z")


# --- the pause entry (GH-594): `spec_cause` is written only when set, never as None -------------

AT = "2026-11-13T00:00:00Z"


def test_a_logged_entry_of_a_patch_without_a_spec_cause_is_the_entry_of_today():
    patch = rules.escalation("questions", "why")
    assert rules.logged(patch, at=AT)["pause_log"] == [
        {"kind": "escalation", "value": "questions", "at": AT}
    ]


def test_a_logged_entry_with_a_none_spec_cause_writes_no_key():
    patch = rules.escalation("cap", "why", spec=SPEC)
    assert patch["spec_cause"] is None
    entry = rules.logged(patch, at=AT)["pause_log"][0]
    assert "spec_cause" not in entry
    assert entry == rules.logged(rules.escalation("cap", "why"), at=AT)["pause_log"][0]


def test_two_causes_give_two_distinct_entries():
    entries = [
        rules.logged(rules.escalation("questions", "why", spec=SPEC, spec_cause=c), at=AT)[
            "pause_log"
        ][0]
        for c in ("contradiction", "data_loss")
    ]
    assert entries[0] != entries[1]
    assert [e["spec_cause"] for e in entries] == ["contradiction", "data_loss"]


def test_escalation_entry_writes_a_spec_cause_only_when_given():
    assert "spec_cause" not in rules.escalation_entry("questions", AT)
    assert (
        rules.escalation_entry("questions", AT, spec_cause="conflict")["spec_cause"] == "conflict"
    )


def test_a_document_round_trip_keeps_the_pause_entries_with_and_without_the_cause():
    plain = rules.logged(rules.escalation("questions", "why"), at=AT)["pause_log"]
    caused = rules.logged(
        rules.escalation("questions", "why", spec=SPEC, spec_cause="data_loss"), at=AT
    )["pause_log"]
    base = {"id": "T-1", "title": "t", "description": "d"}
    for log in (plain, caused):
        assert document.parse(document.render({**base, "pause_log": log}))["pause_log"] == log
    assert "spec_cause" not in document.render({**base, "pause_log": plain})
    assert "spec_cause: data_loss" in document.render({**base, "pause_log": caused})
