"""A task document written before `resets_at` existed loads and is read as before
(ADR-0039, addendum GH-381)."""

from agentic.core.process.application.resume import state_from
from agentic.core.process.domain import document, rules


def _escalated(pause_log: list[dict]) -> dict:
    return {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "status": "waiting_on_human",
        "pause_log": pause_log,
    }


LEGACY_LOG = [
    {"kind": "failure", "value": "account_limit", "section": "Developer"},
    {"kind": "escalation", "value": "failure"},
]


def test_a_document_without_resets_at_loads_and_is_read_as_an_account_limit_pause():
    raw = document.parse(document.render(_escalated(LEGACY_LOG)))
    state = state_from(raw)
    assert state["pause_log"] == LEGACY_LOG
    assert rules.auto_resumable(state) == "account_limit"


def test_a_document_with_resets_at_round_trips_and_is_read_the_same():
    log = [{**LEGACY_LOG[0], "resets_at": "2026-10-27T05:40:00-03:00"}, LEGACY_LOG[1]]
    state = state_from(document.parse(document.render(_escalated(log))))
    assert state["pause_log"] == log
    assert rules.auto_resumable(state) == "account_limit"
