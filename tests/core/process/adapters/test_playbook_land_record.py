"""The landing record on disk: one JSON file per product under `<root>/playbook-land/`.

Seam: `FilesystemLandRecord.load / save`."""

from __future__ import annotations

import json
import os

import pytest

from agentic.core.process.adapters.playbook_land_record import FilesystemLandRecord
from agentic.core.process.domain.playbook_land import LandAttempt

ATTEMPT = LandAttempt("a" * 40, "2026-11-18T10:00:00Z", "refuse", "ci_red", 2, "b" * 40, "e")


def test_a_saved_attempt_loads_back_from_the_product_file_under_the_root(tmp_path):
    record = FilesystemLandRecord(tmp_path)
    record.save("agentic", ATTEMPT)
    assert (tmp_path / "playbook-land" / "agentic.json").is_file()
    assert record.load("agentic") == ATTEMPT
    assert record.load("other") is None


def test_save_writes_through_a_temp_file_in_the_same_directory_then_a_replace(
    tmp_path, monkeypatch
):
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((a, b)), real(a, b))[1])
    FilesystemLandRecord(tmp_path).save("agentic", ATTEMPT)
    target = tmp_path / "playbook-land" / "agentic.json"
    assert [str(dest) for _, dest in replaced] == [str(target)]
    assert os.path.dirname(replaced[0][0]) == str(target.parent)
    assert [p.name for p in target.parent.iterdir()] == ["agentic.json"]


def test_a_failed_replace_leaves_the_old_record_whole_and_no_temp_file(tmp_path, monkeypatch):
    record = FilesystemLandRecord(tmp_path)
    record.save("agentic", ATTEMPT)
    target = tmp_path / "playbook-land" / "agentic.json"
    before = target.read_text(encoding="utf-8")

    def boom(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        record.save("agentic", LandAttempt("c" * 40, "t", "merge"))
    assert target.read_text(encoding="utf-8") == before
    assert [p.name for p in target.parent.iterdir()] == ["agentic.json"]


@pytest.mark.parametrize(
    "content",
    [
        "",
        '{"v": 1, "head": "h", "at"',
        "[]",
        json.dumps({"v": 2, "head": "h", "at": "t", "verdict": "merge"}),
        json.dumps({"v": 1, "head": "h", "at": "t", "verdict": "nope"}),
    ],
)
def test_a_corrupt_or_other_version_file_loads_as_none(tmp_path, content):
    path = tmp_path / "playbook-land" / "agentic.json"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    assert FilesystemLandRecord(tmp_path).load("agentic") is None


def test_an_unreadable_file_loads_as_none(tmp_path):
    path = tmp_path / "playbook-land" / "agentic.json"
    path.mkdir(parents=True)
    assert FilesystemLandRecord(tmp_path).load("agentic") is None


@pytest.mark.parametrize("product", ["../x", "a/b", "", ".hidden", "a..b"])
def test_a_product_name_that_could_leave_the_directory_is_refused_and_loads_none(tmp_path, product):
    record = FilesystemLandRecord(tmp_path)
    with pytest.raises(ValueError):
        record.save(product, ATTEMPT)
    assert record.load(product) is None
