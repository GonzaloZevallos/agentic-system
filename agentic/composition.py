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
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .core.knowledge.adapters.filesystem import FilesystemFlags, FilesystemKnowledge
from .core.knowledge.adapters.git import GitStack
from .core.measurement.adapters.langfuse.scores import LangfuseScores
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
from .shared.configuration import STATE_FILE, Configuration, state_file_for
from .shared.environment import own_configuration_unset

LANGFUSE_VARIABLES = (
    "AGENTIC_LANGFUSE_HOST",
    "AGENTIC_LANGFUSE_PUBLIC_KEY",
    "AGENTIC_LANGFUSE_SECRET_KEY",
)


def task_scorer() -> LangfuseScores | None:
    """The writer of a finished task's Langfuse scores, or `None` when any of its three
    variables is unset or empty (GH-273, ADR-0037 addendum).

    Read from `os.environ` here and handed to the adapter alone: the key pair is
    deliberately not a `Configuration` field, so it never reaches a role's or a check's
    environment. The `AGENTIC_` prefix is what keeps it out of both
    (`own_configuration_unset`, `without_own_configuration`)."""
    host, public_key, secret_key = (os.environ.get(name) for name in LANGFUSE_VARIABLES)
    if not (host and public_key and secret_key):
        return None
    return LangfuseScores(host, public_key, secret_key)


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
async def checkpointer(cfg: Configuration, task_id: str) -> AsyncIterator[BaseCheckpointSaver]:
    """The async saver: the CLI runs the graph under anyio, and the sync `SqliteSaver`
    does not implement the async reads `ainvoke` makes. The ONLY place that opens a
    connection to the checkpoint store or builds an `AsyncSqliteSaver` (ADR-0003,
    addendum 2026-10-04): a task gets its own file unless it already has rows in the
    legacy shared one, `_route`'s own three cases."""
    GitWorkspace(cfg.product_dir, cfg.git_identity).exclude_system_files()
    path = _route(cfg, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield saver


def _route(cfg: Configuration, task_id: str) -> Path:
    """Which file `task_id` lives in: its own, if it has one already; else the legacy
    shared file, if that already carries its rows; else its own, fresh."""
    own = cfg.product_dir / state_file_for(task_id)
    if own.is_file():
        return own
    if _legacy_has_rows_for(cfg, task_id):
        return cfg.product_dir / STATE_FILE
    return own


def _legacy_has_rows_for(cfg: Configuration, task_id: str) -> bool:
    """Whether the legacy shared store already carries a checkpoint for `task_id`, read
    without ever creating or writing it: a product that never had one must not grow
    `.agentic/state.sqlite` just by being asked. The URI is built with `Path.as_uri()`
    on the resolved path, never an f-string, so a product directory holding `#` or `%`
    still resolves correctly -- `as_uri()` percent-encodes both. `no such table` reads
    as no rows (a legacy file from before any checkpoint was ever written); any other
    `OperationalError` -- a `database is locked` among them -- propagates, since it
    surfaces before any role runs and before anything is spent."""
    legacy = (cfg.product_dir / STATE_FILE).resolve()
    if not legacy.is_file():
        return False
    conn = sqlite3.connect(legacy.as_uri() + "?mode=ro", uri=True)
    try:
        cursor = conn.execute("SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (task_id,))
        return cursor.fetchone() is not None
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise
    finally:
        conn.close()


def _arm_for(cfg: Configuration) -> str:
    """The live arm of a node's own `Configuration` (ADR-0030): the one place besides
    `cli.py` that calls `metrics.arm_label`, so `core.process.adapters.langgraph.graph`
    -- restricted to importing `shared` alone (`tests/test_architecture.py`) -- never
    imports `core.measurement` to get it."""
    return arm_label({"review_mode": cfg.review_mode, "skip_ephemeral": str(cfg.skip_ephemeral)})


graph = build_graph(default_ports, telemetry, arm_for=_arm_for)
