"""GH-865: the run lock's record is written atomically, on the real `FilesystemRunLock` over a
temp directory. A write that fails (a full disk) leaves the previous record or none."""

from __future__ import annotations

import errno
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from agentic.core.process.adapters.filesystem.lock import FilesystemRunLock


def make_lock(tmp_path: Path) -> FilesystemRunLock:
    return FilesystemRunLock(tmp_path / "runs", boot_id=lambda: "boot-1")


def current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def full_disk(self: Path, data: str, encoding: str | None = None) -> int:
    """`Path.write_text` on a full disk: half of the text lands, then ENOSPC."""
    with self.open("w", encoding=encoding) as handle:
        handle.write(data[: len(data) // 2])
    raise OSError(errno.ENOSPC, "No space left on device")


def test_a_failed_first_write_leaves_no_lock_and_no_temporary_file(tmp_path: Path) -> None:
    lock = make_lock(tmp_path)
    runs = tmp_path / "runs"

    with patch.object(Path, "write_text", full_disk):
        with pytest.raises(OSError) as raised:
            lock.acquire("GH-7", "sweep")

    assert raised.value.errno == errno.ENOSPC
    assert list(runs.iterdir()) == []
    assert lock.read("GH-7") is None
    assert lock.acquire("GH-7", "sweep") is None
    assert lock.read("GH-7") is not None and [p.name for p in runs.iterdir()] == ["GH-7.lock"]


def test_a_failed_rewrite_keeps_the_previous_record_byte_for_byte(tmp_path: Path) -> None:
    lock = make_lock(tmp_path)
    lock.acquire("GH-7", "agentic run")
    path = tmp_path / "runs" / "GH-7.lock"
    before = path.read_bytes()

    with patch.object(Path, "write_text", full_disk):
        with pytest.raises(OSError):
            lock.keep_crashed("GH-7", "v1")

    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["GH-7.lock"]
    assert lock.read("GH-7") is not None


def test_a_failed_replace_removes_the_temporary_file(tmp_path: Path) -> None:
    lock = make_lock(tmp_path)
    boom = OSError(errno.EIO, "boom")

    with patch("agentic.core.process.adapters.filesystem.lock.os.replace", side_effect=boom):
        with pytest.raises(OSError):
            lock.acquire("GH-7", "sweep")

    assert list((tmp_path / "runs").iterdir()) == []


def test_a_normal_write_has_the_same_json_and_the_umask_derived_mode(tmp_path: Path) -> None:
    lock = make_lock(tmp_path)

    lock.acquire("GH-7", "agentic run")

    path = tmp_path / "runs" / "GH-7.lock"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data)[:5] == ["pid", "host", "started_at", "command", "boot_id"]
    assert (data["pid"], data["host"], data["command"], data["boot_id"]) == (
        os.getpid(),
        socket.gethostname(),
        "agentic run",
        "boot-1",
    )
    assert path.read_text(encoding="utf-8") == json.dumps(data)
    assert path.stat().st_mode & 0o777 == 0o666 & ~current_umask()
    assert [p.name for p in path.parent.iterdir()] == ["GH-7.lock"]


@pytest.mark.parametrize("mask", [0o022, 0o077])
def test_the_mode_follows_the_umask_in_force(tmp_path: Path, mask: int) -> None:
    lock = make_lock(tmp_path)
    previous = os.umask(mask)
    try:
        lock.acquire("GH-7", "agentic run")
    finally:
        os.umask(previous)

    assert (tmp_path / "runs" / "GH-7.lock").stat().st_mode & 0o777 == 0o666 & ~mask
