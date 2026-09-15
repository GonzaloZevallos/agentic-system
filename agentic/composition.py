"""Composition root: the one place that knows every module and adapter (ADR-0007,
ADR-0010).

`default_ports` builds the real ports for a configuration. The graph calls it once
per node, because `product_dir` can arrive with the run's `configurable` and not at
import time; the CLI calls it once. `graph` is the wired, uncompiled graph that
`langgraph.json` exports for `langgraph dev`; the CLI compiles it. Tracing comes
from `core.measurement` and is handed to the process adapters here, so
`core.process` never imports it.
"""

from __future__ import annotations

from .core.knowledge.adapters.filesystem import FilesystemFlags, FilesystemKnowledge
from .core.measurement.adapters.otel import telemetry
from .core.process.adapters.claude_sdk import ClaudeSdkRunner
from .core.process.adapters.filesystem import FilesystemPrompts, FilesystemTaskDocuments
from .core.process.adapters.git import GitWorkspace
from .core.process.adapters.github import GhInbox, GhTracker
from .core.process.adapters.langgraph.graph import build_graph
from .core.process.application.ports import Ports
from .shared.configuration import Configuration


def default_ports(cfg: Configuration) -> Ports:
    return Ports(
        runner=ClaudeSdkRunner(verbose=cfg.verbose, telemetry=telemetry, env=cfg.git_identity),
        documents=FilesystemTaskDocuments(cfg.product_dir),
        knowledge=FilesystemKnowledge(cfg.product_dir),
        prompts=FilesystemPrompts(cfg.product_dir),
        flags=FilesystemFlags(cfg.product_dir),
        inbox=GhInbox(cfg.product_dir),
        workspace=GitWorkspace(cfg.product_dir, cfg.git_identity),
        tracker=GhTracker(cfg.product_dir),
    )


graph = build_graph(default_ports, telemetry)
