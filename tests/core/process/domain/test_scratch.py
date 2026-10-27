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
