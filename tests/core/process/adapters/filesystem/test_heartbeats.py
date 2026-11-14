"""`FilesystemHeartbeats` on a real directory under `tmp_path` (GH-735): the round trip of
`start` and `end`, a hardened reader that turns anything untrustworthy into `Unreadable`, and
a writer that validates before it touches the disk."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.heartbeats import FilesystemHeartbeats
from agentic.core.process.domain.stage0_rotation import Heartbeat, Unreadable

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
GOOD = {"checkout": "/srv/stage", "at": NOW.isoformat(), "phase": "start", "interval_s": 60}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "root"


@pytest.fixture
def beats(root: Path) -> FilesystemHeartbeats:
    return FilesystemHeartbeats(root)


def plant(root: Path, name: str, content: object) -> None:
    (root / "heartbeats").mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (root / "heartbeats" / f"{name}.json").write_text(text)


@pytest.mark.parametrize("phase", ["start", "end"])
def test_a_beat_round_trips_with_its_checkout_resolved(beats, tmp_path, phase):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    beats.write("apostrophe-poll", link, NOW, phase, 30)
    assert beats.read_all() == {"apostrophe-poll": Heartbeat("apostrophe-poll", real, NOW, 30.0)}


def test_a_write_replaces_the_whole_file(beats, root):
    beats.write("a-poll", Path("/x"), NOW, "start", 60)
    beats.write("a-poll", Path("/y"), NOW, "end", 90)
    assert json.loads((root / "heartbeats" / "a-poll.json").read_text()) == {
        "checkout": "/y",
        "at": NOW.isoformat(),
        "phase": "end",
        "interval_s": 90,
    }
    assert [p.name for p in (root / "heartbeats").iterdir()] == ["a-poll.json"]


def test_two_keys_give_two_files_and_two_entries(beats, root):
    beats.write("a-poll", Path("/x"), NOW, "start", 60)
    beats.write("b-poll", Path("/x"), NOW, "end", 60)
    assert sorted(p.name for p in (root / "heartbeats").iterdir()) == ["a-poll.json", "b-poll.json"]
    assert set(beats.read_all()) == {"a-poll", "b-poll"}


def test_a_stop_beat_reads_as_unreadable(beats):
    beats.write("a-poll", Path("/x"), NOW, "stop", 60)
    assert beats.read_all() == {"a-poll": Unreadable()}


def test_a_missing_directory_gives_an_empty_mapping(beats, root):
    assert beats.read_all() == {}
    assert not root.exists()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{not json",
        "[1]",
        "null",
        "[" * 100000,
        {k: v for k, v in GOOD.items() if k != "checkout"},
        {k: v for k, v in GOOD.items() if k != "at"},
        {k: v for k, v in GOOD.items() if k != "phase"},
        {k: v for k, v in GOOD.items() if k != "interval_s"},
        {**GOOD, "at": "2026-11-14T12:00:00"},
        {**GOOD, "at": "yesterday"},
        {**GOOD, "at": 5},
        {**GOOD, "checkout": "relative/dir"},
        {**GOOD, "checkout": 5},
        {**GOOD, "checkout": "/bad\u0000path"},
        {**GOOD, "phase": "stop"},
        {**GOOD, "phase": "middle"},
        {**GOOD, "phase": ["start"]},
        {**GOOD, "interval_s": True},
        {**GOOD, "interval_s": "60"},
        {**GOOD, "interval_s": None},
        {**GOOD, "interval_s": 0},
        {**GOOD, "interval_s": -1},
        {**GOOD, "interval_s": 86401},
        {**GOOD, "interval_s": 1e308},
    ],
)
def test_anything_untrustworthy_reads_as_unreadable(beats, root, content):
    plant(root, "a-poll", content)
    assert beats.read_all() == {"a-poll": Unreadable()}


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
def test_a_non_finite_interval_reads_as_unreadable(beats, root, literal):
    plant(root, "a-poll", GOOD_TEXT.replace("60", literal))
    assert beats.read_all() == {"a-poll": Unreadable()}


GOOD_TEXT = json.dumps(GOOD)


def test_an_unreadable_file_does_not_hide_a_good_one(beats, root):
    plant(root, "a-poll", "{")
    plant(root, "b-poll", GOOD)
    result = beats.read_all()
    assert result["a-poll"] == Unreadable()
    assert isinstance(result["b-poll"], Heartbeat)


def test_a_directory_named_like_a_beat_reads_as_unreadable(beats, root):
    (root / "heartbeats" / "a-poll.json").mkdir(parents=True)
    assert beats.read_all() == {"a-poll": Unreadable()}


@pytest.mark.parametrize(
    "args",
    [
        ("A-poll", Path("/x"), NOW, "start", 60),
        ("../escape", Path("/x"), NOW, "start", 60),
        ("a/b", Path("/x"), NOW, "start", 60),
        ("", Path("/x"), NOW, "start", 60),
        ("a--b", Path("/x"), NOW, "start", 60),
        ("a-poll", Path("rel"), NOW, "start", 60),
        ("a-poll", Path("/x"), datetime(2026, 11, 14), "start", 60),
        ("a-poll", Path("/x"), NOW, "pause", 60),
        ("a-poll", Path("/x"), NOW, "start", True),
        ("a-poll", Path("/x"), NOW, "start", float("inf")),
        ("a-poll", Path("/x"), NOW, "start", float("nan")),
        ("a-poll", Path("/x"), NOW, "start", 0),
        ("a-poll", Path("/x"), NOW, "start", -1),
        ("a-poll", Path("/x"), NOW, "start", 86401),
    ],
)
def test_a_bad_write_raises_before_touching_the_disk(beats, root, args):
    with pytest.raises(ValueError):
        beats.write(*args)
    assert not root.exists()
    assert not root.parent.joinpath("escape.json").exists()


def test_a_failing_replace_leaves_no_temp_file(beats, root, monkeypatch):
    def boom(*_: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        beats.write("a-poll", Path("/x"), NOW, "start", 60)
    assert list((root / "heartbeats").iterdir()) == []


def test_a_beat_written_through_a_moving_link_keeps_the_stage_it_named(beats, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    current = tmp_path / "current"
    current.symlink_to(a)
    beats.write("a-poll", current, NOW, "end", 60)
    current.unlink()
    current.symlink_to(b)
    assert beats.read_all() == {"a-poll": Heartbeat("a-poll", a, NOW, 60.0)}


def test_a_beat_written_with_a_resolved_path_reads_back_identical(beats, tmp_path):
    stage = (tmp_path / "stage").resolve()
    stage.mkdir()
    beats.write("a-poll", stage, NOW, "start", 60)
    assert beats.read_all() == {"a-poll": Heartbeat("a-poll", stage, NOW, 60.0)}
