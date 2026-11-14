"""Application tests of `SmokeFailurePolicy` (GH-632, part 2f): the policy alone and
`stage0_rotate.rotate` run with it, over in-memory doubles of the store and the candidate."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.application.stage0_failure import SmokeFailurePolicy
from agentic.core.process.application.stage0_rotate import NoEscalation, rotate
from agentic.core.process.domain.stage0_rotation import (
    Green,
    Prepared,
    Proceed,
    Red,
    Stop,
    SwitchRecord,
    Unavailable,
    Unreadable,
    WindowEntry,
)

MODULE = Path(__file__).parents[4] / "agentic/core/process/application/stage0_failure.py"
FORBIDDEN_IMPORTS = ("os", "fcntl", "subprocess", "shutil")

A = "a" * 40
B = "b" * 40
C = "c" * 40
NOW = datetime(2026, 11, 13, tzinfo=UTC)
OWN = Path("/own")
EMPTY_ENV = hashlib.sha256(b"").digest()
OTHER_ENV = hashlib.sha256(b"other").digest()
GREEN_B = [WindowEntry(B, "green", True), WindowEntry(C, "green", True)]

Outcome = Green | Red | Unavailable


class Store:
    """In-memory `Stage0Store` that records its calls in order."""

    def __init__(
        self,
        log: list[str],
        *,
        current: str | None = A,
        record: tuple[str, int, str] | None = None,
        digests: list[bytes] | None = None,
    ) -> None:
        self.log = log
        self._current = current
        self.record = record
        self._digests = list(digests or [EMPTY_ENV, EMPTY_ENV])
        self.rejected_set: set[str] = set()

    @contextmanager
    def lock(self) -> Iterator[bool]:
        yield True

    def clear_partials(self) -> None:
        self.log.append("clear_partials")

    def remove_partial(self, sha: str) -> None:
        self.log.append(f"remove_partial {sha}")

    def stages(self) -> list[tuple[str, datetime]]:
        return []

    def current(self) -> str | None:
        return self._current

    def rejected(self) -> frozenset[str]:
        return frozenset(self.rejected_set)

    def reject(self, sha: str) -> None:
        self.log.append(f"reject {sha}")
        self.rejected_set.add(sha)

    def switch_record(self) -> SwitchRecord | None:
        return None

    def write_switch(
        self, previous: str | None, new: str, switched_at: datetime, expected: frozenset[str]
    ) -> None:
        self.log.append(f"write_switch {new}")

    def unavailable_record(self) -> tuple[str, int, str] | None:
        return self.record

    def write_unavailable(self, sha: str, count: int, cause: str) -> None:
        self.log.append(f"write_unavailable {sha} {count} {cause}")
        self.record = (sha, count, cause)

    def clear_unavailable(self) -> None:
        self.log.append("clear_unavailable")
        self.record = None

    def escalation_issue(self) -> str | None:
        return None

    def write_escalation_issue(self, url: str) -> None:
        self.log.append(f"write_escalation_issue {url}")

    def copy_env(self, source: Path, dest: str) -> bytes:
        self.log.append(f"copy_env {dest}")
        # The last digest repeats, so a test that rotates several times sees a steady `.env`.
        return self._digests.pop(0) if len(self._digests) > 1 else self._digests[0]

    def promote(self, sha: str) -> None:
        self.log.append(f"promote {sha}")

    def switch_current(self, sha: str) -> None:
        self.log.append(f"switch_current {sha}")
        self._current = sha

    def remove(self, name: str) -> None:
        self.log.append(f"remove {name}")


class Candidate:
    """`Stage0Candidate` whose smoke answers a scripted sequence per stage name."""

    def __init__(
        self,
        log: list[str],
        *,
        smokes: dict[str, list[Outcome]] | None = None,
        prepared: Prepared | Unavailable | None = None,
    ) -> None:
        self.log = log
        self.smokes = {stage: list(seq) for stage, seq in (smokes or {}).items()}
        self.prepared = prepared
        self.stages_smoked: list[str] = []

    def prepare(self, sha: str) -> Prepared | Unavailable:
        self.log.append(f"prepare {sha}")
        return self.prepared if self.prepared is not None else Prepared(sha)

    def smoke(self, stage: str) -> Green | Red | Unavailable:
        self.log.append(f"smoke {stage}")
        self.stages_smoked.append(stage)
        return self.smokes[stage].pop(0)

    def settle(self, sha: str) -> Green | Unavailable:
        self.log.append(f"settle {sha}")
        return Green()


class Window:
    def entries(self, current: str | None) -> list[WindowEntry] | Unreadable:
        return GREEN_B


class Users:
    def users(self) -> frozenset[str] | Unreadable:
        return frozenset()


class World:
    def __init__(
        self,
        smokes: dict[str, list[Outcome]],
        *,
        current: str | None = A,
        record: tuple[str, int, str] | None = None,
        digests: list[bytes] | None = None,
        prepared: Prepared | Unavailable | None = None,
    ) -> None:
        self.log: list[str] = []
        self.out: list[str] = []
        self.store = Store(self.log, current=current, record=record, digests=digests)
        self.candidate = Candidate(self.log, smokes=smokes, prepared=prepared)
        self.policy = SmokeFailurePolicy(self.store, self.candidate)

    def run(self, *, pin: str | None = None) -> None:
        rotate(
            self.store,
            Users(),
            Window(),
            self.candidate,
            NoEscalation(),
            pin=pin,
            now=NOW,
            own=OWN,
            failure_policy=self.policy,
            out=self.out.append,
        )


def partial(sha: str) -> str:
    return f"{sha}.partial"


# --- rotate run with the policy ----------------------------------------------------------


def test_red_twice_and_green_control_rejects_and_keeps_current() -> None:
    world = World({partial(B): [Red(), Red()], A: [Green()]})
    world.run()
    assert world.store.current() == A
    assert world.store.rejected_set == {B}
    assert world.out == [f"smoke_failed: {B}"]
    assert world.candidate.stages_smoked == [partial(B), partial(B), A]
    assert world.log.index(f"remove_partial {B}") < world.log.index(f"reject {B}")
    assert not any(entry.startswith("promote") for entry in world.log)


def test_red_then_green_rerun_continues_the_adoption() -> None:
    world = World({partial(B): [Red(), Green()]})
    world.run()
    assert world.out == [f"smoke_flaked: {B}", f"switched: {B}"]
    assert world.store.current() == B
    assert world.store.rejected_set == set()
    assert f"remove_partial {B}" not in world.log
    assert world.candidate.stages_smoked == [partial(B), partial(B)]


def test_red_twice_and_red_control_is_unavailable_and_rejects_nothing() -> None:
    world = World({partial(B): [Red(), Red()], A: [Red()]})
    world.run()
    assert world.out == ["keep: unavailable"]
    assert world.store.record == (B, 1, "control_red")
    assert world.store.rejected_set == set()
    assert world.log.index(f"remove_partial {B}") < world.log.index(
        f"write_unavailable {B} 1 control_red"
    )


def test_no_current_never_rejects_and_smokes_no_control() -> None:
    world = World({partial(B): [Red(), Red()]}, current=None)
    world.run()
    assert world.out == ["keep: unavailable"]
    assert world.store.record == (B, 1, "control_red")
    assert world.store.rejected_set == set()
    assert world.candidate.stages_smoked == [partial(B), partial(B)]


def test_timeout_is_unavailable_with_no_rerun() -> None:
    world = World({partial(B): [Unavailable("smoke_timeout")]})
    world.run()
    assert world.out == ["keep: unavailable"]
    assert world.candidate.stages_smoked == [partial(B)]
    assert world.store.record == (B, 1, "smoke_timeout")
    assert world.store.rejected_set == set()
    assert f"remove_partial {B}" in world.log


def test_unavailable_rerun_has_no_control_and_records_its_own_cause() -> None:
    world = World({partial(B): [Red(), Unavailable("disk")]})
    world.run()
    assert world.out == ["keep: unavailable"]
    assert world.candidate.stages_smoked == [partial(B), partial(B)]
    assert world.store.record == (B, 1, "disk")
    assert world.store.rejected_set == set()


@pytest.mark.parametrize("control", [Unavailable("smoke_timeout"), Unavailable("disk")])
def test_unavailable_control_records_its_own_cause_and_rejects_nothing(
    control: Unavailable,
) -> None:
    world = World({partial(B): [Red(), Red()], A: [control]})
    world.run()
    assert world.out == ["keep: unavailable"]
    assert world.store.record == (B, 1, control.cause)
    assert world.store.rejected_set == set()
    assert f"remove_partial {B}" in world.log


def test_prepare_failure_is_unavailable_and_counts() -> None:
    world = World({}, prepared=Unavailable("clone"))
    world.run()
    assert world.out == ["keep: unavailable"]
    assert world.store.record == (B, 1, "clone")
    assert world.store.rejected_set == set()


def test_unavailable_count_increments_for_same_sha_resets_for_other_and_clears() -> None:
    world = World({partial(B): [Unavailable("disk")]})
    world.run()
    world.candidate.smokes[partial(B)] = [Unavailable("disk")]
    world.run()
    assert world.store.record == (B, 2, "disk")
    world.store.record = (C, 5, "disk")
    world.candidate.smokes[partial(B)] = [Unavailable("disk")]
    world.run()
    assert world.store.record == (B, 1, "disk")
    world.candidate.smokes[partial(B)] = [Green()]
    world.run()
    assert world.store.record is None
    assert world.out[-1] == f"switched: {B}"


def test_abbreviated_pin_prepare_failure_then_full_sha_converges_the_record() -> None:
    world = World({partial(B): [Red(), Red()], A: [Red()]})
    world.store.record = (B[:10], 1, "clone")
    world.run()
    assert world.store.record == (B, 2, "control_red")


def test_policy_built_before_current_switches_controls_the_new_current() -> None:
    world = World({partial(B): [Red(), Red()], C: [Green()]})
    world.store.switch_current(C)
    world.run()
    assert world.candidate.stages_smoked == [partial(B), partial(B), C]
    assert world.store.rejected_set == {B}


def test_env_drift_asks_the_policy_twice_consistently() -> None:
    world = World(
        {partial(B): [Red(), Green(), Red(), Red()], A: [Green()]},
        digests=[EMPTY_ENV, OTHER_ENV],
    )
    world.run()
    assert world.out == [f"smoke_flaked: {B}", f"smoke_failed: {B}"]
    assert world.store.current() == A
    assert world.store.rejected_set == {B}
    assert world.candidate.stages_smoked == [partial(B)] * 4 + [A]


# --- policy alone ------------------------------------------------------------------------


def test_prepare_failed_removes_partial_records_and_stops() -> None:
    log: list[str] = []
    store = Store(log)
    policy = SmokeFailurePolicy(store, Candidate(log))
    assert policy.prepare_failed(B, Unavailable("uv")) == Stop("keep: unavailable")
    assert log == [f"remove_partial {B}", f"write_unavailable {B} 1 uv"]


def test_smoke_failed_flake_returns_the_rerun_green() -> None:
    log: list[str] = []
    green = Green()
    policy = SmokeFailurePolicy(Store(log), Candidate(log, smokes={partial(B): [green]}))
    verdict = policy.smoke_failed(B, partial(B), Red())
    assert verdict == Proceed(f"smoke_flaked: {B}", green)
    assert isinstance(verdict, Proceed) and verdict.smoke is green
    assert log == [f"smoke {partial(B)}"]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (None, (B, 1)),
        ((B, 1, "disk"), (B, 2)),
        ((B, 7, "clone"), (B, 8)),
        ((B[:8], 3, "disk"), (B, 4)),
        ((A, 4, "disk"), (B, 1)),
        (("", 4, "disk"), (B, 1)),
    ],
)
def test_counter_arithmetic(record: tuple[str, int, str] | None, expected: tuple[str, int]) -> None:
    log: list[str] = []
    store = Store(log, record=record)
    SmokeFailurePolicy(store, Candidate(log)).prepare_failed(B, Unavailable("disk"))
    assert store.record == (*expected, "disk")


def test_a_full_record_sha_is_kept_when_the_new_one_is_abbreviated() -> None:
    log: list[str] = []
    store = Store(log, record=(B, 1, "disk"))
    SmokeFailurePolicy(store, Candidate(log)).prepare_failed(B[:9], Unavailable("disk"))
    assert store.record == (B, 2, "disk")


def test_succeeded_only_clears_the_record_and_is_idempotent() -> None:
    log: list[str] = []
    store = Store(log, record=(B, 3, "disk"))
    policy = SmokeFailurePolicy(store, Candidate(log))
    policy.succeeded(B)
    policy.succeeded(B)
    assert log == ["clear_unavailable", "clear_unavailable"]
    assert store.record is None


# --- the module's own rule ---------------------------------------------------------------


def _imported_modules(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            found.append(node.module)
    return found


def _forbidden(source: str) -> list[str]:
    return [m for m in _imported_modules(source) if m.split(".")[0] in FORBIDDEN_IMPORTS]


def test_the_module_imports_nothing_from_the_outside_world() -> None:
    assert _forbidden(MODULE.read_text()) == []


@pytest.mark.parametrize(
    "source",
    ["import os", "import os.path", "from shutil import rmtree", "import fcntl, sys"],
)
def test_the_import_guard_rejects_a_forbidden_import(source: str) -> None:
    assert _forbidden(source) != []
