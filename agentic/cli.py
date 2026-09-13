"""CLI. `agentic run "title" "description"` runs one task through the feature path;
`agentic run --issue N` takes both from GitHub issue N of the product repo, via `gh`.

`agentic resume` arrives with the checkpointer (ADR-0003).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

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
    run.add_argument("title", nargs="?")
    run.add_argument("description", nargs="?")
    run.add_argument(
        "--issue",
        type=int,
        metavar="N",
        help="take title and description from GitHub issue N of the product repo (needs gh)",
    )
    run.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    args = parser.parse_args()

    if args.issue is None and (args.title is None or args.description is None):
        run.error("give a title and a description, or --issue N")
    if args.issue is not None and args.title is not None:
        run.error("--issue replaces the title and description; give one or the other")

    if args.product_dir:
        # The env var outranks `configurable`, so an explicit flag has to go in as env.
        os.environ[ENV_PREFIX + "PRODUCT_DIR"] = args.product_dir
    try:
        cfg = Configuration.from_runnable_config(None)
        preflight(cfg)
        if args.issue is not None:
            task_id, title, description = issue_task(args.issue, cfg.product_dir)
        else:
            task_id, title, description = f"T-{uuid.uuid4().hex[:8]}", args.title, args.description
    except (MissingProductDir, ValueError, OSError) as exc:
        parser.exit(2, f"agentic: {exc}\n")
    anyio.run(_run, task_id, title, description, cfg)


def preflight(cfg: Configuration) -> None:
    """Fail before spending money if the product dir or one of its overlays is off."""
    if not cfg.agentic_dir.is_dir():
        raise ValueError(f"{cfg.product_dir} is not a product repo: no .agentic/ directory")
    vocabulary = flags.load(cfg.product_dir)
    for role in (PJM, PRODUCT_ANALYST, PM):
        role.prompt(
            cfg.product_dir,
            max_returns=cfg.max_returns_per_pair,
            remaining_returns=cfg.max_returns_per_pair,
            flags=flags.describe(vocabulary),
        )


def issue_task(number: int, product_dir: Path) -> tuple[str, str, str]:
    """Task id, title and description from a GitHub issue of the product repo.

    `gh` resolves the repo from the product checkout, so the system needs no
    GitHub configuration of its own. The id carries the issue number so the
    document and the issue point at each other.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(number), "--json", "title,body"],
            cwd=product_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("gh is not installed; --issue needs the GitHub CLI") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"gh could not read issue #{number}: {exc.stderr.strip()}") from exc
    issue = json.loads(result.stdout)
    title, body = issue["title"].strip(), (issue.get("body") or "").strip()
    if not body:
        raise ValueError(f"issue #{number} has no body; the roles need a description")
    return f"GH-{number}", title, body


async def _run(task_id: str, title: str, description: str, cfg: Configuration) -> None:
    config = {"configurable": {"thread_id": task_id}}

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
