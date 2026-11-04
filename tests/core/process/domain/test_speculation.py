from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from agentic.core.process.domain import rules, speculation
from agentic.core.process.domain.task import TaskState

HEAD = "a" * 40
NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)
PUSHED = "2026-11-03T12:00:00+00:00"


def _state(**extra: Any) -> TaskState:
    return cast("TaskState", {"id": "T-1", "subtasks": {"solo": {}}, **extra})


def _due(state: TaskState | None = None, **overrides: Any) -> bool:
    args: dict[str, Any] = {
        "unit": "solo",
        "ci_names": ["check"],
        "tiered": True,
        "untrusted": False,
        "head": HEAD,
    }
    args.update(overrides)
    return speculation.push_due(
        state or _state(),
        args["unit"],
        ci_names=args["ci_names"],
        tiered=args["tiered"],
        untrusted=args["untrusted"],
        head=args["head"],
    )


def _record(head: str, passed: bool = True) -> dict[str, Any]:
    return {
        "version": 1,
        "head": head,
        "passed": passed,
        "duration_s": 1.0,
        "declared": {},
        "results": [],
        "witness": None,
    }


def test_push_due_for_the_single_subtask_of_a_ci_product():
    assert _due() is True


def test_push_due_for_the_integration_unit():
    state = _state(subtasks={"a": {}, "b": {}, rules.INTEGRATION: {}})
    assert _due(state, unit=rules.INTEGRATION) is True


@pytest.mark.parametrize(
    ("state", "overrides"),
    [
        pytest.param(None, {"unit": "other"}, id="not the final unit"),
        pytest.param(
            _state(subtasks={"a": {}, "b": {}, rules.INTEGRATION: {}}),
            {"unit": "a"},
            id="a subtask when an integration exists",
        ),
        pytest.param(_state(subtasks={"a": {}, "b": {}}), {"unit": "a"}, id="no final unit"),
        pytest.param(None, {"ci_names": []}, id="no ci"),
        pytest.param(None, {"tiered": False}, id="no delivery_checks"),
        pytest.param(None, {"untrusted": True}, id="untrusted head"),
        pytest.param(None, {"untrusted": None}, id="unknown paths"),
        pytest.param(_state(escalated="stuck"), {}, id="escalated"),
        pytest.param(_state(pull_request="https://x/pull/1"), {}, id="pull request present"),
        pytest.param(_state(full_checks=_record(HEAD)), {}, id="passed on that head"),
        pytest.param(_state(full_checks=_record(HEAD[:12])), {}, id="passed on a short form"),
    ],
)
def test_push_due_refuses(state: TaskState | None, overrides: dict[str, Any]):
    assert _due(state, **overrides) is False


def test_push_due_when_the_record_failed_or_is_on_another_head():
    assert _due(_state(full_checks=_record(HEAD, passed=False))) is True
    assert _due(_state(full_checks=_record("b" * 40))) is True


def test_consumable_for_the_same_sha():
    assert speculation.consumable(_state(ci_speculation={"head": HEAD, "pushed_at": PUSHED}), HEAD)


def test_consumable_refuses_another_sha():
    state = _state(ci_speculation={"head": HEAD, "pushed_at": PUSHED})
    assert speculation.consumable(state, "b" * 40) is False


def test_consumable_refuses_a_short_recorded_head():
    state = _state(ci_speculation={"head": HEAD[:12], "pushed_at": PUSHED})
    assert speculation.consumable(state, HEAD) is False


def test_consumable_refuses_a_missing_or_cleared_record():
    assert speculation.consumable(_state(), HEAD) is False
    assert speculation.consumable(_state(ci_speculation=None), HEAD) is False


def test_consumable_reads_only_ci_speculation():
    # The delivery's own anchors never stand for a speculative push.
    state = _state(pushed_head=HEAD, pushed_at=PUSHED)
    assert speculation.consumable(state, HEAD) is False


def test_event_lost_when_the_push_is_stale_and_no_run_is_seen():
    pushed = (NOW - timedelta(seconds=120)).isoformat()
    assert speculation.event_lost(pushed, NOW, 60.0, read_has_run=False) is True


def test_event_lost_at_the_grace_boundary():
    pushed = (NOW - timedelta(seconds=60)).isoformat()
    assert speculation.event_lost(pushed, NOW, 60.0, read_has_run=False) is True


def test_event_not_lost_inside_the_grace():
    pushed = (NOW - timedelta(seconds=10)).isoformat()
    assert speculation.event_lost(pushed, NOW, 60.0, read_has_run=False) is False


def test_event_not_lost_when_a_run_is_seen():
    pushed = (NOW - timedelta(seconds=600)).isoformat()
    assert speculation.event_lost(pushed, NOW, 60.0, read_has_run=True) is False


@pytest.mark.parametrize("pushed", ["not a date", "", "2026-11-03T10:00:00"])
def test_event_lost_refuses_an_unreadable_pushed_at(pushed: str):
    assert speculation.event_lost(pushed, NOW, 60.0, read_has_run=False) is False
