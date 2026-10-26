"""`FilesystemCheckResults` against a temporary directory (ADR-0065): a pass round-trips,
and anything that is not a verified, fresh entry is a miss, never an exception."""

import json
import os
import stat

from agentic.core.process.adapters.filesystem.check_results import FilesystemCheckResults
from agentic.core.process.domain import check_results as cr
from agentic.core.process.domain.presubmit import CheckResult

NOW = 1_000_000.0
KEY = cr.check_key("p", "tests", "pytest", "t" * 40, "f" * 64)
ROW: CheckResult = {
    "name": "tests",
    "command": "pytest",
    "passed": True,
    "output": "3 passed",
    "duration_s": 1.5,
}


def other_key(n: int) -> str:
    return cr.check_key("p", f"c{n}", "x", "t", "f")


def test_a_pass_round_trips(tmp_path):
    store = FilesystemCheckResults(tmp_path / "store")
    store.put(KEY, ROW, NOW)
    assert store.get(KEY, NOW + 10) == ROW


def test_a_key_never_written_is_a_miss_and_get_creates_nothing(tmp_path):
    store = FilesystemCheckResults(tmp_path / "store")
    assert store.get(KEY, NOW) is None
    assert not (tmp_path / "store").exists()


def test_the_secret_is_32_bytes_mode_0600_and_stays_the_same(tmp_path):
    store = FilesystemCheckResults(tmp_path / "store")
    store.put(KEY, ROW, NOW)
    secret = tmp_path / "store" / ".key"
    assert len(secret.read_bytes()) == 32
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    before = secret.read_bytes()
    store.put(other_key(1), ROW, NOW)
    assert secret.read_bytes() == before


def test_a_reused_flag_is_not_stored(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, {**ROW, "reused": True}, NOW)
    assert store.get(KEY, NOW) == ROW


def test_a_tampered_entry_is_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    entry = tmp_path / f"{KEY}.json"
    data = json.loads(entry.read_text())
    data["row"]["passed"] = False
    entry.write_text(json.dumps(data))
    assert store.get(KEY, NOW) is None
    data["row"]["passed"] = True
    data["row"]["output"] = "forged"
    entry.write_text(json.dumps(data))
    assert store.get(KEY, NOW) is None


def test_an_entry_copied_under_another_key_is_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    other = other_key(2)
    (tmp_path / f"{other}.json").write_text((tmp_path / f"{KEY}.json").read_text())
    assert store.get(other, NOW) is None


def test_a_moved_written_at_is_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    entry = tmp_path / f"{KEY}.json"
    data = json.loads(entry.read_text())
    data["written_at"] = NOW + 5 * cr.MAX_AGE_S
    entry.write_text(json.dumps(data))
    assert store.get(KEY, NOW + 5 * cr.MAX_AGE_S) is None


def test_a_missing_or_replaced_secret_is_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    (tmp_path / ".key").write_bytes(os.urandom(32))
    assert store.get(KEY, NOW) is None
    (tmp_path / ".key").unlink()
    assert store.get(KEY, NOW) is None


def test_a_truncated_secret_is_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    (tmp_path / ".key").write_bytes(b"short")
    assert store.get(KEY, NOW) is None


def test_an_expired_entry_is_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    assert store.get(KEY, NOW + cr.MAX_AGE_S - 1) == ROW
    assert store.get(KEY, NOW + cr.MAX_AGE_S) is None


def test_garbage_and_non_object_entries_are_misses(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    entry = tmp_path / f"{KEY}.json"
    for text in ("", "not json", "[]", '{"row": 3}', "null"):
        entry.write_text(text)
        assert store.get(KEY, NOW) is None, text
    entry.write_bytes(b"\xff\xfe")
    assert store.get(KEY, NOW) is None


def test_a_key_that_is_not_a_digest_is_a_miss_and_never_leaves_the_directory(tmp_path):
    store = FilesystemCheckResults(tmp_path / "store")
    assert store.get("../escape", NOW) is None
    assert store.get("", NOW) is None


def test_put_prunes_the_expired_then_the_oldest_above_the_limit(tmp_path):
    store = FilesystemCheckResults(tmp_path, max_entries=3)
    keys = [other_key(n) for n in range(5)]
    for n, key in enumerate(keys[:2]):
        store.put(key, ROW, NOW + n)
    # Entries 0 and 1 are old enough to expire at the next write.
    later = NOW + cr.MAX_AGE_S + 10
    for n, key in enumerate(keys[2:]):
        store.put(key, ROW, later + n)
        os.utime(tmp_path / f"{key}.json", (later + n, later + n))
    names = {p.stem for p in tmp_path.glob("*.json")}
    assert names == set(keys[2:])
    assert len(names) <= 3


def test_put_keeps_only_the_newest_when_all_are_fresh(tmp_path):
    store = FilesystemCheckResults(tmp_path, max_entries=2)
    keys = [other_key(n) for n in range(4)]
    for n, key in enumerate(keys):
        store.put(key, ROW, NOW + n)
        os.utime(tmp_path / f"{key}.json", (NOW + n, NOW + n))
    assert {p.stem for p in tmp_path.glob("*.json")} == set(keys[2:])


def test_no_temporary_file_is_left_behind(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([".key", f"{KEY}.json"])


def test_an_unwritable_directory_does_not_raise_on_get(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert FilesystemCheckResults(blocker / "store").get(KEY, NOW) is None


def test_a_failure_is_never_stored(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, {**ROW, "passed": False}, NOW)
    assert store.get(KEY, NOW) is None
    assert not (tmp_path / f"{KEY}.json").exists()


def test_a_signed_failure_is_still_a_miss(tmp_path):
    store = FilesystemCheckResults(tmp_path)
    store.put(KEY, ROW, NOW)
    entry = tmp_path / f"{KEY}.json"
    data = json.loads(entry.read_text())
    data["row"]["passed"] = False
    # Re-signed with the real secret, as a writer that holds it would.
    data["mac"] = _sign(tmp_path, KEY, data["written_at"], data["row"])
    entry.write_text(json.dumps(data))
    assert store.get(KEY, NOW) is None


def _sign(directory, key, written_at, row):
    import hashlib
    import hmac

    message = json.dumps([key, written_at, row], sort_keys=True, separators=(",", ":"))
    secret = (directory / ".key").read_bytes()
    return hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
