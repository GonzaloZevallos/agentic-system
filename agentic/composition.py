"""Composition root: the one place that knows every adapter (ADR-0007).

`default_ports` builds the real ports for a configuration. The graph calls it once
per node, because `product_dir` can arrive with the run's `configurable` and not at
import time; the CLI calls it once. `graph` is the wired, uncompiled graph that
`langgraph.json` exports for `langgraph dev`; the CLI compiles it.
"""

from __future__ import annotations

from .adapters.claude_sdk import ClaudeSdkRunner
from .adapters.filesystem import (
    FilesystemFlags,
    FilesystemKnowledge,
    FilesystemPrompts,
    FilesystemTaskDocuments,
)
from .adapters.github import GhInbox
from .adapters.langgraph.graph import build_graph
from .application.ports import Ports
from .domain.configuration import Configuration


def default_ports(cfg: Configuration) -> Ports:
    return Ports(
        runner=ClaudeSdkRunner(verbose=cfg.verbose),
        documents=FilesystemTaskDocuments(cfg.product_dir),
        knowledge=FilesystemKnowledge(cfg.product_dir),
        prompts=FilesystemPrompts(cfg.product_dir),
        flags=FilesystemFlags(cfg.product_dir),
        inbox=GhInbox(cfg.product_dir),
    )


graph = build_graph(default_ports)
