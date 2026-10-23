"""The `ProcessReaper` shell adapter against real processes (GH-282, ADR-0052).

Every child is spawned in its own session, as `setsid nohup` would leave it, carrying a
mark in its environment. The rejected inputs are the ones the reaper must never touch: a
sibling with no mark, one of another task, one of another product, and a reap for a task
nobody ran."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from agentic.core.process.adapters.shell import reaper as reaper_module
from agentic.core.process.adapters.shell.reaper import ShellReaper
from agentic.core.process.domain import reaping
from agentic.core.process.domain.stall import ChildActivity

# One product key per pytest process: a reap here cannot name the marked children of another
# suite running at the same time on this machine, nor can `child_activity` count their CPU.
PRODUCT = reaping.product_key(f"/work/a-product/{uuid4().hex}")
WORKTREES_ROOT = Path("/nonexistent/worktrees")

STUBBORN = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)"
OBEDIENT = "import time; time.sleep(300)"


def kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Children(list):
    """The processes a test spawned, and the one deadline (`WAIT_DEADLINE_S` from the start of
    the test) that bounds every wait of that test, the wait for a ready line or a pid line
    included."""

    def __init__(self) -> None:
        super().__init__()
        self.deadline = time.monotonic() + WAIT_DEADLINE_S


def read_line(child: subprocess.Popen, deadline: float, what: str) -> bytes:
    """A line of the unbuffered stdout of `child`, or a failure naming its pid and what had
    arrived when `deadline` ran out."""
    assert child.stdout is not None
    fd = child.stdout.fileno()
    seen = b""
    while not seen.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        ready = select.select([fd], [], [], max(remaining, 0))[0] if remaining > 0 else []
        assert ready, f"{what} of pid {child.pid} within {WAIT_DEADLINE_S}s; got {seen!r}"
        byte = os.read(fd, 1)
        assert byte, f"pid {child.pid} closed its stdout before {what}; got {seen!r}"
        seen += byte
    return seen.strip()


@pytest.fixture
def children() -> Iterator[Children]:
    spawned = Children()
    yield spawned
    # Each CLI leads its own session and process group (`spawn`), and a grandchild it
    # launched joins that group: the group is what reaches a grandchild no handle knows,
    # also when the CLI is already gone. Test-local: the production reaper never kills a
    # process group (ADR-0052).
    for child in spawned:
        kill_group(child.pid)
    for child in spawned:
        if child.stdout is not None:
            child.stdout.close()
    for child in spawned:
        child.wait()


@pytest.fixture(autouse=True)
def short_waits(monkeypatch):
    monkeypatch.setattr(reaper_module, "REAP_GRACE_S", 1.0)
    monkeypatch.setattr(reaper_module, "REAP_KILL_WAIT_S", 2.0)


def spawn(children, code: str, value: str | None, cwd: Path | None = None) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != reaping.MARK_NAME}
    if value is not None:
        env[reaping.MARK_NAME] = value
    # `-u` and a ready line: the signal handler is installed before the test signals.
    child = subprocess.Popen(
        [sys.executable, "-c", f"import sys; print('up', flush=True); {code}"],
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        bufsize=0,  # no buffer between the pipe and `select` in `read_line`
        start_new_session=True,
    )
    children.append(child)
    assert read_line(child, children.deadline, "the ready line") == b"up"
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

    reaped = ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12")

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

    assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-99") == ()
    assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-1") == ()
    assert alive(mine)


def test_a_process_of_another_product_with_the_same_task_is_left_alone(children):
    other = spawn(children, STUBBORN, mark_of("GH-12", product=reaping.product_key("/work/other")))

    assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12") == ()
    assert alive(other)


def test_the_task_scope_takes_the_subtasks_of_the_task(children):
    sub = spawn(children, OBEDIENT, mark_of("GH-12.reaper"))
    longer = spawn(children, OBEDIENT, mark_of("GH-120"))

    reaped = ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12")

    assert [entry["pid"] for entry in reaped] == [sub.pid]
    assert alive(longer)


def test_a_run_scope_reaps_only_the_dead_runs_processes(children):
    dead = spawn(children, OBEDIENT, mark_of("GH-12", run=111))
    live = spawn(children, OBEDIENT, mark_of("GH-12", run=222))

    reaped = ShellReaper(PRODUCT, WORKTREES_ROOT).reap_run("GH-12", 111)

    assert [entry["pid"] for entry in reaped] == [dead.pid]
    assert alive(live)


def test_an_attempt_scope_reaps_only_that_attempts_processes(children):
    first = spawn(children, OBEDIENT, mark_of("GH-12", run=7, token="aa"))
    second = spawn(children, OBEDIENT, mark_of("GH-12", run=7, token="bb"))

    reaped = ShellReaper(PRODUCT, WORKTREES_ROOT).reap_attempt("GH-12", 7, "aa")

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

    reaped = ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12")

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

    assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12") == ()
    assert alive(victim)


def test_an_unexpected_failure_is_an_error_entry_and_never_an_exception(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(reaper_module.os, "listdir", broken)

    reaped = ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12")

    assert reaped == ({"pid": 0, "command": "RuntimeError", "outcome": "error"},)
    assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_run("GH-12", 1) == reaped
    assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_attempt("GH-12", 1, "t") == reaped


def test_the_reaper_never_signals_its_own_process(children):
    me = reaping.mark(PRODUCT, "GH-12", os.getpid(), "t")
    os.environ[reaping.MARK_NAME] = me
    try:
        assert ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-12") == ()
    finally:
        del os.environ[reaping.MARK_NAME]


# -- child_activity (GH-299, ADR-0057) ----------------------------------------------
#
# The "CLI" of these tests is a marked process whose parent is this test process (the
# orchestrator, `parent_pid`); the child it launches inherits the mark, as a tool the
# CLI ran does.

# What every wait of these tests may take, in all: one deadline per test, shared by its waits.
WAIT_DEADLINE_S = 30.0
POLL_S = 0.05

# The token rides in the command line of every busy loop, so a survivor can be found (and
# told from anything else) by `/proc/*/cmdline`. The regression test's inner run gets a
# fresh one through the environment.
TOKEN_ENV = "AGENTIC_REAPER_TEST_TOKEN"
RUN_TOKEN = os.environ.get(TOKEN_ENV) or uuid4().hex
BUSY = f"while True: pass  # {RUN_TOKEN}"

# The tests the regression test runs in a pytest of their own, and what that run may take:
# a deadline per test plus a minute for the interpreter and the collection.
INNER_TESTS = (
    "test_child_activity_ignores_children_of_another_attempt_or_task_or_product",
    "test_child_activity_counts_cpu_of_a_marked_child_but_not_of_the_cli",
)
INNER_TIMEOUT_S = len(INNER_TESTS) * WAIT_DEADLINE_S + 60


def spawn_cli(
    children, mark: str | None, grandchild: str | None
) -> tuple[subprocess.Popen, int | None]:
    """A marked CLI and, when `grandchild` is code, the pid of the process it launched. The
    grandchild has no stdin and no stdout (the CLI's pipe must not outlive the CLI), and the
    CLI prints its pid once it exists."""
    launch = (
        f"g = subprocess.Popen([sys.executable, '-c', {grandchild!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL); print(g.pid, flush=True)"
        if grandchild
        else "pass"
    )
    child = spawn(children, f"import subprocess, time; {launch}; time.sleep(300)", mark)
    if not grandchild:
        return child, None
    line = read_line(child, children.deadline, "the grandchild pid line")
    assert line.isdigit(), f"the CLI printed no grandchild pid: {line!r}"
    return child, int(line)


def wait_until(deadline: float, what: str, probe: Callable[[], tuple[bool, object]]) -> None:
    """Poll `probe` until it says done; past `deadline` the test fails, naming the last thing
    seen. A wait is never a skip and never a fixed sleep."""
    while True:
        done, seen = probe()
        if done:
            return
        assert time.monotonic() < deadline, f"{what} within {WAIT_DEADLINE_S}s; last seen: {seen}"
        time.sleep(POLL_S)


def wait_for_ticks(deadline: float, pid: int, at_least: int = 1) -> None:
    """Wait for `pid` to have run `at_least` clock ticks: it exists, is scheduled, and
    burns CPU."""
    seen: list[object] = [None]

    def probe() -> tuple[bool, object]:
        try:
            stat = reaper_module._stat(pid)
        except (OSError, ValueError):
            seen[0] = "no /proc entry"
            return False, seen[0]
        seen[0] = f"state {stat.state}, ticks {stat.cpu_ticks}"
        return stat.cpu_ticks >= at_least, seen[0]

    wait_until(deadline, f"pid {pid} has {at_least}+ cpu ticks", probe)


def wait_until_asleep(deadline: float, pid: int) -> None:
    """Wait for `pid` to exist and be asleep: a fresh interpreter is runnable while it starts
    up, and an idle child is one that has got past that."""

    def probe() -> tuple[bool, object]:
        try:
            state = reaper_module._stat(pid).state
        except (OSError, ValueError):
            return False, "no /proc entry"
        return state == "S", f"state {state}"

    wait_until(deadline, f"pid {pid} exists and sleeps", probe)


def activity(task: str = "GH-77", token: str = "t"):
    return ShellReaper(PRODUCT, WORKTREES_ROOT).child_activity(task, 1, token, os.getpid())


def test_child_activity_counts_cpu_of_a_marked_child_but_not_of_the_cli(children):
    mark = mark_of("GH-77")
    _, busy = spawn_cli(children, mark, BUSY)
    assert busy is not None
    try:
        deadline = children.deadline
        first = activity()
        assert first is not None
        grown: list[ChildActivity | None] = [first]

        def probe() -> tuple[bool, object]:
            grown[0] = activity()
            return grown[0] is not None and grown[0].cpu_ticks > first.cpu_ticks, grown[0]

        wait_until(deadline, f"the ticks of the busy child {busy} grow past {first}", probe)
    finally:
        ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-77")


def test_child_activity_sees_a_running_child(children):
    spawn_cli(children, mark_of("GH-77"), BUSY)
    try:
        seen: list[ChildActivity | None] = [None]

        def probe() -> tuple[bool, object]:
            seen[0] = activity()
            return seen[0] is not None and seen[0].running, seen[0]

        wait_until(children.deadline, "the busy child shows as running", probe)
    finally:
        ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-77")


def test_child_activity_of_a_present_but_idle_child_is_flat_and_not_running(children):
    _, idle = spawn_cli(children, mark_of("GH-77"), OBEDIENT)
    assert idle is not None
    try:
        wait_until_asleep(children.deadline, idle)
        first, second = activity(), activity()
        assert first is not None and second is not None
        assert not first.running
        assert first.cpu_ticks == second.cpu_ticks
    finally:
        ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task("GH-77")


def test_child_activity_ignores_the_cli_even_when_it_burns_cpu(children):
    # Rejected input: the marked process whose parent is the orchestrator is the CLI,
    # and its own CPU says nothing about a tool the model is waiting for.
    cli = spawn(children, f"exec({BUSY!r})", mark_of("GH-77"))
    wait_for_ticks(children.deadline, cli.pid)
    assert activity() == ChildActivity(cpu_ticks=0, running=False)


def test_child_activity_ignores_children_of_another_attempt_or_task_or_product(children):
    # Two of these grandchildren (the other product's and the unmarked one) are outside every
    # reap scope by design: they are what `child_activity` must ignore, and only the process
    # group of the `children` fixture takes them down.
    deadline = children.deadline
    busy = [
        spawn_cli(children, mark_of("GH-77", token="other"), BUSY)[1],
        spawn_cli(children, mark_of("GH-78"), BUSY)[1],
        spawn_cli(children, mark_of("GH-77", product=reaping.product_key("/work/b")), BUSY)[1],
        spawn_cli(children, None, BUSY)[1],
    ]
    try:
        for pid in busy:
            assert pid is not None
            wait_for_ticks(deadline, pid)
        assert activity() == ChildActivity(cpu_ticks=0, running=False)
    finally:
        for task in ("GH-77", "GH-78"):
            ShellReaper(PRODUCT, WORKTREES_ROOT).reap_task(task)


def test_child_activity_is_none_when_proc_cannot_be_read(monkeypatch, tmp_path):
    # Unreadable is not "idle": the verdict reads None as activity (invariant 2).
    monkeypatch.setattr(reaper_module, "_PROC", tmp_path / "missing")
    assert activity() is None


# `reap_within` (GH-315): the processes whose cwd is inside a checkout under the worktrees
# root. A `tmp_path` is the root, so every process a test starts is reachable only through
# it, and the `children` fixture reaps whatever a test leaves.


def reap_everything_under(worktrees: Path) -> None:
    """Teardown: whatever a test left running in a checkout under its own `tmp_path`
    worktrees root, a grandchild no `Popen` handle knows included. Never `pkill`."""
    ShellReaper(PRODUCT, worktrees).reap_within(sorted(worktrees.glob("*")))


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    yield worktrees
    reap_everything_under(worktrees)


def checkout(root: Path, name: str, sub: str | None = None) -> Path:
    path = root / name / sub if sub else root / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_an_unmarked_process_in_the_checkout_dies_and_is_reported(children, root, capsys):
    inside = spawn(children, OBEDIENT, None, cwd=checkout(root, "GH-7"))

    reaped = ShellReaper(PRODUCT, root).reap_within([root / "GH-7"])

    assert [(e["pid"], e["outcome"]) for e in reaped] == [(inside.pid, "term")]
    wait_gone(inside)
    assert capsys.readouterr().err == (
        f"reaper: stopped {inside.pid} {reaped[0]['command']} (term)\n"
    )


def test_one_that_ignores_term_is_killed_and_reported_as_kill(children, root):
    stubborn = spawn(children, STUBBORN, None, cwd=checkout(root, "GH-7"))

    reaped = ShellReaper(PRODUCT, root).reap_within([root / "GH-7"])

    assert [(e["pid"], e["outcome"]) for e in reaped] == [(stubborn.pid, "kill")]
    wait_gone(stubborn)


def test_a_marked_process_in_the_same_cwd_survives(children, root):
    cwd = checkout(root, "GH-7")
    unmarked = spawn(children, OBEDIENT, None, cwd=cwd)
    marked = spawn(children, OBEDIENT, mark_of("GH-7"), cwd=cwd)

    reaped = ShellReaper(PRODUCT, root).reap_within([root / "GH-7"])

    assert [e["pid"] for e in reaped] == [unmarked.pid]
    assert alive(marked), "a marked process belongs to the mark reap"


def test_a_sibling_checkout_whose_name_extends_the_root_survives(children, root):
    sibling = spawn(children, OBEDIENT, None, cwd=checkout(root, "GH-70"))

    assert ShellReaper(PRODUCT, root).reap_within([root / "GH-7"]) == ()
    assert alive(sibling), "GH-70 is not inside GH-7"


def test_a_process_in_a_subdirectory_of_the_checkout_dies(children, root):
    deep = spawn(children, OBEDIENT, None, cwd=checkout(root, "GH-7", "web"))

    reaped = ShellReaper(PRODUCT, root).reap_within([root / "GH-7"])

    assert [e["pid"] for e in reaped] == [deep.pid]
    wait_gone(deep)


def test_a_root_outside_the_worktrees_root_is_dropped_and_nothing_there_is_touched(
    children, root, tmp_path
):
    product = tmp_path / "product"
    other = product / ".agentic" / "other"
    other.mkdir(parents=True)
    in_product = spawn(children, OBEDIENT, None, cwd=product)
    in_other = spawn(children, OBEDIENT, None, cwd=other)
    reaper = ShellReaper(PRODUCT, root)

    assert reaper.reap_within([product]) == ()
    assert reaper.reap_within([other]) == ()
    assert reaper.reap_within([root]) == (), "the worktrees root itself is no checkout"
    assert reaper.reap_within([root / ".." / "product"]) == ()
    assert alive(in_product)
    assert alive(in_other)


def test_a_root_that_escapes_through_dotdot_or_a_symlink_is_dropped(children, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    bystander = spawn(children, OBEDIENT, None, cwd=outside)

    assert ShellReaper(PRODUCT, root).reap_within([root / "link"]) == ()
    assert ShellReaper(PRODUCT, root).reap_within([root / "GH-7" / ".." / ".." / "outside"]) == ()
    assert alive(bystander)


def test_no_roots_reap_nothing(children, root):
    inside = spawn(children, OBEDIENT, None, cwd=checkout(root, "GH-7"))

    assert ShellReaper(PRODUCT, root).reap_within([]) == ()
    assert alive(inside)


def test_a_worktrees_root_reached_through_a_symlinked_parent_still_matches(
    children, tmp_path, request
):
    real = tmp_path / "real"
    (real / "worktrees" / "GH-7").mkdir(parents=True)
    request.addfinalizer(lambda: reap_everything_under(real / "worktrees"))
    link = tmp_path / "link"
    link.symlink_to(real)
    inside = spawn(children, OBEDIENT, None, cwd=real / "worktrees" / "GH-7")

    reaped = ShellReaper(PRODUCT, link / "worktrees").reap_within([link / "worktrees" / "GH-7"])

    assert [e["pid"] for e in reaped] == [inside.pid]
    wait_gone(inside)


def test_the_system_and_its_ancestors_are_never_signalled_even_in_a_checkout(
    children, root, monkeypatch
):
    cwd = checkout(root, "GH-7")
    monkeypatch.chdir(cwd)  # this very process now has its cwd inside the checkout
    bystander = spawn(children, OBEDIENT, None, cwd=cwd)

    reaped = ShellReaper(PRODUCT, root).reap_within([cwd])

    assert [e["pid"] for e in reaped] == [bystander.pid], "only the bystander, not pytest"


def test_a_process_with_a_controlling_terminal_is_never_signalled(children, root, monkeypatch):
    cwd = checkout(root, "GH-7")
    inside = spawn(children, OBEDIENT, None, cwd=cwd)
    stat_of = reaper_module._stat
    monkeypatch.setattr(
        reaper_module,
        "_stat",
        lambda pid: stat_of(pid)._replace(tty=34816) if pid == inside.pid else stat_of(pid),
    )

    assert ShellReaper(PRODUCT, root).reap_within([cwd]) == ()
    assert alive(inside)


def test_a_process_whose_directory_was_deleted_is_not_a_checkouts(children, root):
    cwd = checkout(root, "GH-7", "gone")
    gone = spawn(children, OBEDIENT, None, cwd=cwd)
    cwd.rmdir()

    assert ShellReaper(PRODUCT, root).reap_within([root / "GH-7"]) == ()
    assert alive(gone)


def test_a_child_born_during_the_grace_inside_the_checkout_is_reaped_too(
    children, root, monkeypatch
):
    # The parent outlives TERM, so the sweep always waits out the whole grace: a long one
    # lets a loaded machine get the child born before it ends.
    monkeypatch.setattr(reaper_module, "REAP_GRACE_S", 5.0)
    cwd = checkout(root, "GH-7")
    code = (
        "import signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, lambda *a: subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(300)'])); "
        "open('armed', 'w').close(); time.sleep(300)"
    )
    parent = spawn(children, code, None, cwd=cwd)
    # `spawn`'s ready line comes before the handler exists; a TERM that beat the handler
    # would kill the parent with no child born (a race seen on a slow CI runner).
    deadline = time.monotonic() + 10
    while not (cwd / "armed").exists():
        assert time.monotonic() < deadline, "the handler was never installed"
        time.sleep(0.01)

    reaped = ShellReaper(PRODUCT, root).reap_within([cwd])

    assert parent.pid in {e["pid"] for e in reaped}
    assert len(reaped) == 2
    assert all(e["outcome"] in {"term", "kill"} for e in reaped)


# -- the suite leaves nothing behind (GH-322) ----------------------------------------
#
# GH-315's teardown only saw processes whose cwd was under a `tmp_path` root; the busy-loop
# grandchildren of `spawn_cli` kept the real worktree as cwd and outlived the suite. The
# regression test runs the tests that start them in a pytest of its own and looks, by the
# token in their command line, for what is still alive afterwards.

SETTLE_S = 3.0
KILL_WAIT_S = 10.0


def cmdline_of(pid: int) -> bytes | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:  # gone, or not ours to read
        return None


def token_holders(token: str) -> list[int]:
    """The pids (this one excluded) whose command line carries `token`."""
    needle = token.encode()
    holders = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        cmdline = cmdline_of(int(name))
        if cmdline is not None and needle in cmdline:
            holders.append(int(name))
    return holders


def kill_token_holders(token: str) -> None:
    """SIGKILL every holder of `token`, through a pidfd and only after the pidfd is open and
    the command line still has the token: the pid cannot have been reused by then. Never
    `pkill`, never a bare command text."""
    needle = token.encode()
    for pid in token_holders(token):
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            continue
        try:
            cmdline = cmdline_of(pid)
            if cmdline is not None and needle in cmdline:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except OSError:
            pass
        finally:
            os.close(fd)


def detached_holder(token: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep(300)  # {token}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
    )


def test_a_run_of_the_child_activity_tests_leaves_no_busy_loop_behind(tmp_path):
    if os.environ.get(TOKEN_ENV):
        pytest.skip("this is the inner run: the guard that stops the test recursing into itself")
    # Positive control: the scanner this test trusts finds a holder and kills it, and leaves a
    # bystander (another token) alone. Without it, "no survivors" could mean "blind scan".
    control, other = uuid4().hex, uuid4().hex
    holder, bystander = detached_holder(control), detached_holder(other)
    try:
        assert token_holders(control) == [holder.pid]
        kill_token_holders(control)
        assert holder.wait(timeout=KILL_WAIT_S) == -signal.SIGKILL
        assert token_holders(control) == []
        assert bystander.poll() is None
        assert token_holders(other) == [bystander.pid]
    finally:
        for process in (holder, bystander):
            process.kill()
            process.wait()
    token = uuid4().hex
    junit = tmp_path / "j.xml"
    log_path = tmp_path / "inner.log"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *(f"{__file__}::{name}" for name in INNER_TESTS),
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
    ]
    timed_out = False
    survivors: dict[int, bytes | None] = {}
    with log_path.open("wb") as log:
        inner = subprocess.Popen(
            command,
            env={**os.environ, TOKEN_ENV: token},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            try:
                inner.wait(timeout=INNER_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            # Whatever happened above, the inner run's group goes, then the survivors.
            kill_group(inner.pid)
            inner.wait()
            settle = time.monotonic() + SETTLE_S
            survivors = {pid: cmdline_of(pid) for pid in token_holders(token)}
            while survivors and time.monotonic() < settle:
                time.sleep(POLL_S)
                survivors = {pid: cmdline_of(pid) for pid in token_holders(token)}
            kill_token_holders(token)

    output = log_path.read_text(errors="replace")[-2000:]
    assert not timed_out, f"inner run timed out after {INNER_TIMEOUT_S}s:\n{output}"
    assert inner.returncode == 0, f"inner run failed ({inner.returncode}):\n{output}"
    root = ET.parse(junit).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None
    counts = {k: int(suite.get(k, "-1")) for k in ("tests", "failures", "errors", "skipped")}
    assert counts == {"tests": len(INNER_TESTS), "failures": 0, "errors": 0, "skipped": 0}, output
    assert not survivors, f"processes of the inner run outlived it: {survivors}"
