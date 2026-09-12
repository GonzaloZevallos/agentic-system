"""CLI. `agentic run "title" "description"` runs one task through the feature path.

`agentic resume` arrives with the checkpointer (ADR-0003).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

import anyio
from dotenv import load_dotenv

from . import flags
from .configuration import ENV_PREFIX, Configuration, MissingProductDir
from .document import path_for
from .graph import graph
from .roles import PJM, PM, PRODUCT_ANALYST


def main() -> None:
    # `.env` in the working directory, if any. Real env vars win over it.
    load_dotenv()
    parser = argparse.ArgumentParser(prog="agentic")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one task through the feature path")
    run.add_argument("title")
    run.add_argument("description")
    run.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    args = parser.parse_args()

    if args.product_dir:
        # The env var outranks `configurable`, so an explicit flag has to go in as env.
        os.environ[ENV_PREFIX + "PRODUCT_DIR"] = args.product_dir
    try:
        cfg = Configuration.from_runnable_config(None)
        preflight(cfg)
    except (MissingProductDir, ValueError, OSError) as exc:
        parser.exit(2, f"agentic: {exc}\n")
    anyio.run(_run, args.title, args.description, cfg)


def preflight(cfg: Configuration) -> None:
    """Fail before spending money if a product overlay is malformed."""
    vocabulary = flags.load(cfg.product_dir)
    for role in (PJM, PRODUCT_ANALYST, PM):
        role.prompt(
            cfg.product_dir,
            max_returns=cfg.max_returns_per_pair,
            remaining_returns=cfg.max_returns_per_pair,
            flags=flags.describe(vocabulary),
        )


async def _run(title: str, description: str, cfg: Configuration) -> None:
    config = {"configurable": {}}
    task_id = f"T-{uuid.uuid4().hex[:8]}"
    config["configurable"]["thread_id"] = task_id

    final = await graph.compile().ainvoke(
        {"id": task_id, "title": title, "description": description}, config=config
    )
    print(f"\nid:        {task_id}")
    print(f"type:      {final.get('type')}")
    print(f"status:    {final.get('status', 'open')}")
    print(f"roles:     {', '.join(final.get('sections', {}))}")
    print(f"returns:   {final.get('returns', {})}")
    print(f"failures:  {len(final.get('failures', []))}")
    if final.get("escalated"):
        print(f"escalated: {final['escalated']}")
    print(f"cost:      ${final.get('cost_usd', 0):.4f}")
    print(f"doc:       {path_for(task_id, cfg.product_dir)}")
    if final.get("escalated"):
        sys.exit(1)
