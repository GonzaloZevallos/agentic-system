from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain import notify, rules
from agentic.core.process.domain.notify import DigestKey, EventKey, NotifiedRecord, WaitingEvent

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
AT = "2026-11-20T11:00:00+00:00"
AT_UTC = datetime(2026, 11, 20, 11, tzinfo=UTC)
ACTIVE = NotifiedRecord(
    started_at=NOW - timedelta(days=30), failed_at=None, activity_read_at=None, keys={}
)
EMPTY = NotifiedRecord(started_at=None, failed_at=None, activity_read_at=None, keys={})


def _human(at: str | None = AT) -> Any:
    return rules.escalation_entry("questions", at)


def _external(at: str | None = AT) -> list[Any]:
    failure: Any = {"kind": "failure", "value": "account_limit", "section": "Developer: api"}
    return [failure, rules.escalation_entry("failure", at)]


def _exhausted_format(at: str | None = AT) -> list[Any]:
    failure: Any = {
        "kind": "failure",
        "value": "schema",
        "section": "Developer: api",
        "rule": "preview_slice_missing",
    }
    return [failure, rules.escalation_entry("failure", at)]


def _state(*log: Any) -> Any:
    return {"pause_log": list(log)}


def _resumed() -> Any:
    return rules.resumed_entry(at="2026-11-20T11:30:00+00:00")


def _lapse() -> Any:
    return {"kind": "lapse", "value": "a bookkeeping step failed"}


def _waiting(state: Any, *, issue_closed: bool = False) -> WaitingEvent | None:
    return notify.waiting_event(state, "apostrophe", 7, issue_closed=issue_closed)


def _key(instant: datetime = AT_UTC) -> EventKey:
    return EventKey("waiting", "apostrophe", 7, instant)


def test_a_human_escalation_is_a_waiting_send_keyed_by_its_at():
    event = _waiting(_state(_human()))
    assert event == WaitingEvent(key=_key(), action="send")
    assert notify.pending(event, ACTIVE, NOW) == "send"


def test_a_closed_document_supersedes_the_waiting_event():
    event = _waiting(_state(_human()), issue_closed=True)
    assert event is not None and event.action == "superseded"
    assert notify.pending(event, ACTIVE, NOW) == "superseded"


def test_a_resumed_pause_is_superseded_even_after_a_lapse_only_epoch():
    resumed = _waiting(_state(_human(), _resumed()))
    assert resumed == WaitingEvent(key=_key(), action="superseded")
    lapse_only = _waiting(_state(_human(), _resumed(), _lapse()))
    assert lapse_only == WaitingEvent(key=_key(), action="superseded")


def test_the_lookback_is_one_epoch_and_a_second_resume_gives_nothing():
    assert _waiting(_state(_human(), _resumed(), _resumed())) is None


def test_an_external_epoch_gives_nothing_and_so_does_an_exhausted_format_one():
    assert _waiting(_state(*_external())) is None
    assert _waiting(_state(*_exhausted_format())) is None
    assert _waiting(_state(*_external(), _resumed())) is None


def test_an_external_epoch_then_a_human_epoch_gives_send():
    state = _state(*_external("2026-11-20T09:00:00+00:00"), _resumed(), _human())
    assert _waiting(state) == WaitingEvent(key=_key(), action="send")


def test_an_empty_epoch_or_an_escalation_without_a_readable_at_gives_nothing():
    assert _waiting(_state()) is None
    assert _waiting({}) is None
    assert _waiting(_state(_human(None))) is None
    assert _waiting(_state(_human("2026-11-20T11:00:00"))) is None


def test_the_same_instant_in_two_offsets_gives_equal_keys():
    local = _waiting(_state(_human("2026-11-20T08:00:00-03:00")))
    zulu = _waiting(_state(_human("2026-11-20T11:00:00Z")))
    assert local is not None and zulu is not None
    assert local.key == zulu.key
    assert local.key.instant.utcoffset() == timedelta(0)


def test_ten_old_escalations_with_a_fresh_started_at_give_zero_pending():
    record = replace(EMPTY, started_at=NOW - timedelta(hours=1))
    pending = []
    for day in range(1, 11):
        at = (NOW - timedelta(days=day)).isoformat()
        event = notify.waiting_event(_state(_human(at)), "apostrophe", day, issue_closed=False)
        pending.append(notify.pending(event, record, NOW) if event else None)
    assert pending == [None] * 10


def test_a_missing_started_at_gives_zero_due_and_zero_pending():
    event = _waiting(_state(_human()))
    assert event is not None
    assert notify.due(NOW - timedelta(minutes=1), EMPTY, NOW) is False
    assert notify.pending(event, EMPTY, NOW) is None


def test_start_stamps_only_when_missing():
    started = notify.start(EMPTY, NOW)
    assert started.started_at == NOW
    assert notify.start(started, NOW + timedelta(days=1)) is started
    assert notify.start(ACTIVE, NOW) is ACTIVE


def test_a_recorded_key_is_never_pending_again():
    superseded = _waiting(_state(_human()), issue_closed=True)
    assert superseded is not None
    record = replace(ACTIVE, keys={superseded.key: "superseded"})
    reopened = _waiting(_state(_human()), issue_closed=False)
    assert reopened is not None and reopened.key == superseded.key
    assert notify.pending(reopened, record, NOW) is None
    assert notify.pending(superseded, record, NOW) is None


def test_a_later_escalation_is_a_new_key_even_when_an_earlier_one_is_recorded():
    record = replace(ACTIVE, keys={_key(): "sent"})
    later = "2026-11-20T11:30:00+00:00"
    event = _waiting(_state(_human(), _resumed(), _human(later)))
    assert event is not None and event.key != _key()
    assert notify.pending(event, record, NOW) == "send"


def test_due_and_prune_share_the_cutoff_boundary():
    cutoff = NOW - notify.NOTIFY_HORIZON
    started = replace(ACTIVE, started_at=cutoff - timedelta(days=1))
    second = timedelta(seconds=1)
    assert notify.due(cutoff - second, started, NOW) is False
    assert notify.due(cutoff, started, NOW) is False
    assert notify.due(cutoff + second, started, NOW) is True
    keys: dict[Any, Any] = {_key(cutoff + off): "sent" for off in (-second, timedelta(0), second)}
    pruned, dropped = notify.prune(replace(ACTIVE, keys=keys), NOW, UTC)
    assert dropped == 1
    assert sorted(k.instant for k in pruned.keys if isinstance(k, EventKey)) == [
        cutoff,
        cutoff + second,
    ]


def test_due_never_reaches_before_started_at():
    started = replace(ACTIVE, started_at=NOW - timedelta(hours=2))
    assert notify.due(NOW - timedelta(hours=3), started, NOW) is False
    assert notify.due(NOW - timedelta(hours=2), started, NOW) is False
    assert notify.due(NOW - timedelta(hours=1), started, NOW) is True


def test_prune_keeps_a_digest_on_the_cutoff_date_and_drops_the_day_before():
    cutoff_date = (NOW - notify.NOTIFY_HORIZON).date()
    keys: dict[Any, Any] = {
        DigestKey("apostrophe", cutoff_date - timedelta(days=1)): "sent",
        DigestKey("apostrophe", cutoff_date): "sent",
    }
    pruned, dropped = notify.prune(replace(ACTIVE, keys=keys), NOW, UTC)
    assert dropped == 1
    assert list(pruned.keys) == [DigestKey("apostrophe", cutoff_date)]
    assert pruned.started_at == ACTIVE.started_at


def test_prune_reads_the_local_date_of_the_cutoff_on_a_dst_change_day():
    # 2026-11-01 is the New York fall-back day. The cutoff is 2026-11-01T02:30Z, which is
    # 2026-10-31 22:30 in New York (EDT): the local date is the 31st, the UTC date the 1st.
    now = datetime(2026, 11, 8, 2, 30, tzinfo=UTC)
    new_york = ZoneInfo("America/New_York")
    keys: dict[Any, Any] = {
        DigestKey("apostrophe", date(2026, 10, 30)): "sent",
        DigestKey("apostrophe", date(2026, 10, 31)): "sent",
    }
    pruned, dropped = notify.prune(replace(ACTIVE, keys=keys), now, new_york)
    assert dropped == 1
    assert list(pruned.keys) == [DigestKey("apostrophe", date(2026, 10, 31))]
    _, dropped_utc = notify.prune(replace(ACTIVE, keys=keys), now, UTC)
    assert dropped_utc == 2


def test_retry_due_without_failed_at_before_and_at_the_retry_interval():
    assert notify.retry_due(ACTIVE, NOW) is True
    failed = replace(ACTIVE, failed_at=NOW - timedelta(minutes=14, seconds=59))
    assert notify.retry_due(failed, NOW) is False
    assert notify.retry_due(replace(failed, failed_at=NOW - notify.NOTIFY_RETRY_AFTER), NOW)


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 11, 20, 11),
        datetime(2026, 11, 20, 8, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
def test_an_event_key_rejects_a_naive_or_offset_instant(instant: datetime):
    with pytest.raises(ValueError):
        EventKey("waiting", "apostrophe", 7, instant)


@pytest.mark.parametrize(
    "kind, product, issue",
    [
        ("waiting", "apostrophe", 0),
        ("waiting", "apostrophe", -1),
        ("waiting", "apostrophe", True),
        ("digest", "apostrophe", 7),
        ("waiting", "", 7),
        ("waiting", "a\nb", 7),
        ("release", "Apostrophe", 7),
    ],
)
def test_an_event_key_rejects_a_bad_issue_kind_or_product(kind: Any, product: str, issue: Any):
    with pytest.raises(ValueError):
        EventKey(kind, product, issue, AT_UTC)


@pytest.mark.parametrize("local_date", [datetime(2026, 11, 20), datetime(2026, 11, 20, tzinfo=UTC)])
def test_a_digest_key_rejects_a_datetime_as_its_date(local_date: Any):
    with pytest.raises(ValueError):
        DigestKey("apostrophe", local_date)


@pytest.mark.parametrize("product", ["", "a\nb", "A", "a_b", "-a"])
def test_a_digest_key_rejects_a_product_that_is_not_a_slug(product: str):
    with pytest.raises(ValueError):
        DigestKey(product, date(2026, 11, 20))


def test_valid_keys_are_accepted_unchanged():
    assert EventKey("release", "apostrophe", 1, AT_UTC).instant == AT_UTC
    assert DigestKey("agentic-system", date(2026, 11, 20)).local_date == date(2026, 11, 20)


def test_the_slug_pattern_restates_the_knowledge_one():
    assert notify._PRODUCT_SLUG.pattern == stack.SLUG.pattern


def test_notify_defines_no_digest_computation_and_is_inert():
    assert not hasattr(notify, "digest_hour") and not hasattr(notify, "digest_event")
    assert notify.SENDS_PER_ROUND == 5 and notify.DEFAULT_DIGEST_HOUR == 20
