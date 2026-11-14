"""Application tests of `stage0_rotate.rotate` with recording doubles for every port
(GH-632, part 2a)."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.adapters.filesystem.heartbeats import FilesystemHeartbeats
from agentic.core.process.application.stage0_prune import make_prune
from agentic.core.process.application.stage0_rotate import (
    DefaultFailurePolicy,
    NoEscalation,
    Prune,
    rotate,
)
from agentic.core.process.domain.stage0_rotation import (
    Green,
    Heartbeat,
    Prepared,
    Proceed,
    Red,
    Stop,
    SwitchRecord,
    Unavailable,
    Unreadable,
    WindowEntry,
)

NEW_PURE_APPLICATION_MODULES = ("agentic/core/process/application/stage0_rotate.py",)
FORBIDDEN_IMPORTS = ("os", "fcntl", "subprocess", "shutil")

A = "a" * 40
B = "b" * 40
C = "c" * 40
NOW = datetime(2026, 11, 13, tzinfo=UTC)
OWN = Path("/own")
ROOT = Path("/stage0-root")
T0 = NOW - timedelta(minutes=20)
EMPTY_ENV = hashlib.sha256(b"").digest()
GREEN_B = [WindowEntry(B, "green", True), WindowEntry(C, "green", True)]


class Crash(Exception):
    pass


class Store:
    def __init__(
        self,
        log: list[str],
        *,
        current: str | None = A,
        held: bool = True,
        digests: list[bytes] | None = None,
        fail_on: str | None = None,
        staged: list[str] | None = None,
        record: SwitchRecord | None = None,
    ) -> None:
        self.log = log
        self._staged = staged or []
        self._current = current
        self._held = held
        self._digests = list(digests or [EMPTY_ENV, EMPTY_ENV])
        self._fail_on = fail_on
        self.copy_sources: list[tuple[Path, str]] = []
        self.switch_written = record
        self.rejected_calls: list[str] = []
        self.rejected_set: set[str] = set()

    def _call(self, entry: str) -> None:
        self.log.append(entry)
        if self._fail_on is not None and entry.startswith(self._fail_on):
            raise Crash(entry)

    @contextmanager
    def lock(self) -> Iterator[bool]:
        self.log.append("lock")
        try:
            yield self._held
        finally:
            self.log.append("unlock")

    def clear_partials(self) -> None:
        self._call("clear_partials")

    def remove_partial(self, sha: str) -> None:
        self._call(f"remove_partial {sha}")

    def stages(self) -> list[tuple[str, datetime]]:
        return [(name, NOW) for name in self._staged]

    def current(self) -> str | None:
        return self._current

    def rejected(self) -> frozenset[str]:
        return frozenset(self.rejected_set)

    def reject(self, sha: str) -> None:
        self.rejected_calls.append(sha)
        self.rejected_set.add(sha)
        self._call(f"reject {sha}")

    def write_switch(
        self, previous: str | None, new: str, switched_at: datetime, expected: frozenset[str]
    ) -> None:
        self._call(f"write_switch {new}")
        self.switch_written = SwitchRecord(previous, new, switched_at, expected)

    def switch_record(self) -> SwitchRecord | None:
        return self.switch_written

    def unavailable_record(self) -> tuple[str, int, str] | None:
        return None

    def write_unavailable(self, sha: str, count: int, cause: str) -> None:
        self._call(f"write_unavailable {sha}")

    def clear_unavailable(self) -> None:
        self._call("clear_unavailable")

    def escalation_issue(self) -> str | None:
        return None

    def write_escalation_issue(self, url: str) -> None:
        self._call(f"write_escalation_issue {url}")

    def remove(self, name: str) -> None:
        self._call(f"remove {name}")

    def copy_env(self, source: Path, dest: str) -> bytes:
        self.copy_sources.append((source, dest))
        self._call(f"copy_env {dest}")
        return self._digests.pop(0)

    def promote(self, sha: str) -> None:
        self._call(f"promote {sha}")

    def switch_current(self, sha: str) -> None:
        self._call(f"switch_current {sha}")
        self._current = sha


class Users:
    def __init__(self, in_use: frozenset[str] | Unreadable = frozenset()) -> None:
        self.in_use = in_use

    def users(self) -> frozenset[str] | Unreadable:
        return self.in_use


class Beats:
    """In-memory `Heartbeats` that applies the adapter's rules: `stop` reads as `Unreadable`,
    the checkout is stored resolved, and what the adapter refuses raises here too."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.stored: dict[str, Heartbeat | Unreadable] = {}

    def write(self, key: str, checkout: Path, at: datetime, phase: str, interval_s: float) -> None:
        refused = not checkout.is_absolute() or at.utcoffset() is None
        if refused or phase not in ("start", "end", "stop") or not 0 < interval_s <= 86400:
            raise ValueError("refused")
        self.stored[key] = (
            Unreadable()
            if phase == "stop"
            else Heartbeat(key, checkout.resolve(), at, float(interval_s))
        )

    def read_all(self) -> dict[str, Heartbeat | Unreadable]:
        self.log.append("read_all")
        return dict(self.stored)


class Window:
    def __init__(self, log: list[str], entries: list[WindowEntry] | Unreadable) -> None:
        self.log = log
        self._entries = entries
        self.asked_with: list[str | None] = []

    def entries(self, current: str | None) -> list[WindowEntry] | Unreadable:
        self.log.append("window")
        self.asked_with.append(current)
        return self._entries


class Candidate:
    def __init__(
        self,
        log: list[str],
        *,
        smokes: list[Green | Red | Unavailable] | None = None,
        prepared: Prepared | Unavailable | None = None,
        settles: list[Green | Unavailable] | None = None,
    ) -> None:
        self.log = log
        self.smokes = list(smokes if smokes is not None else [Green()])
        self.settles = list(settles if settles is not None else [Green()] * 4)
        self.prepared = prepared
        self.stages_smoked: list[str] = []

    def prepare(self, sha: str) -> Prepared | Unavailable:
        self.log.append(f"prepare {sha}")
        return self.prepared if self.prepared is not None else Prepared(sha)

    def smoke(self, stage: str) -> Green | Red | Unavailable:
        self.log.append(f"smoke {stage}")
        self.stages_smoked.append(stage)
        return self.smokes.pop(0)

    def settle(self, sha: str) -> Green | Unavailable:
        self.log.append(f"settle {sha}")
        return self.settles.pop(0)


class Escalation:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.runs = 0
        self.crash_at: int | None = None

    def run(self) -> None:
        self.runs += 1
        self.log.append("escalation")
        if self.runs == self.crash_at:
            raise Crash("escalation")


class Policy:
    """Records what it is asked and answers what it was told."""

    def __init__(self, log: list[str], answer: Proceed | Stop | None = None) -> None:
        self.log = log
        self.answer = answer
        self.prepare_calls: list[tuple[str, Unavailable]] = []
        self.smoke_calls: list[tuple[str, str, Red | Unavailable]] = []
        self.succeeded_calls: list[str] = []

    def prepare_failed(self, sha: str, outcome: Unavailable) -> Stop:
        self.prepare_calls.append((sha, outcome))
        return Stop("prepare stopped")

    def smoke_failed(self, sha: str, stage: str, outcome: Red | Unavailable) -> Proceed | Stop:
        self.smoke_calls.append((sha, stage, outcome))
        return self.answer if self.answer is not None else Stop("smoke stopped")

    def succeeded(self, sha: str) -> None:
        self.log.append(f"succeeded {sha}")
        self.succeeded_calls.append(sha)


class World:
    def __init__(
        self,
        *,
        entries: list[WindowEntry] | Unreadable | None = None,
        current: str | None = A,
        held: bool = True,
        digests: list[bytes] | None = None,
        smokes: list[Green | Red | Unavailable] | None = None,
        prepared: Prepared | Unavailable | None = None,
        fail_on: str | None = None,
        staged: list[str] | None = None,
        in_use: frozenset[str] | Unreadable = frozenset(),
        settles: list[Green | Unavailable] | None = None,
        record: SwitchRecord | None = None,
    ) -> None:
        self.log: list[str] = []
        self.out: list[str] = []
        self.store = Store(
            self.log,
            current=current,
            held=held,
            digests=digests,
            fail_on=fail_on,
            staged=staged,
            record=record,
        )
        self.beats: Any = Beats(self.log)
        self.clock_at = NOW
        self.clock_calls = 0
        self.users = Users(in_use)
        self.window = Window(self.log, GREEN_B if entries is None else entries)
        self.candidate = Candidate(self.log, smokes=smokes, prepared=prepared, settles=settles)
        self.escalation = Escalation(self.log)
        self.pruned: list[tuple[Path, list[str]]] = []

    def prune(self, store: object, users: object, own: Path, out: object) -> None:
        self.pruned.append((own, list(self.log)))

    def clock(self) -> datetime:
        self.clock_calls += 1
        self.log.append("clock")
        return self.clock_at

    def run(
        self,
        *,
        pin: str | None = None,
        policy: Policy | None = None,
        now: datetime = NOW,
        prune: Prune | None = None,
    ) -> None:
        rotate(
            self.store,
            self.users,
            self.window,
            self.candidate,
            self.escalation,
            pin=pin,
            now=now,
            own=OWN,
            heartbeats=self.beats,
            clock=self.clock,
            root=ROOT,
            failure_policy=policy,
            prune=prune or self.prune,
            out=self.out.append,
        )

    def calls(self, prefix: str) -> list[str]:
        return [entry for entry in self.log if entry.startswith(prefix)]


def test_newest_green_is_adopted_in_order() -> None:
    w = World()
    w.run(policy=Policy(w.log))
    adoption = [
        e
        for e in w.log
        if e.split()[0] in {"promote", "write_switch", "switch_current", "succeeded"}
    ]
    assert adoption == [
        f"promote {B}",
        f"write_switch {B}",
        f"switch_current {B}",
        f"succeeded {B}",
    ]
    assert w.out == [f"switched: {B}"]
    assert w.store.switch_written == SwitchRecord(A, B, NOW, frozenset())


def test_busy_lock_changes_nothing() -> None:
    w = World(held=False)
    w.run()
    assert w.log == ["lock", "unlock"]
    assert w.out == ["keep: busy"]
    assert w.pruned == []


def test_red_smoke_with_default_policy_keeps_current_and_never_rejects() -> None:
    w = World(smokes=[Red()])
    w.run()
    assert w.store.current() == A
    assert w.calls("remove_partial") == [f"remove_partial {B}"]
    assert w.calls("reject") == []
    assert w.calls("promote") == []
    assert w.out == [f"smoke_failed: {B}"]


@pytest.mark.parametrize("where", ["prepare", "smoke"])
def test_unavailable_keeps_and_promotes_nothing(where: str) -> None:
    if where == "prepare":
        w = World(prepared=Unavailable("clone"))
    else:
        w = World(smokes=[Unavailable("smoke_timeout")])
    w.run()
    assert w.out == ["keep: unavailable"]
    assert w.calls("promote") == []
    assert w.calls("switch_current") == []
    assert w.calls("reject") == []
    assert w.calls("remove_partial") == [f"remove_partial {B}"]


def test_changed_env_digest_reruns_smoke_and_red_second_smoke_never_promotes() -> None:
    w = World(digests=[b"one", b"two"], smokes=[Green(), Red()])
    w.run()
    assert w.candidate.stages_smoked == [f"{B}.partial"] * 2
    assert w.calls("promote") == []
    assert w.out == [f"smoke_failed: {B}"]
    assert w.store.current() == A


def test_changed_env_digest_with_green_second_smoke_adopts() -> None:
    w = World(digests=[b"one", b"two"], smokes=[Green(), Green()])
    w.run()
    assert len(w.candidate.stages_smoked) == 2
    assert w.out == [f"switched: {B}"]


def test_equal_digests_smoke_once() -> None:
    w = World(digests=[b"same", b"same"])
    w.run()
    assert w.candidate.stages_smoked == [f"{B}.partial"]
    assert w.out == [f"switched: {B}"]


def test_missing_env_digest_twice_still_adopts_with_one_smoke() -> None:
    w = World(digests=[EMPTY_ENV, EMPTY_ENV])
    w.run()
    assert len(w.candidate.stages_smoked) == 1
    assert w.out == [f"switched: {B}"]


def test_write_switch_precedes_switch_current_and_follows_promote() -> None:
    w = World()
    w.run()
    order = [w.log.index(f"{op} {B}") for op in ("promote", "write_switch", "switch_current")]
    assert order == sorted(order)
    assert w.log.index(f"switch_current {B}") - w.log.index(f"write_switch {B}") == 1


def test_bootstrap_adopts_newest_green_and_copies_from_own() -> None:
    w = World(current=None, entries=[WindowEntry(B, "green", True)])
    w.run()
    assert w.window.asked_with == [None]
    assert w.out == [f"switched: {B}"]
    assert w.store.switch_written == SwitchRecord(None, B, NOW, frozenset())
    assert w.store.copy_sources == [(OWN, f"{B}.partial")] * 2


def test_escalation_runs_inside_the_lock_after_clear_partials() -> None:
    w = World()
    w.run()
    assert w.log[:3] == ["lock", "clear_partials", "escalation"]
    assert w.log.index("escalation") < w.log.index("unlock")
    assert w.log.index("escalation") < w.log.index("window")


def test_unreadable_window_keeps_and_prunes() -> None:
    w = World(entries=Unreadable())
    policy = Policy(w.log)
    w.run(policy=policy)
    assert w.out == ["keep: unreadable"]
    assert policy.succeeded_calls == []
    assert len(w.pruned) == 1


@pytest.mark.parametrize(
    "outcome",
    [Red(), Unavailable("uv"), Unavailable("control_red")],
)
def test_injected_policy_gets_the_exact_outcome_and_stop_never_promotes(
    outcome: Red | Unavailable,
) -> None:
    w = World(smokes=[outcome])
    policy = Policy(w.log, Stop("custom stop"))
    w.run(policy=policy)
    assert policy.smoke_calls == [(B, f"{B}.partial", outcome)]
    assert w.out == ["custom stop"]
    assert w.calls("promote") == []
    assert w.calls("switch_current") == []
    assert policy.succeeded_calls == []


def test_injected_policy_gets_the_prepare_outcome() -> None:
    w = World(prepared=Unavailable("disk"))
    policy = Policy(w.log)
    w.run(policy=policy)
    assert policy.prepare_calls == [(B, Unavailable("disk"))]
    assert w.out == ["prepare stopped"]
    assert w.calls("promote") == []


def test_proceed_after_red_reaches_the_switch_and_prints_its_line() -> None:
    w = World(smokes=[Red()])
    policy = Policy(w.log, Proceed("proceeding anyway", Green()))
    w.run(policy=policy)
    assert w.out == ["proceeding anyway", f"switched: {B}"]
    assert w.calls("switch_current") == [f"switch_current {B}"]
    assert policy.succeeded_calls == [B]


def test_prepared_sha_that_differs_from_the_target_raises() -> None:
    w = World(prepared=Prepared(C))
    with pytest.raises(ValueError):
        w.run()
    assert w.calls("promote") == []
    assert w.calls("write_switch") == []
    assert w.calls("switch_current") == []
    assert w.log[-1] == "unlock"


@pytest.mark.parametrize("bad", ["B" * 40, "b" * 39])
def test_prepared_sha_that_is_not_40_lowercase_hex_raises(bad: str) -> None:
    w = World(entries=[WindowEntry(bad, "green", True)], prepared=Prepared(bad))
    with pytest.raises(ValueError):
        w.run()
    assert w.calls("promote") == []


def test_abbreviated_pin_uses_the_prepared_40_hex_sha_everywhere() -> None:
    w = World(entries=[], prepared=Prepared(B))
    w.run(pin=B[:8].upper())
    assert w.calls("prepare") == [f"prepare {B[:8]}"]
    assert w.candidate.stages_smoked == [f"{B}.partial"]
    assert w.store.copy_sources == [(OWN, f"{B}.partial")] * 2
    assert w.calls("promote") == [f"promote {B}"]
    assert w.calls("write_switch") == [f"write_switch {B}"]
    assert w.calls("switch_current") == [f"switch_current {B}"]
    assert w.out == [f"switched: {B}"]


def test_crash_between_write_switch_and_switch_current_keeps_current_and_retries() -> None:
    w = World(fail_on=f"switch_current {B}")
    with pytest.raises(Crash):
        w.run()
    assert w.store.current() == A
    assert w.calls("write_switch") == [f"write_switch {B}"]
    w.store._fail_on = None
    w.store._digests = [EMPTY_ENV, EMPTY_ENV]
    w.candidate.smokes = [Green()]
    w.log.clear()
    w.out.clear()
    w.run()
    assert w.out == [f"switched: {B}"]
    assert w.store.current() == B


def test_crash_after_switch_current_second_run_calls_succeeded_once() -> None:
    w = World()

    class CrashingPolicy(Policy):
        def succeeded(self, sha: str) -> None:
            raise Crash("before succeeded")

    with pytest.raises(Crash):
        w.run(policy=CrashingPolicy(w.log))
    assert w.store.current() == B
    # the second run finds B current and nothing newer: up to date, `succeeded` once
    w.window._entries = [WindowEntry(B, "green", False)]
    w.out.clear()
    policy = Policy(w.log)
    w.run(policy=policy)
    assert w.out == ["keep: up_to_date"]
    assert policy.succeeded_calls == [B]


@pytest.mark.parametrize(
    ("pin", "entries", "expected"),
    [
        (B, [], "keep: pinned"),
        (None, Unreadable(), "keep: unreadable"),
        ("zz", [], "keep: unreadable"),
        (None, [WindowEntry(C, "red", True)], "keep: no_green_in_window"),
    ],
)
def test_other_keeps_never_call_succeeded(
    pin: str | None, entries: list[WindowEntry] | Unreadable, expected: str
) -> None:
    w = World(current=B, entries=entries)
    policy = Policy(w.log)
    w.run(pin=pin, policy=policy)
    assert w.out == [expected]
    assert policy.succeeded_calls == []


def test_busy_never_calls_succeeded() -> None:
    w = World(held=False)
    policy = Policy(w.log)
    w.run(policy=policy)
    assert policy.succeeded_calls == []


def test_promote_raising_releases_the_lock_and_leaves_current() -> None:
    w = World(fail_on="promote")
    with pytest.raises(Crash):
        w.run()
    assert w.log[-1] == "unlock"
    assert w.store.current() == A
    assert w.calls("switch_current") == []
    assert w.pruned == []


def test_prune_runs_inside_the_lock_with_own() -> None:
    w = World()
    w.run()
    assert [own for own, _ in w.pruned] == [OWN]
    seen = w.pruned[0][1]
    assert "unlock" not in seen
    assert f"switch_current {B}" in seen


def test_default_policy_removes_the_partial_and_never_rejects() -> None:
    log: list[str] = []
    store = Store(log)
    policy = DefaultFailurePolicy(store)
    assert policy.prepare_failed(B, Unavailable("clone")) == Stop("keep: unavailable")
    assert policy.smoke_failed(B, f"{B}.partial", Red()) == Stop(f"smoke_failed: {B}")
    assert policy.smoke_failed(B, f"{B}.partial", Unavailable("disk")) == Stop("keep: unavailable")
    policy.succeeded(B)
    assert log == [f"remove_partial {B}"] * 3
    assert store.rejected_calls == []


def test_no_escalation_does_nothing() -> None:
    assert NoEscalation().run() is None


def _forbidden_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            names = [node.module]
        else:
            continue
        found += [n for n in names if n.split(".")[0] in FORBIDDEN_IMPORTS]
    return found


@pytest.mark.parametrize("module", NEW_PURE_APPLICATION_MODULES)
def test_new_application_modules_import_no_os_level_module(module: str) -> None:
    root = Path(__file__).resolve().parents[4]
    assert _forbidden_imports((root / module).read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import os.path",
        "from fcntl import flock",
        "import subprocess",
        "from shutil import rmtree",
    ],
)
def test_the_import_check_rejects_forbidden_imports(source: str) -> None:
    assert _forbidden_imports(source) != []


@pytest.mark.parametrize(
    "source", ["import re", "from pathlib import Path", "from .ports import Stage0Store"]
)
def test_the_import_check_accepts_other_imports(source: str) -> None:
    assert _forbidden_imports(source) == []


def test_settle_runs_after_promote_and_before_current_moves() -> None:
    w = World()
    w.run(policy=Policy(w.log))

    steps = [
        e.split()[0]
        for e in w.log
        if e.split()[0] in {"smoke", "promote", "settle", "write_switch", "switch_current"}
    ]
    assert steps == ["smoke", "promote", "settle", "write_switch", "switch_current"]
    assert w.out == [f"switched: {B}"]


def test_a_failed_settle_removes_the_stage_counts_it_unavailable_and_keeps_current() -> None:
    w = World(settles=[Unavailable("uv")])
    policy = Policy(w.log)
    w.run(policy=policy)

    assert w.calls("remove ") == [f"remove {B}"]
    assert policy.prepare_calls == [(B, Unavailable("uv"))]
    assert w.store.rejected_calls == []
    assert w.calls("write_switch") == [] and w.calls("switch_current") == []
    assert w.store.current() == A
    assert w.out == ["prepare stopped"]


def test_an_unused_leftover_of_the_target_is_removed_before_prepare() -> None:
    w = World(staged=[A, B])
    w.run(policy=Policy(w.log))

    assert w.log.index(f"remove {B}") < w.log.index(f"prepare {B}")
    assert w.calls("remove ") == [f"remove {B}"]  # never `current`
    assert w.out == [f"switched: {B}"]


def test_a_leftover_named_in_users_is_kept_and_the_rotation_says_busy() -> None:
    w = World(staged=[A, B], in_use=frozenset({B}))
    w.run(policy=Policy(w.log))

    assert w.out == ["keep: busy"]
    assert w.calls("remove ") == [] and w.calls("prepare") == []


def test_a_leftover_with_unreadable_users_is_kept_and_the_rotation_says_busy() -> None:
    w = World(staged=[A, B], in_use=Unreadable())
    w.run(policy=Policy(w.log))

    assert w.out == ["keep: busy"]
    assert w.calls("remove ") == [] and w.calls("prepare") == []


def test_a_leftover_of_another_commit_is_left_alone() -> None:
    w = World(staged=[A, C])
    w.run(policy=Policy(w.log))

    assert w.calls("remove ") == []
    assert w.out == [f"switched: {B}"]


def test_a_remove_that_raises_propagates_and_the_lock_is_released() -> None:
    w = World(staged=[A, B], fail_on="remove")
    with pytest.raises(Crash):
        w.run(policy=Policy(w.log))

    assert w.log[-1] == "unlock"
    assert w.calls("prepare") == []


# --- the rollback step (GH-738) ----------------------------------------------------------

MIN = timedelta(minutes=1)


def adopted(
    *pollers: str,
    phase: str = "end",
    age: timedelta = MIN / 2,
    current: str | None = A,
    staged: list[str] | None = None,
) -> World:
    """A World whose first rotation adopted B at `T0`, after each of `pollers` beat on A
    `age` before it (written through the double)."""
    w = World(current=current, staged=[A] if staged is None else staged)
    w.clock_at = T0
    for poller in pollers:
        w.beats.write(poller, ROOT / A, T0 - age, phase, 60)
    w.run(now=T0)
    assert w.out == [f"switched: {B}"]
    return w


def again(w: World, entries: list[WindowEntry], *, now: datetime = NOW) -> list[str]:
    """Another rotation on the same World over `entries`; its output."""
    w.window._entries = entries
    w.store._digests = [EMPTY_ENV, EMPTY_ENV]
    w.candidate.smokes = [Green()]
    w.out.clear()
    w.run(now=now)
    return w.out


def test_rollback_fires_for_an_expected_poller_still_on_the_old_checkout() -> None:
    w = adopted("a-poll")
    assert w.store.switch_written == SwitchRecord(A, B, T0, frozenset({"a-poll"}))
    w.beats.write("a-poll", ROOT / A, NOW - MIN, "end", 60)
    runs, calls = w.escalation.runs, w.clock_calls
    assert again(w, GREEN_B) == [f"rollback: {B} -> {A}"]
    assert w.store.current() == A
    assert w.store.rejected() == {B}
    assert w.escalation.runs == runs + 2  # the one every run does, and the one after the swap
    assert w.clock_calls == calls
    assert w.calls("prepare") == [f"prepare {B}"]  # only the first rotation's
    steps = [w.log.index(f"reject {B}"), w.log.index(f"switch_current {A}")]
    assert steps == sorted(steps) and w.log.index("escalation", steps[1]) > steps[1]


@pytest.mark.parametrize(
    ("pollers", "phase", "age", "wait"),
    [
        pytest.param(("a-poll",), "stop", MIN / 2, 20 * MIN, id="last-beat-was-stop"),
        pytest.param(("a-poll",), "end", 3 * MIN, 20 * MIN, id="beat-older-than-twice-interval"),
        pytest.param(("a-poll",), "end", MIN / 2, 10 * MIN, id="grace-not-passed"),
        pytest.param((), "end", MIN / 2, 20 * MIN, id="switch-json-has-no-expected"),
    ],
)
def test_rollback_does_not_fire(
    pollers: tuple[str, ...], phase: str, age: timedelta, wait: timedelta
) -> None:
    w = adopted(*pollers, phase=phase, age=age)
    for poller in pollers:
        w.beats.write(poller, ROOT / A, T0 + wait - MIN, "end", 60)  # still beating on A
    assert again(w, [WindowEntry(B, "green", False)], now=T0 + wait) == ["keep: up_to_date"]
    assert w.store.current() == B and w.store.rejected_calls == []


def test_no_repeat_after_a_rollback() -> None:
    w = adopted("a-poll")
    w.beats.write("a-poll", ROOT / A, NOW - MIN, "end", 60)
    assert again(w, GREEN_B) == [f"rollback: {B} -> {A}"]
    assert again(w, [WindowEntry(A, "green", False)]) == ["keep: up_to_date"]
    assert w.store.rejected_calls == [B] and w.store.current() == A


def test_previous_none_says_so_and_goes_on_to_adopt() -> None:
    w = adopted("a-poll", current=None)
    assert w.store.switch_written == SwitchRecord(None, B, T0, frozenset({"a-poll"}))
    assert again(w, [WindowEntry(C, "green", True)]) == ["rollback: no previous", f"switched: {C}"]
    assert w.store.rejected_calls == []


def test_a_missing_previous_says_so_and_goes_on_to_adopt() -> None:
    w = adopted("a-poll", staged=[])
    assert again(w, [WindowEntry(C, "green", True)]) == [
        "rollback: previous missing",
        f"switched: {C}",
    ]
    assert w.store.rejected_calls == []


def test_a_crash_after_reject_is_finished_by_the_next_run() -> None:
    w = adopted("a-poll")
    w.store._fail_on = "switch_current"
    with pytest.raises(Crash):
        again(w, GREEN_B)
    assert w.store.rejected() == {B} and w.store.current() == B
    w.store._fail_on = None
    assert again(w, GREEN_B) == [f"rollback: {B} -> {A}"]
    assert w.store.current() == A


def test_a_crash_after_switch_current_leaves_the_next_run_inert_and_it_escalates_first() -> None:
    w = adopted("a-poll")
    w.escalation.crash_at = w.escalation.runs + 2
    with pytest.raises(Crash):
        again(w, GREEN_B)
    assert w.store.current() == A
    runs, rejects = w.escalation.runs, list(w.store.rejected_calls)
    assert again(w, [WindowEntry(A, "green", False)]) == ["keep: up_to_date"]
    assert w.escalation.runs == runs + 1
    assert w.log.index("escalation", -12) < w.log.index("window", -12)
    assert w.store.rejected_calls == rejects


@pytest.mark.parametrize(("offset", "expected"), [(30, {"a-poll"}), (90, set())])
def test_the_switch_is_dated_by_the_clock_and_a_beat_up_to_then_is_expected(
    offset: int, expected: set[str]
) -> None:
    w = World()
    w.clock_at = NOW + MIN
    w.beats.write("a-poll", ROOT / A, NOW + timedelta(seconds=offset), "end", 60)
    w.run()
    assert w.store.switch_written == SwitchRecord(A, B, NOW + MIN, frozenset(expected))
    assert w.clock_calls == 1
    seen = [e for e in w.log if e in {"read_all", "clock", f"write_switch {B}"}]
    assert seen == ["read_all", "clock", f"write_switch {B}"]


def test_one_silent_poller_of_two_triggers_the_rollback() -> None:
    w = adopted("a-poll", "b-poll")
    assert w.store.switch_written == SwitchRecord(A, B, T0, frozenset({"a-poll", "b-poll"}))
    w.beats.write("a-poll", ROOT / B, NOW - MIN, "end", 60)  # a came back, b is silent
    w.beats.write("b-poll", ROOT / A, NOW - MIN, "end", 60)
    assert again(w, GREEN_B) == [f"rollback: {B} -> {A}"]


def test_known_risk_stale_beat_on_new_counts_as_arrived() -> None:
    w = adopted("a-poll")
    w.beats.write("a-poll", ROOT / B, T0 + MIN, "end", 60)  # it came, beat once and died
    assert again(w, [WindowEntry(B, "green", False)]) == ["keep: up_to_date"]
    assert w.store.rejected_calls == []


def test_prune_after_a_rollback_spares_both_stages_and_the_record() -> None:
    d = "0" * 40
    w = adopted("a-poll")
    w.store._staged = [A, B, C, d]
    w.users.in_use = frozenset({B})
    w.beats.write("a-poll", ROOT / A, NOW - MIN, "end", 60)
    w.window._entries = GREEN_B
    w.out.clear()
    w.log.clear()
    w.run(prune=make_prune(ROOT))
    assert w.out[0] == f"rollback: {B} -> {A}"
    assert w.calls("remove ") == [f"remove {d}"]
    assert w.store.switch_record() == SwitchRecord(A, B, T0, frozenset({"a-poll"}))


def test_an_infinity_in_a_heartbeat_file_does_not_make_rotate_raise(tmp_path: Path) -> None:
    (tmp_path / "heartbeats").mkdir()
    (tmp_path / "heartbeats" / "a-poll.json").write_text(
        '{"checkout": "/x", "at": "2026-11-13T00:00:00+00:00", "phase": "end", '
        '"interval_s": Infinity}'
    )
    w = World(current=B, staged=[A], record=SwitchRecord(A, B, T0, frozenset({"a-poll"})))
    w.beats = FilesystemHeartbeats(tmp_path)
    w.run()
    assert w.out == [f"rollback: {B} -> {A}"]
