import itertools
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain import notify, rules
from agentic.core.process.domain.notify import (
    DigestEvent,
    DigestKey,
    EventKey,
    NotifiedRecord,
    WaitingEvent,
)

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


def test_an_external_epoch_after_a_human_one_gives_nothing_not_a_superseded():
    state = _state(_human("2026-11-20T09:00:00+00:00"), _resumed(), *_external())
    assert _waiting(state) is None
    assert _waiting(state, issue_closed=True) is None


def test_an_external_epoch_then_a_human_epoch_gives_send():
    state = _state(*_external("2026-11-20T09:00:00+00:00"), _resumed(), _human())
    assert _waiting(state) == WaitingEvent(key=_key(), action="send")


def test_a_current_escalation_with_an_unreadable_at_looks_back_like_one_with_none():
    # Recorded choice: only rules.escalation_at can read `at`, so a corrupt one is "no escalation".
    state = _state(_human(), _resumed(), _human(None))
    assert _waiting(state) == WaitingEvent(key=_key(), action="superseded")


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


def test_notify_constants_are_the_agreed_ones():
    assert notify.SENDS_PER_ROUND == 5 and notify.DEFAULT_DIGEST_HOUR == 20


def test_touches_person_reads_either_flag_without_a_type_filter():
    assert rules.touches_person({"type": "bug", "flags": {"touches_ui": True}})
    assert rules.touches_person({"flags": {"touches_copy": True}})
    assert not rules.touches_person({"flags": {"touches_ui": False}})
    assert not rules.touches_person({})


CLOSED = datetime(2026, 11, 20, 10, tzinfo=UTC)
MERGED = datetime(2026, 11, 20, 9, tzinfo=UTC)


def _part(issue: Any, parent_of: Any, *, merged: Any = True, pr: Any = None) -> notify.PartFact:
    return notify.PartFact(issue, merged, pr, parent_of)


def _split(
    parts: tuple[notify.PartFact, ...] = (),
    *,
    parents: tuple[Any, ...] = (),
    closed: datetime | None = None,
) -> notify.SplitFacts:
    return notify.SplitFacts(parents, closed, parts)


def _release(
    state: Any,
    split: notify.SplitFacts | None,
    *,
    issue: int = 10,
    merged_at: datetime | None = None,
) -> notify.ReleaseEvent | None:
    return notify.release_event(state, "apostrophe", issue, split, auto_merged_at=merged_at)


def test_a_parent_with_three_merged_ui_parts_gives_one_release_and_three_covered():
    parts = (_part(13, 10, pr=3), _part(11, 10, pr=1), _part(12, 10, pr=2))
    root = _release({}, _split(parts, closed=CLOSED))
    assert root == notify.ReleaseEvent(
        EventKey("release", "apostrophe", 10, CLOSED),
        "send",
        (parts[1], parts[2], parts[0]),
    )
    for part in parts:
        covered = _release({}, _split(parents=(10,)), issue=part.issue, merged_at=MERGED)
        assert covered == notify.ReleaseEvent(
            EventKey("release", "apostrophe", part.issue, MERGED), "covered", ()
        )


def test_a_depth_two_tree_covers_leaf_and_intermediate_and_releases_the_root_once():
    parts = (_part(20, 10, merged=False), _part(21, 20), _part(22, 20), _part(23, 10))
    root = _release({}, _split(parts, closed=CLOSED))
    assert root is not None
    assert root.action == "send"
    assert root.key == EventKey("release", "apostrophe", 10, CLOSED)
    assert [(leaf.issue, leaf.parent_of) for leaf in root.leaves] == [(21, 20), (22, 20), (23, 10)]
    leaf = _release({}, _split(parents=(20, 10)), issue=21, merged_at=MERGED)
    assert leaf is not None and leaf.action == "covered" and leaf.leaves == ()
    inter = _release(
        {}, _split((_part(21, 20), _part(22, 20)), parents=(10,), closed=CLOSED), issue=20
    )
    assert inter == notify.ReleaseEvent(
        EventKey("release", "apostrophe", 20, CLOSED), "covered", ()
    )


def test_an_intermediate_whose_children_name_it_builds_and_is_covered():
    facts = _split((_part(21, 20),), parents=(10,), closed=CLOSED)
    event = _release({}, facts, issue=20)
    assert event is not None and event.action == "covered"


def test_a_merged_leaf_of_an_abandoned_parent_is_covered_and_the_root_gives_none():
    leaf = _release({}, _split(parents=(10,)), issue=11, merged_at=MERGED)
    assert leaf is not None and leaf.action == "covered"
    assert _release({}, _split((_part(11, 10),), closed=None)) is None


def test_a_part_with_no_instant_gives_none():
    assert _release({}, _split(parents=(10,)), issue=11) is None
    assert _release({}, _split((_part(21, 20),), parents=(10,)), issue=20) is None


def test_an_auto_merged_ui_task_without_parent_gives_a_release():
    event = _release({"flags": {"touches_ui": True}}, _split(), merged_at=MERGED)
    assert event == notify.ReleaseEvent(EventKey("release", "apostrophe", 10, MERGED), "send", ())
    bug = _release({"type": "bug", "flags": {"touches_copy": True}}, _split(), merged_at=MERGED)
    assert bug is not None and bug.action == "send"


def test_an_auto_merged_task_with_empty_flags_gives_none():
    assert _release({"flags": {}}, _split(), merged_at=MERGED) is None
    assert _release({}, _split(), merged_at=MERGED) is None


def test_a_ui_task_not_auto_merged_gives_none():
    assert _release({"flags": {"touches_ui": True}}, _split()) is None


def test_an_unreadable_split_gives_none_even_when_auto_merged():
    assert _release({"flags": {"touches_ui": True}}, None, merged_at=MERGED) is None


def test_an_unclosed_root_with_auto_merged_at_gives_none():
    root = _split((_part(11, 10),))
    assert _release({"flags": {"touches_ui": True}}, root, merged_at=MERGED) is None


def test_a_closed_root_with_no_merged_leaf_gives_none():
    assert _release({}, _split((_part(11, 10, merged=False),), closed=CLOSED)) is None


def test_parts_naming_a_third_issue_raise_in_release_event():
    with pytest.raises(ValueError, match="parts hang from 99"):
        _release({}, _split((_part(11, 99),), closed=CLOSED), issue=10)


def test_a_root_is_not_filtered_by_the_flags_of_its_leaves():
    assert _release({"flags": {}}, _split((_part(11, 10),), closed=CLOSED)) is not None


_PLUS_ONE = timezone(timedelta(hours=1))


@pytest.mark.parametrize(
    "build",
    [
        lambda: _split((_part(11, 11),)),
        lambda: _split((_part(11, 12), _part(12, 11))),
        lambda: _split((_part(11, 10), _part(12, 99))),
        lambda: _split((_part(11, 10), _part(12, 11), _part(13, 12), _part(14, 13))),
        lambda: _split((_part(11, 10), _part(11, 10))),
        lambda: _split(parents=(1, 2, 3)),
        lambda: _split(parents=(0,)),
        lambda: _split(parents=(True,)),
        lambda: _split(closed=CLOSED),
        lambda: _split(parents=(10,), closed=datetime(2026, 11, 20, 10)),
        lambda: _split(parents=(10,), closed=datetime(2026, 11, 20, 10, tzinfo=_PLUS_ONE)),
        lambda: _part(0, 10),
        lambda: _part(True, 10),
        lambda: _part(11, -1),
        lambda: _part(11, 10, pr=0),
        lambda: _part(11, 10, pr=True),
        lambda: _part(11, 10, merged=1),
    ],
)
def test_construction_rejects_an_incoherent_value(build: Any):
    with pytest.raises(ValueError):
        build()


def test_a_tree_two_levels_below_the_task_is_accepted():
    assert _split((_part(11, 10), _part(12, 11)))


def test_pending_covers_a_due_unrecorded_release_and_nothing_else():
    event = notify.ReleaseEvent(EventKey("release", "apostrophe", 10, CLOSED), "covered", ())
    assert notify.pending(event, ACTIVE, NOW) == "covered"
    recorded = replace(ACTIVE, keys={event.key: "covered"})
    assert notify.pending(event, recorded, NOW) is None
    old = notify.ReleaseEvent(
        EventKey("release", "apostrophe", 10, NOW - timedelta(days=8)), "send", ()
    )
    assert notify.pending(old, ACTIVE, NOW) is None
    assert notify.pending(event, EMPTY, NOW) is None


def _at(minute: int) -> datetime:
    return datetime(2026, 11, 20, 10, tzinfo=UTC) + timedelta(minutes=minute)


def _row(minute: int, keyed: bool = True) -> tuple[datetime, EventKey | None]:
    return _at(minute), (_key(_at(minute)) if keyed else None)


def test_a_failed_candidate_in_the_middle_stops_just_before_it():
    rows = [_row(1), _row(2), _row(3, keyed=False), _row(4)]
    assert notify.advance_read_at(_at(0), rows, cut_at=None) == _at(2)


def test_the_first_row_unkeyed_returns_read_at():
    assert notify.advance_read_at(_at(0), [_row(1, keyed=False), _row(2)], cut_at=None) == _at(0)


def test_no_read_at_with_every_row_keyed_returns_the_greatest():
    assert notify.advance_read_at(None, [_row(3), _row(1), _row(2)], cut_at=None) == _at(3)


def test_a_tie_exactly_at_the_cut_drops_the_whole_group():
    rows = [_row(1), _row(2), _row(3), _row(3)]
    assert notify.advance_read_at(_at(0), rows, cut_at=_at(3)) == _at(2)


def test_a_cut_after_fifty_mixed_rows_advances_to_the_last_kept_row():
    rows = [_row(minute) for minute in range(1, 49)] + [_row(50), _row(50)]
    assert len(rows) == 50
    assert notify.advance_read_at(None, rows, cut_at=_at(50)) == _at(48)


def test_a_single_group_larger_than_the_limit_is_kept_and_advances_to_the_cut():
    assert notify.advance_read_at(_at(0), [_row(5)] * 50, cut_at=_at(5)) == _at(5)


def test_a_single_group_with_one_unkeyed_row_does_not_advance():
    rows = [_row(5)] * 49 + [_row(5, keyed=False)]
    assert notify.advance_read_at(_at(0), rows, cut_at=_at(5)) == _at(0)


def test_only_stale_rows_with_a_cut_return_read_at():
    assert notify.advance_read_at(_at(5), [_row(1), _row(5)], cut_at=_at(9)) == _at(5)


@pytest.mark.parametrize(
    ("read_at", "rows", "cut_at"),
    [
        (_at(5), [_row(1)], _at(5)),
        (_at(0), [_row(1), _row(4)], _at(3)),
        (_at(0), [(datetime(2026, 11, 20, 10, 1), None)], None),
        (datetime(2026, 11, 20, 10), [], None),
        (None, [(_at(1).astimezone(timezone(timedelta(hours=2))), None)], None),
        (None, [_row(1)], datetime(2026, 11, 20, 10, 1)),
    ],
)
def test_an_invalid_input_raises(read_at: Any, rows: Any, cut_at: Any):
    with pytest.raises(ValueError):
        notify.advance_read_at(read_at, rows, cut_at=cut_at)


def _considered(read_at: datetime | None, rows: list[Any], cut_at: datetime | None) -> list[Any]:
    live = [(i, k) for i, k in rows if read_at is None or i > read_at]
    if cut_at is not None and any(i != cut_at for i, _ in live):
        live = [(i, k) for i, k in live if i < cut_at]
    return live


def _oracle(read_at: datetime | None, rows: list[Any], cut_at: datetime | None) -> datetime | None:
    live = _considered(read_at, rows, cut_at)
    best = read_at
    for candidate, _ in live:
        if all(k is not None for i, k in live if i <= candidate):
            best = candidate if best is None else max(best, candidate)
    return best


def test_every_small_input_matches_the_oracle_in_any_order():
    pool = [_at(1), _at(2), _at(3)]
    cells = [(instant, keyed) for instant in pool for keyed in (True, False)]
    for size in range(4):
        for combo in itertools.combinations_with_replacement(cells, size):
            rows = [(i, _key(i) if keyed else None) for i, keyed in combo]
            for read_at, cut_at in itertools.product([None, pool[0]], [None, *pool]):
                live = [i for i, _ in rows if read_at is None or i > read_at]
                if cut_at is not None and (
                    (read_at is not None and cut_at <= read_at) or any(cut_at < i for i in live)
                ):
                    with pytest.raises(ValueError):
                        notify.advance_read_at(read_at, rows, cut_at=cut_at)
                    continue
                got = notify.advance_read_at(read_at, rows, cut_at=cut_at)
                assert got == _oracle(read_at, rows, cut_at)
                assert read_at is None or (got is not None and got >= read_at)
                assert got in [read_at, *(i for i, _ in rows)]
                unkeyed = [i for i, k in _considered(read_at, rows, cut_at) if k is None]
                assert all(got is None or got < i for i in unkeyed)
                for order in itertools.permutations(rows):
                    assert notify.advance_read_at(read_at, list(order), cut_at=cut_at) == got


NY = ZoneInfo("America/New_York")


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _started(at: datetime) -> NotifiedRecord:
    return NotifiedRecord(started_at=at, failed_at=None, activity_read_at=None, keys={})


def _digest(now: datetime, record: NotifiedRecord, hour: int = 20) -> DigestEvent | None:
    return notify.digest_event(now, NY, hour, record, "apostrophe")


def test_an_activation_at_21_local_with_hour_20_gives_no_digest():
    # Replaces the GH-1056 invariant "never due on the local date of started_at": due() alone
    # keeps an instant at or before the activation from being owed.
    started = datetime(2026, 11, 20, 21, tzinfo=NY).astimezone(UTC)
    record = _started(started)
    assert _digest(started + timedelta(minutes=1), record) is None
    assert _digest(started + timedelta(hours=2), record) is None


def test_an_activation_at_19_local_gives_a_digest_due_at_20_local_the_same_day():
    # Replaces the GH-1056 invariant "never due on the local date of started_at".
    started = datetime(2026, 11, 20, 19, tzinfo=NY).astimezone(UTC)
    record = _started(started)
    assert _digest(started + timedelta(minutes=59), record) is None
    event = _digest(started + timedelta(hours=1), record)
    assert event == DigestEvent(DigestKey("apostrophe", date(2026, 11, 20)), _utc(2026, 11, 21, 1))


def test_spring_forward_reads_the_missing_hour_as_the_pre_transition_instant():
    record = _started(_utc(2026, 3, 1, 0))
    assert _digest(_utc(2026, 3, 8, 6, 59), record, hour=2) is None
    event = _digest(_utc(2026, 3, 8, 7), record, hour=2)
    assert event == DigestEvent(DigestKey("apostrophe", date(2026, 3, 8)), _utc(2026, 3, 8, 7))


def test_fall_back_uses_the_first_occurrence_and_not_the_second_one_again():
    record = _started(_utc(2026, 10, 25, 0))
    key = DigestKey("apostrophe", date(2026, 11, 1))
    assert _digest(_utc(2026, 11, 1, 4, 59), record, hour=1) is None
    assert _digest(_utc(2026, 11, 1, 5), record, hour=1) == DigestEvent(key, _utc(2026, 11, 1, 5))
    sent = replace(record, keys={key: "sent"})
    assert _digest(_utc(2026, 11, 1, 6), sent, hour=1) is None


@pytest.mark.parametrize("outcome", ["sent", "covered", "superseded", "unbuildable"])
def test_a_second_digest_for_the_same_local_date_is_not_due(outcome: Any):
    record = _started(_utc(2026, 11, 1, 0))
    key = DigestKey("apostrophe", date(2026, 11, 20))
    assert _digest(_utc(2026, 11, 21, 2), record) is not None
    assert _digest(_utc(2026, 11, 21, 2), replace(record, keys={key: outcome})) is None


def test_a_digest_of_another_product_or_date_does_not_settle_this_one():
    record = _started(_utc(2026, 11, 1, 0))
    keys: Any = {
        DigestKey("other", date(2026, 11, 20)): "sent",
        DigestKey("apostrophe", date(2026, 11, 19)): "sent",
    }
    assert _digest(_utc(2026, 11, 21, 2), replace(record, keys=keys)) is not None


def test_without_an_activation_no_digest_is_due():
    assert _digest(_utc(2026, 11, 21, 2), EMPTY) is None


def test_only_the_local_date_of_now_is_considered_so_yesterdays_digest_is_lost():
    record = _started(_utc(2026, 11, 1, 0))
    now = datetime(2026, 11, 21, 0, 10, tzinfo=NY).astimezone(UTC)
    assert _digest(now, record) is None
    event = _digest(now, record, hour=0)
    assert event is not None and event.key.local_date == date(2026, 11, 21)


def test_the_key_date_is_the_local_date_of_now_not_that_of_the_instant():
    record = _started(_utc(2026, 11, 1, 0))
    now = datetime(2026, 11, 20, 23, 30, tzinfo=NY).astimezone(UTC)
    event = _digest(now, record, hour=23)
    assert event is not None
    assert event.key.local_date == date(2026, 11, 20)
    assert event.instant == datetime(2026, 11, 20, 23, tzinfo=NY).astimezone(UTC)
    assert event.instant.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("now", "hour"),
    [
        (datetime(2026, 11, 21, 2), 20),
        (datetime(2026, 11, 21, 2, tzinfo=NY), 20),
        (datetime(2026, 11, 21, 3, tzinfo=timezone(timedelta(hours=1))), 20),
        (_utc(2026, 11, 21, 2), 24),
        (_utc(2026, 11, 21, 2), -1),
    ],
)
def test_digest_event_rejects_a_naive_or_non_utc_now_and_an_hour_out_of_range(
    now: datetime, hour: int
):
    with pytest.raises(ValueError):
        _digest(now, _started(_utc(2026, 11, 1, 0)), hour=hour)


@pytest.mark.parametrize("raw", [0, "0", 23, "23", "7", 7])
def test_digest_hour_reads_a_valid_hour(raw: object):
    assert notify.digest_hour(raw) == int(str(raw))


@pytest.mark.parametrize(
    "raw", [True, False, 20.5, "20.5", " 20 ", "24", -1, 24, "", None, "+5", "٣"]
)
def test_digest_hour_reads_anything_else_as_the_default(raw: object):
    assert notify.digest_hour(raw) == 20


DIGEST_KEY = DigestKey("apostrophe", date(2026, 11, 20))
DIGEST = DigestEvent(DIGEST_KEY, _utc(2026, 11, 20, 20))
RELEASE = EventKey("release", "apostrophe", 9, AT_UTC)


def _settle(
    keys: dict[Any, Any],
    *,
    instant: datetime = _utc(2026, 11, 20, 19),
    digest: DigestEvent | None = None,
    release: EventKey | None = None,
    judged: bool = False,
) -> Any:
    return notify.row_settlement(
        replace(ACTIVE, keys=keys), instant=instant, digest=digest, release=release, judged=judged
    )


def test_a_digest_sent_settles_the_rows_at_or_before_its_instant_by_its_key():
    keys = {DIGEST_KEY: "sent"}
    assert _settle(keys, digest=DIGEST) == DIGEST_KEY
    assert _settle(keys, digest=DIGEST, instant=DIGEST.instant) == DIGEST_KEY


def test_a_digest_leaves_a_row_after_its_instant_or_with_the_key_not_sent_unsettled():
    assert _settle({DIGEST_KEY: "sent"}, digest=DIGEST, instant=_utc(2026, 11, 20, 21)) is None
    assert _settle({}, digest=DIGEST) is None
    assert _settle({DIGEST_KEY: "covered"}, digest=DIGEST) is None


def test_a_digest_on_ignores_the_release_and_the_judgment():
    keys: dict[Any, Any] = {RELEASE: "sent"}
    assert _settle(keys, digest=DIGEST, release=RELEASE, judged=True) is None


@pytest.mark.parametrize("outcome", ["sent", "superseded", "covered", "unbuildable"])
def test_a_recorded_release_settles_its_row_under_any_outcome(outcome: Any):
    assert _settle({RELEASE: outcome}, release=RELEASE, judged=True) == RELEASE


def test_an_unrecorded_release_is_judged_or_unsettled():
    assert _settle({}, release=RELEASE, judged=True) is notify.Judged.JUDGED
    assert _settle({}, judged=True) is notify.Judged.JUDGED
    assert _settle({}, release=RELEASE) is None
    assert _settle({}) is None


def test_the_cursor_advances_past_judged_rows_and_stops_at_the_first_unsettled_one():
    rows: list[tuple[datetime, Any]] = [
        (_at(1), notify.Judged.JUDGED),
        (_at(2), _key(_at(2))),
        (_at(3), None),
        (_at(4), notify.Judged.JUDGED),
    ]
    assert notify.advance_read_at(None, rows, cut_at=None) == _at(2)
    assert notify.advance_read_at(None, [rows[0], rows[3]], cut_at=None) == _at(4)


@pytest.mark.parametrize(
    "now",
    [_utc(2026, 3, 8, 6), _utc(2026, 3, 8, 8), _utc(2026, 11, 1, 3), _utc(2026, 11, 1, 6)],
)
def test_digest_instant_equals_the_instant_of_digest_event_across_a_dst_change(now: datetime):
    record = _started(_utc(2026, 1, 1, 0))
    for hour in (1, 2, 20):
        expected = notify.digest_instant(now, NY, hour)
        event = _digest(now, record, hour=hour)
        assert event is None or event.instant == expected
        assert expected.utcoffset() == timedelta(0)


def test_digest_instant_reads_the_spring_forward_gap_and_rejects_bad_input():
    assert notify.digest_instant(_utc(2026, 3, 8, 8), NY, 2) == _utc(2026, 3, 8, 7)
    with pytest.raises(ValueError):
        notify.digest_instant(datetime(2026, 11, 21, 2), NY, 20)
    with pytest.raises(ValueError):
        notify.digest_instant(_utc(2026, 11, 21, 2), NY, 24)
