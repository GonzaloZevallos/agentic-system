"""`rate_limit`: the GitHub rate limit as a pause of its own (GH-989 part 1)."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentic.core.process.domain import document, rate_limit, rules, task
from agentic.core.process.domain.rate_limit import (
    RATE_LIMIT_PERSISTED,
    RATE_LIMIT_WAIT_MAX,
    rate_limit_exhausted,
    rate_limit_notice_owed,
    rate_limit_pause_patch,
    rate_limit_persisted_patch,
    reset_offset,
)

AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
AT_S = AT.isoformat()


def _state(log: list[Any] | None = None, **extra: Any) -> Any:
    return {"pause_log": log or [], **extra}


def _fail(reason: str, section: str = "") -> Any:
    return {"kind": "failure", "value": reason, "section": section}


def _esc() -> Any:
    return {"kind": "escalation", "value": "failure"}


def _resumed() -> Any:
    return {"kind": "resumed", "at": "2026-11-19T00:00:00+00:00"}


def _github_epoch() -> list[Any]:
    return [_fail("github_rate_limit"), _esc(), _resumed()]


def _paused_at(state: Any) -> str:
    return str(rate_limit_pause_patch(state, resets_at=None, at=AT_S)["rate_limit_paused_at"])


def _resets(patch: dict[str, Any]) -> str | None:
    return patch["pause_log"][0].get("resets_at")


def test_the_pause_writes_one_github_failure_then_a_failure_escalation():
    patch = rate_limit_pause_patch(_state(), resets_at=None, at=AT_S)
    failure, escalation = patch["pause_log"]
    assert (failure["kind"], failure["value"], failure["section"]) == (
        "failure",
        "github_rate_limit",
        "",
    )
    assert escalation["kind"] == "escalation" and patch["escalation_class"] == "failure"
    assert len(patch["pause_log"]) == 2


def test_the_stated_reset_is_kept_raw_only_inside_the_window():
    for delta, kept in [
        (timedelta(hours=-1), False),
        (timedelta(0), False),
        (timedelta(seconds=1), True),
        (RATE_LIMIT_WAIT_MAX, True),
        (RATE_LIMIT_WAIT_MAX + timedelta(seconds=1), False),
    ]:
        reset = AT + delta
        patch = rate_limit_pause_patch(_state(), resets_at=reset, at=AT_S)
        assert (_resets(patch) == reset.isoformat()) is kept, delta
        assert (_resets(patch) is not None) is kept, delta


def test_a_naive_reset_is_dropped():
    patch = rate_limit_pause_patch(_state(), resets_at=datetime(2026, 11, 20, 13, 0), at=AT_S)
    assert _resets(patch) is None


def test_the_first_pause_starts_the_wait_at_the_pause():
    assert _paused_at(_state(rate_limit_paused_at="2026-11-20T08:00:00+00:00")) == AT_S


def test_a_retake_of_the_same_limit_keeps_the_first_instant():
    first = "2026-11-20T08:00:00+00:00"
    state = _state(_github_epoch(), rate_limit_paused_at=first)
    assert _paused_at(state) == first


def test_a_github_epoch_with_a_missing_or_malformed_field_restarts_at_the_pause():
    for bad in [
        {},
        {"rate_limit_paused_at": "soon"},
        {"rate_limit_paused_at": "2026-11-20T08:00:00"},
    ]:
        assert _paused_at(_state(_github_epoch(), **bad)) == AT_S, bad


def test_a_field_later_than_the_pause_is_replaced():
    state = _state(_github_epoch(), rate_limit_paused_at="2026-11-20T13:00:00+00:00")
    assert _paused_at(state) == AT_S


def test_a_retried_failure_before_the_terminal_github_one_still_keeps_it():
    first = "2026-11-20T08:00:00+00:00"
    log = [
        _fail("network", "build"),
        _fail("github_rate_limit", "build"),
        _esc(),
        _resumed(),
    ]
    assert _paused_at(_state(log, rate_limit_paused_at=first)) == first


def test_a_stale_field_from_an_account_limit_epoch_is_replaced():
    log = [_fail("account_limit"), _esc(), _resumed()]
    state = _state(log, rate_limit_paused_at="2026-11-20T08:00:00+00:00")
    assert _paused_at(state) == AT_S


def test_only_the_epoch_the_last_resume_closed_counts():
    first = "2026-11-20T08:00:00+00:00"
    log = [*_github_epoch(), _fail("account_limit"), _esc(), _resumed()]
    assert _paused_at(_state(log, rate_limit_paused_at=first)) == AT_S
    log = [_fail("account_limit"), _esc(), _resumed(), *_github_epoch()]
    assert _paused_at(_state(log, rate_limit_paused_at=first)) == first


def test_exhausted_is_false_at_11h59_and_true_at_12h01_from_the_persisted_instant():
    state = _state(rate_limit_paused_at=AT_S)
    assert not rate_limit_exhausted(state, AT + timedelta(hours=11, minutes=59))
    assert rate_limit_exhausted(state, AT + timedelta(hours=12, minutes=1))


def test_exhausted_holds_after_a_document_save_and_reload():
    base = {"id": "GH-9", "title": "t", "description": "d", "type": "feature", "sections": {}}
    reloaded = document.parse(document.render({**base, "rate_limit_paused_at": AT_S}))
    assert reloaded["rate_limit_paused_at"] == AT_S
    assert not rate_limit_exhausted(reloaded, AT + timedelta(hours=11, minutes=59))
    assert rate_limit_exhausted(reloaded, AT + timedelta(hours=12, minutes=1))


def test_exhausted_is_false_not_an_error_on_unreadable_input():
    for state in [
        _state(),
        _state(rate_limit_paused_at="soon"),
        _state(rate_limit_paused_at=42),
        _state(rate_limit_paused_at="2026-11-20T12:00:00"),
    ]:
        assert rate_limit_exhausted(state, AT + timedelta(days=2)) is False
    aware = _state(rate_limit_paused_at=AT_S)
    assert rate_limit_exhausted(aware, datetime(2026, 11, 25)) is False


def test_the_persisted_patch_is_a_failure_class_with_one_other_entry():
    patch = rate_limit_persisted_patch(at=AT_S)
    assert patch["escalated"] == RATE_LIMIT_PERSISTED and patch["escalation_class"] == "failure"
    failures = [e for e in patch["pause_log"] if e["kind"] == "failure"]
    assert [f["value"] for f in failures] == ["other"]


def test_the_notice_is_owed_after_the_persisted_patch_until_it_is_noticed():
    state = _state(**rate_limit_persisted_patch(at=AT_S))
    assert rate_limit_notice_owed(state) is True
    assert rate_limit_notice_owed({**state, "rate_limit_noticed_at": AT_S}) is False
    assert rate_limit_notice_owed(_state(escalated="something else")) is False


def test_a_document_without_the_fields_renders_and_loads_as_before():
    base = {"id": "GH-9", "title": "t", "description": "d", "type": "feature", "sections": {}}
    old = document.render(base)
    assert "rate_limit_paused_at" not in old and "rate_limit_noticed_at" not in old
    assert document.render(document.parse(old) | {"sections": {}}) == old
    both = document.parse(
        document.render({**base, "rate_limit_paused_at": AT_S, "rate_limit_noticed_at": AT_S})
    )
    assert both["rate_limit_paused_at"] == AT_S and both["rate_limit_noticed_at"] == AT_S


def test_the_offset_is_deterministic_within_30_seconds_and_differs_per_product():
    offsets = {key: reset_offset(key) for key in ("alpha", "beta", "gamma", "delta")}
    assert all(timedelta(0) <= o <= timedelta(seconds=30) for o in offsets.values())
    assert reset_offset("alpha") == offsets["alpha"]
    assert len(set(offsets.values())) > 1


def test_the_constants_are_the_ones_rules_defines():
    assert rate_limit.GITHUB_RATE_LIMIT_SCHEDULE is rules.GITHUB_RATE_LIMIT_SCHEDULE == (60,) * 12
    assert RATE_LIMIT_WAIT_MAX == timedelta(hours=12)


def _imports_rate_limit(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[-1] == "rate_limit" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "rate_limit":
                return True
            if any(a.name == "rate_limit" for a in node.names):
                return True
    return False


def test_the_import_checker_finds_every_form_of_importing_rate_limit():
    assert _imports_rate_limit("from . import (\n    a,\n    rate_limit,\n)")
    assert _imports_rate_limit("from .rate_limit import reset_offset")
    assert _imports_rate_limit("import agentic.core.process.domain.rate_limit")
    assert not _imports_rate_limit("from . import a, b\nimport hashlib")


def test_rules_never_imports_rate_limit():
    source = Path(rules.__file__).read_text(encoding="utf-8")
    assert not _imports_rate_limit(source)


# --- GH-1032: the rules and the two policies the nodes call ------------------------------------

_LATER = (AT + timedelta(hours=2)).isoformat()


def _paused(**extra: Any) -> Any:
    """A state paused by the GitHub limit: the pause patch applied over an empty one."""
    state: Any = {"pause_log": [], **extra}
    return task.apply(state, rate_limit_pause_patch(state, resets_at=None, at=AT_S))


def test_a_github_rate_limit_epoch_is_an_external_pause():
    assert rules.pause_kind([_fail("github_rate_limit"), _esc()]) == "external"
    assert "github_rate_limit" in rules.EXTERNAL_REASONS


def test_a_paused_task_is_auto_resumable_as_the_github_key_and_needs_no_human():
    state = _paused()
    assert rules.auto_resumable(state) == "github_rate_limit"
    assert rules.needs_a_human(state) is False


def test_the_persisted_escalation_needs_a_human_even_though_the_key_reads_github():
    persisted = task.apply(_paused(), rate_limit_persisted_patch(at=_LATER))
    assert persisted["escalated"] == RATE_LIMIT_PERSISTED
    assert rules.needs_a_human(persisted) is True
    # Rejected input: the pause itself, written under the persisted text, is a human's.
    forged: Any = {**_paused(), "escalated": RATE_LIMIT_PERSISTED}
    assert rules.needs_a_human(forged) is True


def test_a_github_failure_mixed_with_an_own_failure_still_needs_a_human():
    state: Any = {
        "pause_log": [_fail("github_rate_limit"), _fail("sdk"), _esc()],
        "escalated": "x",
    }
    assert rules.auto_resumable(state) is None
    assert rules.needs_a_human(state) is True


def test_the_stated_reset_reads_a_github_failure_and_ignores_other_reasons():
    reset = AT + timedelta(minutes=30)
    github: Any = {
        "pause_log": [
            {**_fail("github_rate_limit"), "resets_at": reset.isoformat()},
            _esc(),
        ]
    }
    assert rules.stated_reset(github) == reset
    upstream: Any = {"pause_log": [{**_fail("upstream"), "resets_at": reset.isoformat()}, _esc()]}
    assert rules.stated_reset(upstream) is None
    account: Any = {
        "pause_log": [{**_fail("account_limit"), "resets_at": reset.isoformat()}, _esc()]
    }
    assert rules.stated_reset(account) == reset


def test_the_github_key_follows_its_own_minutely_calendar_of_twelve():
    assert rules.schedule_for("github_rate_limit") == rules.GITHUB_RATE_LIMIT_SCHEDULE
    assert rules.schedule_for("github_rate_limit", chase_prior=10_000) == (
        rules.GITHUB_RATE_LIMIT_SCHEDULE
    )
    assert rules.retake_calendar("github_rate_limit", 3, 0, 12) == (
        rules.GITHUB_RATE_LIMIT_SCHEDULE,
        3,
        12,
    )
    assert rules.auto_resume_limit({}, "github_rate_limit") == 12
    assert rules.auto_resume_limit({}, "github_rate_limit") != rules.MECHANICAL_RETAKE_MAX


def _candidate(**over: Any) -> rules.ResumeCandidate:
    fields: dict[str, Any] = {
        "issue": 1,
        "reason": "github_rate_limit",
        "since": AT,
        "prior": 0,
        "limit": 12,
        "chase_prior": 0,
        "retaken": False,
        "resets_at": AT + timedelta(minutes=30),
    }
    fields.update(over)
    return rules.ResumeCandidate(**fields)


def test_a_stated_github_reset_governs_the_first_retake_and_not_a_later_one():
    c = _candidate()
    assert rules.retake_due(c, AT + timedelta(minutes=29)) is None
    assert rules.retake_due(c, AT + timedelta(minutes=30)) == AT + timedelta(minutes=30)
    assert rules.resume_exhausted(c) is False
    later = _candidate(retaken=True, prior=1)
    assert rules.retake_due(later, AT) is None


def _resume(state: Any, *, by_poller: bool) -> Any:
    patch = rules.resumed(state, None, at=_LATER, by_poller=by_poller)
    return task.apply(state, patch)


def test_three_poller_resumes_keep_the_pause_field_and_the_retake_budget():
    state = _paused(rate_limit_noticed_at=AT_S, changes_requested={"a": 2})
    budget = state.get("changes_requested")
    for _ in range(3):
        state = _resume(state, by_poller=True)
        assert state["rate_limit_paused_at"] == AT_S
        assert state["rate_limit_noticed_at"] == AT_S
        assert state.get("changes_requested") == budget or state.get("changes_requested") == {}
        state = _paused_again(state)
        assert state["rate_limit_paused_at"] == AT_S
    assert len([e for e in state["pause_log"] if e["kind"] == "resumed"]) == 3
    assert "retakes" not in state


def _paused_again(state: Any) -> Any:
    return task.apply(state, rate_limit_pause_patch(state, resets_at=None, at=_LATER))


def test_a_manual_resume_removes_both_fields_from_the_saved_document():
    base = {"id": "GH-9", "title": "t", "description": "d", "type": "feature", "sections": {}}
    state = {**base, **_paused(rate_limit_noticed_at=AT_S)}
    state = _resume(state, by_poller=False)
    saved = document.render(state)
    assert "rate_limit_paused_at" not in saved and "rate_limit_noticed_at" not in saved
    reloaded = document.parse(saved)
    assert "rate_limit_paused_at" not in reloaded and "rate_limit_noticed_at" not in reloaded
    assert rate_limit_exhausted(state, AT + timedelta(days=2)) is False


def test_a_resume_that_is_not_the_polls_is_manual_by_default():
    state = _paused(rate_limit_noticed_at=AT_S)
    patch = rules.resumed(state, None, at=_LATER)
    assert "rate_limit_paused_at" in patch and "rate_limit_noticed_at" in patch
    assert not patch["rate_limit_paused_at"] and not patch["rate_limit_noticed_at"]
    kept = rules.resumed(state, None, at=_LATER, by_poller=True)
    assert "rate_limit_paused_at" not in kept and "rate_limit_noticed_at" not in kept
    # A task the limit never paused gets no new key on a manual resume.
    assert "rate_limit_paused_at" not in rules.resumed({"pause_log": []}, None, at=_LATER)


def test_tracker_silent_for_a_fresh_pause_and_for_the_persisted_escalation():
    assert rate_limit.tracker_silent(_paused()) is True
    persisted = task.apply(_paused(), rate_limit_persisted_patch(at=_LATER))
    assert rate_limit.tracker_silent(persisted) is True


def test_tracker_silent_is_false_for_an_account_limit_and_for_a_returns_or_questions_pause():
    account: Any = {
        "pause_log": [_fail("account_limit"), _esc()],
        "escalated": "limit",
    }
    assert rate_limit.tracker_silent(account) is False
    for cls in ("returns", "questions"):
        other: Any = {
            "pause_log": [_fail("github_rate_limit"), {"kind": "escalation", "value": cls}],
            "escalated": "needs you",
        }
        assert rate_limit.tracker_silent(other) is False, cls
    assert rate_limit.tracker_silent({"pause_log": []}) is False


def _closing_state(**extra: Any) -> Any:
    """What a node holds when it closes: the poller already retook the pause."""
    return _resume(_paused(**extra), by_poller=True)


def test_cleared_on_close_removes_both_fields_for_a_closing_patch_that_is_no_limit_pause():
    state = _closing_state(rate_limit_noticed_at=AT_S)
    patch = rate_limit.cleared_on_close(state, {"status": "ready_for_review"})
    assert patch["status"] == "ready_for_review"
    assert patch["rate_limit_paused_at"] is None and patch["rate_limit_noticed_at"] is None


def test_cleared_on_close_keeps_the_fields_for_a_new_limit_pause_and_the_persisted_one():
    state = _closing_state()
    again = rate_limit_pause_patch(state, resets_at=None, at=_LATER)
    assert rate_limit.cleared_on_close(state, again) == again
    persisted = rate_limit_persisted_patch(at=_LATER)
    assert rate_limit.cleared_on_close(state, persisted) == persisted
    # Rejected input: another class of escalation is no limit pause, so it clears.
    other = {**rules.escalation("returns", "needs you"), "pause_log": [_esc_of("returns")]}
    cleared = rate_limit.cleared_on_close(state, other)
    assert cleared["rate_limit_paused_at"] is None
    assert task.apply(state, cleared).get("rate_limit_paused_at") is None


def _esc_of(cls: str) -> Any:
    return {"kind": "escalation", "value": cls}


def test_cleared_on_close_is_a_no_op_without_the_field_and_never_writes_a_null_to_the_document():
    patch = {"status": "ready_for_review"}
    assert rate_limit.cleared_on_close({"pause_log": []}, patch) == patch
    base = {"id": "GH-9", "title": "t", "description": "d", "type": "feature", "sections": {}}
    state = {**base, **_closing_state(rate_limit_noticed_at=AT_S)}
    closed = rate_limit.cleared_on_close(state, {"status": "ready_for_review"})
    saved = document.render(task.apply(state, closed))
    assert "rate_limit_paused_at" not in saved and "rate_limit_noticed_at" not in saved
    assert not any(name.startswith("rate_limit") for name in document.parse(saved))


# -- the poller's side (GH-989 part 2): pure decisions over facts ----------------------------

SYSTEM = ("Escalated: ", "Resumed by the poller: ")


def _at(**delta: float) -> datetime:
    return AT + timedelta(**delta)


def test_pause_at_is_the_current_epochs_last_escalation_and_none_when_unreadable():
    log = [
        {"kind": "escalation", "value": "failure", "at": _at(hours=-5).isoformat()},
        {"kind": "resumed", "at": _at(hours=-4).isoformat()},
        {"kind": "failure", "value": "github_rate_limit", "section": ""},
        {"kind": "escalation", "value": "failure", "at": AT_S},
    ]
    assert rate_limit.pause_at(_state(log)) == AT
    assert rate_limit.pause_at(_state(log[:2])) is None
    assert rate_limit.pause_at(_state([{"kind": "escalation", "value": "failure"}])) is None
    for bad in ("not a date", "2026-11-20T12:00:00", 5):
        assert (
            rate_limit.pause_at(_state([{"kind": "escalation", "value": "x", "at": bad}])) is None
        )


def test_person_spoke_needs_a_non_system_comment_strictly_after_the_pause_and_the_last_system():
    spoke = rate_limit.person_spoke
    for seconds in (0, -1, -119, -121):
        assert not spoke([("hi", _at(seconds=seconds))], AT, SYSTEM)
    assert spoke([("hi", _at(seconds=1))], AT, SYSTEM)
    assert not spoke([("Resumed by the poller: x", _at(seconds=1))], AT, SYSTEM)
    after_system = [("hi", _at(seconds=5)), ("Resumed by the poller: x", _at(seconds=9))]
    assert not spoke(after_system, AT, SYSTEM)
    assert spoke([*after_system, ("hi again", _at(seconds=10))], AT, SYSTEM)


def test_poller_retakes_counts_only_prefixed_comments_strictly_after_the_pause():
    prefix = "Resumed by the poller: "
    comments = [
        (prefix + "a", _at(seconds=-1)),
        (prefix + "b", AT),
        (prefix + "c", _at(seconds=1)),
        ("Auto-resumed: d", _at(seconds=2)),
        (prefix + "e", _at(minutes=70)),
    ]
    assert rate_limit.poller_retakes(comments, AT, prefix) == 2


def test_retake_due_at_follows_the_stated_reset_once_then_the_hourly_calendar():
    due = rate_limit.retake_due_at
    reset = _at(minutes=20)
    target = reset + reset_offset("p")
    assert due(reset, AT, 0, "p", target - timedelta(seconds=1)) is None or reset_offset("p") == (
        timedelta(0)
    )
    assert due(reset, AT, 0, "p", target) == target
    # A retake already made: the reset no longer governs, the hourly slot does.
    assert due(reset, AT, 1, "p", _at(minutes=59)) is None
    assert due(reset, AT, 1, "p", _at(minutes=60)) == _at(minutes=60)
    assert due(None, AT, 0, "p", _at(minutes=60)) == _at(minutes=60)
    assert due(None, AT, 12, "p", _at(hours=99)) is None


def _paused_state(paused_at: datetime = AT) -> Any:
    return _state(
        [_fail("github_rate_limit"), _esc()],
        escalated="GitHub rate limit outlasted the wait",
        rate_limit_paused_at=paused_at.isoformat(),
    )


def test_persist_due_reads_the_document_alone_and_only_after_the_wait_max():
    state = _paused_state()
    assert not rate_limit.persist_due(state, AT + RATE_LIMIT_WAIT_MAX - timedelta(seconds=1))
    assert rate_limit.persist_due(state, AT + RATE_LIMIT_WAIT_MAX)
    assert not rate_limit.persist_due({**state, "escalated": ""}, AT + RATE_LIMIT_WAIT_MAX)
    other = _state([_fail("other"), _esc()], escalated="x", rate_limit_paused_at=AT_S)
    assert not rate_limit.persist_due(other, AT + RATE_LIMIT_WAIT_MAX)


def test_notice_posted_matches_the_heading_strictly_after_the_pause_only():
    heading = f"Escalated: {RATE_LIMIT_PERSISTED}"
    assert rate_limit.notice_posted([(heading, _at(seconds=1))], AT, heading)
    assert rate_limit.notice_posted([(heading + "\n\nmore", _at(seconds=1))], AT, heading)
    assert not rate_limit.notice_posted([(heading, _at(seconds=-1))], AT, heading)
    assert not rate_limit.notice_posted([(heading, AT)], AT, heading)
    assert not rate_limit.notice_posted([("Escalated: something else", _at(hours=1))], AT, heading)


def test_the_new_decisions_never_read_the_clock_tolerance():
    source = Path(rate_limit.__file__).read_text()
    assert "CLOCK_TOLERANCE" not in source


def test_governing_reset_adds_the_products_offset_once_and_needs_a_stated_reset():
    reset = _at(minutes=20)
    assert rate_limit.governing_reset(reset, "p") == reset + reset_offset("p")
    assert rate_limit.governing_reset(None, "p") is None
