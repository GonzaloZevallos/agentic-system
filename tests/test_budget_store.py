"""`FilesystemBudgetStore` (GH-1071): the BudgetStore port over one file and one flock per
account, on `tmp_path` with a clock that only moves when the test moves it."""

import errno
import fcntl
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem import budget_store
from agentic.core.process.adapters.filesystem.budget_store import FilesystemBudgetStore
from agentic.core.process.application.ports import BudgetStore
from agentic.core.process.domain.github_budget import BUDGET_TTL, Budget, ResourceBudget

KEY = "github.com-0123456789abcdef"
T = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
_lock_clock = [0.0]


def _sleep(seconds: float) -> None:
    _lock_clock[0] += seconds


def _store(directory: Path) -> FilesystemBudgetStore:
    _lock_clock[0] = 0.0
    return FilesystemBudgetStore(directory, clock=lambda: _lock_clock[0], sleep=_sleep)


def _budget(at: datetime = T) -> Budget:
    return Budget(
        {
            "core": ResourceBudget(4000, at + timedelta(minutes=30)),
            "graphql": None,
            "search": ResourceBudget(7, at + timedelta(seconds=40)),
        },
        at,
    )


def _file(tmp_path: Path, **changes: object) -> Path:
    record: dict[str, object] = {
        "version": 1,
        "read_at": 1_000,
        "fetched_at": None,
        "resources": {"core": {"remaining": 5, "reset": 2_000}},
    }
    record.update(changes)
    path = tmp_path / f"{KEY}.json"
    path.write_text(json.dumps(record))
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_round_trip(tmp_path):
    store = _store(tmp_path / "budget")
    assert store.load(KEY) is None
    claim = store.claim(KEY, T)
    assert claim == T
    store.put(KEY, claim, _budget())
    assert store.load(KEY) == _budget()


def test_new_directory_and_files_are_private(tmp_path):
    store = _store(tmp_path / "cache" / "budget")
    store.put(KEY, T, _budget())
    assert _mode(tmp_path / "cache" / "budget") == 0o700
    assert _mode(tmp_path / "cache" / "budget" / f"{KEY}.json") == 0o600
    assert _mode(tmp_path / "cache" / "budget" / f"{KEY}.lock") == 0o600


def test_existing_directory_is_fixed_to_0700(tmp_path):
    directory = tmp_path / "budget"
    directory.mkdir()
    directory.chmod(0o755)
    _store(directory).load(KEY)
    assert _mode(directory) == 0o700


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"version": True},
        {"extra": 1},
        {"read_at": True},
        {"read_at": "1000"},
        {"fetched_at": 1.5},
        {"resources": {"core": {"remaining": True, "reset": 2_000}}},
        {"resources": {"core": {"remaining": 5, "reset": 2_000, "used": 1}}},
        {"resources": {"other": {"remaining": 5, "reset": 2_000}}},
        {"resources": []},
    ],
)
def test_a_file_that_is_not_the_schema_reads_as_unknown(tmp_path, changes):
    _file(tmp_path, **changes)
    assert _store(tmp_path).load(KEY) is None


def test_a_torn_file_reads_as_unknown(tmp_path):
    (tmp_path / f"{KEY}.json").write_text('{"version": 1, "read_at"')
    assert _store(tmp_path).load(KEY) is None


def test_a_file_in_the_schema_reads_with_absent_resources_unknown(tmp_path):
    _file(tmp_path, fetched_at=1_500)
    budget = _store(tmp_path).load(KEY)
    assert budget == Budget(
        {
            "core": ResourceBudget(5, datetime.fromtimestamp(2_000, UTC)),
            "graphql": None,
            "search": None,
        },
        datetime.fromtimestamp(1_500, UTC),
    )


@pytest.mark.parametrize(
    "key", ["github.com-ABCDEF0123456789", "github.com-0123", "x" + KEY, KEY + "\n"]
)
def test_a_key_of_another_shape_raises(tmp_path, key):
    store = _store(tmp_path)
    for call in (
        lambda: store.load(key),
        lambda: store.claim(key, T),
        lambda: store.put(key, T, _budget()),
    ):
        with pytest.raises(ValueError):
            call()


def test_a_claim_under_the_ttl_is_refused_and_at_the_ttl_accepted(tmp_path):
    store = _store(tmp_path)
    assert store.claim(KEY, T) == T
    assert store.claim(KEY, T) is None
    assert store.claim(KEY, T + BUDGET_TTL - timedelta(seconds=1)) is None
    assert store.claim(KEY, T + BUDGET_TTL) == T + BUDGET_TTL


def test_a_claim_keeps_the_stored_resources_and_fetched_at(tmp_path):
    store = _store(tmp_path)
    store.put(KEY, T, _budget())
    later = T + timedelta(seconds=90)
    assert store.claim(KEY, later) == later
    assert store.load(KEY) == _budget()
    assert json.loads((tmp_path / f"{KEY}.json").read_text())["read_at"] == int(later.timestamp())


def test_a_stored_read_at_in_the_future_counts_as_expired(tmp_path):
    store = _store(tmp_path)
    store.put(KEY, T + timedelta(hours=1), _budget())
    assert store.claim(KEY, T) == T


def test_a_claim_compares_and_returns_whole_seconds(tmp_path):
    store = _store(tmp_path)
    assert store.claim(KEY, T + timedelta(milliseconds=900)) == T
    assert store.claim(KEY, T + timedelta(seconds=59, milliseconds=999)) is None
    at = T + timedelta(seconds=60, milliseconds=1)
    assert store.claim(KEY, at) == T + timedelta(seconds=60)


def test_a_late_write_is_dropped(tmp_path):
    store = _store(tmp_path)
    a = store.claim(KEY, T)
    b = store.claim(KEY, T + timedelta(seconds=61))
    store.put(KEY, b, _budget(b))
    store.put(KEY, a, _budget(a))
    assert store.load(KEY) == _budget(b)
    assert json.loads((tmp_path / f"{KEY}.json").read_text())["read_at"] == int(b.timestamp())
    assert store.claim(KEY, T + timedelta(seconds=62)) is None


def test_a_read_only_parent_raises_nothing(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)
    store = _store(parent / "budget")
    try:
        assert store.load(KEY) is None
        assert store.claim(KEY, T) is None
        store.put(KEY, T, _budget())
    finally:
        parent.chmod(0o700)


def test_a_full_disk_raises_nothing_and_leaves_no_temp_file(tmp_path, monkeypatch):
    def replace(src, dst):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(budget_store.os, "replace", replace)
    store = _store(tmp_path)
    assert store.claim(KEY, T) is None
    store.put(KEY, T, _budget())
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{KEY}.lock"]


def test_flock_failing_raises_nothing(tmp_path, monkeypatch):
    def flock(fd, operation):
        raise OSError(errno.ENOLCK, "no locks")

    monkeypatch.setattr(budget_store.fcntl, "flock", flock)
    store = _store(tmp_path)
    assert store.claim(KEY, T) is None
    store.put(KEY, T, _budget())
    assert store.load(KEY) is None


def test_a_lock_held_elsewhere_times_out_after_one_second(tmp_path):
    store = _store(tmp_path)
    holder = os.open(tmp_path / f"{KEY}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    try:
        assert store.claim(KEY, T) is None
        assert 1.0 <= _lock_clock[0] < 1.1
        store.put(KEY, T, _budget())
    finally:
        os.close(holder)
    assert store.load(KEY) is None
    assert store.claim(KEY, T) == T


def test_a_symlinked_directory_disables_the_store(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    store = _store(link)
    assert store.claim(KEY, T) is None
    store.put(KEY, T, _budget())
    assert store.load(KEY) is None
    assert list(real.iterdir()) == []


def test_a_foreign_directory_disables_the_store(tmp_path, monkeypatch):
    mine = os.getuid()
    monkeypatch.setattr(budget_store.os, "getuid", lambda: mine + 1)
    store = _store(tmp_path)
    assert store.claim(KEY, T) is None
    assert store.load(KEY) is None
    assert list(tmp_path.iterdir()) == []


def test_a_failing_chmod_disables_the_store(tmp_path, monkeypatch):
    def chmod(path, mode):
        raise PermissionError(errno.EPERM, "no")

    monkeypatch.setattr(budget_store.os, "chmod", chmod)
    store = _store(tmp_path)
    assert store.claim(KEY, T) is None
    assert store.load(KEY) is None


def test_a_symlinked_data_file_reads_as_unknown(tmp_path):
    target = _file(tmp_path)
    target.rename(tmp_path / "target.json")
    (tmp_path / f"{KEY}.json").symlink_to(tmp_path / "target.json")
    assert _store(tmp_path).load(KEY) is None


def test_the_file_store_satisfies_the_port(tmp_path):
    store: BudgetStore = FilesystemBudgetStore(tmp_path)
    assert store.load(KEY) is None


# --- observe ----------------------------------------------------------------------------


def _observed(core: int, reset: datetime | None = None) -> Budget:
    when = reset if reset is not None else T + timedelta(minutes=30)  # the window of _budget()
    return Budget({"core": ResourceBudget(core, when), "graphql": None, "search": None}, T)


def _seeded(tmp_path: Path) -> FilesystemBudgetStore:
    """A store holding `_budget()` read at T, with its lock file in place."""
    store = _store(tmp_path)
    store.put(KEY, T, _budget())
    return store


def _listing(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_observe_writes_a_lower_count_and_keeps_read_at_and_the_other_resources(tmp_path):
    store = _seeded(tmp_path)
    store.observe(KEY, _observed(3999))
    assert store.load(KEY) == Budget(
        {
            "core": ResourceBudget(3999, T + timedelta(minutes=30)),
            "graphql": None,
            "search": ResourceBudget(7, T + timedelta(seconds=40)),
        },
        T,
    )
    assert json.loads((tmp_path / f"{KEY}.json").read_text())["read_at"] == int(T.timestamp())
    assert store.claim(KEY, T) is None  # read_at is still T: the read is not claimable


def test_observe_ignores_a_higher_count(tmp_path):
    store = _seeded(tmp_path)
    store.observe(KEY, _observed(4001))
    assert store.load(KEY) == _budget()


def test_observe_takes_a_new_window(tmp_path):
    store = _seeded(tmp_path)
    later = T + timedelta(hours=2)
    store.observe(KEY, _observed(4900, later))
    loaded = store.load(KEY)
    assert loaded is not None
    assert loaded.resources["core"] == ResourceBudget(4900, later)


def test_observe_unchanged_leaves_the_file_byte_identical(tmp_path):
    store = _seeded(tmp_path)
    path = tmp_path / f"{KEY}.json"
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    store.observe(KEY, _observed(4000))
    store.observe(KEY, _observed(4001))
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime
    assert _listing(tmp_path) == [f"{KEY}.json", f"{KEY}.lock"]


def test_observe_on_a_missing_record_writes_nothing(tmp_path):
    (tmp_path / f"{KEY}.lock").touch()
    before = _listing(tmp_path)
    _store(tmp_path).observe(KEY, _observed(1))
    assert _listing(tmp_path) == before


def test_observe_on_a_missing_directory_leaves_it_missing(tmp_path):
    directory = tmp_path / "budget"
    _store(directory).observe(KEY, _observed(1))
    assert not directory.exists()
    assert _listing(tmp_path) == []


def test_observe_without_a_lock_file_creates_none_and_writes_nothing(tmp_path):
    path = _file(tmp_path)
    before = path.read_bytes()
    _store(tmp_path).observe(KEY, _observed(1))
    assert path.read_bytes() == before
    assert _listing(tmp_path) == [path.name]


def test_observe_on_a_torn_record_writes_nothing(tmp_path):
    (tmp_path / f"{KEY}.lock").touch()
    path = tmp_path / f"{KEY}.json"
    path.write_text('{"version": 1, "read_at"')
    _store(tmp_path).observe(KEY, _observed(1))
    assert path.read_text() == '{"version": 1, "read_at"'


def test_observe_on_a_foreign_directory_writes_nothing(tmp_path, monkeypatch):
    store = _seeded(tmp_path)
    before = (tmp_path / f"{KEY}.json").read_bytes()
    mine = os.getuid()
    monkeypatch.setattr(budget_store.os, "getuid", lambda: mine + 1)
    store.observe(KEY, _observed(1))
    assert (tmp_path / f"{KEY}.json").read_bytes() == before


def test_observe_returns_at_once_when_another_descriptor_holds_the_lock(tmp_path):
    slept: list[float] = []
    store = FilesystemBudgetStore(tmp_path, sleep=slept.append)
    store.put(KEY, T, _budget())
    path = tmp_path / f"{KEY}.json"
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    holder = os.open(tmp_path / f"{KEY}.lock", os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store.observe(KEY, _observed(1))
    finally:
        os.close(holder)
    assert slept == []
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_observe_makes_one_lock_attempt_and_never_reads_the_clock(tmp_path, monkeypatch):
    def clock() -> float:
        raise AssertionError("observe reads no clock")

    _seeded(tmp_path)
    store = FilesystemBudgetStore(tmp_path, clock=clock, sleep=lambda _: None)
    attempts: list[int] = []
    real = fcntl.flock

    def flock(fd: int, how: int) -> None:
        attempts.append(how)
        real(fd, how)

    monkeypatch.setattr(budget_store.fcntl, "flock", flock)
    store.observe(KEY, _observed(3999))
    assert attempts == [fcntl.LOCK_EX | fcntl.LOCK_NB]


def test_observe_swallows_an_os_error(tmp_path, monkeypatch):
    store = _seeded(tmp_path)

    def broken(*args, **kwargs):
        raise PermissionError(errno.EACCES, "no")

    monkeypatch.setattr(budget_store.os, "replace", broken)
    store.observe(KEY, _observed(1))
    assert store.load(KEY) == _budget()
    assert [name for name in _listing(tmp_path) if name.endswith(".tmp")] == []


def test_observe_refuses_a_key_of_another_shape(tmp_path):
    with pytest.raises(ValueError):
        _store(tmp_path).observe("github.com-token", _observed(1))
