"""Composition root: the one place that knows every module and adapter (ADR-0007,
ADR-0010).

`default_ports` builds the real ports for a configuration. The graph calls it once
per node, because `product_dir` can arrive with the run's `configurable` and not at
import time; the CLI calls it once. `graph` is the wired, uncompiled graph that
`langgraph.json` exports for `langgraph dev`; the CLI compiles it with `checkpointer`,
the SQLite file in the product where a paused task waits (ADR-0003). Tracing comes
from `core.measurement` and is handed to the process adapters here, so
`core.process` never imports it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .core.knowledge.adapters.filesystem import FilesystemFlags, FilesystemKnowledge
from .core.knowledge.adapters.git import GitStack
from .core.measurement.adapters.otel import telemetry
from .core.process.adapters.claude_sdk import ClaudeSdkRunner
from .core.process.adapters.filesystem import FilesystemPrompts, FilesystemTaskDocuments
from .core.process.adapters.git import GitWorkspace
from .core.process.adapters.github import GhInbox, GhTracker
from .core.process.adapters.langgraph.graph import build_graph
from .core.process.adapters.shell import ShellCommands
from .core.process.application.ports import Ports
from .shared.configuration import Configuration


def default_ports(cfg: Configuration) -> Ports:
    return Ports(
        runner=ClaudeSdkRunner(verbose=cfg.verbose, telemetry=telemetry, env=cfg.role_env),
        documents=FilesystemTaskDocuments(cfg.product_dir),
        knowledge=FilesystemKnowledge(cfg.product_dir),
        prompts=FilesystemPrompts(cfg.product_dir),
        flags=FilesystemFlags(cfg.product_dir),
        inbox=GhInbox(cfg.product_dir),
        workspace=GitWorkspace(cfg.product_dir, cfg.git_identity),
        tracker=GhTracker(cfg.product_dir),
        stack=GitStack(cfg.product_dir),
        commands=ShellCommands(),
    )


@asynccontextmanager
async def checkpointer(cfg: Configuration) -> AsyncIterator[BaseCheckpointSaver]:
    """The async saver: the CLI runs the graph under anyio, and the sync `SqliteSaver`
    does not implement the async reads `ainvoke` makes."""
    GitWorkspace(cfg.product_dir, cfg.git_identity).exclude_system_files()
    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.state_file)) as saver:
        yield saver


graph = build_graph(default_ports, telemetry)
