"""CLI. `agentic run "title" "description"` runs one task through the feature path;
`agentic run --issue N` takes both from GitHub issue N of the product repo, via `gh`.

A driving adapter: it parses arguments, builds the configuration and the ports, and
invokes the graph. `agentic resume` arrives with the checkpointer (ADR-0003).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

import anyio
from dotenv import load_dotenv

from .composition import default_ports, graph
from .core.measurement.adapters.otel import telemetry
from .core.process.application.ports import Ports
from .core.process.application.preflight import preflight
from .core.process.domain.task import TaskInput
from .shared.configuration import ENV_PREFIX, Configuration, MissingProductDir
from .shared.environment import without_own_toolchain


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
    run.add_argument("--product-dir", help="root of the product repo (AGENTIC_PRODUCT_DIR)")
    run.add_argument(
        "--verbose", action="store_true", help="stream each role's generation to stderr"
    )
    args = parser.parse_args()

    if args.issue is None and (args.title is None or args.description is None):
        run.error("give a title and a description, or --issue N")
    if args.issue is not None and args.title is not None:
        run.error("--issue replaces the title and description; give one or the other")

    # The env var outranks `configurable`, so an explicit flag has to go in as env.
    if args.product_dir:
        os.environ[ENV_PREFIX + "PRODUCT_DIR"] = args.product_dir
    if args.verbose:
        os.environ[ENV_PREFIX + "VERBOSE"] = "1"
    try:
        cfg = Configuration.from_sources({}, os.environ)
        if not cfg.agentic_dir.is_dir():
            raise ValueError(f"{cfg.product_dir} is not a product repo: no .agentic/ directory")
        ports = default_ports(cfg)
        preflight(cfg, ports)
        if args.issue is not None:
            task = ports.inbox.task(args.issue)
        else:
            task = {
                "id": f"T-{uuid.uuid4().hex[:8]}",
                "title": args.title,
                "description": args.description,
            }
    except (MissingProductDir, ValueError, OSError) as exc:
        parser.exit(2, f"agentic: {exc}\n")
    anyio.run(_run, task, cfg, ports)


def _replace_environ(env: dict[str, str]) -> None:
    for key in set(os.environ) - set(env):
        del os.environ[key]
    os.environ.update(env)


async def _run(task: TaskInput, cfg: Configuration, ports: Ports) -> None:
    config = {"configurable": {"thread_id": task["id"]}}

    try:
        with telemetry.task_span(task["id"], task["title"]) as run:
            try:
                final = await graph.compile().ainvoke(task, config=config)
            except Exception:
                # The run broke outside any use case; the task span still closes with a
                # status, so Grafana counts it (`crashed` is telemetry-only, never state).
                run.finish({**task, "status": "crashed"})
                raise
            run.finish(final)
    finally:
        telemetry.flush()
    print(f"\nid:        {task['id']}")
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
    print(f"doc:       {ports.documents.path_for(task['id'])}")
    if final.get("escalated"):
        sys.exit(1)
