"""`rate_limit`: the GitHub rate limit as a pause of its own (GH-989 part 1)."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentic.core.process.domain import document, rate_limit, rules
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
