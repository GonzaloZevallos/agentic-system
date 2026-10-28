"""`FilesystemScratch`: the owner record is written before the directory and removed after
it, on every way out (GH-391). Real files under `tmp_path`: the scratch root is
`tmp_path/root` and holds this product's partition (`KEY`), the product is `tmp_path/product`
(GH-397); the boot id and the clock are faked."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.scratch import FilesystemScratch
from agentic.core.process.domain.scratch import OwnerRecord, Removal, ScratchListing, SweepDecision

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
TOKEN = "0123456789abcdef"
KEY = "0a1b2c3d4e5f"


def partition(base: Path) -> Path:
    return base / "root" / KEY


def scratch_at(base: Path) -> FilesystemScratch:
    return FilesystemScratch(
        base / "root", KEY, base / "product", boot_id=lambda: "boot-a", clock=lambda: NOW
    )


def test_open_gives_the_directory_the_path_for_names(tmp_path):
    with scratch_at(tmp_path).open("GH-391", "c", TOKEN, "tests") as directory:
        assert directory == tmp_path.resolve() / "root" / KEY / "GH-391/c-01234567"
        assert directory.is_dir()


def test_the_record_holds_pid_boot_slug_kind_and_time(tmp_path):
    with scratch_at(tmp_path).open("GH-391", "r", TOKEN, "developer") as directory:
        record = OwnerRecord.from_json(Path(f"{directory}.json").read_text())
    assert record == OwnerRecord(os.getpid(), "boot-a", "developer", "r", NOW)


def test_the_record_exists_before_the_directory_is_made(tmp_path, monkeypatch):
    seen: list[bool] = []
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name == "c-01234567":
            seen.append(self.with_name(self.name + ".json").is_file())
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with scratch_at(tmp_path).open("GH-391", "c", TOKEN, "tests"):
        pass
    assert seen == [True]


def test_the_record_never_shows_half_written(tmp_path, monkeypatch):
    # Written to a temp name and renamed: the only name `<owner>.json` can ever have is
    # a whole record, so the rename is what puts it there.
    renamed: list[tuple[str, str]] = []
    real_replace = os.replace

    def replace(source, target):
        renamed.append((Path(source).name, Path(target).name))
        assert json.loads(Path(source).read_text())["slug"] == "tests"
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    with scratch_at(tmp_path).open("GH-391", "c", TOKEN, "tests"):
        pass
    assert len(renamed) == 1
    assert renamed[0][1] == "c-01234567.json" and renamed[0][0] != renamed[0][1]


def test_after_the_context_neither_the_directory_nor_the_record_remains(tmp_path):
    with scratch_at(tmp_path).open("GH-391", "c", TOKEN, "tests") as directory:
        (directory / "deep").mkdir()
        (directory / "deep" / "file").write_text("x")
    parent = partition(tmp_path) / "GH-391"
    assert list(parent.iterdir()) == []


def test_after_an_exception_neither_remains_and_the_exception_propagates(tmp_path):
    with (
        pytest.raises(RuntimeError, match="boom"),
        scratch_at(tmp_path).open("GH-391", "c", TOKEN, "tests") as directory,
    ):
        (directory / "file").write_text("x")
        raise RuntimeError("boom")
    assert list((partition(tmp_path) / "GH-391").iterdir()) == []


def test_the_record_is_removed_after_the_directory(tmp_path, monkeypatch):
    # Invariant: the record outlives the directory, so a crash between the two leaves a
    # record that still names an owner, never an ownerless directory.
    import shutil

    seen: list[bool] = []
    real_rmtree = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        seen.append(Path(f"{path}.json").is_file())
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", rmtree)
    with scratch_at(tmp_path).open("GH-391", "c", TOKEN, "tests"):
        pass
    assert seen == [True]


def test_a_directory_that_cannot_be_made_leaves_no_record(tmp_path, monkeypatch):
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name == "c-01234567":
            raise OSError("no")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(OSError, match="no"), scratch_at(tmp_path).open("GH-391", "c", TOKEN, "t"):
        pass
    assert list((partition(tmp_path) / "GH-391").iterdir()) == []


def test_no_task_goes_under_untasked(tmp_path):
    with scratch_at(tmp_path).open(None, "c", TOKEN, "tests") as directory:
        assert directory.parent.name == "_untasked"


def test_two_owners_of_one_task_do_not_share_a_directory(tmp_path):
    scratch = scratch_at(tmp_path)
    with (
        scratch.open("GH-1", "c", "a" * 16, "x") as first,
        scratch.open("GH-1", "c", "b" * 16, "x") as second,
    ):
        assert first != second
        assert first.is_dir() and second.is_dir()
    assert not first.exists() and not second.exists()


def test_ensure_kept_creates_the_folder_and_never_removes_it(tmp_path):
    scratch = scratch_at(tmp_path)
    kept = scratch.ensure_kept("GH-391")
    assert kept == tmp_path.resolve() / "product/.agentic/kept/GH-391" and kept.is_dir()
    (kept / "capture.png").write_text("x")
    assert scratch.ensure_kept("GH-391") == kept  # idempotent
    with scratch.open("GH-391", "r", TOKEN, "developer"):
        pass
    assert (kept / "capture.png").read_text() == "x"


def test_the_default_boot_id_is_the_kernels(tmp_path):
    with FilesystemScratch(tmp_path / "root", KEY, tmp_path).open(
        "GH-391", "c", TOKEN, "tests"
    ) as directory:
        record = OwnerRecord.from_json(Path(f"{directory}.json").read_text())
    assert record is not None and record.pid == os.getpid()


# The sweep's side of the port (GH-391, c-poller-sweep-floor): listing reads facts, `sweep`
# carries a decision out. The decision itself is the domain's.


def _make_owner(base: Path, task: str, name: str, record: str | None, key: str = KEY) -> Path:
    directory = base / "root" / key / task / name
    directory.mkdir(parents=True)
    if record is not None:
        (directory.parent / f"{name}.json").write_text(record)
    return directory


def _record(pid: int, boot: str = "boot-a") -> str:
    return OwnerRecord(pid, boot, "tests", "c", NOW).to_json()


def test_listing_reports_the_liveness_of_each_record_and_the_unreadable_ones(tmp_path):
    _make_owner(tmp_path, "GH-1", "c-alive000", _record(os.getpid()))
    _make_owner(tmp_path, "GH-1", "c-otherboot", _record(os.getpid(), "boot-b"))
    _make_owner(tmp_path, "GH-1", "c-garbage0", "not json")
    _make_owner(tmp_path, "GH-1", "c-norecord", None)
    (partition(tmp_path) / "GH-2").mkdir()
    listing = scratch_at(tmp_path).listing()
    assert listing.tasks == ("GH-1", "GH-2")
    seen = {e.name: e for e in listing.owners}
    assert seen["c-alive000"].record is not None and seen["c-alive000"].record.alive is True
    assert seen["c-otherboot"].record is not None and seen["c-otherboot"].record.alive is False
    assert seen["c-garbage0"].record is not None and seen["c-garbage0"].record.alive is None
    assert seen["c-norecord"].record is None and seen["c-norecord"].directory_mtime is not None


def test_listing_an_empty_product_is_empty(tmp_path):
    assert scratch_at(tmp_path).listing() == ScratchListing((), ())
    assert scratch_at(tmp_path).kept_folders() == []


def test_sweep_removes_what_the_decision_names_and_nothing_else(tmp_path):
    keep = _make_owner(tmp_path, "GH-1", "c-keep0000", _record(os.getpid()))
    gone = _make_owner(tmp_path, "GH-1", "c-gone0000", _record(1, "boot-b"))
    orphan = _make_owner(tmp_path, "GH-1", "c-orphan00", None)
    closed = _make_owner(tmp_path, "GH-2", "c-closed000", _record(os.getpid()))
    (tmp_path / "product/.agentic/kept/GH-2").mkdir(parents=True)
    (tmp_path / "product/.agentic/kept/GH-3").mkdir()
    scratch_at(tmp_path).sweep(
        SweepDecision(
            tasks=("GH-2",),
            owners=(
                Removal("GH-1", "c-gone0000", True, True),
                Removal("GH-1", "c-orphan00", True, False),
            ),
            kept=("GH-2",),
        )
    )
    assert keep.is_dir() and keep.with_name("c-keep0000.json").is_file()
    assert not gone.exists() and not gone.with_name("c-gone0000.json").exists()
    assert not orphan.exists()
    assert not closed.parent.exists()
    assert not (tmp_path / "product/.agentic/kept/GH-2").exists()
    assert (tmp_path / "product/.agentic/kept/GH-3").is_dir()


def test_sweeping_what_is_already_gone_is_not_an_error(tmp_path):
    scratch_at(tmp_path).sweep(
        SweepDecision(("GH-9",), (Removal("GH-1", "c-none0000", True, True),), ("GH-9",))
    )


def test_kept_folders_are_listed_with_their_mtime(tmp_path):
    (tmp_path / "product/.agentic/kept/GH-5").mkdir(parents=True)
    ((task, mtime),) = scratch_at(tmp_path).kept_folders()
    assert task == "GH-5" and mtime.tzinfo is not None


def test_listing_and_sweep_touch_only_this_products_partition(tmp_path):
    mine = _make_owner(tmp_path, "GH-1", "c-mine0000", _record(1, "boot-b"))
    theirs = _make_owner(tmp_path, "GH-1", "c-their000", _record(1, "boot-b"), key="ffffffffffff")
    legacy = tmp_path / "product/.agentic/tmp/GH-1/c-legacy00"
    legacy.mkdir(parents=True)
    scratch = scratch_at(tmp_path)
    assert [e.name for e in scratch.listing().owners] == ["c-mine0000"]
    scratch.sweep(SweepDecision(("GH-1",), (Removal("GH-1", "c-their000", True, True),)))
    assert not mine.exists()
    assert theirs.is_dir() and theirs.with_name("c-their000.json").is_file()
    assert legacy.is_dir()


@pytest.mark.parametrize("key", ["", "a/b", "..", ".hidden"])
def test_a_product_key_that_is_not_a_plain_segment_builds_no_scratch(tmp_path, key):
    with pytest.raises(ValueError, match="product key"):
        FilesystemScratch(tmp_path / "root", key, tmp_path / "product")
