"""GH-1019 part 2: the rules read a format epoch through `format_retakes` alone. The key, the
limit, the label decision and what a resume releases, plus the characterization of the epochs that
are not one (mixed, rule-less) so their classification did not move."""

from __future__ import annotations

from typing import Any

import pytest

from agentic.core.process.domain import drawing_answer, format_retakes, rules
from agentic.core.process.domain.task import RefusalRule

AT = "2026-11-22T10:00:00Z"
RULE: RefusalRule = "schema_invalid"
OTHER: RefusalRule = "part_over_budget"
HOLD = {"rule": RULE, "cause": "rule_spent", "at": AT}


def _failure(value: str, rule: str | None = None, section: str = "Tech Lead") -> dict[str, Any]:
    entry: dict[str, Any] = {"kind": "failure", "value": value, "section": section}
    if rule is not None:
        entry["rule"] = rule
    return entry


ESCALATION: dict[str, Any] = {"kind": "escalation", "value": "failure"}


def _state(log: list[dict[str, Any]], **more: Any) -> Any:
    return {"id": "GH-1", "escalated": "x", "pause_log": log, **more}


def _format(*rule_names: str, reason: str = "schema") -> Any:
    return [_failure(reason, r, section=f"S{i}") for i, r in enumerate(rule_names)] + [ESCALATION]


def test_a_format_epoch_is_keyed_by_the_reason_of_its_last_failure():
    assert rules.auto_resumable(_state(_format(RULE))) == "schema"
    check = [_failure("schema", RULE, "A"), _failure("check", OTHER, "B"), ESCALATION]
    assert rules.auto_resumable(_state(check)) == "check"
    assert rules.pause_kind(check) == "external"
    assert (
        "schema" in rules.FORMAT_REASONS and rules.FORMAT_REASONS is format_retakes.FORMAT_REASONS
    )


@pytest.mark.parametrize(
    "log",
    [
        [_failure("upstream", section="A"), _failure("schema", RULE, "B"), ESCALATION],
        [_failure("schema", RULE, "A"), _failure("upstream", section="B"), ESCALATION],
        [_failure("schema"), ESCALATION],
        [_failure("schema", RULE), {"kind": "escalation", "value": "questions"}],
        [_failure("other", RULE), ESCALATION],
    ],
    ids=["external-then-format", "format-then-external", "no-rule", "other-class", "other-reason"],
)
def test_an_epoch_that_is_not_a_format_one_is_classified_as_before(log):
    state = _state(log)
    assert rules.auto_resumable(state) is None
    assert rules.pause_kind(log) == "human"
    assert rules.needs_a_human(state) is True
    assert rules.format_epoch(state) is False


def test_the_external_epochs_and_their_calendars_did_not_move():
    external = _state([_failure("account_limit", section="A"), ESCALATION])
    assert rules.auto_resumable(external) == "account_limit"
    assert rules.needs_a_human(external) is False
    assert rules.EXTERNAL_REASONS == (
        "account_limit",
        "upstream",
        "network",
        "disk_full",
        "github_rate_limit",
    )
    assert rules.schedule_for("schema") is rules.RETAKE_SCHEDULE
    assert rules.schedule_for("check") is rules.RETAKE_SCHEDULE


def test_the_limit_is_what_format_retakes_leaves_per_rule_and_per_task():
    log = _format(RULE, OTHER)
    assert rules.auto_resume_limit(_state(log), "schema") == 2
    counts = {RULE: 1, OTHER: 0}
    assert rules.auto_resume_limit(_state(log, format_retakes=counts), "schema") == 1
    assert rules.auto_resume_limit(_state(log, format_retakes={RULE: 2}), "schema") == 0
    backstop = {RULE: 1, OTHER: 1, "x": 2, "y": 2}
    assert rules.auto_resume_limit(_state(log, format_retakes=backstop), "schema") == 0


def test_a_format_epoch_needs_no_label_waiting_for_a_retake_spent_or_held():
    fresh = _state(_format(RULE))
    assert rules.needs_a_human(fresh) is False
    assert rules.needs_a_human(_state(_format(RULE), format_retakes={RULE: 1})) is False
    spent = _state(_format(RULE), format_retakes={RULE: 2})
    assert rules.format_hold_due(spent) is True
    assert rules.needs_a_human(spent) is False
    held = _state(_format(RULE), format_retakes={RULE: 2}, format_hold=HOLD)
    assert rules.needs_a_human(held) is False
    # a failure that is not a format epoch keeps the label even when it carries a hold key
    mixed = _state([_failure("upstream", section="A"), *_format(RULE)], format_hold=HOLD)
    assert rules.needs_a_human(mixed) is True


def test_format_held_is_the_one_question_about_a_hold():
    assert rules.format_held(_state(_format(RULE))) is False
    assert rules.format_held(_state(_format(RULE), format_hold=HOLD)) is True


def _held() -> Any:
    return _state(
        _format(RULE),
        format_retakes={RULE: 2},
        format_hold=HOLD,
        status="waiting_on_human",
    )


@pytest.mark.parametrize("decision", [None, "Decision: fix the schema"])
def test_the_owners_resume_releases_the_hold_and_resets_the_counts(decision):
    patch = rules.resumed(_held(), decision, at=AT)
    assert patch["format_retakes"] is None and patch["format_hold"] is None


def test_drawing_answer_resume_patch_releases_it_too():
    patch = drawing_answer.resume_patch(_held(), "Decision: x", None, [], at=AT)
    assert patch["format_retakes"] is None and patch["format_hold"] is None


def test_a_poller_resume_keeps_the_counts():
    patch = rules.resumed(_held(), None, at=AT, by_poller=True)
    assert "format_retakes" not in patch and "format_hold" not in patch
    patch = drawing_answer.resume_patch(_held(), None, None, [], at=AT, by_poller=True)
    assert "format_retakes" not in patch
