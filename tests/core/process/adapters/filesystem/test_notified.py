"""GH-1056 part 3: the email notifier's sent-record on disk, over a tmp_path.

A file that is missing, unreadable or partly malformed loads as the empty record, as a whole:
ADR-0106 accepts that cost ("Límites conocidos"), a corrupt file loses up to 7 days of pending
events.
"""

import json
import os
from datetime import UTC, date, datetime

import pytest

from agentic.core.process.adapters.filesystem.notified import FilesystemNotified
from agentic.core.process.domain.notify import DigestKey, EventKey, NotifiedRecord

EMPTY = NotifiedRecord(None, None, None, {})
WHEN = datetime(2026, 11, 20, 12, 30, tzinfo=UTC)
RECORD = NotifiedRecord(
    started_at=datetime(2026, 11, 1, tzinfo=UTC),
    failed_at=datetime(2026, 11, 21, 8, 0, tzinfo=UTC),
    activity_read_at=datetime(2026, 11, 22, 9, 15, tzinfo=UTC),
    keys={
        EventKey("waiting", "my-prod", 7, WHEN): "sent",
        EventKey("release", "my-prod", 8, WHEN): "covered",
        EventKey("waiting", "my-prod", 9, WHEN): "superseded",
        EventKey("release", "my-prod", 10, WHEN): "unbuildable",
        DigestKey("my-prod", date(2026, 11, 20)): "sent",
    },
)


def _file(keys, **top):
    body = {"started_at": None, "failed_at": None, "activity_read_at": None, "keys": keys}
    return json.dumps({**body, **top})


def _event(**over):
    key = {
        "kind": "waiting",
        "product": "my-prod",
        "issue": 7,
        "instant": "2026-11-20T12:30:00+00:00",
        "outcome": "sent",
    }
    return {**key, **over}


def _digest(**over):
    key = {"kind": "digest", "product": "my-prod", "local_date": "2026-11-20", "outcome": "sent"}
    return {**key, **over}


def test_a_record_round_trips_with_every_kind_outcome_and_timestamp(tmp_path):
    store = FilesystemNotified(tmp_path / "state" / "notify" / "prod.json")
    store.save(RECORD)
    assert store.load() == RECORD


def test_the_file_is_a_list_of_typed_keys_with_the_raw_product(tmp_path):
    path = tmp_path / "n.json"
    FilesystemNotified(path).save(RECORD)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["activity_read_at"] == "2026-11-22T09:15:00+00:00"
    assert {k["kind"] for k in raw["keys"]} == {"waiting", "release", "digest"}
    assert all(k["product"] == "my-prod" for k in raw["keys"])
    assert "local_date" in next(k for k in raw["keys"] if k["kind"] == "digest")


def test_a_missing_file_loads_empty(tmp_path):
    assert FilesystemNotified(tmp_path / "none.json").load() == EMPTY


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe not json at all",
        b'{"started_at": null, "keys": [',
        b"[]",
        b"7",
        _file([_event(), _event(product="a.b/c", issue=8)]).encode(),
        _file([_event(kind="other")]).encode(),
        _file([_event(outcome="lost")]).encode(),
        _file([_event(instant="not a date")]).encode(),
        _file([_event(instant="2026-11-20T12:30:00+02:00")]).encode(),
        _file([_event(instant="2026-11-20T12:30:00")]).encode(),
        _file([_event(issue=0)]).encode(),
        _file([_digest(local_date="2026-11-20T00:00:00")]).encode(),
        _file([_digest(local_date="nope")]).encode(),
        _file([{"kind": "waiting", "product": "my-prod", "outcome": "sent"}]).encode(),
        _file([_event(), _event()]).encode(),
        _file([_event()], started_at="yesterday").encode(),
        _file([_event()], failed_at="2026-11-21T08:00:00+05:00").encode(),
        _file("not a list").encode(),
        b'{"keys": []}',
    ],
)
def test_garbage_or_one_corrupt_key_loads_the_whole_record_empty(tmp_path, content):
    path = tmp_path / "n.json"
    path.write_bytes(content)
    assert FilesystemNotified(path).load() == EMPTY


def test_an_unreadable_path_loads_empty(tmp_path):
    path = tmp_path / "is-a-directory.json"
    path.mkdir()
    assert FilesystemNotified(path).load() == EMPTY


def test_an_extra_unknown_field_is_ignored(tmp_path):
    path = tmp_path / "n.json"
    path.write_text(_file([_event(extra=1)], future="x"), encoding="utf-8")
    assert FilesystemNotified(path).load() == NotifiedRecord(
        None, None, None, {EventKey("waiting", "my-prod", 7, WHEN): "sent"}
    )


def test_a_save_leaves_only_the_target_in_its_directory(tmp_path):
    store = FilesystemNotified(tmp_path / "d" / "n.json")
    store.save(RECORD)
    store.save(EMPTY)
    assert [p.name for p in (tmp_path / "d").iterdir()] == ["n.json"]


def test_a_save_that_fails_leaves_no_temp_file_and_the_old_target(tmp_path, monkeypatch):
    path = tmp_path / "n.json"
    store = FilesystemNotified(path)
    store.save(RECORD)

    def boom(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        store.save(EMPTY)
    monkeypatch.undo()

    assert [p.name for p in tmp_path.iterdir()] == ["n.json"]
    assert store.load() == RECORD
