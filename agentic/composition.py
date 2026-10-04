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

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .core.knowledge.adapters.filesystem import FilesystemFlags, FilesystemKnowledge
from .core.knowledge.adapters.git import GitStack
from .core.measurement.adapters.otel import telemetry
from .core.measurement.domain.metrics import arm_label
from .core.process.adapters.claude_sdk import ClaudeSdkRunner
from .core.process.adapters.filesystem import FilesystemPrompts, FilesystemTaskDocuments
from .core.process.adapters.git import GitWorkspace
from .core.process.adapters.github import GhInbox, GhTracker
from .core.process.adapters.langgraph.graph import build_graph
from .core.process.adapters.shell import ShellCommands, ShellSampleApp
from .core.process.application.ports import Ports
from .core.process.application.preflight import refuse_its_own_checkout
from .shared.configuration import Configuration
from .shared.environment import own_configuration_unset


def default_ports(cfg: Configuration) -> Ports:
    ports = _ports(cfg)
    # Here and not only in `preflight`, which is the CLI's: `langgraph dev`, Studio and
    # the LangGraph API import the wired graph and never see the CLI, and this is the
    # door all of them share. The refusal is the application's; the root only asks
    # (issue #44).
    refuse_its_own_checkout(cfg, ports)
    return ports


def _ports(cfg: Configuration) -> Ports:
    # Append mode, line buffered: up to `max_concurrent_roles` nodes hold their own
    # handle on the same path, and whole lines are what makes that safe to interleave
    # (README.md, 14). `None` with no `log_file` -- the `langgraph dev` door -- opens
    # nothing, byte-for-byte the runner this composed before this task (issue #33).
    stream = open(cfg.log_file, "a", buffering=1) if cfg.log_file is not None else None
    return Ports(
        # The roles get the configuration the run decided, never the one the human's
        # shell carries: the `AGENTIC_*` of this process are unset for them, which only
        # the composition root can do because only it sees the real environment.
        runner=ClaudeSdkRunner(
            verbose=cfg.verbose,
            stream=stream,
            telemetry=telemetry,
            env={**cfg.role_env, **own_configuration_unset(os.environ)},
        ),
        documents=FilesystemTaskDocuments(
            cfg.product_dir,
            system_version=telemetry.system_version,
            system_baseline=telemetry.system_baseline,
        ),
        knowledge=FilesystemKnowledge(cfg.product_dir),
        prompts=FilesystemPrompts(cfg.product_dir),
        flags=FilesystemFlags(cfg.product_dir),
        inbox=GhInbox(cfg.product_dir),
        workspace=GitWorkspace(cfg.product_dir, cfg.git_identity),
        tracker=GhTracker(cfg.product_dir),
        stack=GitStack(cfg.product_dir),
        commands=ShellCommands(),
        sample_app=ShellSampleApp(),
    )


@asynccontextmanager
async def checkpointer(cfg: Configuration) -> AsyncIterator[BaseCheckpointSaver]:
    """The async saver: the CLI runs the graph under anyio, and the sync `SqliteSaver`
    does not implement the async reads `ainvoke` makes."""
    GitWorkspace(cfg.product_dir, cfg.git_identity).exclude_system_files()
    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(cfg.state_file)) as saver:
        yield saver


def _arm_for(cfg: Configuration) -> str:
    """The live arm of a node's own `Configuration` (ADR-0030): the one place besides
    `cli.py` that calls `metrics.arm_label`, so `core.process.adapters.langgraph.graph`
    -- restricted to importing `shared` alone (`tests/test_architecture.py`) -- never
    imports `core.measurement` to get it."""
    return arm_label({"review_mode": cfg.review_mode, "skip_ephemeral": str(cfg.skip_ephemeral)})


graph = build_graph(default_ports, telemetry, arm_for=_arm_for)
