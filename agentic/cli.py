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
import json
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

import anyio
import yaml
from dotenv import load_dotenv
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from . import composition
from .composition import default_ports, graph
from .core.knowledge.domain import knowledge
from .core.knowledge.domain import stack as knowledge_stack
from .core.measurement.adapters.otel import telemetry
from .core.measurement.adapters.otel.telemetry import InvocationKind
from .core.measurement.domain import evaluation, playbook_window, scorecard
from .core.measurement.domain import metrics as delivery_metrics
from .core.process.adapters.filesystem import (
    FilesystemRunLock,
    FilesystemRunRegistry,
    FilesystemTaskDocuments,
    liveness,
)
from .core.process.adapters.git.workspace import GitError
from .core.process.adapters.langgraph.graph import ESCALATE
from .core.process.adapters.shell import inhibitor
from .core.process.application import archive, reflect_run
from .core.process.application import poll as pollmod
from .core.process.application import resume as resuming
from .core.process.application.escalate import NEEDS_HUMAN
from .core.process.application.landing import land
from .core.process.application.merge import merge_task
from .core.process.application.ports import (
    Launcher,
    LockHeld,
    LockRecord,
    Ports,
    RunLock,
    RunRegistry,
    Upstream,
)
from .core.process.application.preflight import (
    PLACEHOLDERS,
    declared_stack,
    declared_stack_of,
    no_forbidden,
    no_setup,
    preflight,
    product_name,
    refuse_its_own_checkout,
    unfixed_stage0,
)
from .core.process.application.run_role import load_prompt
from .core.process.application.stack_review import stack_review as run_stack_review
from .core.process.domain import document as task_document
from .core.process.domain import legacy_names, rules, stack_signals
from .core.process.domain import stack_review as stack_review_texts
from .core.process.domain.playbook import Delta, Playbook
from .core.process.domain.roles import ROLES
from .core.process.domain.task import PullRequest, TaskInput, TaskState
from .shared.configuration import ENV_PREFIX, LOGS_DIR, Configuration, MissingProductDir
from .shared.environment import without_own_toolchain

# The stack review's own corpus (ADR-0034): beside `.agentic/tasks/`, never inside it,
# so `close --all`, `eval score` and `_bucket_documents` -- which reach the task corpus
# through `documents.ids()` -- stay blind to it (`document.relative_path`/
# `FilesystemTaskDocuments`'s own `subdir` keyword).
STACK_REVIEWS_SUBDIR = Path(".agentic/stack-reviews")

Checkpointer = Callable[[Configuration, str], AbstractAsyncContextManager[BaseCheckpointSaver]]
# Re-execs `argv` (already stripped of `--detach`) detached from this session and
# returns the child's pid: the shape `checkpointer` already uses, injectable on
# `_run`/`_resume` so a test asserts argv and redirection without spawning anything
# (Tech Lead plan, subtask `--detach`).
Spawner = Callable[[list[str], str], int]
# `eval run` launches one `agentic run --fresh --issue N` per set task, waited before
# the next one starts: `RunLimits` is per Corrida keyed by `thread_id` (ADR-0004, fifth
# addendum), so five in parallel would put twenty roles in flight and the 429s that
# follow would read as prompt regressions (Tech Lead plan, subtask `eval-cli`).
# `Spawner`'s own shape beside it -- one alias, one default implementation, `None` on
# the call site meaning the default -- takes the child's argv and an environment
# overlay and hands back nothing: the outcome is read from the task's own document
# afterwards, never from this call, so an exit status here would be a second source of
# truth the way `_report`'s `sys.exit(1)` already is not one.
EvalLauncher = Callable[[list[str], dict[str, str]], None]

# What the lock claims, said whenever it refuses (Tech Lead plan, subtask `run-lock`):
# it protects one CLI invocation from another, nothing a `langgraph dev` server runs.
LOCK_SCOPE = (
    "the lock protects `agentic run`/`agentic resume`/`agentic close`/`agentic eval run` "
    "invocations from each other; a `langgraph dev` server holds every thread in one "
    "process and is not covered by it"
)

# The one string that spells an unknown `system_version` wherever `close` emits one:
# absent, empty and null alike, all read back as this single literal (Tech Lead plan,
# subtask `close-command`) -- `close` runs from a possibly different checkout than the
# one that delivered, so it names the *delivering* run's version, never
# `telemetry.system_version()`, which would name its own.
UNKNOWN_SYSTEM_VERSION = "unknown"


def no_brief(ports: Ports) -> str | None:
    """Why this run reads no Brief, or None when one is on disk (ADR-0025). Never a
    refusal, the same fact `no_setup` is: a product that has not written one yet still
    runs, one warning line at a time, until its human writes one. The verdict on a
    Brief that IS present belongs to `brief_refusal`, not here."""
    if ports.knowledge.brief() is not None:
        return None
    return (
        "the product has no BRIEF.md: PM and the Tech Lead decide with no north star "
        "(ADR-0025). Write it at the product's root, and commit it:\n\n"
        + knowledge.DEFAULT_BRIEF_TEMPLATE.read_text(encoding="utf-8")
    )


def brief_refusal(ports: Ports) -> str | None:
    """Why the product's own `BRIEF.md` cannot be used, or None when it is absent (that
    is `no_brief`'s fact) or valid. The one join of the pure verdict
    (`core.knowledge.domain.knowledge.brief_refusal`) with the port that reads the
    text (ADR-0025): raised on `run`, printed on `resume`, in `main`'s per-command
    layer, next to `no_forbidden`'s own split."""
    text = ports.knowledge.brief()
    return None if text is None else knowledge.brief_refusal(text)


def _warn_no_tty(detach: bool) -> None:
    """The fact `run`/`resume`/`eval run` share: a process with no TTY and no
    `--detach` dies the moment its session closes (2026-09-15). Factored out so `eval
    run` prints the identical line without joining the `preflight` gate `eval` skips
    entirely (Tech Lead plan, subtask `eval-cli`)."""
    if not detach and not sys.stdin.isatty():
        print(
            "agentic: no TTY and no --detach: this process dies if its session "
            "closes (2026-09-15); pass --detach to survive it",
            file=sys.stderr,
        )


class CannotStart(Exception):
    """The command refuses before spending anything; the message says why and what to do."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# 2026-10-05 rename: a checkpoint written before it still names the old role
# (`legacy_names.RENAME_CAUSE`, the one place the old name is spelled out), and
# `aupdate_state` with translated values is never used on an existing thread -- the
# `operator.add`/`merge` reducers (`TaskState.change_history`/`.changes_requested`/
# `.design_items`/`.pause_log`) would duplicate runs, failures and cost. A paused
# snapshot (`snapshot.interrupts` non-empty) takes the document branch instead, exactly
# as `--from-document` does; a crashed one (no interrupt) has nothing to reseed from
# safely and refuses.
_LEGACY_DISAGREEMENT_CAUSE = (
    f"this task's checkpoint {legacy_names.RENAME_CAUSE}; it resumes from its "
    "document, which was checked against git:"
)


def _legacy_crashed_message(task_id: str, ports: Ports) -> str:
    number = ports.inbox.issue_of(task_id)
    flag = f"--issue {number}" if number is not None else f"--issue N  # {task_id}"
    return (
        f"{task_id}'s checkpoint {legacy_names.RENAME_CAUSE}, and it crashed rather "
        f"than paused: resume it with `agentic resume {flag} --from-document`."
    )


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
    close.add_argument("--issue", type=int, metavar="N")
    close.add_argument(
        "--all",
        action="store_true",
        help="close every task document with a pull request and no recorded closure "
        "(Tech Lead plan, subtask `close-all`)",
    )
    close.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    merge = sub.add_parser(
        "merge",
        help="merge a task's approved pull request when the checks pass on its head merged "
        "with the upstream master (GH-178, ADR-0045); the poller spawns it, nobody else needs to",
    )
    merge.add_argument("--issue", type=int, metavar="N", required=True)
    merge.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    land = sub.add_parser(
        "land",
        help="take the landing step for a task's approved pull request that the automatic "
        "merge refused (GH-239, ADR-0048); the poller spawns it, nobody else needs to",
    )
    land.add_argument("--issue", type=int, metavar="N", required=True)
    land.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    eval_parser = sub.add_parser(
        "eval", help="drive or score README.md 9's fixed five-task evaluation set"
    )
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_sub.add_parser(
        "run", help="run every task of the evaluation set from scratch, one at a time"
    )
    eval_run.add_argument(
        "--budget",
        type=float,
        metavar="USD",
        required=True,
        help="stop launching further tasks once the observed spend would exceed this",
    )
    eval_run.add_argument(
        "--yes",
        action="store_true",
        help="confirm the estimated spend; required to launch anything",
    )
    eval_run.add_argument(
        "--dry-run",
        action="store_true",
        help="show the set, the resolved issues and the estimate, spending nothing",
    )
    eval_run.add_argument(
        "--detach",
        action="store_true",
        help="detach this eval driver (setsid), print the pid and the log path, and exit",
    )
    eval_run.add_argument(
        "--set", metavar="PATH", help="override the product's own .agentic/eval-set.yaml"
    )
    eval_run.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    eval_score = eval_sub.add_parser(
        "score", help="write one JSONL line per evaluation-set task, read off its own document"
    )
    eval_score.add_argument(
        "--out", metavar="PATH", required=True, help="destination JSONL; refuses if it exists"
    )
    eval_score.add_argument(
        "--set", metavar="PATH", help="override the product's own .agentic/eval-set.yaml"
    )
    eval_score.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    eval_compare = eval_sub.add_parser(
        "compare",
        help="ADR-0028's scorecard for two baselines, from task documents and local git alone",
    )
    eval_compare.add_argument("frm", metavar="from", help="the baseline compared from")
    eval_compare.add_argument("to", metavar="to", help="the baseline compared against `from`")
    eval_compare.add_argument(
        "--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)"
    )
    eval_compare.add_argument(
        "--system-repo",
        metavar="PATH",
        help="the system checkout to resolve a document's own system_commit against "
        "(defaults to the pinned stage0 checkout)",
    )
    stack = sub.add_parser("stack", help="the product's own stack (ADR-0034)")
    stack_sub = stack.add_subparsers(dest="stack_command", required=True)
    stack_review = stack_sub.add_parser(
        "review",
        help="the Tech Lead in proactive mode decides whether the product's stack "
        "still fits its Brief; never a direct change, always a proposal issue",
    )
    stack_review.add_argument(
        "--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)"
    )
    stack_review.add_argument(
        "--reason",
        metavar="TEXT",
        help="a reason of your own; counts as a trigger on its own and is reproduced "
        "verbatim among the signals in the issue body",
    )
    stack_review.add_argument(
        "--dry-run",
        action="store_true",
        help="show which signals hold, the anchor and what would be opened; spends "
        "nothing, takes no lock, writes no file and makes no `gh` call",
    )
    poll_parser = sub.add_parser(
        "poll",
        help="a long-lived process per product (GH-137): closes merged pull requests, "
        "retakes a pause an external API cut caused once its own wait elapses, and "
        "launches the next unblocked issue, never past AGENTIC_MAX_CONCURRENT_RUNS",
    )
    poll_parser.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    poll_parser.add_argument(
        "--detach",
        action="store_true",
        help="detach this poller (setsid), print the pid and the log path, and exit",
    )
    poll_parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="how long to wait between rounds (default 60)",
    )
    reflect = sub.add_parser(
        "reflect",
        help="what closed tasks' returns teach each role (ADR-0043): candidate playbook "
        "deltas on the branch agentic/playbook, in one pull request for a person to merge",
    )
    reflect.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    reflect_scope = reflect.add_mutually_exclusive_group(required=True)
    reflect_scope.add_argument("--issue", type=int, metavar="N", help="reflect on issue N's task")
    reflect_scope.add_argument(
        "--all", action="store_true", help="reflect on every eligible closed task, up to the cap"
    )
    reflect.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="reach back to tasks closed on or after this day (the first run reflects "
        "nothing older than itself otherwise)",
    )
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
    elif args.command == "close":
        if args.issue is None and not args.all:
            close.error("give --issue N or --all")
        if args.issue is not None and args.all:
            close.error("--issue and --all are exclusive")
    elif args.command == "eval" and args.eval_command == "run":
        # The same fact `resume --detach` already checks, for the same reason: a
        # detached driver has no stdin, neither to confirm the estimate on nor to
        # report a dry-run to.
        if args.detach and args.dry_run:
            eval_run.error("--detach and --dry-run are exclusive: --dry-run has no child to report")
        if args.detach and not args.yes:
            eval_run.error("--detach requires --yes: a detached child has no stdin to confirm on")
    # close has neither flag to check: it never joined the `for command in (run, resume)`
    # loop above, so `--detach`/`--dry-run` are unrecognized arguments there, a parse
    # error argparse raises on its own. `eval score` is the same shape.

    # The env var outranks `configurable`, so an explicit flag has to go in as env.
    if args.product_dir:
        os.environ[ENV_PREFIX + "PRODUCT_DIR"] = args.product_dir
    if getattr(args, "verbose", False):
        os.environ[ENV_PREFIX + "VERBOSE"] = "1"
    if getattr(args, "task_budget", None) is not None:
        os.environ[ENV_PREFIX + "TASK_BUDGET_USD"] = str(args.task_budget)
    try:
        cfg = Configuration.from_sources({}, os.environ)
        # Every command but `stack review` still refuses byte for byte on a directory
        # with no `.agentic/` at all: a greenfield product has nothing else, and that
        # is exactly the case this one verb exists to run over (ADR-0034, item 4).
        if args.command != "stack" and not cfg.agentic_dir.is_dir():
            raise ValueError(f"{cfg.product_dir} is not a product repo: no .agentic/ directory")
        ports = default_ports(cfg)
        if args.command not in (
            "close",
            "eval",
            "stack",
            "poll",
            "reflect",
            "merge",
            "land",
        ):
            # `close` runs no role and spends nothing, so the gate that renders every
            # role's prompt and checks the product's stack before a run spends money
            # does not apply to it (Tech Lead plan, subtask `close-command`): only
            # `run` and `resume` still call it, byte-identically to before this task.
            # `eval` is the same fact one step further: `eval score` runs no role and
            # spends nothing either, and `eval run` runs no role itself -- each child
            # runs the full `preflight` for its own task at its own base, the only
            # process where its failure can still escalate (ADR-0011, third addendum;
            # Tech Lead plan, subtask `eval-cli`).
            preflight(cfg, ports)
            # Said and not enforced: the run is worth less, it is not wrong.
            for warn in (unfixed_stage0, no_setup, no_brief):
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
                # A malformed or over-cap Brief refuses `run` outright (ADR-0025): an
                # absent one is `no_brief`'s fact above, never this one's.
                if (problem := brief_refusal(ports)) is not None:
                    raise ValueError(problem)
            _warn_no_tty(getattr(args, "detach", False))
        elif args.command == "eval" and args.eval_command == "run":
            # `eval run` spends real money and detaches the same way `run`/`resume`
            # do; `eval score` never spends anything and skips this warning entirely.
            _warn_no_tty(args.detach)
        elif args.command == "poll":
            # `poll` spends nothing itself (every role runs in a spawned child's own
            # process); it still detaches the same way, and the same TTY warning applies.
            _warn_no_tty(args.detach)
        stop: StopCause | None = None
        if args.command == "resume":
            task_id = ports.inbox.task_id(args.issue)
            base = declared_stack_of(task_id, ports)
            if (warning := no_forbidden(ports, base)) is not None:
                print(f"agentic: {warning}", file=sys.stderr)
            # A malformed or over-cap Brief only prints on `resume` and the run
            # proceeds: a human's edit to his own file must never strand every paused
            # task at the boot gate with no task named (ADR-0003; ADR-0025).
            if (problem := brief_refusal(ports)) is not None:
                print(f"agentic: {problem}", file=sys.stderr)
            log_file = _log_file_for(cfg, task_id)
            options = (args.from_document, args.dry_run, args.yes)
            stop = StopCause()
            work: Callable[[], Awaitable[None]] = partial(
                _resume,
                task_id,
                cfg,
                ports,
                *options,
                log_file=log_file,
                detach=args.detach,
                child_argv=_child_argv(),
                stop=stop,
            )
        elif args.command == "close":
            if args.all:
                work = partial(_close_all, cfg, ports)
            else:
                task_id = ports.inbox.task_id(args.issue)
                work = partial(_close, task_id, cfg, ports)
        elif args.command == "merge":
            work = partial(_merge, ports.inbox.task_id(args.issue), args.issue, cfg, ports)
        elif args.command == "land":
            work = partial(_land, ports.inbox.task_id(args.issue), cfg, ports)
        elif args.command == "eval":
            if args.eval_command == "run":
                log_file = _log_file_for(cfg, "eval")
                work = partial(
                    _eval_run,
                    cfg,
                    ports,
                    args.set,
                    args.budget,
                    args.yes,
                    args.dry_run,
                    log_file=log_file,
                    detach=args.detach,
                    child_argv=_child_argv(),
                )
            elif args.eval_command == "score":
                work = partial(_eval_score, cfg, ports, args.set, args.out)
            else:
                work = partial(_eval_compare, cfg, ports, args.frm, args.to, args.system_repo)
        elif args.command == "reflect":
            if args.since is not None:
                date.fromisoformat(args.since)  # ValueError -> exit 2, before anything is spent
            work = partial(
                _reflect,
                cfg,
                ports,
                None if args.all else ports.inbox.task_id(args.issue),
                args.since,
            )
        elif args.command == "stack":
            work = partial(_stack_review, cfg, ports, args.reason, args.dry_run)
        elif args.command == "poll":
            log_file = _log_file_for(cfg, "poll")
            work = partial(
                _poll,
                cfg,
                ports,
                args.interval,
                log_file=log_file,
                detach=args.detach,
                child_argv=_child_argv(),
            )
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
            stop = StopCause()
            work = partial(
                _run,
                task,
                cfg,
                ports,
                args.fresh,
                log_file=log_file,
                detach=args.detach,
                child_argv=_child_argv(),
                stop=stop,
            )
    except (MissingProductDir, ValueError, OSError) as exc:
        parser.exit(2, f"agentic: {exc}\n")
    try:
        # Only `run`/`resume` carry a `StopCause`: the other commands release as ever.
        guard_args = (work,) if stop is None else (work, stop)
        anyio.run(_signal_guarded, *guard_args)
    except CannotStart as exc:
        parser.exit(exc.code, f"agentic: {exc}\n")


@dataclass
class StopCause:
    """Why this invocation was stopped, one per invocation and handed down as an
    argument (never module state): `_signal_guarded` sets `at_shutdown` before it
    cancels, `_locked` reads it in its `finally` to keep the lock record or release it."""

    at_shutdown: bool = False


def _system_is_stopping() -> bool:
    """True only when systemd says the whole system is shutting down, not when a human
    or a service manager merely stops this one process. Any failure answers False."""
    try:
        done = subprocess.run(
            ["systemctl", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.stdout.strip() == "stopping"


async def _signal_guarded(
    work: Callable[[], Awaitable[None]],
    stop: StopCause | None = None,
    shutdown_probe: Callable[[], bool] = _system_is_stopping,
) -> None:
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
                if stop is not None:
                    # Before the cancel: `_locked`'s `finally` reads the answer.
                    stop.at_shutdown = await anyio.to_thread.run_sync(shutdown_probe)
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
    return _mint_log_path(cfg, task_id)


def _mint_log_path(cfg: Configuration, label: str) -> str:
    """The stamped path itself, `_log_file_for`'s own tail, factored out for
    `SpawnLauncher` (GH-137): a poller's child always gets a fresh file, never the
    poller's own `cfg.log_file` short-circuit above, which exists only for a
    `--detach` child re-execed inside the *same* invocation, not a poller's unrelated
    grandchild."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = cfg.product_dir / LOGS_DIR / f"{label}-{stamp}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"agentic: log at {path}")
    return str(path)


def _self_executable() -> str:
    """This process's own executable, absolute (GH-140): `sys.argv[0]`, the console
    script `pyproject.toml` declares (`agentic = "agentic.cli:main"`), resolved against
    the current directory so a relative invocation (`./bin/agentic`, or a bare
    `agentic` a shell found on `$PATH`) still names a path that keeps working from
    wherever a spawned child's own `cwd` ends up. The one source of a child's own
    argv0, here and in `cli._poll`'s own default -- `application/` never reads
    `sys.argv` or the filesystem to find it."""
    return os.path.abspath(sys.argv[0])


def _child_argv() -> list[str]:
    """This invocation's own argv, `--detach` stripped so the child can never re-detach
    (Tech Lead plan, subtask `--detach`), and `sys.argv[0]` resolved through
    `_self_executable` rather than read bare: a relative or PATH-found argv0 would
    still work for a re-exec in the same directory, but GH-140's poller spawns from
    wherever it was started, long after that directory stopped mattering."""
    return [_self_executable(), *(arg for arg in sys.argv[1:] if arg != "--detach")]


def _spawn_detached(argv: list[str], log_path: str) -> int:
    """The default `Spawner`: a new process image (`Popen`, never a fork -- forking
    after `setsid` would still hold this terminal's fds, and forking after
    `telemetry.configure` would carry its background export threads into a run that
    then says nothing about them), detached from this session (`start_new_session`),
    with stdin closed (a detached child cannot answer a prompt) and stdout/stderr onto
    the log file before any role runs. `AGENTIC_LOG_FILE` carries this invocation's
    already-computed, already-printed path into the child, so `_log_file_for` there
    returns the same string instead of minting a second file.

    When `Popen` itself raises (GH-140: a poller's own `argv0` not found, a bare
    `FileNotFoundError`), the log file this call opened is removed before the
    exception propagates -- but only when this very call created it (`existed`,
    checked before the `open`): a poller's own child failing every round must not leak
    one empty log file per round forever, but a log path a previous, successful
    invocation already wrote into is never this call's file to delete. The `env` this
    builds does not otherwise change: this product's own child inherits it whole."""
    env = {**os.environ, ENV_PREFIX + "LOG_FILE": log_path}
    existed = os.path.exists(log_path)
    try:
        with open(log_path, "ab", buffering=0) as log:
            process = subprocess.Popen(
                argv,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
    except OSError:
        if not existed and os.path.exists(log_path):
            os.unlink(log_path)
        raise
    return process.pid


class SpawnLauncher:
    """The real `Launcher` (GH-137): the poller's one way to close, resume or launch,
    each a detached `agentic close|resume|run` child, never a role in its own process.
    Wraps `_spawn_detached` exactly as `run`/`resume --detach` do; the log path is
    minted fresh (`_mint_log_path`, never `_log_file_for`'s `cfg.log_file`
    short-circuit, which would make every child share the poller's own log), keyed off
    the child's own command and issue number, parsed out of `argv` alone -- a poller
    action has no task id of its own to pass, by the port's own shape."""

    def __init__(self, cfg: Configuration) -> None:
        self._cfg = cfg

    def spawn(self, argv: list[str]) -> int:
        return _spawn_detached(argv, _mint_log_path(self._cfg, _poll_child_label(argv)))


def _poll_child_label(argv: list[str]) -> str:
    """`poll-<command>-<issue>` when both are found in `argv` (always, for a poller's
    own children), `poll-<command>` otherwise -- never raises on a shape this module
    does not itself build, so a double or a future caller with a different argv still
    gets a label instead of an exception."""
    command = argv[1] if len(argv) > 1 else "run"
    issue = None
    if "--issue" in argv:
        at = argv.index("--issue")
        if at + 1 < len(argv):
            issue = argv[at + 1]
    return f"poll-{command}-{issue}" if issue else f"poll-{command}"


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
def _locked(lock: RunLock, task_id: str, stop: StopCause | None = None) -> Iterator[None]:
    """Around the invocation only (Tech Lead plan): refuses before anything is spent,
    releases whether the invocation delivers, escalates or raises -- except when the
    system shutdown stopped it (`stop.at_shutdown`): the record stays, marked, so the
    next boot can retake the run."""
    taken_over = _acquire(lock, task_id)
    if taken_over is not None:
        print(
            f"agentic: took over the lock left by pid {taken_over.pid} on "
            f"{taken_over.host} ({taken_over.started_at}), no longer running"
        )
    try:
        yield
    finally:
        if stop is not None and stop.at_shutdown:
            lock.keep_at_shutdown(task_id)
        else:
            lock.release(task_id)


@contextmanager
def _inhibited(why: str) -> Iterator[None]:
    """Holds a sleep/shutdown inhibitor for the invocation (GH-176). Best effort, the
    same shape as `_registered`: where it is unavailable the run goes on, one stderr
    line the only trace. Entered after the lock, inside the process that runs, so there
    is a single pid and a refused run never took it."""
    with inhibitor.hold(why) as unavailable:
        if unavailable is not None:
            print(f"agentic: sleep/shutdown inhibitor unavailable: {unavailable}", file=sys.stderr)
        yield


def _registry_for(cfg: Configuration) -> RunRegistry:
    return FilesystemRunRegistry(cfg.run_registry_dir)


@contextmanager
def _registered(registry: RunRegistry, product: str, task_id: str) -> Iterator[None]:
    """Registers this invocation under `Configuration.run_registry_dir` so the
    poller's own concurrency cap (`AGENTIC_MAX_CONCURRENT_RUNS`, GH-137) can count it
    across every product on this machine. Best effort only, the same shape every other
    diagnostic write in this module already has: a `run`/`resume` that cannot write
    here still runs, one stderr line the only trace -- it must never turn into a
    `CannotStart`, since nothing about the task itself failed."""
    try:
        registry.register(product, task_id)
    except OSError as exc:
        print(f"agentic: could not register the run: {exc}", file=sys.stderr)
    try:
        yield
    finally:
        try:
            registry.unregister()
        except OSError as exc:
            print(f"agentic: could not clear the run registration: {exc}", file=sys.stderr)


async def _poll(
    cfg: Configuration,
    ports: Ports,
    interval_s: float,
    run_lock: RunLock | None = None,
    registry: RunRegistry | None = None,
    launcher: Launcher | None = None,
    log_file: str | None = None,
    detach: bool = False,
    child_argv: list[str] | None = None,
    spawn: Spawner | None = None,
    argv0: str | None = None,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> None:
    """`agentic poll --product-dir <dir> [--detach]` (GH-137): a long-lived process per
    product, one `poll.run_once` round every `interval_s` seconds, under the same
    per-product `FilesystemRunLock` `run`/`resume`/`close` already take -- keyed by
    `poll.LOCK_KEY` instead of a task id, so two pollers over the same product refuse
    each other the same way two `run`s over the same task already do. `--detach` re-execs
    exactly as `run --detach`/`resume --detach` do (`_detach`); `_signal_guarded`
    (`main`'s own wrapper) turns a SIGTERM/SIGHUP into a clean stop between rounds, the
    lock released on the way out.

    `argv0` names the executable every spawned child gets (GH-140); `main`'s own
    `_poll` dispatch never passes it, so the default `_self_executable()` wins -- the
    same value `_child_argv()[0]` would give, computed once there already. `argv0`
    exists for whoever calls `_poll` directly, a test among them, to pass its own
    string. Every line a round
    produced is printed with `flush=True` as soon as it is known, the process still
    alive to write it: block-buffered to a file (a redirected stdout's own default) is
    exactly how the first real poller's 32 minutes of `nothing to do` sat unseen until
    it died. `sleep` replaces the bare `anyio.sleep` call between rounds, injectable
    the same way `poll.run_once`'s own `sleep` already is, for a test to prove every
    line of a round is flushed before this ever awaits it."""
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    if detach:
        _detach(lock, pollmod.LOCK_KEY, child_argv or [], log_file or "", spawn)
        return
    registry = registry if registry is not None else _registry_for(cfg)
    real_launcher = launcher if launcher is not None else SpawnLauncher(cfg)
    executable = argv0 if argv0 is not None else _self_executable()
    with _locked(lock, pollmod.LOCK_KEY):
        while True:
            for line in pollmod.run_once(
                cfg,
                ports,
                registry,
                real_launcher,
                datetime.now(UTC),
                executable,
                lock=lock,
                current_boot=liveness.current_boot_id(),
                eval_set=_round_eval_set(ports),
            ):
                print(line, flush=True)
            await sleep(interval_s)


async def _run(
    task: TaskInput,
    cfg: Configuration,
    ports: Ports,
    fresh: bool = False,
    checkpointer: Checkpointer | None = None,
    run_lock: RunLock | None = None,
    registry: RunRegistry | None = None,
    log_file: str | None = None,
    detach: bool = False,
    child_argv: list[str] | None = None,
    spawn: Spawner | None = None,
    stop: StopCause | None = None,
) -> None:
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    registry = registry if registry is not None else _registry_for(cfg)
    config: RunnableConfig = {"configurable": _configurable(task["id"], log_file)}
    open_checkpointer = checkpointer or composition.checkpointer
    async with open_checkpointer(cfg, task["id"]) as saver:
        app = graph.compile(checkpointer=saver)
        if not fresh:
            await _refuse_a_started_task(app, config, task["id"], ports)
        if detach:
            _detach(lock, task["id"], child_argv or [], log_file or "", spawn)
            return
        with (
            _locked(lock, task["id"], stop),
            _registered(registry, product_name(ports), task["id"]),
            _inhibited(f"{task['id']}: run"),
        ):
            _archive_previous_run(task["id"], ports)
            # A thread keeps its channels: a new run over it would add to the old cost.
            await saver.adelete_thread(task["id"])
            # The thread just dropped: routing now resolves to the task's own file
            # (ADR-0003, addendum 2026-10-04), so an old task migrates off the shared
            # store the moment it is started over. A no-op in cost alone for a task
            # already on its own file.
            async with open_checkpointer(cfg, task["id"]) as migrated_saver:
                migrated_app = graph.compile(checkpointer=migrated_saver)
                # The one place `decided` is set (`rules.entering`): before any node
                # runs, so `--fresh`, the poller and `eval run` all reach it.
                entered = rules.entering(task, cap=cfg.decided_cap)
                final = await _invoke(migrated_app, entered, config, entered, ports, cfg, "run")
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
    registry: RunRegistry | None = None,
    log_file: str | None = None,
    detach: bool = False,
    child_argv: list[str] | None = None,
    spawn: Spawner | None = None,
    stop: StopCause | None = None,
) -> None:
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    registry = registry if registry is not None else _registry_for(cfg)
    config: RunnableConfig = {"configurable": _configurable(task_id, log_file)}
    open_checkpointer = checkpointer or composition.checkpointer
    async with open_checkpointer(cfg, task_id) as saver:
        app = graph.compile(checkpointer=saver)
        snapshot = await app.aget_state(config)
        try:
            if from_document or not snapshot.values:
                paused = resuming.from_document(task_id, ports)
                r = resuming.prepare(task_id, paused, "document", cfg, ports)
            elif not snapshot.next:
                status = snapshot.values.get("status", "open")
                raise CannotStart(f"{task_id} is not paused: its run ended ({status})")
            elif legacy_names.has_legacy_roles(snapshot.values):
                if not snapshot.interrupts:
                    # Crashed, not paused: nothing to reseed from safely.
                    raise CannotStart(_legacy_crashed_message(task_id, ports))
                # Paused: take the document branch exactly as `--from-document` does,
                # never `aupdate_state` with translated values on this existing thread.
                try:
                    paused = resuming.from_document(task_id, ports)
                except ValueError as exc:
                    prefix = f"the document of {task_id} and git disagree:"
                    message = str(exc)
                    if message.startswith(prefix):
                        listed = message[len(prefix) :].lstrip("\n")
                        raise ValueError(f"{_LEGACY_DISAGREEMENT_CAUSE}\n{listed}") from exc
                    raise
                r = resuming.prepare(task_id, paused, "document", cfg, ports)
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

        with (
            _locked(lock, task_id, stop),
            _registered(registry, product_name(ports), task_id),
            _inhibited(f"{task_id}: resume"),
        ):
            if r.source == "document":
                # Seeded as if `escalate` had just run: the task pauses once more,
                # without telling the human again, and the answer below resumes it
                # like a checkpoint. The thread just dropped: routing now resolves to
                # the task's own file (ADR-0003, addendum 2026-10-04), so a task
                # resumed from its document migrates off the shared store. Seeding
                # happens before `resuming.announce` (today's order, unchanged): a
                # failure reopening the store -- the legacy lookup's own
                # `database is locked` risk among them -- must not unlabel
                # `needs-human` or post a "Resumed:" comment for a resume that never
                # actually seeded anything.
                await saver.adelete_thread(task_id)
                async with open_checkpointer(cfg, task_id) as migrated_saver:
                    migrated_app = graph.compile(checkpointer=migrated_saver)
                    await migrated_app.aupdate_state(config, r.paused, as_node=ESCALATE)
                    crashed = r.source == "checkpoint" and not snapshot.interrupts
                    command: Command | None = (
                        None if crashed else Command(resume={"decision": r.decision})
                    )
                    resuming.announce(r, ports)
                    final = await _invoke(
                        migrated_app, command, config, r.paused, ports, cfg, "resume", r.decision
                    )
            else:
                crashed = r.source == "checkpoint" and not snapshot.interrupts
                command = None if crashed else Command(resume={"decision": r.decision})
                resuming.announce(r, ports)
                final = await _invoke(
                    app, command, config, r.paused, ports, cfg, "resume", r.decision
                )
    _report(final, task_id, ports)


CloseStatus = Literal[
    "closed",
    "still_open",
    "already_closed",
    "backfilled",
    "no_pull_request",
    "no_document",
    "tracker_error",
]


@dataclass(frozen=True)
class _ClosureOutcome:
    """What `_close_document` hands back instead of raising: a status from the closed
    set `CloseStatus` plus the message either caller prints (`--issue`'s wrapper turns
    every status but `"closed"` and `"backfilled"` into `CannotStart(message)`, byte-for-
    byte what `_close` used to raise inline; `--all`'s sweep prints one line per task and
    keeps going). The four figures are set only when `status == "closed"`, the path when
    a document was written (Tech Lead plan, subtask `close-all`)."""

    status: CloseStatus
    message: str
    outcome: delivery_metrics.DeliveryOutcome | None = None
    human_commits: int | None = None
    human_lines_changed: int | None = None
    time_to_merge: float | None = None
    path: Path | None = None


def _close_document(task_id: str, cfg: Configuration, ports: Ports) -> _ClosureOutcome:
    """The closure of one task: read -> ask `gh` -> write, the body `_close` used to
    run inline and `_close_all`'s sweep now shares (Tech Lead plan, subtask
    `close-all`) -- one definition, so `--issue` and `--all` cannot drift on what
    "closed" means. Runs under the caller's own lock (`_close`'s and `_close_all`'s per-
    task `_locked`, never taken here): a second `close` over the same task cannot race
    this read -> write.

    Every refusal is independently reachable, in this order: a missing document, a
    missing `pull_request` value or an already-recorded closure, read off the document
    alone, before `gh` is ever asked anything; and a pull request `gh` cannot be read
    or is still open, last of all. No sample backs this verdict any more -- Tech Lead
    plan, subtask `close-verdict`: the metric endpoint that used to gate the write, and
    the ordering that made the write wait on a true flush, both had a sample as their
    subject, and a predicate with no subject is dead weight. `record_closure` now runs
    directly off the figures computed from the pull request, the same `gh` read a retry
    would repeat."""
    raw = ports.documents.load(task_id)
    if raw is None:
        return _ClosureOutcome("no_document", f"{task_id} has no document to close")
    reference = raw.get("pull_request")
    if not reference:
        return _ClosureOutcome("no_pull_request", f"{task_id} has no pull request to close")
    closed = bool(raw.get("closed_at"))
    if closed and not stack_signals.needs_merge_commit(raw):
        return _ClosureOutcome("already_closed", f"{task_id} already closed on {raw['closed_at']}")
    try:
        pull_request: PullRequest | None = ports.tracker.pull_request(reference)
    except (ValueError, OSError) as exc:
        return _ClosureOutcome("tracker_error", str(exc))
    if pull_request is None:
        return _ClosureOutcome(
            "tracker_error", f"{task_id}: could not read its pull request ({reference})"
        )
    if closed:
        return _backfill_merge_commit(task_id, raw, pull_request, ports)
    if pull_request.state == "OPEN":
        return _ClosureOutcome(
            "still_open", f"{task_id}: the pull request is still open; nothing to close"
        )

    author = cfg.git_author_email
    outcome = delivery_metrics.delivery_outcome(
        pull_request, author, auto_merge_head=raw.get("auto_merge_head")
    )
    human_commits = delivery_metrics.human_commits(pull_request, author)
    human_lines_changed = delivery_metrics.human_lines_changed(pull_request, author)
    time_to_merge = delivery_metrics.time_to_merge_seconds(pull_request)

    values = {
        "closed_at": _now_iso(),
        "delivery_outcome": outcome,
        "human_commits": str(human_commits),
        "human_lines_changed": str(human_lines_changed),
    }
    # Written only when the pull request merged: `time_to_merge` is `None` for one that
    # never did, and the key is omitted entirely rather than written empty (Tech Lead
    # plan, subtask `close-all`). Rounded to a whole second and quoted as a
    # string: `record_closure` writes every value through `_scalar_line`, which
    # yaml-dumps whatever it is given, and `document.parse` hands every frontmatter
    # scalar back as a string -- `evaluation.TaskDocument` types it `str` to match.
    if time_to_merge is not None:
        values["time_to_merge_seconds"] = str(int(time_to_merge))
    # Same rule: omitted, never empty, for a pull request that never merged (ADR-0038).
    if pull_request.merge_commit:
        values["merge_commit"] = pull_request.merge_commit
    path = ports.documents.record_closure(task_id, values)
    return _ClosureOutcome(
        "closed",
        "",
        outcome=outcome,
        human_commits=human_commits,
        human_lines_changed=human_lines_changed,
        time_to_merge=time_to_merge,
        path=path,
    )


def _backfill_merge_commit(
    task_id: str, raw: Mapping[str, object], pull_request: PullRequest, ports: Ports
) -> _ClosureOutcome:
    """A document closed as merged before `merge_commit` existed gets that one key and
    nothing else: `closed_at`, `delivery_outcome`, `human_commits`,
    `human_lines_changed` and `time_to_merge_seconds` are never rewritten (ADR-0038).
    When `gh` has no merge commit to give, nothing is written and the document reads as
    already closed."""
    if not pull_request.merge_commit:
        return _ClosureOutcome(
            "already_closed",
            f"{task_id} already closed on {raw['closed_at']}; its pull request has no merge commit",
        )
    path = ports.documents.record_closure(task_id, {"merge_commit": pull_request.merge_commit})
    return _ClosureOutcome(
        "backfilled",
        f"{task_id}: merge_commit backfilled ({pull_request.merge_commit})",
        path=path,
    )


def _upstream_eval_set(ports: Ports, upstream: Upstream) -> frozenset[int] | None:
    """The issues of the evaluation set as the upstream sha declares them (GH-178): their
    merge is the human's verdict, so `rules.auto_merge_decision` never merges one. No file
    there is an empty set; a file that does not parse, an upstream that fell back with a
    note, or none at all is `None`, which refuses every merge -- nothing then proves the
    issue is outside the set. `cli.py` is the one place that reads the declaration."""
    if upstream.sha is None or upstream.note:
        return None
    text = ports.workspace.show(upstream.sha, str(evaluation.DECLARATION_PATH))
    if text is None:
        return frozenset()
    try:
        return frozenset(evaluation.parse_eval_set(yaml.safe_load(text), "eval set").issues)
    except (ValueError, yaml.YAMLError):
        return None


def _round_eval_set(ports: Ports) -> frozenset[int] | None:
    """What one poll round hands `poll.run_once`: read (one upstream fetch) only when some
    delivered task awaits a merge at all, the empty set otherwise, which nothing reads."""
    if not pollmod.awaiting_merge(ports):
        return frozenset()
    return _upstream_eval_set(ports, ports.workspace.upstream())


async def _merge(task_id: str, issue: int, cfg: Configuration, ports: Ports) -> None:
    """`agentic merge --issue N` (GH-178, ADR-0045): the task's pull request merged when
    `rules.auto_merge_decision` says so, after the checks ran on its head merged with the
    upstream. Under the task's own run lock and registered like any run; spends no model
    money and opens no span, like `close`. The closure runs in this process once the pull
    request is merged."""
    lock = _lock_for(cfg)
    with _locked(lock, task_id), _registered(_registry_for(cfg), product_name(ports), task_id):
        raw = ports.documents.load(task_id)
        if raw is None or not raw.get("pull_request"):
            raise CannotStart(f"{task_id} has no pull request to merge")
        upstream = ports.workspace.upstream()
        result = merge_task(
            task_id,
            issue,
            cfg,
            ports,
            upstream,
            _upstream_eval_set(ports, upstream),
            lambda line: print(line, flush=True),
        )
        closed = _close_document(task_id, cfg, ports) if result.kind == "merged" else None
    suffix = f": {result.reason}" if result.reason else ""
    print(f"agentic: merge: {task_id} -> {result.kind}{suffix}", flush=True)
    if closed is not None and closed.status != "closed":
        print(f"agentic: merge: {task_id}: not closed yet: {closed.message}", flush=True)


async def _land(task_id: str, cfg: Configuration, ports: Ports) -> None:
    """`agentic land --issue N` (GH-239, ADR-0048): the step `landing_step` names for the
    refusal of the task's pull request, recorded under the task's run lock and then
    dispatched. Registered like any run; spends no model money itself and opens no span."""
    lock = _lock_for(cfg)
    with _registered(_registry_for(cfg), product_name(ports), task_id):
        upstream = ports.workspace.upstream()
        try:
            result = land(
                task_id,
                cfg,
                ports,
                lock,
                upstream,
                _upstream_eval_set(ports, upstream),
                lambda line: print(line, flush=True),
                launcher=SpawnLauncher(cfg),
                argv0=_self_executable(),
            )
        except LockHeld as exc:
            raise CannotStart(f"{exc}: {LOCK_SCOPE}") from exc
    suffix = f": {result.reason}" if result.reason else ""
    print(f"agentic: land: {task_id} -> {result.kind}{suffix}", flush=True)


async def _close(task_id: str, cfg: Configuration, ports: Ports) -> None:
    """`agentic close --issue N`: the human's verdict on a task's pull request, read
    through `gh` and recorded once. Runs no role and opens no span (`preflight` never
    runs here either); the refusal it still checks before touching a document is the
    product's own name (`ports.stack.product()`) -- a call kept for its refusal alone
    (Tech Lead plan, subtask `close-verdict`: no sample carries the product name any
    more, so the value itself goes unused).

    A thin wrapper over `_close_document` (Tech Lead plan, subtask `close-all`): the
    same `FilesystemRunLock` as `run`/`resume`, through `_locked`, covers the whole of
    read -> write, and every non-`"closed"` status becomes `CannotStart` with
    that status's own message, byte-for-byte what this function raised inline before
    the body moved -- the existing assertions in `tests/test_cli.py` that pin them are
    the proof."""
    try:
        ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    lock = _lock_for(cfg)
    with _locked(lock, task_id):
        result = _close_document(task_id, cfg, ports)
    if result.status == "backfilled":
        print(f"agentic: {result.message}")
        print(f"doc:                 {result.path}")
        print("agentic: document written, not committed")
        return
    if result.status != "closed":
        raise CannotStart(result.message)
    print(f"outcome:             {result.outcome}")
    print(f"human_commits:       {result.human_commits}")
    print(f"human_lines_changed: {result.human_lines_changed}")
    print(f"time_to_merge:       {result.time_to_merge}")
    print(f"doc:                 {result.path}")
    print("agentic: document written, not committed")


async def _close_all(cfg: Configuration, ports: Ports) -> None:
    """`agentic close --all [--product-dir PATH]`: sweeps every task document with a
    `pull_request` and no `closed_at` -- exactly `_refuse_open_pull_request`'s own
    reading of "not yet closed", so the two commands agree -- and closes each one
    through the same `_close_document` `--issue` uses (Tech Lead plan, subtask
    `close-all`). Idempotent: a document `_close_document` already closed is never
    selected, so rerunning it finds nothing left to do.

    The sweep runs unconditionally (Tech Lead plan, subtask `close-verdict`): no sample
    backs the verdict any more, so the capability predicate that used to refuse the
    whole sweep before touching a document (`telemetry.metrics_configured`, GH-119) has
    no subject left to guard, and a predicate that always returns true is a dead gate.
    A machine with no `OTEL_EXPORTER_OTLP_ENDPOINT` can now close, which is strictly
    more capable. Each task still takes its own lock, one at a time, the way `_close`
    already does -- never the `"eval"` lock, which would block an evaluation run for the
    duration of this sweep.

    The stop-on-first-failure structure survives with its trigger changed from a false
    flush to a `gh` failure: `_close_document`'s own `"tracker_error"` status, raised
    when the pull request cannot be read at all. A task after the stopped one is never
    even asked `gh` about, so it prints as not attempted, never as a claim about a pull
    request state this sweep stopped reading. Non-zero exit only when not one document
    in the corpus could be read at all; a corpus with nothing left to close, or a sweep
    that stopped partway, both exit zero with what happened printed.

    One line per document, the five kinds the plan names: `closed`, `still open`
    (`gh` says so), `already closed` (nothing to do -- a previous `close --issue N` or
    sweep got there first), `no pull request` (nothing to do -- the task has none yet)
    and `locked, skipped` (another process holds this task right now). The last two
    read off the document alone, the same fact `_refuse_open_pull_request` already
    reads, so no `gh` call is spent on a document this sweep would not select anyway."""
    try:
        ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    lock = _lock_for(cfg)
    task_ids = ports.documents.ids()
    readable = 0
    closed: list[str] = []
    backfilled: list[str] = []
    remaining: list[str] = []
    stopped = False
    for task_id in task_ids:
        try:
            raw = ports.documents.load(task_id)
        except (ValueError, OSError) as exc:
            print(f"agentic: {task_id}: could not be read: {exc}")
            continue
        readable += 1
        raw = raw or {}
        number = ports.inbox.issue_of(task_id)
        label = f"{task_id} (issue {number})" if number is not None else task_id
        reference = raw.get("pull_request")
        if not reference:
            print(f"agentic: {label}: no pull request")
            continue
        if raw.get("closed_at") and not stack_signals.needs_merge_commit(raw):
            print(f"agentic: {label}: already closed")
            continue
        if stopped:
            print(f"agentic: {label}: not attempted (sweep stopped on an earlier gh failure)")
            remaining.append(label)
            continue
        refusal = lock.refusal(task_id)
        if refusal is not None:
            print(f"agentic: {label}: locked, skipped ({refusal})")
            remaining.append(label)
            continue
        with _locked(lock, task_id):
            result = _close_document(task_id, cfg, ports)
        if result.status == "closed":
            print(f"agentic: {label}: closed ({result.outcome})")
            closed.append(label)
        elif result.status == "backfilled":
            print(f"agentic: {label}: {result.message.partition(': ')[2]}")
            backfilled.append(label)
        elif result.status == "still_open":
            print(f"agentic: {label}: still open")
            remaining.append(label)
        else:
            print(f"agentic: {label}: {result.message}")
            remaining.append(label)
            if result.status == "tracker_error":
                stopped = True
    if task_ids and readable == 0:
        raise CannotStart("no document could be read")
    print(f"agentic: closed {len(closed)}: {', '.join(closed) or '(none)'}")
    if backfilled:
        print(f"agentic: backfilled {len(backfilled)}: {', '.join(backfilled)}")
    print(f"agentic: remaining {len(remaining)}: {', '.join(remaining) or '(none)'}")


def _landing_commit(product_dir: Path, relative: str) -> str:
    """The commit that brought `relative` to the main line, ADR-0038: the last commit to
    it on `HEAD`'s first-parent chain -- the landing merge in a repo that merges with
    `--no-ff`, the commit itself in a squash or fast-forward one. `HEAD` is passed
    explicitly and the walk is first-parent, never date order. `""` when the path was
    never committed there (a greenfield product's `.agentic/stack.yaml`, or a repo with
    no `BRIEF.md` yet)."""
    result = subprocess.run(
        ["git", "log", "--first-parent", "-1", "--format=%H", "HEAD", "--", relative],
        cwd=product_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _main_line_positions(product_dir: Path) -> dict[str, int]:
    """`sha -> 0-based position` over `HEAD`'s first-parent chain, oldest first: one
    read per invocation, linear in the length of the main line (ADR-0038). Empty when
    `HEAD` has no commit."""
    result = subprocess.run(
        ["git", "rev-list", "--first-parent", "--reverse", "HEAD"],
        cwd=product_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return {sha: position for position, sha in enumerate(result.stdout.split())}


def _task_deliveries(ports: Ports, positions: Mapping[str, int]) -> list[tuple[int | None, str]]:
    """Every task document's `(position of its merge commit on the main line,
    delivery_outcome)`, off the product's own `.agentic/tasks/` corpus `ports.documents`
    already is (never the stack review's own `.agentic/stack-reviews/`, which this
    command is about to write to): the plain values `stack_signals.signals`'s own
    `deliveries` argument takes -- the pure module itself takes no port (ADR-0034). A
    document with no `merge_commit`, or one not on the main line, has no position and
    is not counted: the undercount ADR-0038 names."""
    deliveries: list[tuple[int | None, str]] = []
    for task_id in ports.documents.ids():
        raw = ports.documents.load(task_id) or {}
        position = positions.get(str(raw.get("merge_commit") or ""))
        deliveries.append((position, str(raw.get("delivery_outcome") or "")))
    return deliveries


def _stack_review_id() -> str:
    """`stack-<YYYY-MM-DD>` (ADR-0034): a same-day rerun overwrites its own document,
    and the shape is deliberately outside `^GH-(\\d+)$`, so every keyed `Tracker` verb
    -- `comment`, `label`, `pull_request` -- is a no-op against it. Never reused to open
    a pull request: `open_pull_request` is the one verb that does not guard on
    `issue_number` (Tech Lead plan, subtask `verb`)."""
    return f"stack-{datetime.now(UTC).strftime('%Y-%m-%d')}"


def _print_stack_dry_run(signals: stack_signals.Signals) -> None:
    """`--dry-run`'s own report: the anchor, every signal's own text and what the real
    run would open -- no `gh` call, no lock, no file (invariant)."""
    print(f"agentic: anchor: {signals.anchor or '(none, greenfield)'}")
    for line in stack_signals.description(signals).splitlines():
        print(f"agentic: {line}")
    if stack_signals.fired(signals):
        print(
            "agentic: would open: a proposal issue (or a [needs-info] issue, if the "
            "Brief is missing or the interview cannot decide)"
        )
    else:
        print("agentic: no signal fired; would open nothing")


def _open_needs_info_for_missing_brief(
    ports: Ports, review_id: str, signals: stack_signals.Signals
) -> str:
    """The spec's own item 1: no `BRIEF.md`, no decision (ADR-0025) -- a `[needs-info]`
    issue with the system's own template, the same text `no_brief` already prints on
    `run`, and no role runs at all."""
    verdict = (
        "the product has no `BRIEF.md`: a stack review needs it to decide against "
        "(ADR-0025). Write it at the product's root, and commit it:\n\n"
        + knowledge.DEFAULT_BRIEF_TEMPLATE.read_text(encoding="utf-8")
    )
    body = stack_review_texts.body(
        signals.anchor, stack_signals.description(signals), verdict, "", "", ""
    )
    if (refusal := stack_review_texts.body_refusal(body)) is not None:
        raise CannotStart(refusal)
    return ports.tracker.open_issue(f"[needs-info] Stack review {review_id}: no BRIEF.md", body)


def _window_summary(
    documents: Sequence[Mapping[str, Any]],
    playbooks: Mapping[str, Playbook],
    applied: Sequence[Delta],
    gained: int,
) -> tuple[str, list[Delta]]:
    """The summary every 10 reflected tasks (`measurement.playbook_window`), plus the
    retirements it proposes as deltas: the root is the one place that may join the
    process use case to the measurement window (ADR-0010)."""
    comparisons: list[playbook_window.Comparison] = []
    proposals: list[playbook_window.CheckProposal] = []
    retirements: list[Delta] = []
    for slug in sorted(playbooks):
        found = playbook_window.compare(documents, playbooks[slug])
        comparisons += found
        proposals += playbook_window.check_proposals(found, playbooks[slug])
        retirements += [
            Delta("retire", slug, item_id=item_id)
            for comparison in found
            if comparison.conclusive
            for item_id in comparison.retire
        ]
    text = playbook_window.summary_markdown(
        comparisons, proposals, [*applied, *retirements], items_gained=gained
    )
    return text, retirements


async def _reflect(
    cfg: Configuration,
    ports: Ports,
    task_id: str | None,
    since: str | None,
    run_lock: RunLock | None = None,
) -> None:
    """`agentic reflect --product-dir D [--issue N | --all] [--since YYYY-MM-DD]` (ADR-0043):
    the reflector over the closed tasks no earlier run reflected on, at most
    `reflect_max_tasks_per_run` of them, delivered as candidate playbook deltas on
    `agentic/playbook`. Under the run lock keyed `playbook`, so two invocations (the
    poller's and a person's) never reflect on the same task twice. It runs no task and
    writes to none: a reflection that fails is recorded in `reflected.yaml`, never raised
    into a task."""
    if ports.workspace.repository_at(cfg.product_dir) is None:
        raise CannotStart(f"{cfg.product_dir} is not a git repository, or git is not installed")
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    try:
        with _locked(lock, reflect_run.TASK_ID):
            outcome = await reflect_run.reflect(
                cfg, ports, now=_now_iso(), summarise=_window_summary, since=since, task_id=task_id
            )
    except (ValueError, GitError, OSError) as exc:
        raise CannotStart(f"reflect: {exc}") from exc
    print(f"reflected {outcome.reflected}, {outcome.left} left for the next run")
    if outcome.url:
        print(f"candidate:  {outcome.url}")


async def _stack_review(
    cfg: Configuration, ports: Ports, reason: str | None, dry_run: bool
) -> None:
    """`agentic stack review [--reason TEXT] [--dry-run]`: the Tech Lead in proactive
    mode over the product, with no task behind it at all (ADR-0034) -- no PjM, no issue
    it came from, no branch. Composes its own gate here instead of a flag on
    `preflight` (`preflight.py` stays byte-identical): `refuse_its_own_checkout` and
    the free per-role prompt render always; `declared_stack`/`ports.stack.product()`
    only when `.agentic/stack.yaml` is already declared at HEAD, since its absence is
    the one fact this verb exists to change and `preflight` would otherwise refuse on
    it (`knowledge_stack.NotDeclared` is the one exception this gate swallows; any
    other failure of a declared stack still propagates).

    Two refusals of the verb's own, both before anything is spent: `product_dir` not a
    git repository, or git not installed (`repository_at` answers the same `None` for
    both, so both halves are named), and an already-open proposal this path opened
    before (`Inbox.open_issues` + `domain.stack_review.open_proposal`), which refuses
    with that issue's URL and prints the anchor so a stale one reads as stale.
    `--dry-run` returns before either the `gh` read or the lock: it reports the
    signals, the anchor and what would be opened, and spends nothing."""
    if ports.workspace.repository_at(cfg.product_dir) is None:
        raise CannotStart(f"{cfg.product_dir} is not a git repository, or git is not installed")
    refuse_its_own_checkout(cfg, ports)
    vocabulary = ports.flags.load()
    for role in ROLES:
        load_prompt(role, cfg, ports, vocabulary, **PLACEHOLDERS)
    try:
        declared_stack(ports)
        ports.stack.product()
    except knowledge_stack.NotDeclared:
        pass

    positions = _main_line_positions(cfg.product_dir)
    anchor = _landing_commit(cfg.product_dir, str(knowledge_stack.OVERLAY))
    brief = _landing_commit(cfg.product_dir, "BRIEF.md")
    signals = stack_signals.signals(
        anchor=anchor,
        anchor_pos=positions.get(anchor),
        brief_pos=positions.get(brief),
        deliveries=_task_deliveries(ports, positions),
        reason=reason or "",
    )
    if dry_run:
        _print_stack_dry_run(signals)
        return

    open_issues = ports.inbox.open_issues()
    if (existing := stack_review_texts.open_proposal(open_issues)) is not None:
        raise CannotStart(
            f"a stack review proposal is already open: {existing} (anchor "
            f"{signals.anchor or '(none)'}); close it before this path opens another"
        )

    review_id = _stack_review_id()
    lock = _lock_for(cfg)
    if ports.knowledge.brief() is None:
        with _locked(lock, review_id):
            url = _open_needs_info_for_missing_brief(ports, review_id, signals)
        print(f"agentic: opened {url}")
        return
    if not stack_signals.fired(signals):
        for line in stack_signals.description(signals).splitlines():
            print(f"agentic: {line}")
        print("agentic: no signal fired; nothing to review")
        return

    stack_ports = replace(
        ports, documents=FilesystemTaskDocuments(cfg.product_dir, subdir=STACK_REVIEWS_SUBDIR)
    )
    description = stack_signals.description(signals)
    with _locked(lock, review_id):
        # A stack review's synthetic `TaskState` is capped by the flat ceiling, 250,
        # like any task (`rules.task_cap` through `run_role`; ADR-0004, 2026-10-05
        # addendum).
        result = await run_stack_review(review_id, description, signals.anchor, cfg, stack_ports)
    print(f"id:         {review_id}")
    print(f"needs_info: {result.needs_info}")
    print(f"issue:      {result.issue_url}")
    print(f"doc:        {result.document}")


def _now_iso() -> str:
    """ISO-8601 UTC, millisecond precision -- the same shape `closed_at`'s neighbours in
    the frontmatter already use (`run_role._now_iso`, not importable across layers from
    a driving adapter, so this is its own copy of the same one-liner)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _read_eval_set(cfg: Configuration, set_path: str | None) -> tuple[evaluation.EvalSet, str, str]:
    """The declaration as a value, its own text (`evaluation.baseline_id`'s own input)
    and where it came from: `--set` overrides the product's own `.agentic/eval-set.yaml`
    (`evaluation.DECLARATION_PATH`) for a spike, and the caller reports which of the two
    it read (Tech Lead plan, subtask `eval-cli`). No port: `cli.py` is the composition
    root and already reads `.agentic/` directly, the same precedent `flags.py`'s adapter
    has for `.agentic/flags.yaml` -- a second caller would earn this its own port, not a
    first one with no other module to share it with."""
    if set_path is not None:
        path, source = Path(set_path), set_path
    else:
        path = cfg.product_dir / evaluation.DECLARATION_PATH
        source = str(evaluation.DECLARATION_PATH)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotStart(f"{source}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CannotStart(f"{source}: {exc}") from exc
    try:
        declaration = evaluation.parse_eval_set(data, source)
    except ValueError as exc:
        raise CannotStart(str(exc)) from exc
    return declaration, text, source


def _refuse_open_pull_request(ports: Ports, task_id: str) -> None:
    """A set task whose pull request is still open would have its branch archived out
    from under that pull request by the `--fresh` each child runs with (ADR-0011, sixth
    addendum). Read off the document alone, never `gh`: neither `eval` phase may call
    it (Tech Lead plan invariant). `pull_request` present with no `closed_at` is the
    document's own way of saying "not yet closed" -- `agentic close` is what would set
    `closed_at`, and it is the one place allowed to ask `gh` whether a pull request is
    still open. A task never before run, or one `agentic close` already recorded, never
    refuses here."""
    document = ports.documents.load(task_id)
    reference = (document or {}).get("pull_request")
    if reference and not (document or {}).get("closed_at"):
        raise CannotStart(
            f"{task_id}: has a pull request with no recorded closure (`pull_request` "
            "set, no `closed_at`); --fresh would archive the branch it points at. Run "
            f"`agentic close --issue N` first."
        )


def _run_eval_child(argv: list[str], env_overlay: dict[str, str]) -> None:
    """The default `EvalLauncher`: a waited `subprocess.run`, never `start_new_session`
    and never `_spawn_detached`, so `eval run`'s sequencing -- one child at a time,
    `RunLimits` being per Corrida (ADR-0004, fifth addendum) -- is a property of the
    code and not of a comment. `AGENTIC_LOG_FILE` is dropped from every child's
    environment: `_spawn_detached` sets it on a detached eval driver's own process, and
    `Configuration` treats it as a per-field override, so without the drop every child
    would log into the eval driver's own log file instead of minting its own."""
    env = {**os.environ, **env_overlay}
    env.pop(ENV_PREFIX + "LOG_FILE", None)
    subprocess.run(argv, env=env, check=False)


async def _eval_run(
    cfg: Configuration,
    ports: Ports,
    set_path: str | None,
    budget: float,
    yes: bool,
    dry_run: bool,
    run_lock: RunLock | None = None,
    log_file: str | None = None,
    detach: bool = False,
    child_argv: list[str] | None = None,
    spawn: Spawner | None = None,
    launch: EvalLauncher | None = None,
) -> None:
    """`agentic eval run`: drives README.md 9's fixed set, one `agentic run --fresh
    --issue N` per task, waited before the next starts (Tech Lead plan, subtask
    `eval-cli`). Every refusal below fires before the first child is launched: the
    declaration, the product's own name, a set task's still-open pull request, a set
    task's own run lock, and the eval lock itself -- keyed on the fixed string `"eval"`,
    one driver per product repo. `--dry-run` never acquires anything, the same shape
    `resume --dry-run` already has: it reports and returns."""
    lock = run_lock if run_lock is not None else _lock_for(cfg)
    declaration, _text, source = _read_eval_set(cfg, set_path)
    estimate = len(declaration.issues) * evaluation.PER_TASK_ESTIMATE_USD
    print(
        f"agentic: {source}: {len(declaration.issues)} task(s), estimated "
        f"${estimate:.2f} at ${evaluation.PER_TASK_ESTIMATE_USD:.2f}/task "
        f"(smoke: issue {declaration.smoke})"
    )
    if dry_run:
        for issue in declaration.issues:
            task_id = ports.inbox.task_id(issue)
            document = ports.documents.load(task_id)
            status = document.get("status", "open") if document else "no document yet"
            print(f"  {task_id} (issue {issue}): {status}")
            _print_lock(lock, task_id)
        return
    if not yes:
        raise CannotStart(
            f"estimated spend ${estimate:.2f} for {len(declaration.issues)} task(s): "
            "pass --yes to proceed"
        )
    if detach:
        _detach(lock, "eval", child_argv or [], log_file or "", spawn)
        return
    try:
        ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    for issue in declaration.issues:
        task_id = ports.inbox.task_id(issue)
        _refuse_open_pull_request(ports, task_id)
        _refuse_if_locked(lock, task_id)
    with _locked(lock, "eval"):
        spent = 0.0
        for issue in declaration.issues:
            task_id = ports.inbox.task_id(issue)
            if spent + evaluation.PER_TASK_ESTIMATE_USD > budget:
                print(
                    f"agentic: ${budget:.2f} budget would be exceeded before {task_id} "
                    f"(issue {issue}); stopping with {spent:.2f} spent"
                )
                break
            before = ports.documents.load(task_id)
            argv = [sys.argv[0], "run", "--fresh", "--issue", str(issue)]
            overlay = {ENV_PREFIX + "PRODUCT_DIR": str(cfg.product_dir)}
            (launch or _run_eval_child)(argv, overlay)
            after = ports.documents.load(task_id)
            # A task whose document did not move charges the full estimate: a child
            # that dies before writing anything cannot loop for free (Tech Lead plan).
            moved = after is not None and after != before
            cost = float(after.get("cost_usd", 0.0)) if moved else evaluation.PER_TASK_ESTIMATE_USD
            spent += cost
            print(
                f"agentic: {task_id} (issue {issue}) done, ${cost:.2f} (${spent:.2f} spent so far)"
            )


async def _eval_score(
    cfg: Configuration, ports: Ports, set_path: str | None, out_path: str
) -> None:
    """`agentic eval score`: one JSONL line per evaluation-set task, each entirely
    `evaluation.evaluation_record`'s own read of that task's document -- no `gh`, no
    Langfuse, no metric emission, no write other than `out_path` (Tech Lead plan,
    subtask `eval-cli`). Refuses outright when `out_path` already exists, before the
    declaration is even read: a new file per invocation, merge and history left to
    git, so no run can eat a baseline. Blockable by nothing except the documents it
    reads.

    Each line also carries `declaration_source` (which of the product's own
    `.agentic/eval-set.yaml` or `--set` produced this run) and `baseline_id`
    (`evaluation.baseline_id`: that task's own `system_version` plus a content hash of
    the declaration's text, mixing in the document's own `experiment` snapshot when the
    task ran an arm, issue #28) -- both identity, not a figure `evaluation_record`
    itself would have to read a second source for, so they are added onto its own dict
    rather than duplicated inside `core.measurement.domain.evaluation`, whose field
    list the plan already enumerates in full."""
    destination = Path(out_path)
    if destination.exists():
        raise CannotStart(f"{destination}: already exists; write a new file, merge with git")
    declaration, declaration_text, source = _read_eval_set(cfg, set_path)
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    lines: list[str] = []
    fingerprints: dict[str, list[str]] = {}
    grouped: dict[str, list[evaluation.TaskDocument]] = {}
    pending: list[int] = []
    for issue in declaration.issues:
        task_id = ports.inbox.task_id(issue)
        document = ports.documents.load(task_id)
        if document is None:
            print(f"agentic: {task_id} (issue {issue}) has no document yet; skipped")
            continue
        record = evaluation.evaluation_record(product, issue, document)
        version = document.get("system_version") or UNKNOWN_SYSTEM_VERSION
        line: dict[str, object] = {
            **record,
            "declaration_source": source,
            "baseline_id": evaluation.baseline_id(
                version, declaration_text, document.get("experiment", {})
            ),
        }
        lines.append(json.dumps(line, sort_keys=True))
        fingerprints.setdefault(record["prompts_fingerprint"], []).append(task_id)
        grouped.setdefault(record["prompts_fingerprint"], []).append(document)
        if record["score"] is None:
            pending.append(issue)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"agentic: {source}: wrote {len(lines)} line(s) to {destination}")
    if len(fingerprints) > 1:
        print(
            "agentic: prompts fingerprint diverges across "
            f"{len(fingerprints)} groups (a task that stopped early ran fewer prompts):"
        )
        for fingerprint, task_ids in fingerprints.items():
            print(f"  {fingerprint}: {', '.join(sorted(task_ids))}")
        playbook_roles, template_roles = evaluation.fingerprint_divergence(list(grouped.values()))
        if playbook_roles:
            print(f"  playbook differs for: {', '.join(playbook_roles)}")
        if template_roles:
            print(f"  template differs for: {', '.join(template_roles)}")
    for issue in pending:
        print(f"agentic close --issue {issue}")


def _bucket_documents(
    product_dir: Path, repo: Path
) -> tuple[dict[str, list[tuple[str, scorecard.TaskFigures]]], int]:
    """Every task document under `.agentic/tasks/`: this function owns only the glob
    and the parse (Tech Lead review, subtask `scorecard`), handing what it read to
    `scorecard.bucket_by_baseline` for the actual grouping -- a pure function, testable
    at a declared seam (`tests/core/measurement/domain/test_scorecard.py`) without a
    filesystem or git. No port: `cli.py` is the composition root and already reads
    `.agentic/` directly, `_read_eval_set`'s own precedent for `.agentic/eval-set.yaml`.

    `document.parse` rejects a document three ways -- malformed frontmatter YAML
    (`yaml.YAMLError`, from its own unguarded `yaml.safe_load`), a missing frontmatter
    block, or a file this process cannot read (`ValueError`/`OSError`) -- all three
    caught here into the single `None` sentinel `bucket_by_baseline` counts and
    excludes, never raised out of a read-only comparison."""
    tasks_dir = product_dir / task_document.TASKS_SUBDIR
    paths = sorted(tasks_dir.glob("*.md")) if tasks_dir.is_dir() else []
    parsed: list[scorecard.ParsedDocument] = []
    for path in paths:
        try:
            raw = task_document.parse(path.read_text(encoding="utf-8"), path)
        except (ValueError, OSError, yaml.YAMLError):
            parsed.append(None)
            continue
        parsed.append((raw.get("id", path.stem), cast(scorecard.ScorecardDocument, raw)))
    return scorecard.bucket_by_baseline(parsed, lambda sha: telemetry.resolve_baseline(sha, repo))


def _fmt_figure(figure: scorecard.Figure) -> str:
    return "n/a" if figure.value is None else f"{figure.value:.4f}"


def _print_scorecard(product: str, baseline: str, sc: scorecard.Scorecard) -> None:
    """Every figure names the product it was computed over, the denominator it was
    computed over, and the number of documents excluded from that denominator (Tech
    Lead plan, standard 7): no printed figure sums documents from more than one product
    directory, since `sc` is already this one invocation's own bucket of this one
    product's own documents."""
    print(f"-- {product} / {baseline} --")
    print(
        f"autonomy:          {_fmt_figure(sc.autonomy)} "
        f"({sc.autonomy.denominator} judged, {sc.autonomy.excluded} excluded)"
    )
    print(
        f"own failures/task: {_fmt_figure(sc.own_failures_per_task)} "
        f"({sc.own_failures_per_task.denominator} terminated, "
        f"{sc.own_failures_per_task.excluded} excluded)"
    )
    print(
        "  caveat: `sdk` counts as ours on both sides; a run before ADR-0029 recorded "
        "its 429s as `sdk` and is never reclassified -- a delta here is a quality delta "
        "only if both sides show the post-ADR-0029 vocabulary"
    )
    print(f"  by reason: {sc.failures_by_reason}")
    print(
        f"cost/delivery:     {_fmt_figure(sc.cost_per_delivery)} "
        f"({sc.cost_per_delivery.denominator} judged, {sc.cost_per_delivery.excluded} excluded)"
    )
    print(
        f"rework/task:       {_fmt_figure(sc.rework_per_task)} "
        f"({sc.rework_per_task.denominator} terminated, {sc.rework_per_task.excluded} excluded)"
    )
    print(f"  time to merge known for {sc.time_to_merge_known} of {sc.judged} judged deliveries")


async def _eval_compare(
    cfg: Configuration, ports: Ports, frm: str, to: str, system_repo: str | None
) -> None:
    """`agentic eval compare <from> <to>`: ADR-0028's scorecard for two baselines, from
    task documents and local git alone (Tech Lead plan, subtask `scorecard`) -- no
    lock, no `gh`, no network. One product directory per invocation, the one `main()`
    already resolved into `cfg`/`ports`: a second product is another invocation, never
    a second `Configuration`/`Ports` built here."""
    try:
        product = ports.stack.product()
    except (ValueError, OSError) as exc:
        raise CannotStart(str(exc)) from exc
    repo = Path(system_repo) if system_repo else telemetry.REPO
    buckets, unreadable = _bucket_documents(cfg.product_dir, repo)
    available = sorted(buckets)
    for name in (frm, to):
        if name not in buckets:
            counts = ", ".join(f"{b} ({len(buckets[b])})" for b in available)
            raise CannotStart(f"{name}: unknown baseline; available: {counts or 'none'}")
    if unreadable:
        print(f"agentic: {unreadable} document(s) could not be read; excluded from every bucket")
    if unknown := buckets.get("unknown"):
        print(
            f"agentic: {len(unknown)} document(s) whose `system_commit` this repository "
            "cannot resolve; re-pin stage0 or pass --system-repo"
        )

    frm_sc = scorecard.build([figures for _, figures in buckets[frm]])
    to_sc = scorecard.build([figures for _, figures in buckets[to]])
    _print_scorecard(product, frm, frm_sc)
    _print_scorecard(product, to, to_sc)
    v = scorecard.verdict(frm_sc, frm, to_sc, to)
    print(f"verdict: {v.outcome} ({v.reason})")

    # The eval-set declaration is read on a warning path (ADR-0025's `no_brief`/
    # `brief_refusal` asymmetry): absent or malformed says so plainly and the
    # comparison already printed above stands, never a `CannotStart` that would die
    # before a figure is printed (this repo declares no `.agentic/eval-set.yaml`).
    try:
        declaration, _text, _source = _read_eval_set(cfg, None)
    except CannotStart as exc:
        print(f"agentic: eval set: {exc}")
        return
    for name, tasks in ((frm, buckets[frm]), (to, buckets[to])):
        present = {task_id for task_id, _ in tasks}
        missing = [
            issue for issue in declaration.issues if ports.inbox.task_id(issue) not in present
        ]
        if missing:
            print(
                f"agentic: {name}: missing {len(missing)} of {len(declaration.issues)} "
                f"eval-set issue(s): {', '.join(str(i) for i in missing)}"
            )


async def _invoke(
    app,
    graph_input,
    config: RunnableConfig,
    task: TaskInput,
    ports: Ports,
    cfg: Configuration,
    kind: InvocationKind = "run",
    decision: str | None = None,
) -> TaskState:
    # The state as this invocation entered: `_run`'s fresh `TaskInput` (no prior cost),
    # or `_resume`'s paused state. `finish_invocation` diffs it against the state the
    # invocation leaves with, so a resume reports only what it spent (README.md, 14).
    entering = cast(TaskState, task)
    # The live arm of this invocation's own `Configuration` (ADR-0030): `cli.py` is the
    # composition root, so it calls `metrics.arm_label` directly, the same function
    # `composition._arm_for` closes over for the graph's own per-node role spans.
    arm = delivery_metrics.arm_label(
        {"review_mode": cfg.review_mode, "skip_ephemeral": str(cfg.skip_ephemeral)}
    )
    try:
        with telemetry.task_span(task["id"], task["title"], product_name(ports), arm=arm) as run:
            try:
                final = await app.ainvoke(graph_input, config=config)
            except Exception:
                # The run broke outside any use case; the task span still closes with a
                # status, so the trace carries it (`crashed` is telemetry-only, never state).
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
