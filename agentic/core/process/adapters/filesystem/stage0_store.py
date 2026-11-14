"""`Stage0Store` over one directory (GH-632, part 2b). The behaviour of every method is the
port's; this module adds only how it touches the disk: every name or sha is validated by
`_check` before it is joined under the root, state files are replaced whole (temp file,
fsync, `os.replace`, directory fsync), and nothing outside the root is ever deleted."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

_NAME = re.compile(r"[0-9a-f]{40}(\.partial)?")
_SHA = re.compile(r"[0-9a-f]{40}")


def _check(name: str, *, partial: bool = False) -> str:
    """The one name check: 40 lowercase hex, `.partial` only where `partial` admits it."""
    if _NAME.fullmatch(name) is None or (not partial and name.endswith(".partial")):
        raise ValueError(f"not a stage name: {name!r}")
    return name


def _within(path: str, base: str) -> bool:
    real = os.path.realpath(path)
    return real == base or real.startswith(base + os.sep)


def _guard(base: str) -> Callable[[Callable[..., Any], str, BaseException], None]:
    """The `rmtree` error handler for the resolved `.partial` directory `base`: makes the
    parent of a failing entry (and the entry itself) writable and retries once, only for
    paths that really lie under `base`. A symlink or a path outside is never chmod-ed."""

    def handle(func: Callable[..., Any], path: str, exc: BaseException) -> None:
        if func not in (os.unlink, os.rmdir):
            raise exc
        for target in (path, os.path.dirname(path)):
            if os.path.islink(target) or not _within(target, base):
                continue
            mode = os.lstat(target).st_mode
            os.chmod(
                target,
                mode | (stat.S_IRWXU if stat.S_ISDIR(mode) else stat.S_IWUSR),
                follow_symlinks=False,
            )
        func(path)

    return handle


def _rmtree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    else:
        shutil.rmtree(path, onexc=_guard(os.path.realpath(path)))


class FilesystemStage0Store:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @contextmanager
    def lock(self) -> Iterator[bool]:
        self._root.mkdir(parents=True, exist_ok=True)
        with open(self._root / ".lock", "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def clear_partials(self) -> None:
        if not self._root.is_dir():
            return
        for entry in self._root.iterdir():
            if re.fullmatch(r"[0-9a-f]{40}\.partial", entry.name):
                _rmtree(entry)
            elif entry.name.startswith(".tmp-"):
                entry.unlink(missing_ok=True)

    def remove_partial(self, sha: str) -> None:
        path = self._root / f"{_check(sha)}.partial"
        if path.is_symlink() or path.exists():
            _rmtree(path)

    def stages(self) -> list[tuple[str, datetime]]:
        if not self._root.is_dir():
            return []
        found = []
        for entry in self._root.iterdir():
            if _SHA.fullmatch(entry.name) and not entry.is_symlink() and entry.is_dir():
                found.append((entry.name, datetime.fromtimestamp(entry.stat().st_mtime, UTC)))
        return found

    def current(self) -> str | None:
        try:
            return os.path.basename(os.readlink(self._root / "current"))
        except FileNotFoundError:
            return None

    def _read(self, name: str) -> bytes | None:
        try:
            return (self._root / name).read_bytes()
        except FileNotFoundError:
            return None

    def _json(self, name: str) -> dict[str, Any] | None:
        data = self._read(name)
        try:
            loaded = json.loads(data) if data is not None else None
        except ValueError:
            return None
        return loaded if isinstance(loaded, dict) else None

    def _write(self, name: str, data: bytes) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        temp = self._root / f".tmp-{uuid4().hex}"
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self._root / name)
        directory = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def rejected(self) -> frozenset[str]:
        data = self._read("rejected")
        lines = data.decode().split("\n") if data is not None else []
        return frozenset(_check(line) for line in lines if line)

    def reject(self, sha: str) -> None:
        shas = sorted(self.rejected() | {_check(sha)})
        self._write("rejected", "".join(f"{line}\n" for line in shas).encode())

    def switch_record(self) -> tuple[str | None, str, datetime] | None:
        record = self._json("switch.json")
        if record is None:
            return None
        try:
            previous, new = record["previous"], record["new"]
            switched_at = datetime.fromisoformat(record["switched_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if (previous is not None and not isinstance(previous, str)) or not isinstance(new, str):
            return None
        return (previous, new, switched_at) if switched_at.tzinfo is not None else None

    def write_switch(self, previous: str | None, new: str, switched_at: datetime) -> None:
        record = {"previous": previous, "new": new, "switched_at": switched_at.isoformat()}
        self._write("switch.json", json.dumps(record).encode())

    def unavailable_record(self) -> tuple[str, int, str] | None:
        record = self._json("unavailable.json") or {}
        sha, count, cause = record.get("sha"), record.get("count"), record.get("cause")
        if isinstance(sha, str) and type(count) is int and isinstance(cause, str):
            return (sha, count, cause)
        return None

    def write_unavailable(self, sha: str, count: int, cause: str) -> None:
        self._write(
            "unavailable.json", json.dumps({"sha": sha, "count": count, "cause": cause}).encode()
        )

    def clear_unavailable(self) -> None:
        (self._root / "unavailable.json").unlink(missing_ok=True)

    def escalation_issue(self) -> str | None:
        url = (self._json("escalations.json") or {}).get("url")
        return url if isinstance(url, str) else None

    def write_escalation_issue(self, url: str) -> None:
        self._write("escalations.json", json.dumps({"url": url}).encode())

    def copy_env(self, source: Path, dest: str) -> bytes:
        stage = self._root / _check(dest, partial=True)
        if stage.is_symlink() or not stage.is_dir():
            raise ValueError(f"no such stage: {dest}")
        try:
            data = (source / ".env").read_bytes()
        except FileNotFoundError:
            (stage / ".env").unlink(missing_ok=True)
            return sha256(b"").digest()
        temp = stage / f".tmp-{uuid4().hex}"
        try:
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, stage / ".env")
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return sha256(data).digest()

    def promote(self, sha: str) -> None:
        os.rename(self._root / f"{_check(sha)}.partial", self._root / sha)

    def switch_current(self, sha: str) -> None:
        temp = self._root / f".tmp-{uuid4().hex}"
        os.symlink(_check(sha), temp)
        os.replace(temp, self._root / "current")

    def remove(self, name: str) -> None:
        path = self._root / _check(name, partial=True)
        if path.is_symlink() or path.resolve().parent != self._root:
            raise ValueError(f"refusing to remove {name!r}")
        if name == self.current():
            raise ValueError(f"refusing to remove the current stage {name!r}")
        if not path.exists():
            return
        if _SHA.fullmatch(name):
            partial = self._root / f"{name}.partial"
            if partial.is_symlink() or partial.exists():
                _rmtree(partial)
            os.rename(path, partial)
            path = partial
        _rmtree(path)
