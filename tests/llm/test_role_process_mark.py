"""Gate (GH-282, ADR-0052): does the mark reach a process a role starts in the background
through the real CLI's Bash tool? Excluded from CI: `uv run pytest -m llm`.

Everything the reaper does rests on one fact the pure code cannot prove: the environment
`options.env` hands the CLI is what a `setsid nohup` child of its Bash tool inherits. This
runs a toy role, asks it for exactly one Bash command that leaves a `sleep` behind, takes
the pid from the Bash tool RESULT (never the model's prose) and reads `/proc` for the
marked processes, then reaps them with the real `ShellReaper` once the stream is closed.

The CLI's Bash tool runs a background job as a process-group leader, so `setsid` forks and
`$!` is the short-lived wrapper, not the `sleep` (first run: the echoed pid 1388766 was
gone, the marked `sleep 300` was 1388767). The leftover is therefore found by its mark and
its command, which is also how the reaper finds it; the echoed pid only proves the call
ran.

The toy role is built here, not in `agentic/`: no prompt file, no entry in `ROLES`.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from agentic.core.process.adapters.claude_sdk.runner import options_for
from agentic.core.process.adapters.shell.reaper import ShellReaper
from agentic.core.process.domain import reaping, schemas
from agentic.core.process.domain.roles import Role
from agentic.shared.configuration import Configuration

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

COMMAND = "setsid nohup sleep 300 >/dev/null 2>&1 & echo $!"
TASK = "GH-GATE"
PRODUCT = reaping.product_key("/gate/product")

TOY_ROLE = Role(
    name="Toy Developer",
    slug="toy_process_mark_gate",
    kind="actor",
    schema=schemas.Delivery,
    executes=True,
    effort="low",
    max_turns=3,
    tools=("Bash",),
    allowed=("Bash",),
)

PROMPT = (
    "Run exactly one Bash command, verbatim, and nothing else:\n\n"
    f"{COMMAND}\n\n"
    "Then report the number it printed and stop."
)


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _marked_now() -> dict[int, str]:
    """Every live process of the gate's scope: pid -> command. The CLI itself is one."""
    scope = reaping.ReapScope(PRODUCT, TASK, None, None)
    marked: dict[int, str] = {}
    for name in os.listdir("/proc"):
        try:
            if name.isdigit() and reaping.matches(
                Path(f"/proc/{name}/environ").read_bytes(), scope
            ):
                marked[int(name)] = reaping.truncate_command(
                    Path(f"/proc/{name}/cmdline").read_bytes()
                )
        except OSError:
            continue
    return marked


async def test_the_mark_reaches_a_background_process_of_the_bash_tool(tmp_path: Path):
    from claude_agent_sdk import _cli_version

    value = reaping.mark(PRODUCT, TASK, os.getpid(), "gate")
    options = options_for(
        TOY_ROLE,
        system_prompt="You run one shell command for a harness probe and report its output.",
        model=Configuration.model_fields["actor_model"].default,
        json_schema=schemas.json_schema(schemas.Delivery, {}),
        cwd=tmp_path,
        resume=None,
        max_budget_usd=1.0,
        traceparent=None,
        partial=False,
        env={reaping.MARK_NAME: value},
        resource_attributes=None,
    )
    bash_uses: dict[str, str] = {}
    results: dict[str, str] = {}
    at_result: dict[int, str] = {}
    echoed: list[int] = []
    cost = 0.0
    try:
        async for message in query(prompt=PROMPT, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == "Bash":
                        bash_uses[block.id] = str(block.input.get("command", ""))
            if isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        results[block.tool_use_id] = _text(block.content)
                        found = re.findall(r"\b\d+\b", results[block.tool_use_id])
                        if bash_uses.get(block.tool_use_id, "").strip() == COMMAND and found:
                            echoed.append(int(found[-1]))
                            # Read-only: the CLI carries the mark too, so reaping now would
                            # kill it mid-stream.
                            at_result = _marked_now()
            if isinstance(message, ResultMessage):
                cost = message.total_cost_usd or 0.0
    finally:
        # What the runner does once the stream is closed: reap the attempt's leftovers.
        at_close = _marked_now()
        reaped = ShellReaper(PRODUCT).reap_task(TASK)

    print(f"CLI version: {_cli_version.__cli_version__}")
    print(f"SDK version: {importlib.metadata.version('claude-agent-sdk')}")
    print(f"Bash calls: {bash_uses}")
    print(f"Bash results: {results}")
    print(f"pid echoed by the Bash result: {echoed}")
    print(f"marked processes at tool-result time: {at_result}")
    print(f"marked processes when the stream closed: {at_close}")
    print(f"reaped: {reaped}")
    print(f"cost_usd: {cost}")
    assert echoed, "inconclusive: no tool call"
    sleeps = [pid for pid, command in at_result.items() if command == "sleep 300"]
    assert sleeps, "the mark did not reach the background process"
    leftover = [pid for pid in sleeps if pid in at_close]
    assert leftover, "the background process did not outlive the stream: nothing to reap"
    outcomes = {entry["pid"]: entry["outcome"] for entry in reaped}
    assert all(outcomes.get(pid) in {"term", "kill"} for pid in leftover)
    assert "survived" not in outcomes.values()
    assert not any(Path(f"/proc/{pid}/environ").exists() for pid in leftover)
