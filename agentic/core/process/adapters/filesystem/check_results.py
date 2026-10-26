"""`FilesystemCheckResults`, over `Configuration.check_results_dir`: the machine's store of
passing check results (ADR-0065), outside every checkout and shared by every product.

One `<key>.json` per key, written whole and atomically. Each entry carries an HMAC-SHA256
under a 32-byte machine secret (`.key`, mode 0600, created by the first `put`): the gate
trusts what it reads here, so an entry nobody signed with that secret is a miss. The MAC
names the problem of state shared between writers that are not the gate; it stops an
accidental or hand-edited entry, not a process under the same uid that reads the secret
(ADR-0065, residual). Anything unreadable, unverified or expired is a miss, never an
exception."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import tempfile
from pathlib import Path

from ...domain import check_results as domain
from ...domain.presubmit import CheckResult

_SECRET = ".key"
_SECRET_BYTES = 32
_KEY = re.compile(r"[0-9a-f]{64}")


class FilesystemCheckResults:
    def __init__(self, dir: Path, max_entries: int = domain.MAX_ENTRIES):
        self.dir = dir
        self.max_entries = max_entries

    def get(self, key: str, now: float) -> CheckResult | None:
        if not _KEY.fullmatch(key):
            return None
        try:
            secret = self._secret()
            data = json.loads((self.dir / f"{key}.json").read_text(encoding="utf-8"))
            written_at, row, mac = data["written_at"], data["row"], data["mac"]
            if secret is None or not _verified(secret, key, written_at, row, mac):
                return None
            if not domain.is_fresh(float(written_at), now):
                return None
            return _row_of(row)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OverflowError,
            RecursionError,
        ):
            return None

    def put(self, key: str, row: CheckResult, now: float) -> None:
        if not _KEY.fullmatch(key):
            raise ValueError(f"not a check key: {key!r}")
        if not row["passed"]:
            # Only a pass is ever stored (ADR-0065): a failure may be flaky, so the
            # store refuses it however it got here.
            return
        # `get` signs `float(written_at)`: an int `now` must sign as the float it reads back.
        now = float(now)
        self.dir.mkdir(parents=True, exist_ok=True)
        secret = self._secret(create=True)
        if secret is None:
            raise OSError(f"the check-results secret under {self.dir} is not usable")
        stored = _stored(row)
        entry = {
            "key": key,
            "written_at": now,
            "row": stored,
            "mac": _mac(secret, key, now, stored),
        }
        handle, temporary = tempfile.mkstemp(dir=self.dir, prefix=".entry-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(entry, out)
            # The file's own clock is the injected one, so pruning ages entries the way
            # `is_fresh` does and a test never has to wait.
            os.utime(temporary, (now, now))
            os.replace(temporary, self.dir / f"{key}.json")
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        self._prune(now)

    def _secret(self, create: bool = False) -> bytes | None:
        path = self.dir / _SECRET
        try:
            secret = path.read_bytes()
        except FileNotFoundError:
            if not create:
                return None
            secret = self._create_secret(path)
        return secret if len(secret) == _SECRET_BYTES else None

    def _create_secret(self, path: Path) -> bytes:
        secret = os.urandom(_SECRET_BYTES)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another process made it first: its secret is the one.
            return path.read_bytes()
        with os.fdopen(descriptor, "wb") as out:
            out.write(secret)
        return secret

    def _prune(self, now: float) -> None:
        """The entries older than `MAX_AGE_S` go, then the oldest above `max_entries`.
        Linear in the directory listing, and only on writes."""
        aged: list[tuple[float, Path]] = []
        for path in self.dir.glob("*.json"):
            with contextlib.suppress(OSError):
                aged.append((path.stat().st_mtime, path))
        aged.sort(key=lambda item: item[0])
        fresh = []
        for mtime, path in aged:
            if domain.is_fresh(mtime, now):
                fresh.append(path)
            else:
                with contextlib.suppress(OSError):
                    path.unlink()
        for path in fresh[: max(0, len(fresh) - self.max_entries)]:
            with contextlib.suppress(OSError):
                path.unlink()


def _stored(row: CheckResult) -> dict[str, object]:
    """The row as the entry holds it: never `reused`, which is the reader's to set."""
    stored: dict[str, object] = {
        "name": row["name"],
        "command": row["command"],
        "passed": row["passed"],
        "output": row["output"],
    }
    if "duration_s" in row:
        stored["duration_s"] = row["duration_s"]
    if "mode" in row:
        stored["mode"] = row["mode"]
    return stored


def _mac(secret: bytes, key: str, written_at: float, row: dict[str, object]) -> str:
    message = json.dumps([key, written_at, row], sort_keys=True, separators=(",", ":"))
    return hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


def _verified(secret: bytes, key: str, written_at: object, row: object, mac: object) -> bool:
    if not isinstance(row, dict) or not isinstance(mac, str):
        return False
    if isinstance(written_at, bool) or not isinstance(written_at, int | float):
        return False
    return hmac.compare_digest(_mac(secret, key, float(written_at), row), mac)


def _row_of(row: object) -> CheckResult | None:
    """The row an entry holds, rebuilt field by field: a row that is not exactly a pass
    with the types a `CheckResult` has is a miss."""
    if not isinstance(row, dict):
        return None
    name, command, output = row.get("name"), row.get("command"), row.get("output")
    if not (isinstance(name, str) and isinstance(command, str) and isinstance(output, str)):
        return None
    if row.get("passed") is not True:
        return None
    result: CheckResult = {"name": name, "command": command, "passed": True, "output": output}
    if "duration_s" in row:
        duration = row["duration_s"]
        if isinstance(duration, bool) or not isinstance(duration, int | float):
            return None
        result["duration_s"] = float(duration)
    if "mode" in row:
        if row["mode"] == "full":
            result["mode"] = "full"
        elif row["mode"] == "affected-only":
            result["mode"] = "affected-only"
        else:
            return None
    return result
