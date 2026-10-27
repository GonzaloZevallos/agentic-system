"""The `run_check` tool as an in-process SDK MCP server (ADR-0065, GH-359).

One server, one tool, given only to the Developer and its integrator: the model supplies the
name of a declared check and the system runs it (`application/run_check.py`), so the exit
code, the output and the time it reads are the system's. The call is blocking and can last as
long as a suite: it runs off the event loop that talks to the CLI, inside the attempt's
`HookActivity`, so the stall watchdog sees it as alive (ADR-0057), as it sees the delivery
gate."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import anyio
from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool

from ...application.ports import CheckTool, RunStopped
from ...domain.run_check import DESCRIPTION
from .hooks import HookActivity

SERVER = "agentic"
TOOL = "run_check"
# The CLI waits this long, in milliseconds, for one MCP tool call unless told otherwise.
TIMEOUT_ENV = "MCP_TOOL_TIMEOUT"


def check_server(check: CheckTool, activity: HookActivity | None = None) -> McpSdkServerConfig:
    return create_sdk_mcp_server(SERVER, tools=[run_check_tool(check, activity)])


def run_check_tool(check: CheckTool, activity: HookActivity | None = None) -> SdkMcpTool[Any]:
    @tool(TOOL, DESCRIPTION, {"name": str})
    async def run_check(args: dict[str, Any]) -> dict[str, Any]:
        with activity.running() if activity is not None else nullcontext():
            try:
                text = await anyio.to_thread.run_sync(check, str(args.get("name", "")))
            except RunStopped:
                # The stop is not a tool result (GH-355): wait for the attempt's cancellation.
                await anyio.sleep_forever()
        return {"content": [{"type": "text", "text": text}]}

    return run_check


def tool_timeout_ms(check: CheckTool) -> str:
    """`MCP_TOOL_TIMEOUT` for an attempt that carries `check`: the longest one call can take."""
    return str(int(check.timeout_s * 1000))
