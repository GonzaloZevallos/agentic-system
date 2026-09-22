"""CLI. `agentic run "title" "description"` runs one task through the feature path;
`agentic run --issue N` takes both from GitHub issue N of the product repo, via `gh`.
`agentic resume --issue N` resumes that task where an escalation paused it (ADR-0003).
`agentic close --issue N` reads the human's verdict on that task's pull request off
`gh`, emits it and records the closure in the task document -- no role runs and no
graph is invoked, since there is nothing left to spend (Tech Lead plan, subtask
`close-command`).

A driving adapter: it parses arguments, builds the configuration and the ports, opens
the checkpointer and invokes the graph. What a resume does is decided in
`application.resume`; the graph calls are here.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, contextmanager
from datetime import UTC, datetime
from functools import partial
from typing import cast

import anyio
from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from . import composition
from .composition import default_ports, graph
from .core.measurement.adapters.otel import telemetry
from .core.measurement.adapters.otel.telemetry import InvocationKind
from .core.measurement.domain import metrics as delivery_metrics
from .core.process.adapters.filesystem import FilesystemRunLock
from .core.process.adapters.git.workspace import GitError
from .core.process.adapters.langgraph.graph import ESCALATE
from .core.process.application import archive
from .core.process.application import resume as resuming
from .core.process.application.escalate import NEEDS_HUMAN
from .core.process.application.ports import LockHeld, LockRecord, Ports, RunLock
from .core.process.application.preflight import (
    declared_stack_of,
    no_forbidden,
    no_setup,
    preflight,
    product_name,
    unfixed_stage0,
)
from .core.process.domain.task import PullRequest, TaskInput, TaskState
from .shared.configuration import ENV_PREFIX, LOGS_DIR, Configuration, MissingProductDir
from .shared.environment import without_own_toolchain

Checkpointer = Callable[[Configuration], AbstractAsyncContextManager[BaseCheckpointSaver]]
# Re-execs `argv` (already stripped of `--detach`) detached from this session and
# returns the child's pid: the shape `checkpointer` already uses, injectable on
# `_run`/`_resume` so a test asserts argv and redirection without spawning anything
# (Tech Lead plan, subtask `--detach`).
Spawner = Callable[[list[str], str], int]

# What the lock claims, said whenever it refuses (Tech Lead plan, subtask `run-lock`):
# it protects one CLI invocation from another, nothing a `langgraph dev` server runs.
LOCK_SCOPE = (
    "the lock protects `agentic run`/`agentic resume`/`agentic close` invocations from "
    "each other; a `langgraph dev` server holds every thread in one process and is not "
    "covered by it"
)

# The one string that spells an unknown `system_version` wherever `close` emits one:
# absent, empty and null alike, all read back as this single literal (Tech Lead plan,
# subtask `close-command`) -- `close` runs from a possibly different checkout than the
# one that delivered, so it names the *delivering* run's version, never
# `telemetry.system_version()`, which would name its own.
UNKNOWN_SYSTEM_VERSION = "unknown"


class CannotStart(Exception):
    """The command refuses before spending anything; the message says why and what to do."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def main() -> None:
    # `.env` in the working directory, if any. Real env vars win over it.
    load_dotenv()
    # `uv run agentic` activated our venv; nothing we spawn in the product (git, a
    # role running its tests) may inherit it. The SDK copies this process's whole
    # environment into every role's, so the scrub is here, once, after `.env` so
    # nothing reintroduces it and before anything runs.
    _replace_environ(without_own_toolchain(os.environ))
    parser = argparse.ArgumentParser(prog="agentic")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one task through the feature path")
    run.add_argument("title", nargs="?")
    run.add_argument("description", nargs="?")
    run.add_argument(
        "--issue",
        type=int,
        metavar="N",
        help="take title and description from GitHub issue N of the product repo (needs gh)",
    )
    run.add_argument(
        "--fresh",
        action="store_true",
        help="start over a task that is paused or ran before: its checkpoint is dropped and "
        "its previous branches, if any, archived under archive/run<N>-<sha>/ before a fresh "
        "run begins",
    )
    resume = sub.add_parser("resume", help="resume the task of issue N where it paused")
    resume.add_argument("--issue", type=int, metavar="N", required=True)
    resume.add_argument(
        "--task-budget",
        type=float,
        metavar="USD",
        help="raise the task cap for this run, never lower it (AGENTIC_TASK_BUDGET_USD)",
    )
    resume.add_argument(
        "--from-document",
        action="store_true",
        help="seed the state from the task document instead of the checkpoint",
    )
    resume.add_argument(
        "--dry-run", action="store_true", help="show what would resume and stop, spending nothing"
    )
    resume.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    close = sub.add_parser(
        "close", help="record the human's verdict on a task's pull request and close it"
    )
    close.add_argument("--issue", type=int, metavar="N", required=True)
    close.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    for command in (run, resume):
        command.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
        command.add_argument(
            "--verbose", action="store_true", help="stream each role's generation to stderr"
        )
        command.add_argument(
            "--detach",
            action="store_true",
            help="detach from this session (setsid), print the pid and the log path, and exit",
        )
    args = parser.parse_args()

    if args.command == "run":
        if args.issue is None and (args.title is None or args.description is None):
            run.error("give a title and a description, or --issue N")
        if args.issue is not None and args.title is not None:
            run.error("--issue replaces the title and description; give one or the other")
    elif args.command == "resume":
        # A detached child has no stdin: neither to confirm on nor to report a dry-run
        # to, so both are rejected before anything is spent (Tech Lead plan, `--detach`).
        if args.detach and args.dry_run:
            resume.error("--detach and --dry-run are exclusive: --dry-run has no child to report")
        if args.detach and not args.yes:
            resume.error("--detach requires --yes: a detached child has no stdin to confirm on")
    # close has neither flag to check: it never joined the `for command in (run, resume)`
    # loop above, so `--detach`/`--dry-run` are unrecognized arguments there, a parse
    # error argparse raises on its own.

    # The env var outranks `configurable`, so an explicit flag has to go in as env.
    if args.product_dir:
        os.environ[ENV_PREFIX + "PRODUCT_DIR"] = args.product_dir
    if getattr(args, "verbose", False):
        os.environ[ENV_PREFIX + "VERBOSE"] = "1"
    if getattr(args, "task_budget", None) is not None:
        os.environ[ENV_PREFIX + "TASK_BUDGET_USD"] = str(args.task_budget)
    try:
        cfg = Configuration.from_sources({}, os.environ)
        if not cfg.agentic_dir.is_dir():
            raise ValueError(f"{cfg.product_dir} is not a product repo: no .agentic/ directory")
        ports = default_ports(cfg)
        if args.command != "close":
            # `close` runs no role and spends nothing, so the gate that renders every
            # role's prompt and checks the product's stack before a run spends money
            # does not apply to it (Tech Lead plan, subtask `close-command`): only
            # `run` and `resume` still call it, byte-identically to before this task.
            preflight(cfg, ports)
            # Said and not enforced: the run is worth less, it is not wrong.
            for warn in (unfixed_stage0, no_setup):
                if (warning := warn(ports)) is not None:
                    print(f"agentic: {warning}", file=sys.stderr)
            if args.command == "run":
                # Against HEAD: the revision a fresh task's stack is read at
                # (`declared_stack`'s own default). `resume` asks the same fact below,
                # against the base `declared_stack_of` actually validated, the revision
                # that run is governed by -- never both, and never joined into the loop
                # above, whose two warnings share one revision this one does not.
                if (warning := no_forbidden(ports, "HEAD")) is not None:
                    print(f"agentic: {warning}", file=sys.stderr)
            if not getattr(args, "detach", False) and not sys.stdin.isatty():
                print(
                    "agentic: no TTY and no --detach: this process dies if its session "
                    "closes (2026-09-15); pass --detach to survive it",
                    file=sys.stderr,
                )
        if args.command == "resume":
            task_id = ports.inbox.task_id(args.issue)
            base = declared_stack_of(task_id, ports)
            if (warning := no_forbidden(ports, base)) is not None:
                print(f"agentic: {warning}", file=sys.stderr)
            log_file = _log_file_for(cfg, task_id)
            options = (args.from_document, args.dry_run, args.yes)
            work: Callable[[], Awaitable[None]] = partial(
                _resume,
                task_id,
                cfg,
                ports,
                *options,
                log_file=log_file,
                detach=args.detach,
                child_argv=_child_argv(),
            )
        elif args.command == "close":
            task_id = ports.inbox.task_id(args.issue)
            work = partial(_close, task_id, cfg, ports)
        else:
            if args.issue is not None:
                task = ports.inbox.task(args.issue)
            else:
                task = {
                    "id": f"T-{uuid.uuid4().hex[:8]}",
                    "title": args.title,
                    "description": args.description,
                }
            # A task that already has a branch is measured against its own base, which
            # may predate the day this product declared a stack: HEAD alone would let it
            # through and it would die inside a node, where nothing escalates. A task
            # with no branch answers HEAD here, already checked. `--fresh` skips this
            # entirely (ADR-0011, sixth addendum): the task's base is about to stop
            # existing under `--fresh` (archiving renames the branch it comes from), and
            # `preflight`'s own check at HEAD is what governs the checkout that follows.
            if not args.fresh:
                declared_stack_of(task["id"], ports)
            log_file = _log_file_for(cfg, task["id"])
            work = partial(
                _run,
                task,
                cfg,
                ports,
                args.fresh,
                log_file=log_file,
                detach=args.detach,
                child_argv=_child_argv(),
            )
    except (MissingProductDir, ValueError, OSError) as exc:
        parser.exit(2, f"agentic: {exc}\n")
    try:
        anyio.run(_signal_guarded, work)
    except CannotStart as exc:
        parser.exit(exc.code, f"agentic: {exc}\n")


async def _signal_guarded(work: Callable[[], Awaitable[None]]) -> None:
    """Wraps `work` (`_run`/`_resume`, built in `main`) in a task group a SIGTERM or a
    SIGHUP cancels (Tech Lead plan, subtask `signals`). This is the one place that
    restructures the `anyio.run(work)` call; every other subtask takes its shape as it
    finds it.

    Cancelling the scope is the only new mechanism: everything the invocation already
    does in a `finally` -- `_locked`'s lock release, `_invoke`'s telemetry flush,
    `task_span`'s own context manager exit -- runs exactly as it would on any other
    exception, since anyio delivers a cancellation as one at the current await point.
    Nothing is written here to the task document: the only `TaskState` this scope holds
    is the invocation's own `entering` snapshot (`_invoke`'s docstring), and writing it
    would erase every section this invocation's nodes already wrote, racing the fan-in
    (ADR-0004, fifth addendum). No issue comment is added either: `resume.SYSTEM_COMMENTS`
    is untouched, since an unregistered prefix would read back as a human decision on the
    next resume (`resume.human_decision`).

    `work`'s own exception -- `CannotStart`, an escalated run's `sys.exit`, anything --
    is caught inside the group and re-raised after it closes, never let escape the
    `async with tg:` block itself: anyio wraps whatever crosses it in an
    `ExceptionGroup`, even a lone exception, which would break `main`'s bare
    `except CannotStart` and change the exit code the invariants say must not move."""
    caught: list[BaseException] = []
    interrupted = False

    async def watch_signals() -> None:
        nonlocal interrupted
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGHUP) as signals:
            async for _ in signals:
                interrupted = True
                tg.cancel_scope.cancel()
                return

    async def run_work() -> None:
        try:
            await work()
        except anyio.get_cancelled_exc_class():
            raise
        except BaseException as exc:
            caught.append(exc)
        finally:
            tg.cancel_scope.cancel()

    async with anyio.create_task_group() as tg:
        tg.start_soon(watch_signals)
        tg.start_soon(run_work)

    if caught:
        raise caught[0]
    if interrupted:
        # The three actions the plan asks for: telemetry flushed and the lock released,
        # both already done by `work`'s own `finally` blocks as the cancellation
        # unwound it; this is the third, a non-zero exit for whoever sent the signal.
        raise SystemExit(1)


def _configurable(task_id: str, log_file: str | None) -> dict[str, str]:
    configurable = {"thread_id": task_id}
    if log_file is not None:
        configurable["log_file"] = log_file
    return configurable


def _log_file_for(cfg: Configuration, task_id: str) -> str:
    """Where this invocation logs, computed once the task id is known (not at line
    119, where `cfg` is built and no task exists yet): `<product>/.agentic/logs/
    <task>-<UTC timestamp>.log`, to the second, so two invocations of the same task
    never share a file (README.md, 14; issue #33). Printed once, and returned as a
    plain `str`: what travels through `configurable`, which the saver persists, is a
    JSON-serialisable scalar, never a `Path` (invariant 12).

    A `--detach` child reuses its parent's path instead of minting its own: `cfg.log_file`
    is already set from `AGENTIC_LOG_FILE`, `Configuration`'s generic per-field env
    override, which `_spawn_detached` puts in the child's environment. Without that, the
    child's own `configurable["log_file"]` and the OS-redirected stdout/stderr the parent
    already told the human about would name two different files."""
    if cfg.log_file is not None:
        return str(cfg.log_file)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = cfg.product_dir / LOGS_DIR / f"{task_id}-{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"agentic: log at {path}")
    return str(path)


def _child_argv() -> list[str]:
    """This invocation's own argv, `--detach` stripped so the child can never re-detach
    (Tech Lead plan, subtask `--detach`). Launched through `sys.argv[0]`, the console
    script `pyproject.toml` declares (`agentic = "agentic.cli:main"`), which is already
    how this process itself was started -- there is no `agentic/__main__.py`, so
    `[sys.executable, "-m", "agentic"]` is not an option."""
    return [sys.argv[0], *(arg for arg in sys.argv[1:] if arg != "--detach")]


def _spawn_detached(argv: list[str], log_path: str) -> int:
    """The default `Spawner`: a new process image (`Popen`, never a fork -- forking
    after `setsid` would still hold this terminal's fds, and forking after
    `telemetry.configure` would carry its background export threads into a run that
    then says nothing about them), detached from this session (`start_new_session`),
    with stdin closed (a detached child cannot answer a prompt) and stdout/stderr onto
    the log file before any role runs. `AGENTIC_LOG_FILE` carries this invocation's
    already-computed, already-printed path into the child, so `_log_file_for` there
    returns the same string instead of minting a second file."""
    env = {**os.environ, ENV_PREFIX + "LOG_FILE": log_path}
    with open(log_path, "ab", buffering=0) as log:
        process = subprocess.Popen(
            argv,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
    return process.pid


def _refuse_if_locked(lock: RunLock, task_id: str) -> None:
    """The read-only half of `_acquire`'s check, for `--detach`'s parent: it must show a
    refusal on the human's terminal without writing the lock itself (invariant: no
    subtask writes a lock from a process that then forks or exits). Delegates the
    liveness rule and both message shapes to `lock.refusal` -- the same one `acquire`
    itself enforces -- so this copy cannot drift from it. The child re-checks
    authoritatively, atomically, through the real `_acquire` right after it re-execs;
    this only saves the human a `setsid` and a wasted log file when the answer was
    already known."""
    message = lock.refusal(task_id)
    if message is not None:
        raise CannotStart(f"{message}: {LOCK_SCOPE}")


def _replace_environ(env: dict[str, str]) -> None:
    for key in set(os.environ) - set(env):
        del os.environ[key]
    os.environ.update(env)


def _lock_for(cfg: Configuration) -> RunLock:
    return FilesystemRunLock(cfg.agentic_dir / "runs")


def _acquire(lock: RunLock, task_id: str) -> LockRecord | None:
    try:
        return lock.acquire(task_id, " ".join(sys.argv))
    except LockHeld as exc:
        raise CannotStart(f"{exc}: {LOCK_SCOPE}") from exc


def _print_lock(lock: RunLock, task_id: str) -> None:
    """What `--dry-run` shows instead of acquiring: the worried human's own tool."""
    if (record := lock.read(task_id)) is not None:
        print(
            f"lock:      pid {record.pid} on {record.host}, running since "
            f"{record.started_at} ({record.command})"
        )


@contextmanager
def _locked(lock: RunLock, task_id: str) -> Iterator[None]:
    """Around the invocation only (Tech Lead plan): refuses before anything is spent,
    releases whether the invocation delivers, escalates or raises."""
    taken_over = _acquire(lock, task_id)
    if taken_over is not None:
        print(
            f"agentic: took over the lock left by pid {taken_over.pid} on "
            f"{taken_over.host} ({taken_over.started_at}), no longer running"
        )
    try:
        yield
    finally:
        lock.release(task_id)


async def _run(
    task: TaskInput,
    cfg: Configuration,
    ports: Ports,
    fresh: bool = False,
    checkpointer: Checkpointer | None = None,
    run_lock: RunLock | None = None,
    log_file: str | None = None,
    detach: bool = False,
    child_argv: list[str] | None = None,
    spawn: Spawner | None = None,
) -> None:
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    config: RunnableConfig = {"configurable": _configurable(task["id"], log_file)}
    async with (checkpointer or composition.checkpointer)(cfg) as saver:
        app = graph.compile(checkpointer=saver)
        if not fresh:
            await _refuse_a_started_task(app, config, task["id"], ports)
        if detach:
            _detach(lock, task["id"], child_argv or [], log_file or "", spawn)
            return
        with _locked(lock, task["id"]):
            _archive_previous_run(task["id"], ports)
            # A thread keeps its channels: a new run over it would add to the old cost.
            await saver.adelete_thread(task["id"])
            final = await _invoke(app, task, config, task, ports, "run")
    _report(final, task["id"], ports)


def _archive_previous_run(task_id: str, ports: Ports) -> None:
    """Archives what a previous run of `task_id` left before its thread is dropped
    (ADR-0011, sixth addendum): the first statement inside the lock, under the same
    exclusion as the rest of the invocation. A `GitError` becomes `CannotStart` so the
    thread survives -- archiving is idempotent by construction, so a later retry
    finishes what a cut-short rename left half done.

    The printed line and the issue's `needs-human` label are two separate facts:
    whether anything was archived (`result.prefix`) says nothing about whether the
    document the task paused on can even be read, and a document that cannot be read
    should not keep a restarted task in the escalated queue either."""
    try:
        result = archive.archive_previous_run(task_id, ports)
    except GitError as exc:
        raise CannotStart(str(exc)) from exc
    if result.prefix is not None:
        verb = (
            "archived previous run as" if result.written else "previous run was already archived as"
        )
        line = f"agentic: {verb} {result.prefix}"
        if not result.document_kept:
            line += " (archived without the document: the product ignores .agentic/tasks/)"
        print(line)
        ports.tracker.comment(
            task_id, f"{archive.STARTED_OVER}previous run archived as {result.prefix}/"
        )
    try:
        raw = ports.documents.load(task_id)
        unreadable = False
    except (ValueError, OSError):
        raw, unreadable = None, True
    if unreadable or (raw or {}).get("escalated"):
        ports.tracker.unlabel(task_id, NEEDS_HUMAN)


def _detach(
    lock: RunLock, task_id: str, child_argv: list[str], log_file: str, spawn: Spawner | None
) -> None:
    """The parent's own half of `--detach`: refuse on this terminal if the task is
    already running (`_refuse_if_locked`, read-only), else re-exec and print the
    child's pid -- the pid that holds the lock, since the parent never acquires it
    (Tech Lead plan, subtask `--detach`)."""
    _refuse_if_locked(lock, task_id)
    pid = (spawn or _spawn_detached)(child_argv, log_file)
    print(f"agentic: detached, pid {pid}, log at {log_file}")


async def _refuse_a_started_task(app, config: RunnableConfig, task_id: str, ports: Ports) -> None:
    """A run over a task that already has a state would rewrite its document and reuse
    its branches under another plan (issue #21)."""
    snapshot = await app.aget_state(config)
    document = ports.documents.load(task_id)
    if snapshot.values:
        why = f"is paused: {snapshot.values.get('escalated')}"
        if not snapshot.next:
            why = f"already ran ({snapshot.values.get('status', 'open')})"
        elif not snapshot.interrupts:
            why = "stopped in the middle of a run"
    elif document is not None and document.get("escalated"):
        why = f"is paused, per its document: {document['escalated']}"
    else:
        return
    hint = "Start it over with --fresh."
    if (number := ports.inbox.issue_of(task_id)) is not None:
        hint = f"Resume it with `agentic resume --issue {number}`, or start it over with --fresh."
    raise CannotStart(f"{task_id} {why}. {hint}")


async def _resume(
    task_id: str,
    cfg: Configuration,
    ports: Ports,
    from_document: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    checkpointer: Checkpointer | None = None,
    run_lock: RunLock | None = None,
    log_file: str | None = None,
    detach: bool = False,
    child_argv: list[str] | None = None,
    spawn: Spawner | None = None,
) -> None:
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    config: RunnableConfig = {"configurable": _configurable(task_id, log_file)}
    async with (checkpointer or composition.checkpointer)(cfg) as saver:
        app = graph.compile(checkpointer=saver)
        snapshot = await app.aget_state(config)
        try:
            if from_document or not snapshot.values:
                paused = resuming.from_document(task_id, ports)
                r = resuming.prepare(task_id, paused, "document", cfg, ports)
            elif not snapshot.next:
                status = snapshot.values.get("status", "open")
                raise CannotStart(f"{task_id} is not paused: its run ended ({status})")
            else:
                # No interrupt with nodes still to run: the run crashed rather than paused.
                pending = () if snapshot.interrupts else tuple(snapshot.next)
                paused = cast(TaskState, snapshot.values)
                r = resuming.prepare(task_id, paused, "checkpoint", cfg, ports, pending)
        except resuming.WaitingOnHuman as exc:
            raise CannotStart(str(exc), code=1) from exc
        except ValueError as exc:
            raise CannotStart(str(exc)) from exc

        print(resuming.summary(r, cfg, ports))
        if dry_run:
            _print_lock(lock, task_id)
            return
        if not yes and input("\nResume? [y/N] ").strip().lower() not in ("y", "yes"):
            raise CannotStart("not resumed", code=1)
        if detach:
            _detach(lock, task_id, child_argv or [], log_file or "", spawn)
            return

        with _locked(lock, task_id):
            if r.source == "document":
                # Seeded as if `escalate` had just run: the task pauses once more,
                # without telling the human again, and the answer below resumes it
                # like a checkpoint.
                await saver.adelete_thread(task_id)
                await app.aupdate_state(config, r.paused, as_node=ESCALATE)
            crashed = r.source == "checkpoint" and not snapshot.interrupts
            command: Command | None = None if crashed else Command(resume={"decision": r.decision})
            resuming.announce(r, ports)
            final = await _invoke(app, command, config, r.paused, ports, "resume", r.decision)
    _report(final, task_id, ports)


async def _close(task_id: str, cfg: Configuration, ports: Ports) -> None:
    """`agentic close --issue N`: the human's verdict on a task's pull request, read
    through `gh` and recorded once. Runs no role and opens no span (`preflight` never
    runs here either); the one thing it still needs from the product is its name
    (`ports.stack.product()`), since every sample this emits carries it.

    The same `FilesystemRunLock` as `run`/`resume`, through `_locked`, covers the whole
    of read -> emit -> write, so a second `close` (or a `run`/`resume`) over this task
    cannot race it. Every refusal below goes through `CannotStart` -- non-zero, nothing
    emitted, no document byte written -- and each is independently reachable: a missing
    document or `pull_request` value and an already-recorded closure are read off the
    document alone, before `gh` is ever asked anything; a pull request still open is
    read off `gh`; a false flush (`record_delivery`'s own docstring: `None` with no
    meter provider configured is indistinguishable, to a caller, from one that ran and
    failed) is the last refusal, and the same re-runnable state as any of the others --
    `record_closure` only runs after a *true* flush, so a retry finds the same document
    it started with."""
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    lock = _lock_for(cfg)
    with _locked(lock, task_id):
        raw = ports.documents.load(task_id)
        if raw is None:
            raise CannotStart(f"{task_id} has no document to close")
        reference = raw.get("pull_request")
        if not reference:
            raise CannotStart(f"{task_id} has no pull request to close")
        if raw.get("closed_at"):
            raise CannotStart(f"{task_id} already closed on {raw['closed_at']}")
        try:
            pull_request: PullRequest | None = ports.tracker.pull_request(reference)
        except (ValueError, OSError) as exc:
            raise CannotStart(str(exc)) from exc
        if pull_request is None:
            raise CannotStart(f"{task_id}: could not read its pull request ({reference})")
        if pull_request.state == "OPEN":
            raise CannotStart(f"{task_id}: the pull request is still open; nothing to close")

        author = cfg.git_author_email
        outcome = delivery_metrics.delivery_outcome(pull_request, author)
        human_commits = delivery_metrics.human_commits(pull_request, author)
        human_lines_changed = delivery_metrics.human_lines_changed(pull_request, author)
        time_to_merge = delivery_metrics.time_to_merge_seconds(pull_request)
        version = raw.get("system_version") or UNKNOWN_SYSTEM_VERSION
        task_type = raw.get("type") or "unknown"
        fingerprint = delivery_metrics.prompts_fingerprint(cast(TaskState, raw))

        flushed = telemetry.record_delivery(
            product=product,
            task_type=task_type,
            prompts_fingerprint=fingerprint,
            system_version=version,
            outcome=outcome,
            human_commits=human_commits,
            human_lines_changed=human_lines_changed,
            time_to_merge_seconds=time_to_merge,
        )
        if not flushed:
            raise CannotStart(f"{task_id}: no metrics endpoint configured; nothing recorded")

        path = ports.documents.record_closure(
            task_id,
            {
                "closed_at": _now_iso(),
                "delivery_outcome": outcome,
                "human_commits": str(human_commits),
                "human_lines_changed": str(human_lines_changed),
            },
        )
    print(f"outcome:             {outcome}")
    print(f"human_commits:       {human_commits}")
    print(f"human_lines_changed: {human_lines_changed}")
    print(f"time_to_merge:       {time_to_merge}")
    print(f"doc:                 {path}")
    print("agentic: document written, not committed")


def _now_iso() -> str:
    """ISO-8601 UTC, millisecond precision -- the same shape `closed_at`'s neighbours in
    the frontmatter already use (`run_role._now_iso`, not importable across layers from
    a driving adapter, so this is its own copy of the same one-liner)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


async def _invoke(
    app,
    graph_input,
    config: RunnableConfig,
    task: TaskInput,
    ports: Ports,
    kind: InvocationKind = "run",
    decision: str | None = None,
) -> TaskState:
    # The state as this invocation entered: `_run`'s fresh `TaskInput` (no prior cost),
    # or `_resume`'s paused state. `finish_invocation` diffs it against the state the
    # invocation leaves with, so a resume reports only what it spent (README.md, 14).
    entering = cast(TaskState, task)
    try:
        with telemetry.task_span(task["id"], task["title"], product_name(ports)) as run:
            try:
                final = await app.ainvoke(graph_input, config=config)
            except Exception:
                # The run broke outside any use case; the task span still closes with a
                # status, so Grafana counts it (`crashed` is telemetry-only, never state).
                crashed = cast(TaskState, {**task, "status": "crashed"})
                run.finish(crashed)
                run.finish_invocation(entering, crashed, kind, decision)
                raise
            run.finish(final)
            run.finish_invocation(entering, final, kind, decision)
    finally:
        telemetry.flush()
    return final


def _report(final: TaskState, task_id: str, ports: Ports) -> None:
    print(f"\nid:        {task_id}")
    print(f"type:      {final.get('type')}")
    print(f"status:    {final.get('status', 'open')}")
    print(f"roles:     {', '.join(final.get('sections', {}))}")
    print(f"returns:   {final.get('returns', {})}")
    print(f"failures:  {len(final.get('failures', []))}")
    if final.get("escalated"):
        print(f"escalated: {final['escalated']}")
    for question in final.get("questions_for_human", []):
        print(f"question:  {question}")
    if final.get("branch"):
        print(f"branch:    {final['branch']}")
    if final.get("pull_request"):
        print(f"pr:        {final['pull_request']}")
    print(f"cost:      ${final.get('cost_usd', 0):.4f}")
    print(f"doc:       {ports.documents.path_for(task_id)}")
    if final.get("escalated"):
        if (number := ports.inbox.issue_of(task_id)) is not None:
            print(f"resume:    agentic resume --issue {number}")
        sys.exit(1)
