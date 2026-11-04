"""`FileInbox`: the task of an evaluation run, read from its frozen entry file (GH-470)."""

from pathlib import Path

import pytest
import yaml

from agentic.core.process.adapters.filesystem.file_inbox import FileInbox

BASE = "a" * 40
ENTRY = {
    "id": "EVAL-7-aaaaaaaa-default-1",
    "issue": 7,
    "base": BASE,
    "title": "Frozen title",
    "body": "Frozen body",
}


def written(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "entry.yaml"
    path.write_text(yaml.safe_dump({**ENTRY, **overrides}))
    return path


def test_the_task_is_the_frozen_entry_marked_as_an_evaluation(tmp_path):
    inbox = FileInbox(written(tmp_path))
    assert inbox.task(7) == {
        "id": "EVAL-7-aaaaaaaa-default-1",
        "title": "Frozen title",
        "description": "Frozen body",
        "evaluation": {"issue": 7, "base": BASE, "ci": "local"},
    }


def test_the_id_and_the_issue_come_from_the_file_whatever_is_asked(tmp_path):
    inbox = FileInbox(written(tmp_path))
    assert inbox.task_id(7) == "EVAL-7-aaaaaaaa-default-1"
    assert inbox.issue_of("EVAL-7-aaaaaaaa-default-1") == 7
    assert inbox.issue_of("GH-7") is None


def test_no_open_issue_is_ever_read(tmp_path):
    inbox = FileInbox(written(tmp_path))
    assert inbox.open_issues() == []
    assert inbox.open_issue_bodies() == []


def test_asking_for_another_issue_than_the_files_is_refused(tmp_path):
    with pytest.raises(ValueError, match="issue 8"):
        FileInbox(written(tmp_path)).task(8)


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": ""},
        {"id": 5},
        {"issue": "7"},
        {"issue": True},
        {"base": 3},
        {"title": ""},
        {"body": " "},
    ],
)
def test_a_malformed_file_is_refused_with_its_path(tmp_path, overrides):
    path = written(tmp_path, **overrides)
    with pytest.raises(ValueError, match="entry.yaml"):
        FileInbox(path)


def test_a_missing_or_non_mapping_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match="missing.yaml"):
        FileInbox(tmp_path / "missing.yaml")
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n")
    with pytest.raises(ValueError, match="list.yaml"):
        FileInbox(path)
