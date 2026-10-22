"""The `ProcessReaper` shell adapter against real processes (GH-282, ADR-0052).

Every child is spawned in its own session, as `setsid nohup` would leave it, carrying a
mark in its environment. The rejected inputs are the ones the reaper must never touch: a
sibling with no mark, one of another task, one of another product, and a reap for a task
nobody ran."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic.core.process.adapters.shell import reaper as reaper_module
from agentic.core.process.adapters.shell.reaper import ShellReaper
from agentic.core.process.domain import reaping
from agentic.core.process.domain.stall import ChildActivity

PRODUCT = reaping.product_key("/work/a-product")

STUBBORN = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"
OBEDIENT = "import time; time.sleep(300)"


@pytest.fixture
def children() -> Iterator[list[subprocess.Popen]]:
    spawned: list[subprocess.Popen] = []
    yield spawned
    for child in spawned:
        if child.poll() is None:
            child.kill()
        child.wait()


@pytest.fixture(autouse=True)
def short_waits(monkeypatch):
    monkeypatch.setattr(reaper_module, "REAP_GRACE_S", 1.0)
    monkeypatch.setattr(reaper_module, "REAP_KILL_WAIT_S", 2.0)


def spawn(children, code: str, value: str | None) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != reaping.MARK_NAME}
    if value is not None:
        env[reaping.MARK_NAME] = value
    # `-u` and a ready line: the signal handler is installed before the test signals.
    child = subprocess.Popen(
        [sys.executable, "-c", f"import sys; print('up', flush=True); {code}"],
        env=env,
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == b"up"
    children.append(child)
    return child


def mark_of(task: str, run: int = 1, token: str = "t", product: str = PRODUCT) -> str:
    return reaping.mark(product, task, run, token)


def alive(child: subprocess.Popen) -> bool:
    return child.poll() is None


def wait_gone(child: subprocess.Popen) -> None:
    child.wait(timeout=5)


def test_a_process_that_obeys_term_is_reaped_as_term_and_one_that_ignores_it_as_kill(children):
    obedient = spawn(children, OBEDIENT, mark_of("GH-12"))
    stubborn = spawn(children, STUBBORN, mark_of("GH-12"))
    sibling = spawn(children, OBEDIENT, None)

    reaped = ShellReaper(PRODUCT).reap_task("GH-12")

    by_pid = {entry["pid"]: entry for entry in reaped}
    assert by_pid[obedient.pid]["outcome"] == "term"
    assert by_pid[stubborn.pid]["outcome"] == "kill"
    assert set(by_pid) == {obedient.pid, stubborn.pid}
    assert all(
        "python" in entry["command"] or sys.executable in entry["command"] for entry in reaped
    )
    wait_gone(obedient)
    wait_gone(stubborn)
    assert obedient.returncode == -signal.SIGTERM
    assert stubborn.returncode == -signal.SIGKILL
    assert alive(sibling), "an unmarked process is the system's or the human's: never signalled"


def test_a_reap_for_another_task_signals_nothing(children):
    mine = spawn(children, STUBBORN, mark_of("GH-12"))

    assert ShellReaper(PRODUCT).reap_task("GH-99") == ()
    assert ShellReaper(PRODUCT).reap_task("GH-1") == ()
    assert alive(mine)


def test_a_process_of_another_product_with_the_same_task_is_left_alone(children):
    other = spawn(children, STUBBORN, mark_of("GH-12", product=reaping.product_key("/work/other")))

    assert ShellReaper(PRODUCT).reap_task("GH-12") == ()
    assert alive(other)


def test_the_task_scope_takes_the_subtasks_of_the_task(children):
    sub = spawn(children, OBEDIENT, mark_of("GH-12.reaper"))
    longer = spawn(children, OBEDIENT, mark_of("GH-120"))

    reaped = ShellReaper(PRODUCT).reap_task("GH-12")

    assert [entry["pid"] for entry in reaped] == [sub.pid]
    assert alive(longer)


def test_a_run_scope_reaps_only_the_dead_runs_processes(children):
    dead = spawn(children, OBEDIENT, mark_of("GH-12", run=111))
    live = spawn(children, OBEDIENT, mark_of("GH-12", run=222))

    reaped = ShellReaper(PRODUCT).reap_run("GH-12", 111)

    assert [entry["pid"] for entry in reaped] == [dead.pid]
    assert alive(live)


def test_an_attempt_scope_reaps_only_that_attempts_processes(children):
    first = spawn(children, OBEDIENT, mark_of("GH-12", run=7, token="aa"))
    second = spawn(children, OBEDIENT, mark_of("GH-12", run=7, token="bb"))

    reaped = ShellReaper(PRODUCT).reap_attempt("GH-12", 7, "aa")

    assert [entry["pid"] for entry in reaped] == [first.pid]
    assert alive(second)


def test_a_process_that_survives_kill_is_recorded_and_announced_on_stderr(
    children, monkeypatch, capsys
):
    stubborn = spawn(children, STUBBORN, mark_of("GH-12"))
    # A process KILL cannot take (uninterruptible sleep) looks, to the reaper, like one the
    # scan keeps finding: simulate it by making the signal a no-op.
    monkeypatch.setattr(reaper_module.signal, "pidfd_send_signal", lambda fd, sig: None)
    monkeypatch.setattr(reaper_module, "REAP_GRACE_S", 0.2)
    monkeypatch.setattr(reaper_module, "REAP_KILL_WAIT_S", 0.2)

    reaped = ShellReaper(PRODUCT).reap_task("GH-12")

    assert [(e["pid"], e["outcome"]) for e in reaped] == [(stubborn.pid, "survived")]
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and str(stubborn.pid) in err
    assert alive(stubborn)


def test_a_process_whose_environ_cannot_be_read_is_skipped_not_signalled_nor_recorded(
    children, monkeypatch
):
    victim = spawn(children, OBEDIENT, mark_of("GH-12"))
    real = Path.read_bytes

    def refuse(path: Path) -> bytes:
        if path == Path(f"/proc/{victim.pid}/environ"):
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", refuse)

    assert ShellReaper(PRODUCT).reap_task("GH-12") == ()
    assert alive(victim)


def test_an_unexpected_failure_is_an_error_entry_and_never_an_exception(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(reaper_module.os, "listdir", broken)

    reaped = ShellReaper(PRODUCT).reap_task("GH-12")

    assert reaped == ({"pid": 0, "command": "RuntimeError", "outcome": "error"},)
    assert ShellReaper(PRODUCT).reap_run("GH-12", 1) == reaped
    assert ShellReaper(PRODUCT).reap_attempt("GH-12", 1, "t") == reaped


def test_the_reaper_never_signals_its_own_process(children):
    me = reaping.mark(PRODUCT, "GH-12", os.getpid(), "t")
    os.environ[reaping.MARK_NAME] = me
    try:
        assert ShellReaper(PRODUCT).reap_task("GH-12") == ()
    finally:
        del os.environ[reaping.MARK_NAME]


# -- child_activity (GH-299, ADR-0056) ----------------------------------------------
#
# The "CLI" of these tests is a marked process whose parent is this test process (the
# orchestrator, `parent_pid`); the child it launches inherits the mark, as a tool the
# CLI ran does.

BUSY = "while True: pass"


def spawn_cli(children, mark: str, grandchild: str | None) -> subprocess.Popen:
    launch = f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])" if grandchild else "None"
    return spawn(children, f"import subprocess, time; {launch}; time.sleep(300)", mark)


def activity(task: str = "GH-77", token: str = "t"):
    return ShellReaper(PRODUCT).child_activity(task, 1, token, os.getpid())


def test_child_activity_counts_cpu_of_a_marked_child_but_not_of_the_cli(children):
    mark = mark_of("GH-77")
    spawn_cli(children, mark, BUSY)
    try:
        deadline = time.monotonic() + 10
        first = activity()
        assert first is not None
        grown = first
        while time.monotonic() < deadline and grown.cpu_ticks == first.cpu_ticks:
            time.sleep(0.2)
            grown = activity()
            assert grown is not None
        assert grown.cpu_ticks > first.cpu_ticks
    finally:
        ShellReaper(PRODUCT).reap_task("GH-77")


def test_child_activity_sees_a_running_child(children):
    spawn_cli(children, mark_of("GH-77"), BUSY)
    try:
        deadline = time.monotonic() + 10
        seen = activity()
        while time.monotonic() < deadline and not (seen and seen.running):
            time.sleep(0.05)
            seen = activity()
        assert seen is not None and seen.running
    finally:
        ShellReaper(PRODUCT).reap_task("GH-77")


def test_child_activity_of_a_present_but_idle_child_is_flat_and_not_running(children):
    spawn_cli(children, mark_of("GH-77"), OBEDIENT)
    try:
        time.sleep(0.5)
        first, second = activity(), activity()
        assert first is not None and second is not None
        assert not first.running
        assert first.cpu_ticks == second.cpu_ticks
    finally:
        ShellReaper(PRODUCT).reap_task("GH-77")


def test_child_activity_ignores_the_cli_even_when_it_burns_cpu(children):
    # Rejected input: the marked process whose parent is the orchestrator is the CLI,
    # and its own CPU says nothing about a tool the model is waiting for.
    spawn(children, "exec('while True: pass')", mark_of("GH-77"))
    time.sleep(0.5)
    assert activity() == ChildActivity(cpu_ticks=0, running=False)


def test_child_activity_ignores_children_of_another_attempt_or_task_or_product(children):
    spawn_cli(children, mark_of("GH-77", token="other"), BUSY)
    spawn_cli(children, mark_of("GH-78"), BUSY)
    spawn_cli(children, mark_of("GH-77", product=reaping.product_key("/work/b")), BUSY)
    spawn_cli(children, None, BUSY)
    try:
        time.sleep(0.5)
        assert activity() == ChildActivity(cpu_ticks=0, running=False)
    finally:
        for task in ("GH-77", "GH-78"):
            ShellReaper(PRODUCT).reap_task(task)


def test_child_activity_is_none_when_proc_cannot_be_read(monkeypatch, tmp_path):
    # Unreadable is not "idle": the verdict reads None as activity (invariant 2).
    monkeypatch.setattr(reaper_module, "_PROC", tmp_path / "missing")
    assert activity() is None
