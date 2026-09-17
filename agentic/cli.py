"""CLI. `agentic run "title" "description"` runs one task through the feature path;
`agentic run --issue N` takes both from GitHub issue N of the product repo, via `gh`.
`agentic resume --issue N` resumes that task where an escalation paused it (ADR-0003).

A driving adapter: it parses arguments, builds the configuration and the ports, opens
the checkpointer and invokes the graph. What a resume does is decided in
`application.resume`; the graph calls are here.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
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
from .core.process.adapters.langgraph.graph import ESCALATE
from .core.process.application import resume as resuming
from .core.process.application.ports import Ports
from .core.process.application.preflight import no_setup, preflight, unfixed_stage0
from .core.process.domain.task import TaskInput, TaskState
from .shared.configuration import ENV_PREFIX, Configuration, MissingProductDir
from .shared.environment import without_own_toolchain

Checkpointer = Callable[[Configuration], AbstractAsyncContextManager[BaseCheckpointSaver]]


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
        help="start over a task that is paused or ran before: its checkpoint is dropped, "
        "its document rewritten, its branches reused as they are",
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
    for command in (run, resume):
        command.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
        command.add_argument(
            "--verbose", action="store_true", help="stream each role's generation to stderr"
        )
    args = parser.parse_args()

    if args.command == "run":
        if args.issue is None and (args.title is None or args.description is None):
            run.error("give a title and a description, or --issue N")
        if args.issue is not None and args.title is not None:
            run.error("--issue replaces the title and description; give one or the other")

    # The env var outranks `configurable`, so an explicit flag has to go in as env.
    if args.product_dir:
        os.environ[ENV_PREFIX + "PRODUCT_DIR"] = args.product_dir
    if args.verbose:
        os.environ[ENV_PREFIX + "VERBOSE"] = "1"
    if getattr(args, "task_budget", None) is not None:
        os.environ[ENV_PREFIX + "TASK_BUDGET_USD"] = str(args.task_budget)
    try:
        cfg = Configuration.from_sources({}, os.environ)
        if not cfg.agentic_dir.is_dir():
            raise ValueError(f"{cfg.product_dir} is not a product repo: no .agentic/ directory")
        ports = default_ports(cfg)
        preflight(cfg, ports)
        # Said and not enforced: the run is worth less, it is not wrong.
        for warn in (unfixed_stage0, no_setup):
            if (warning := warn(ports)) is not None:
                print(f"agentic: {warning}", file=sys.stderr)
        if args.command == "resume":
            task_id = ports.inbox.task_id(args.issue)
            options = (args.from_document, args.dry_run, args.yes)
            work: Callable[[], Awaitable[None]] = partial(_resume, task_id, cfg, ports, *options)
        else:
            if args.issue is not None:
                task = ports.inbox.task(args.issue)
            else:
                task = {
                    "id": f"T-{uuid.uuid4().hex[:8]}",
                    "title": args.title,
                    "description": args.description,
                }
            work = partial(_run, task, cfg, ports, args.fresh)
    except (MissingProductDir, ValueError, OSError) as exc:
        parser.exit(2, f"agentic: {exc}\n")
    try:
        anyio.run(work)
    except CannotStart as exc:
        parser.exit(exc.code, f"agentic: {exc}\n")


def _replace_environ(env: dict[str, str]) -> None:
    for key in set(os.environ) - set(env):
        del os.environ[key]
    os.environ.update(env)


async def _run(
    task: TaskInput,
    cfg: Configuration,
    ports: Ports,
    fresh: bool = False,
    checkpointer: Checkpointer | None = None,
) -> None:
    config: RunnableConfig = {"configurable": {"thread_id": task["id"]}}
    async with (checkpointer or composition.checkpointer)(cfg) as saver:
        app = graph.compile(checkpointer=saver)
        if not fresh:
            await _refuse_a_started_task(app, config, task["id"], ports)
        # A thread keeps its channels: a new run over it would add to the old cost and runs.
        await saver.adelete_thread(task["id"])
        final = await _invoke(app, task, config, task, ports)
    _report(final, task["id"], ports)


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
) -> None:
    config: RunnableConfig = {"configurable": {"thread_id": task_id}}
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
            return
        if not yes and input("\nResume? [y/N] ").strip().lower() not in ("y", "yes"):
            raise CannotStart("not resumed", code=1)

        if r.source == "document":
            # Seeded as if `escalate` had just run: the task pauses once more, without
            # telling the human again, and the answer below resumes it like a checkpoint.
            await saver.adelete_thread(task_id)
            await app.aupdate_state(config, r.paused, as_node=ESCALATE)
        crashed = r.source == "checkpoint" and not snapshot.interrupts
        command: Command | None = None if crashed else Command(resume={"decision": r.decision})
        resuming.announce(r, ports)
        final = await _invoke(app, command, config, r.paused, ports)
    _report(final, task_id, ports)


async def _invoke(
    app, graph_input, config: RunnableConfig, task: TaskInput, ports: Ports
) -> TaskState:
    try:
        with telemetry.task_span(task["id"], task["title"]) as run:
            try:
                final = await app.ainvoke(graph_input, config=config)
            except Exception:
                # The run broke outside any use case; the task span still closes with a
                # status, so Grafana counts it (`crashed` is telemetry-only, never state).
                run.finish({**task, "status": "crashed"})
                raise
            run.finish(final)
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
