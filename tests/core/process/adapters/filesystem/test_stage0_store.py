"""`FilesystemStage0Store` on a real directory under `tmp_path` (GH-632, part 2b): the lock,
the atomic link swap, state files replaced whole, the `.env` copy mode and the confined
`remove`. Real files and real descriptors; only `os.replace`/`os.open`/`shutil.rmtree` are
spied on or made to fail where the test is about them."""

import hashlib
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.stage0_store import FilesystemStage0Store

A = "a" * 40
B = "b" * 40
C = "c" * 40
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
needs_user = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "root"


@pytest.fixture
def store(root: Path) -> FilesystemStage0Store:
    root.mkdir()
    return FilesystemStage0Store(root)


@pytest.fixture
def replaced(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The destinations of every `os.replace` the code under test makes."""
    real = os.replace
    seen: list[Path] = []

    def spy(src: str, dst: str) -> None:
        seen.append(Path(dst))
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    return seen


def leftovers(root: Path) -> list[str]:
    return [p.name for p in root.rglob(".tmp-*")]


def test_lock_held_elsewhere_yields_false_at_once(root: Path):
    first, second = FilesystemStage0Store(root), FilesystemStage0Store(root)
    with first.lock() as held:
        assert held is True
        with second.lock() as busy:
            assert busy is False
    with second.lock() as again:
        assert again is True


def test_lock_is_released_when_the_body_raises(root: Path):
    store = FilesystemStage0Store(root)
    with pytest.raises(RuntimeError):
        with store.lock():
            raise RuntimeError("boom")
    with FilesystemStage0Store(root).lock() as held:
        assert held is True


def test_switch_current_without_current_creates_it_by_replace(store, root, replaced):
    (root / A).mkdir()
    store.switch_current(A)
    assert store.current() == A
    assert replaced == [root.resolve() / "current"]
    assert leftovers(root) == []


def test_switch_current_replaces_an_existing_link_by_replace(store, root, replaced):
    (root / A).mkdir()
    (root / B).mkdir()
    store.switch_current(A)
    store.switch_current(B)
    assert store.current() == B
    assert (root / "current").resolve() == (root / B).resolve()
    assert replaced == [root.resolve() / "current"] * 2
    assert leftovers(root) == []


@pytest.mark.parametrize("target", [lambda r: str(r / A), lambda r: A, lambda r: "../elsewhere/x"])
def test_current_is_the_basename_of_any_target(store, root, target):
    os.symlink(target(root), root / "current")
    assert store.current() == Path(target(root)).name


def test_current_is_none_without_a_link(store):
    assert store.current() is None


def test_stages_lists_only_40_hex_real_directories_with_aware_mtime(store, root, tmp_path):
    (root / A).mkdir()
    (root / f"{B}.partial").mkdir()
    (root / "abc").mkdir()
    (root / ("A" * 40)).mkdir()
    (root / C).write_text("a file")
    (tmp_path / "outside").mkdir()
    os.symlink(tmp_path / "outside", root / ("d" * 40))
    os.symlink(A, root / "current")
    listed = store.stages()
    assert [name for name, _ in listed] == [A]
    assert listed[0][1].tzinfo is not None


def test_a_missing_root_reads_as_empty_and_absent(root):
    store = FilesystemStage0Store(root)
    assert store.stages() == []
    assert store.current() is None
    assert store.rejected() == frozenset()
    assert store.switch_record() is None
    assert store.unavailable_record() is None
    assert store.escalation_issue() is None
    assert not root.exists()


def test_state_files_are_written_whole_by_replace_and_read_back(store, root, replaced):
    store.write_switch(None, A, NOW)
    store.write_unavailable(B, 2, "uv")
    store.write_escalation_issue("https://example.test/issues/1")
    store.reject(C)
    assert store.switch_record() == (None, A, NOW)
    assert store.unavailable_record() == (B, 2, "uv")
    assert store.escalation_issue() == "https://example.test/issues/1"
    assert store.rejected() == frozenset({C})
    assert replaced == [
        root.resolve() / "switch.json",
        root.resolve() / "unavailable.json",
        root.resolve() / "escalations.json",
        root.resolve() / "rejected",
    ]
    assert leftovers(root) == []


def test_a_state_write_creates_the_root(root):
    FilesystemStage0Store(root).write_unavailable(A, 1, "disk")
    assert (root / "unavailable.json").is_file()


def test_clear_unavailable_removes_the_record_and_tolerates_none(store):
    store.clear_unavailable()
    store.write_unavailable(A, 1, "disk")
    store.clear_unavailable()
    assert store.unavailable_record() is None


@pytest.mark.parametrize("content", ["", "   ", "{not json", "[1]", "{}", '{"sha": 1}'])
def test_empty_or_corrupt_records_read_as_absent(store, root, content):
    for name in ("switch.json", "unavailable.json", "escalations.json"):
        (root / name).write_text(content)
    assert store.switch_record() is None
    assert store.unavailable_record() is None
    assert store.escalation_issue() is None


def test_a_naive_switch_instant_reads_as_absent(store, root):
    (root / "switch.json").write_text('{"previous": null, "new": "x", "switched_at": "2026-01-01"}')
    assert store.switch_record() is None


def test_reject_reads_adds_and_rewrites_whole_sorted(store, root):
    store.reject(B)
    store.reject(A)
    store.reject(A)
    assert (root / "rejected").read_text() == f"{A}\n{B}\n"
    assert store.rejected() == frozenset({A, B})


def test_a_corrupt_rejected_raises(store, root):
    (root / "rejected").write_text(f"{A}\nnot-a-sha\n")
    with pytest.raises(ValueError):
        store.rejected()
    with pytest.raises(ValueError):
        store.reject(B)


def test_every_path_method_validates_its_name_before_touching_the_disk(store, root, tmp_path):
    (tmp_path / "victim").mkdir()
    bad = ["../victim", "abc", "A" * 40, f"{A}.partial.partial", "", f"{A}/..", ".tmp-x"]
    for name in bad:
        with pytest.raises(ValueError):
            store.remove(name)
        with pytest.raises(ValueError):
            store.copy_env(tmp_path, name)
    for name in [*bad, f"{A}.partial"]:
        for only_sha in (store.promote, store.remove_partial, store.switch_current, store.reject):
            with pytest.raises(ValueError):
                only_sha(name)
    assert (tmp_path / "victim").is_dir()
    assert sorted(p.name for p in root.iterdir()) == []


def test_promote_renames_the_partial_to_the_stage(store, root):
    (root / f"{A}.partial").mkdir()
    store.promote(A)
    assert (root / A).is_dir()
    assert not (root / f"{A}.partial").exists()


def test_copy_env_is_0600_from_creation_and_returns_the_digest(
    store, root, tmp_path, monkeypatch, capsys
):
    source = tmp_path / "own"
    source.mkdir()
    secret = b"TOKEN=hunter2-very-secret\n"
    (source / ".env").write_bytes(secret)
    (source / ".env").chmod(0o644)
    (root / f"{A}.partial").mkdir()
    opened: list[tuple[int, int]] = []
    real_open = os.open

    def spy(path, flags, mode=0o777, **kw):
        opened.append((flags, mode))
        return real_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", spy)
    digest = store.copy_env(source, f"{A}.partial")
    assert digest == hashlib.sha256(secret).digest()
    assert len(digest) == 32
    assert (root / f"{A}.partial" / ".env").read_bytes() == secret
    assert stat.S_IMODE((root / f"{A}.partial" / ".env").stat().st_mode) == 0o600
    created = [(f, m) for f, m in opened if f & os.O_CREAT]
    assert created
    assert all(f & os.O_EXCL and m == 0o600 for f, m in created)
    assert leftovers(root) == []
    captured = capsys.readouterr()
    assert "hunter2" not in captured.out + captured.err


def test_copy_env_without_a_source_env_removes_the_destination_env(store, root, tmp_path):
    source = tmp_path / "own"
    source.mkdir()
    (root / A).mkdir()
    (root / A / ".env").write_text("OLD=1")
    assert store.copy_env(source, A) == hashlib.sha256(b"").digest()
    assert not (root / A / ".env").exists()
    assert store.copy_env(source, A) == hashlib.sha256(b"").digest()


def test_copy_env_into_a_missing_stage_raises(store, tmp_path):
    (tmp_path / ".env").write_text("X=1")
    with pytest.raises(ValueError):
        store.copy_env(tmp_path, A)


def test_remove_deletes_a_stage_and_ignores_a_missing_one(store, root):
    (root / A / "sub").mkdir(parents=True)
    (root / A / "sub" / "f").write_text("x")
    store.remove(A)
    store.remove(A)
    assert list(root.iterdir()) == []


def test_remove_refuses_a_symlink_a_foreign_target_and_current(store, root, tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "keep").write_text("x")
    os.symlink(tmp_path / "outside", root / A)
    (root / B).mkdir()
    os.symlink(B, root / "current")
    with pytest.raises(ValueError):
        store.remove(A)
    with pytest.raises(ValueError):
        store.remove(B)
    with pytest.raises(ValueError):
        store.remove("../outside")
    assert (tmp_path / "outside" / "keep").is_file()
    assert (root / B).is_dir()


def test_a_cut_between_the_rename_and_the_delete_leaves_no_stage_name(store, root, monkeypatch):
    (root / A).mkdir()
    (root / A / "f").write_text("x")

    def cut(*args, **kwargs):
        raise OSError("disk fell off")

    monkeypatch.setattr(shutil, "rmtree", cut)
    with pytest.raises(OSError):
        store.remove(A)
    assert store.stages() == []
    assert (root / f"{A}.partial" / "f").is_file()
    monkeypatch.undo()
    store.clear_partials()
    assert list(root.iterdir()) == []


def test_clear_partials_removes_only_partials_and_temporaries(store, root):
    (root / f"{A}.partial").mkdir()
    (root / f"{B}.partial").mkdir()
    (root / A).mkdir()
    (root / "other.partial").mkdir()
    (root / ".tmp-1").write_text("x")
    os.symlink(A, root / ".tmp-2")
    store.clear_partials()
    assert sorted(p.name for p in root.iterdir()) == sorted([A, "other.partial"])


def test_remove_partial_removes_the_partial_and_never_the_stage(store, root):
    (root / A).mkdir()
    (root / f"{A}.partial").mkdir()
    store.remove_partial(A)
    store.remove_partial(B)
    assert [p.name for p in root.iterdir()] == [A]


@needs_user
def test_remove_deletes_a_read_only_subdirectory(store, root):
    (root / A / "ro").mkdir(parents=True)
    (root / A / "ro" / "f").write_text("x")
    (root / A / "ro" / "deep").mkdir()
    (root / A / "ro" / "deep" / "g").write_text("y")
    (root / A / "ro" / "deep").chmod(0o555)
    (root / A / "ro").chmod(0o555)
    store.remove(A)
    assert list(root.iterdir()) == []


@needs_user
def test_remove_never_changes_the_mode_of_a_symlink_target_outside(store, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_text("x")
    (outside / "file").chmod(0o444)
    outside.chmod(0o555)
    (root / A / "ro").mkdir(parents=True)
    os.symlink(outside / "file", root / A / "ro" / "link")
    os.symlink(outside, root / A / "ro" / "dirlink")
    (root / A / "ro").chmod(0o555)
    try:
        store.remove(A)
        assert list(root.iterdir()) == []
        assert stat.S_IMODE((outside / "file").stat().st_mode) == 0o444
        assert stat.S_IMODE(outside.stat().st_mode) == 0o555
    finally:
        outside.chmod(0o755)
