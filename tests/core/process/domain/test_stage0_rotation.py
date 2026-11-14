"""Table-driven tests of the pure stage0 rotation rules (GH-622, part 1a)."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.domain import stage0_rotation as sr
from agentic.core.process.domain.stage0_rotation import (
    CiState,
    Heartbeat,
    Keep,
    Target,
    Unreadable,
    WindowEntry,
    expected_pollers,
    partial_name,
    prunable,
    reexec_due,
    rollback_due,
    target,
)

A = "a" * 40
B = "b" * 40
C = "c" * 40
D = "d" * 40
E = "e" * 40


def w(sha: str, ci: CiState, descends: bool | None = True) -> WindowEntry:
    return WindowEntry(sha, ci, descends)


@pytest.mark.parametrize(
    ("window", "current", "pin", "rejected", "expected"),
    [
        # pending head with an older green adopted
        ([w(D, "pending"), w(C, "green"), w(B, "green")], A, None, frozenset(), Target(C)),
        # cancelled is not red: skipped like pending, the older green is adopted
        ([w(D, "cancelled"), w(C, "green")], A, None, frozenset(), Target(C)),
        ([w(D, "red"), w(C, "green")], A, None, frozenset(), Target(C)),
        ([w(D, "unknown"), w(C, "green")], A, None, frozenset(), Target(C)),
        ([w(D, "green", None), w(C, "green")], A, None, frozenset(), Target(C)),
        # the newest green wins
        ([w(D, "green"), w(C, "green")], A, None, frozenset(), Target(D)),
        # a pin goes backwards, whatever its CI or ancestry
        ([w(D, "green"), w(B, "red", False)], D, B, frozenset(), Target(B)),
        ([w(D, "green")], D, C, frozenset(), Target(C)),
        # pin equal to current -> pinned, also when rejected, also abbreviated
        ([w(D, "green")], A, A, frozenset(), Keep("pinned")),
        ([w(D, "green")], A, A, frozenset({A}), Keep("pinned")),
        ([w(D, "green")], A, "aaaaaaa", frozenset(), Keep("pinned")),
        # abbreviated pin that differs: lowercased target
        ([], A, "BBBBBBB", frozenset(), Target("bbbbbbb")),
        # malformed pins: unreadable, never 'no pin'
        ([w(D, "green")], A, "zzzzzzz", frozenset(), Keep("unreadable")),
        ([w(D, "green")], A, "", frozenset(), Keep("unreadable")),
        ([w(D, "green")], A, "abc12", frozenset(), Keep("unreadable")),
        ([w(D, "green")], A, "a" * 41, frozenset(), Keep("unreadable")),
        # pin in rejected
        ([w(D, "green")], A, B, frozenset({B}), Keep("rejected")),
        # rejected candidate skipped, the older green adopted
        ([w(D, "green"), w(C, "green")], A, None, frozenset({D}), Target(C)),
        # only rejected -> rejected
        ([w(D, "green"), w(C, "red")], A, None, frozenset({D}), Keep("rejected")),
        # rejected by prefix and by case
        ([w(D, "green")], A, None, frozenset({"ddddddd"}), Keep("rejected")),
        ([w(D, "green")], A, None, frozenset({"DDDDDDD"}), Keep("rejected")),
        # current is at the front: nothing descends
        ([w(C, "red", False), w(A, "green", False)], A, None, frozenset(), Keep("up_to_date")),
        ([], A, None, frozenset(), Keep("up_to_date")),
        # unreadable ancestry or a cancelled descendant: no green in the window
        ([w(D, "green", None)], A, None, frozenset(), Keep("no_green_in_window")),
        ([w(D, "cancelled", True)], A, None, frozenset(), Keep("no_green_in_window")),
        (
            [w(D, "pending", True), w(C, "green", False)],
            A,
            None,
            frozenset(),
            Keep("no_green_in_window"),
        ),
        # no current known: a pin differs from it
        ([], None, B, frozenset(), Target(B)),
    ],
)
def test_target(
    window: list[WindowEntry],
    current: str | None,
    pin: str | None,
    rejected: frozenset[str],
    expected: Target | Keep,
) -> None:
    assert target(window, current, pin, rejected) == expected


def test_partial_name() -> None:
    assert partial_name(A) == A + ".partial"


@pytest.mark.parametrize("pin", [None, "", "zz", A[:7], B])
@pytest.mark.parametrize(
    "window",
    [
        [],
        [WindowEntry(B, "green", True)],
        [WindowEntry(B, "red", True), WindowEntry(C, "green", None)],
        [WindowEntry(B, "green", False)],
    ],
)
def test_target_never_returns_unavailable(window: list[WindowEntry], pin: str | None) -> None:
    for rejected in (frozenset(), frozenset({B})):
        result = target(window, A, pin, rejected)
        assert result != Keep("unavailable")


T0 = datetime(2026, 11, 1, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def stages(*items: tuple[str, int]) -> list[tuple[str, datetime]]:
    return [(name, at(m)) for name, m in items]


@pytest.mark.parametrize(
    ("items", "current", "own", "users", "keep", "previous", "expected"),
    [
        # keep-2 rule: the two newest survive
        (
            [(A, 1), (B, 2), (C, 3), (D, 4), (E, 5)],
            None,
            None,
            frozenset(),
            2,
            None,
            {A, B, C},
        ),
        # current, own and users are never pruned
        (
            [(A, 1), (B, 2), (C, 3), (D, 4), (E, 5)],
            A,
            B,
            frozenset({C}),
            2,
            None,
            set(),
        ),
        # .partial newer than all does not take a keep slot and is never returned
        ([(A, 1), (B, 2), (C, 3), (D + ".partial", 9)], None, None, frozenset(), 2, None, {A}),
        # uppercase 40-char and abbreviated names are not candidates
        (
            [("A" * 40, 1), ("abc1234", 1), (B, 2), (C, 3), (D, 4)],
            None,
            None,
            frozenset(),
            2,
            None,
            {B},
        ),
        # a previous stage, older than the keep newest, is protected
        ([(A, 1), (B, 2), (C, 3), (D, 4)], D, None, frozenset(), 2, A, {B}),
        # abbreviated users entry protects its stage
        ([(A, 1), (B, 2), (C, 3), (D, 4)], None, None, frozenset({"aaaaaaa"}), 2, None, {B}),
        # keep=0 prunes every unprotected candidate
        ([(A, 1), (B, 2)], A, None, frozenset(), 0, None, {B}),
        # nothing to prune is decided, not unknown
        ([(A, 1)], None, None, frozenset(), 2, None, set()),
        ([], None, None, frozenset(), 2, None, set()),
    ],
)
def test_prunable(
    items: list[tuple[str, int]],
    current: str | None,
    own: str | None,
    users: frozenset[str],
    keep: int,
    previous: str | None,
    expected: set[str],
) -> None:
    result = prunable(stages(*items), current, own, users, keep=keep, previous=previous)
    assert result == frozenset(expected)


def test_prunable_tie_on_mtime_is_deterministic() -> None:
    forward = [(A, T0), (B, T0), (C, T0)]
    assert prunable(forward, None, None, frozenset()) == frozenset({A})
    assert prunable(list(reversed(forward)), None, None, frozenset()) == frozenset({A})


def test_prunable_unreadable_users_deletes_nothing() -> None:
    result = prunable(stages((A, 1), (B, 2), (C, 3), (D, 4)), None, None, Unreadable())
    assert result == Unreadable()


@pytest.mark.parametrize("naive_name", [A, A + ".partial"])
def test_prunable_naive_mtime_is_unreadable(naive_name: str) -> None:
    items = [(naive_name, datetime(2026, 11, 1)), (B, at(2)), (C, at(3)), (D, at(4))]
    assert prunable(items, None, None, frozenset()) == Unreadable()


P1 = Path("/s/1")
P2 = Path("/s/2")
LINK = Path("/link/poller.py")
REAL = Path("/s/1/poller.py")


@pytest.mark.parametrize(
    ("argv0_abs", "argv0_real", "current_target", "own", "expected"),
    [
        (REAL, REAL, None, P1, None),
        (REAL, REAL, P1, P1, None),
        (REAL, REAL, P2, P1, P2),
        # argv0 through a link triggers a re-exec even on the current checkout
        (LINK, REAL, P1, P1, P1),
        (LINK, REAL, P2, P1, P2),
        (LINK, REAL, None, P1, None),
    ],
)
def test_reexec_due(
    argv0_abs: Path, argv0_real: Path, current_target: Path | None, own: Path, expected: Path | None
) -> None:
    result = reexec_due(argv0_abs, argv0_real, current_target, own)
    assert result == expected
    if result is not None:
        # fixed point: after the re-exec the process runs from `result`, argv0 resolved
        assert reexec_due(argv0_real, argv0_real, current_target, result) is None


def beat(poller: str, checkout: Path, when: datetime, interval_s: float = 60) -> Heartbeat:
    return Heartbeat(poller, checkout, when, interval_s)


SWITCH = at(100)


@pytest.mark.parametrize(
    ("heartbeat", "expected"),
    [
        (beat("p", P1, SWITCH - timedelta(seconds=119)), True),
        (beat("p", P1, SWITCH - timedelta(seconds=120)), True),  # border included
        (beat("p", P1, SWITCH - timedelta(seconds=121)), False),
        (beat("p", P1, SWITCH + timedelta(seconds=1)), False),  # after the switch
        (beat("p", P1, SWITCH), False),
        (beat("p", P1, SWITCH - timedelta(seconds=1), interval_s=0), False),
        (beat("p", P1, SWITCH - timedelta(seconds=1), interval_s=-5), False),
        (beat("p", P1, datetime(2026, 11, 1, 1, 39)), False),  # naive at
        (Unreadable(), False),
    ],
)
def test_expected_pollers(heartbeat: Heartbeat | Unreadable, expected: bool) -> None:
    assert (expected_pollers({"p": heartbeat}, SWITCH) == frozenset({"p"})) is expected


def test_expected_pollers_naive_switched_at_raises() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        expected_pollers({}, datetime(2026, 11, 1))


GRACE = timedelta(minutes=15)
NOW_PAST = SWITCH + GRACE + timedelta(seconds=1)


@pytest.mark.parametrize(
    ("now", "expected_set", "heartbeats", "due"),
    [
        # inside the grace: never, even with everything stuck
        (SWITCH + timedelta(minutes=1), {"p"}, {"p": beat("p", P1, SWITCH)}, False),
        (SWITCH + GRACE, {"p"}, {"p": beat("p", P1, SWITCH)}, False),  # exact border
        # past the grace: a live expected poller stuck on the old checkout
        (NOW_PAST, {"p"}, {"p": beat("p", P1, NOW_PAST)}, True),
        # a stopped poller (not in expected) never causes a rollback
        (NOW_PAST, {"p"}, {"p": beat("p", P2, NOW_PAST), "q": beat("q", P1, SWITCH)}, False),
        (NOW_PAST, set(), {"q": beat("q", P1, SWITCH)}, False),
        # an expected poller with no heartbeat, or an unreadable one: absent
        (NOW_PAST, {"p"}, {}, True),
        (NOW_PAST, {"p"}, {"p": Unreadable()}, True),
        # all on the new checkout
        (NOW_PAST, {"p", "q"}, {"p": beat("p", P2, SWITCH), "q": beat("q", P2, SWITCH)}, False),
        # one of two missing
        (NOW_PAST, {"p", "q"}, {"p": beat("p", P2, SWITCH)}, True),
    ],
)
def test_rollback_due(
    now: datetime,
    expected_set: set[str],
    heartbeats: Mapping[str, Heartbeat | Unreadable],
    due: bool,
) -> None:
    assert rollback_due(SWITCH, P2, frozenset(expected_set), heartbeats, now) is due


@pytest.mark.parametrize(
    ("switched_at", "now"),
    [(SWITCH, datetime(2026, 11, 2)), (datetime(2026, 11, 1), NOW_PAST)],
)
def test_rollback_due_naive_datetime_raises(switched_at: datetime, now: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        rollback_due(switched_at, P2, frozenset({"p"}), {}, now)


@pytest.mark.parametrize(
    ("product", "key"),
    [
        ("apostrophe", "apostrophe-poll"),
        ("agentic", "agentic-poll"),
        ("my-app2", "my-app2-poll"),
        ("Apostrophe", None),
        ("my app", None),
        ("", None),
        ("a_b", None),
    ],
)
def test_heartbeat_key(product: str, key: str | None) -> None:
    assert sr.heartbeat_key(product, "poll") == key


EXE = Path("/rot/aaa/.venv/bin/agentic")
PRODUCT = Path("/work/product")


def test_reexec_argv_is_absolute_and_never_detaches() -> None:
    argv = sr.reexec_argv(EXE, PRODUCT, 60.0)
    assert argv == [str(EXE), "poll", "--product-dir", str(PRODUCT), "--interval", "60.0"]
    assert "--detach" not in argv and "--dry-run" not in argv


@pytest.mark.parametrize(("exe", "product"), [(Path("agentic"), PRODUCT), (EXE, Path("product"))])
def test_reexec_argv_refuses_a_relative_path(exe: Path, product: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        sr.reexec_argv(exe, product, 60.0)


OLD_BIN = Path("/old/.venv/bin")
TARGET = Path("/rot/aaa")
RESOLVED = {
    "AGENTIC_PRODUCT_DIR": PRODUCT,
    "AGENTIC_LOG_FILE": Path("/work/log/poll.log"),
    "AGENTIC_SCRATCH_DIR": Path("/work/scratch"),
    "AGENTIC_RUN_REGISTRY_DIR": Path("/work/runs"),
}


def test_reexec_env_drops_the_venv_and_marks_the_origin() -> None:
    before = {
        "VIRTUAL_ENV": "/old/.venv",
        "PATH": "/old/.venv/bin:/usr/bin:/bin",
        "HOME": "/home/x",
        "AGENTIC_PRODUCT_DIR": "relative/product",
        "AGENTIC_LOG_FILE": "poll.log",
        "AGENTIC_MAX_CONCURRENT_RUNS": "3",
    }
    snapshot = dict(before)
    env = sr.reexec_env(before, RESOLVED, OLD_BIN, TARGET)
    assert before == snapshot  # a copy, never the caller's mapping
    assert "VIRTUAL_ENV" not in env
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/x" and env["AGENTIC_MAX_CONCURRENT_RUNS"] == "3"
    assert env["AGENTIC_REEXEC_FROM"] == str(TARGET)
    for name in sr.REEXEC_PATH_VARS:
        assert env[name] == str(RESOLVED[name])


def test_reexec_env_without_a_log_file_leaves_none() -> None:
    resolved = {k: v for k, v in RESOLVED.items() if k != "AGENTIC_LOG_FILE"}
    env = sr.reexec_env({"AGENTIC_LOG_FILE": "poll.log"}, resolved, OLD_BIN, TARGET)
    assert "AGENTIC_LOG_FILE" not in env and "PATH" not in env


def test_reexec_env_refuses_a_relative_resolved_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        sr.reexec_env({}, {"AGENTIC_PRODUCT_DIR": Path("p")}, OLD_BIN, TARGET)


@pytest.mark.parametrize(
    ("marker", "blocked"),
    [(str(TARGET), True), (None, False), ("/rot/bbb", False), ("", False)],
)
def test_reexec_blocked(marker: str | None, blocked: bool) -> None:
    assert sr.reexec_blocked(TARGET, marker) is blocked


def test_inherited_zombies() -> None:
    states = {1: "Z", 2: "S", 3: "Z", 9: "Z"}
    assert sr.inherited_zombies(frozenset({1, 2, 3, 4}), states) == frozenset({1, 3})
    assert sr.inherited_zombies(frozenset(), states) == frozenset()


FORBIDDEN_CALLS = {"resolve", "exists", "is_symlink", "stat", "open", "now", "utcnow"}
CI_MODULE = "agentic.core.process.domain.ci"


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"os", "time"} or alias.name == CI_MODULE:
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in {"os", "time"} or module == CI_MODULE:
                found.append(module)
            if node.level and (
                module == "ci" or (not module and any(a.name == "ci" for a in node.names))
            ):
                found.append("relative ci")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in FORBIDDEN_CALLS:
                found.append(name + "()")
        elif isinstance(node, ast.Attribute) and node.attr == "environ":
            found.append("environ")
        elif isinstance(node, ast.Name) and node.id == "environ":
            found.append("environ")
    return found


def test_module_is_pure() -> None:
    assert _violations(Path(sr.__file__).read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import time",
        "from os import environ",
        "from agentic.core.process.domain.ci import CiVerdict",
        "from . import ci",
        "from .ci import CiVerdict",
        "p.resolve()",
        "p.exists()",
        "p.is_symlink()",
        "p.stat()",
        "open('x')",
        "datetime.now()",
        "datetime.utcnow()",
        "x = environ",
    ],
)
def test_purity_check_rejects_impure_sources(source: str) -> None:
    assert _violations(source) != []
