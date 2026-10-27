"""GH-375, ADR-0070: the board mirror's memory on disk, over a tmp_path."""

import json
import os
from datetime import UTC, datetime

import pytest

from agentic.core.process.adapters.filesystem.board_sent import FilesystemBoardSent
from agentic.core.process.application.ports import SentEntry, SentMemory


def _with_issue(number, present):
    issues = {number: {"fingerprint": "a", "present": present}}
    return json.dumps({"project_id": "P", "failed_at": None, "issues": issues})


MEMORY = SentMemory(
    project_id="PVT_1",
    failed_at=datetime(2026, 10, 27, 12, 0, tzinfo=UTC),
    issues={
        7: SentEntry("abc123", frozenset({"etapa", "bloqueo"})),
        9: SentEntry("def456", frozenset({"etapa"})),
    },
)


def test_a_memory_round_trips(tmp_path):
    store = FilesystemBoardSent(tmp_path / "state" / "board.json")
    store.save(MEMORY)
    assert store.load() == MEMORY


def test_a_missing_file_is_an_empty_memory_without_a_complaint(tmp_path):
    got = FilesystemBoardSent(tmp_path / "board.json").load()
    assert got == SentMemory() and not got.unreadable


@pytest.mark.parametrize(
    "content",
    [
        '{"project_id": "PVT_1", "failed_at": null, "iss',
        "[]",
        '{"project_id": 4, "failed_at": null, "issues": {}}',
        '{"project_id": "P", "failed_at": "not a date", "issues": {}}',
        _with_issue("7", ["bloque"]),
        _with_issue("x", []),
    ],
)
def test_an_unreadable_file_reads_empty_and_says_so(tmp_path, content):
    path = tmp_path / "board.json"
    path.write_text(content, encoding="utf-8")
    got = FilesystemBoardSent(path).load()
    assert got.unreadable and got.issues == {} and got.project_id is None


def test_a_failed_replace_leaves_the_old_file_whole_and_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "board.json"
    store = FilesystemBoardSent(path)
    store.save(MEMORY)
    before = path.read_text(encoding="utf-8")

    def boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save(SentMemory(project_id="PVT_2"))
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == ["board.json"]


def test_the_file_is_written_whole_through_a_temp_file_and_a_replace(tmp_path, monkeypatch):
    path = tmp_path / "board.json"
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((a, b)), real(a, b))[1])
    FilesystemBoardSent(path).save(MEMORY)
    assert [str(dest) for _, dest in replaced] == [str(path)]
    assert replaced[0][0] != str(path)
    assert json.loads(path.read_text(encoding="utf-8"))["project_id"] == "PVT_1"
