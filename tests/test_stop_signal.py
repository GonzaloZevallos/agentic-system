"""A real SIGTERM, sent from outside, stops a real `agentic run` process (GH-355).

The seam is a child process that runs `cli._signal_guarded` with the real `ShellCommands`,
the real reaper and a real run lock over a temporary git repository (`tests/stop_signal_child`).
The test sends it the signal while it is busy on a `sleep 300`, as an operator's `kill -TERM`
would, and never signals the pytest process itself. In every case the process ends with exit
code 1 within the time the pause procedure promises, what it ran is gone, and it left nothing
behind: no commit, a clean checkout, a released lock, the task document as it was, no flake.
"""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agentic.core.process.domain.document import TASKS_SUBDIR

REPO = Path(__file__).resolve().parent.parent
TASK = "GH-1"
DOCUMENT = "# GH-1\n\nstatus: planned\n"
PROMPT_S = 30  # the cases whose command dies of the signal's own reap
BOUND_S = 60  # the cases that wait for the hard deadline
# The hard deadline the child is given in the cases that reach it: the tests do not wait
# the 40 s production declares (`reaping.STOP_DEADLINE_S`).
DEADLINE_S = 3.0


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def product(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / "README.md").write_text("product\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    (root / TASKS_SUBDIR).mkdir(parents=True)
    (root / TASKS_SUBDIR / f"{TASK}.md").write_text(DOCUMENT)
    return root


@dataclass
class Child:
    """The process under test and the files it leaves for the test to read."""

    process: subprocess.Popen[bytes]
    workdir: Path
    product: Path
    pins: dict[str, int] = field(default_factory=dict)

    def pid_of(self, name: str, within_s: float = 60.0) -> int:
        path = self.workdir / f"{name}.pid"
        end = time.monotonic() + within_s
        while time.monotonic() < end:
            if path.exists() and path.read_text().strip():
                return int(path.read_text())
            assert self.process.poll() is None, f"child ended before {name} started"
            time.sleep(0.05)
        raise AssertionError(f"{name} never started")

    def pin(self, name: str) -> None:
        """Wait for the process to exist and hold a pidfd on it: the proof it is gone later."""
        self.pins[name] = os.pidfd_open(self.pid_of(name))


def _gone(fd: int) -> bool:
    return bool(select.select([fd], [], [], 5)[0])


def _environment(tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTIC_")}
    env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    return env


def _spawn(
    mode: str, scenario: str, product: Path, workdir: Path, tmp_path: Path
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.stop_signal_child",
            mode,
            scenario,
            str(product),
            str(workdir),
            str(DEADLINE_S),
        ],
        cwd=REPO,
        env=_environment(tmp_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def child(tmp_path: Path, product: Path) -> Iterator[Callable[[str], Child]]:
    started: list[Child] = []

    def start(scenario: str) -> Child:
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        made = Child(_spawn("run", scenario, product, workdir, tmp_path), workdir, product)
        started.append(made)
        return made

    yield start
    for made in started:
        if made.process.poll() is None:
            made.process.kill()
        made.process.wait()
        if made.process.stderr is not None:
            made.process.stderr.close()
        for fd in made.pins.values():
            os.close(fd)
        for stray in made.workdir.glob("*.pid"):
            try:
                os.kill(int(stray.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def _stop(made: Child) -> tuple[int, float]:
    """The operator's `kill -TERM <pid>`, once: the exit code and the seconds it took."""
    sent = time.monotonic()
    os.kill(made.process.pid, signal.SIGTERM)
    code = made.process.wait(timeout=BOUND_S + 30)
    return code, time.monotonic() - sent


def _snapshot(product: Path) -> dict[str, str]:
    return {
        "head": _git(product, "rev-parse", "HEAD"),
        "document": (product / TASKS_SUBDIR / f"{TASK}.md").read_text(),
    }


def _assert_left_nothing(made: Child, before: dict[str, str]) -> None:
    product = made.product
    after = _snapshot(product)
    assert after["head"] == before["head"]
    assert after["document"] == DOCUMENT
    assert not (product / ".agentic" / "runs" / f"{TASK}.lock").exists(), "lock still held"
    checkout = product / ".agentic" / "worktrees" / TASK
    if checkout.exists():
        assert _git(checkout, "status", "--porcelain") == ""
        assert _git(checkout, "rev-parse", "HEAD") == before["head"]
    report = made.workdir / "report.json"
    if report.exists():
        assert json.loads(report.read_text())["flakes"] == []


def _resume(made: Child, scenario: str, tmp_path: Path) -> dict[str, object]:
    process = _spawn("resume", scenario, made.product, made.workdir, tmp_path)
    _, error = process.communicate(timeout=120)
    assert process.returncode == 0, error.decode()
    return json.loads((made.workdir / "resume.json").read_text())


def test_a_signal_during_a_role_turn_stops_the_delivery_gate_and_its_check(child, product):
    before = _snapshot(product)
    made = child("gate")
    made.pin("check")

    code, seconds = _stop(made)

    assert code == 1
    assert seconds < PROMPT_S
    assert _gone(made.pins["check"])
    _assert_left_nothing(made, before)


def test_a_signal_during_the_full_checks_node_leaves_the_checkpoint_to_repeat_it(
    child, product, tmp_path
):
    before = _snapshot(product)
    made = child("full_checks")
    made.pin("check")

    code, seconds = _stop(made)

    assert code == 1
    assert seconds < PROMPT_S
    assert _gone(made.pins["check"])
    _assert_left_nothing(made, before)
    resumed = _resume(made, "full_checks", tmp_path)
    # The stopped node wrote nothing: no record, no error, no pending write...
    assert resumed["before"] is None
    assert resumed["errors"] == []
    assert resumed["pending_writes"] == 0
    # ...so the graph is still waiting at it, and a resume runs it, to its end this time.
    assert resumed["next"] == ["full_checks"]
    after = resumed["after"]
    assert isinstance(after, dict) and after["passed"] is True


def test_a_signal_during_the_setup_discards_the_checkout_so_a_resume_makes_it_again(
    child, product, tmp_path
):
    before = _snapshot(product)
    made = child("setup")
    made.pin("check")

    code, seconds = _stop(made)

    assert code == 1
    assert seconds < PROMPT_S
    assert _gone(made.pins["check"])
    _assert_left_nothing(made, before)
    subtask = product / ".agentic" / "worktrees" / f"{TASK}.api"
    assert not subtask.exists(), "a half-set-up checkout is left for the resume to take as ready"
    # Branch and commits are untouched: the subtask branch still sits where it started.
    assert _git(product, "rev-parse", f"agentic/{TASK}.api") == _git(
        product, "rev-parse", f"agentic/{TASK}"
    )
    resumed = _resume(made, "setup", tmp_path)
    assert resumed == {"created": True, "setup_ran": True}


def test_a_check_that_ignores_sigterm_and_a_role_that_ignores_the_cancel_die_at_the_deadline(
    child, product
):
    before = _snapshot(product)
    made = child("escapes")
    for name in ("check", "grandchild", "role"):
        made.pin(name)

    sent = time.monotonic()
    os.kill(made.process.pid, signal.SIGTERM)
    time.sleep(1)
    # The roles' processes are the runner's while the cancellation unwinds: not reaped yet.
    assert not select.select([made.pins["role"]], [], [], 0)[0]
    code = made.process.wait(timeout=BOUND_S + 30)

    assert code == 1
    assert time.monotonic() - sent < BOUND_S
    for name in ("check", "grandchild", "role"):
        assert _gone(made.pins[name]), f"{name} survived the stop"
    _assert_left_nothing(made, before)


def test_a_command_run_straight_on_the_event_loop_is_still_stopped_by_the_signal(child, product):
    before = _snapshot(product)
    made = child("blocked")
    made.pin("check")

    code, seconds = _stop(made)

    assert code == 1
    assert seconds < BOUND_S
    assert _gone(made.pins["check"])
    _assert_left_nothing(made, before)


def test_a_signal_during_qa_performance_stops_the_measure_and_leaves_no_worktree_or_lock(
    child, product, tmp_path
):
    """GH-385: the node measures in a thread, the runner and the lock hear the run's stop and
    the stop reaps the perf command by its mark. The process ends by the SystemExit the guard
    raises (its report exists), not by the hard deadline's `os._exit`, and does not wait for it."""
    before = _snapshot(product)
    made = child("perf")
    made.pin("perf")

    code, seconds = _stop(made)

    assert code == 1
    assert seconds < PROMPT_S
    assert (made.workdir / "report.json").exists(), "ended by the hard deadline, not the stop"
    assert _gone(made.pins["perf"])
    _assert_left_nothing(made, before)
    worktrees = product / ".agentic" / "worktrees"
    assert not list(worktrees.glob("perf-*")), "the throwaway checkout was left behind"
    assert "perf-" not in _git(product, "worktree", "list")
    assert not (tmp_path / "xdg-state" / "agentic" / "perf.lock").exists(), "perf lock still held"
