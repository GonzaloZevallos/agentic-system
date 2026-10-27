"""The scratch domain: where an owner's temporary directory lives under the product root,
and the record that says who owns it. Pure (GH-391)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

import pytest

from agentic.core.process.domain import scratch


def test_a_checks_directory_is_named_by_task_kind_and_the_first_eight_of_the_token():
    owner = scratch.owner("c", "0123456789abcdef")
    assert scratch.path_for("GH-391", owner) == PurePosixPath(".agentic/tmp/GH-391/c-01234567")
    assert scratch.record_path_for("GH-391", owner) == PurePosixPath(
        ".agentic/tmp/GH-391/c-01234567.json"
    )


def test_a_role_attempt_is_kind_r_and_no_task_is_the_untasked_key():
    owner = scratch.owner("r", "abcdef0123456789")
    assert scratch.path_for(None, owner) == PurePosixPath(".agentic/tmp/_untasked/r-abcdef01")
    assert scratch.UNTASKED == "_untasked"


def test_the_part_below_the_root_is_at_most_forty_characters_for_a_sixteen_character_key():
    key = "k" * 16
    for kind in ("c", "r"):
        relative = str(scratch.path_for(key, scratch.owner(kind, "f" * 32)))
        assert not relative.endswith("/")
        assert len(relative) <= 40


def test_the_kept_folder_of_a_task_is_under_dot_agentic_kept():
    assert scratch.kept_path_for("GH-391") == PurePosixPath(".agentic/kept/GH-391")
    assert scratch.kept_path_for(None) == PurePosixPath(".agentic/kept/_untasked")


@pytest.mark.parametrize("key", ["", "a/b", "a\\b", "..", "../x", "x/.."])
def test_a_task_key_that_could_leave_the_scratch_root_is_refused(key):
    with pytest.raises(ValueError, match="task key"):
        scratch.path_for(key, scratch.owner("c", "0" * 32))
    with pytest.raises(ValueError, match="task key"):
        scratch.kept_path_for(key)


@pytest.mark.parametrize("token", ["", "../etc", "ab/cd", "x y"])
def test_a_token_that_is_not_a_plain_word_is_refused(token):
    with pytest.raises(ValueError, match="token"):
        scratch.owner("c", token)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        scratch.owner("x", "0" * 32)  # type: ignore[arg-type]


def test_an_owner_record_round_trips_through_json():
    record = scratch.OwnerRecord(
        pid=42,
        boot_id="boot-a",
        slug="developer",
        kind="r",
        created_at=datetime(2026, 10, 27, 12, 0, tzinfo=UTC),
    )
    assert scratch.OwnerRecord.from_json(record.to_json()) == record
    unknown_boot = scratch.OwnerRecord(42, None, "check", "c", record.created_at)
    assert scratch.OwnerRecord.from_json(unknown_boot.to_json()) == unknown_boot


def _record_json(**changes: object) -> str:
    fields: dict[str, object] = {
        "pid": 1,
        "boot_id": None,
        "slug": "s",
        "kind": "c",
        "created_at": "2026-10-27T00:00:00+00:00",
    }
    return json.dumps({**fields, **changes})


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        _record_json(pid="x"),
        _record_json(kind="z"),
        _record_json(created_at="yesterday"),
        '{"pid": 1}',
    ],
)
def test_an_unreadable_record_is_none_and_never_raises(text):
    assert scratch.OwnerRecord.from_json(text) is None


def test_the_declared_constants():
    assert scratch.SCRATCH_ORPHAN_GRACE == timedelta(minutes=10)
    assert scratch.KEPT_RETENTION == timedelta(days=14)
    assert scratch.INODE_FLOOR == 10_000


def test_a_space_reading_is_frozen():
    reading = scratch.SpaceReading(free_bytes=5, free_inodes=7)
    with pytest.raises(AttributeError):
        reading.free_bytes = 1  # type: ignore[misc]


NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
GRACE = scratch.SCRATCH_ORPHAN_GRACE


def _entry(name="c-aaaaaaaa", task="GH-1", directory_age=None, record=None):
    mtime = None if directory_age is None else NOW - directory_age
    return scratch.OwnerEntry(task, name, mtime, record)


def _record(alive, age=timedelta(0)):
    return scratch.RecordFact(NOW - age, alive)


def _decide(*entries, tasks=("GH-1",), closed=(), kept=()):
    listing = scratch.ScratchListing(tuple(tasks), tuple(entries))
    return scratch.sweep_decision(listing, set(closed), list(kept), NOW)


def test_the_whole_folder_of_a_closed_task_goes_and_an_open_one_stays():
    decision = _decide(
        _entry(task="GH-1", directory_age=timedelta(0), record=_record(True)),
        _entry(task="GH-2", directory_age=timedelta(0), record=_record(True)),
        tasks=("GH-1", "GH-2", "GH-3"),
        closed={"GH-1", "GH-3"},
    )
    assert decision.tasks == ("GH-1", "GH-3")
    assert decision.owners == ()


def test_a_dead_owner_loses_its_directory_and_its_record_an_alive_one_keeps_both():
    dead = _entry("c-dead0000", directory_age=timedelta(0), record=_record(False))
    alive = _entry("c-live0000", directory_age=timedelta(days=3), record=_record(True))
    assert _decide(dead, alive).owners == (scratch.Removal("GH-1", "c-dead0000", True, True),)


def test_a_dead_record_with_no_directory_is_removed_alone():
    entry = _entry("r-gone0000", record=_record(False))
    assert _decide(entry).owners == (scratch.Removal("GH-1", "r-gone0000", False, True),)


def test_an_orphan_directory_is_kept_inside_the_grace_and_removed_after_it():
    young = _entry("c-young000", directory_age=GRACE)
    old = _entry("c-old00000", directory_age=GRACE + timedelta(seconds=1))
    assert _decide(young, old).owners == (scratch.Removal("GH-1", "c-old00000", True, False),)


def test_a_directory_recreated_after_its_record_was_removed_is_an_orphan_once_old():
    # The record is gone (the owner removed it last) and a late writer made the directory
    # again: nothing names an owner, so only the age decides.
    recreated = _entry("c-again000", directory_age=timedelta(hours=1), record=None)
    assert _decide(recreated).owners == (scratch.Removal("GH-1", "c-again000", True, False),)


def test_an_unreadable_record_counts_as_alive_when_young_and_an_orphan_when_old():
    young = _entry("c-young000", directory_age=timedelta(0), record=_record(None, GRACE))
    old = _entry("c-old00000", directory_age=timedelta(0), record=_record(None, GRACE * 2))
    assert _decide(young, old).owners == (scratch.Removal("GH-1", "c-old00000", True, True),)


def test_a_kept_folder_goes_only_once_its_task_closed_longer_ago_than_the_retention():
    kept = [
        scratch.KeptFolder("GH-1", NOW - scratch.KEPT_RETENTION),
        scratch.KeptFolder("GH-2", NOW - scratch.KEPT_RETENTION + timedelta(seconds=1)),
        scratch.KeptFolder("GH-3", None),
    ]
    assert _decide(kept=kept).kept == ("GH-1",)


def test_nothing_to_remove_is_a_falsy_decision():
    assert not _decide(_entry(directory_age=timedelta(0), record=_record(True)))
    assert _decide(_entry(directory_age=timedelta(days=1)))


def test_a_task_key_that_could_name_two_directories_has_no_folder():
    assert scratch.task_path_for("GH-9") == PurePosixPath(".agentic/tmp/GH-9")
    with pytest.raises(ValueError):
        scratch.task_path_for("../x")
