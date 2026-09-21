"""The declared gate (Tech Lead round 1, Q3): `runner.py:119` already sets
`TRACEPARENT` in the child env, and the 505 historic orphans (GH-70) are data from a
system that already does that — so before any propagation code ships, this spike
answers, against the pinned SDK and CLI, whether `TRACEPARENT` alone gives a
sub-agent's `claude_code.llm_request` a parent at all (1), which span id it parents to
(2), and whether a second env var or nothing changes that (3).

Requires the local Langfuse stack (`docker compose up` in `observability/langfuse/`)
and a Claude Code subscription login. Excluded from CI: `uv run pytest -m llm`.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from agentic.shared.environment import trace_id_of, with_resource_marker

pytestmark = [pytest.mark.llm, pytest.mark.anyio]

# `cli.py` loads the product's own root `.env` before spawning anything (real env
# wins), which is where `.env.example` puts the `OTEL_*` exporter vars that make a
# spawned Claude Code process report to the local collector at all. A bare `pytest`
# process — this worktree carries no `.env` of its own, being untracked — does not see
# them; confirmed live, without these a first run of this spike reached Langfuse zero
# times for either arm, and the collector was not the problem (`probe.py` passed).
# Mirrors `.env.example`, not a hand-written guess.
_OTEL_DEFAULTS = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
    "OTEL_LOG_USER_PROMPTS": "1",
    "OTEL_LOG_ASSISTANT_RESPONSES": "1",
    "OTEL_LOG_TOOL_DETAILS": "1",
    "OTEL_LOG_TOOL_CONTENT": "1",
}

CLICKHOUSE = "http://localhost:8123/"
ENV_FILE = Path(__file__).resolve().parents[2] / "observability" / "langfuse" / ".env"
MODEL = "claude-haiku-4-5-20251001"  # cheapest pinned model: this spike prices turns, not quality
WAIT_SECONDS = 90
PROMPT = (
    "Use the Agent tool exactly once: launch a general-purpose sub-agent with the "
    "prompt 'Reply with exactly the word: done'. Wait for it to finish, then reply "
    "with exactly the word 'finished' and stop."
)


async def test_traceparent_alone_gives_a_sub_agents_call_a_parent(tmp_path):
    with_marker = await _run(tmp_path, marker=True)
    without_marker = await _run(tmp_path, marker=False)

    print("\n--- with TRACEPARENT + our OTEL_RESOURCE_ATTRIBUTES marker ---")
    _report(with_marker)
    print("\n--- with TRACEPARENT only, no resource-attributes marker ---")
    _report(without_marker)

    # Condition 1: does a sub-agent's `claude_code.llm_request` carry any parent at
    # all when `TRACEPARENT` is in the child env?
    parented = [r for r in with_marker if r["parent_span_id"]]
    print(
        f"\ncondition (1) any parent at all: {bool(parented)} ({len(parented)}/{len(with_marker)})"
    )

    # Condition 2: which span id does it parent to?
    parents = sorted({r["parent_span_id"] for r in parented})
    print(f"condition (2) parent span id(s) seen: {parents}")

    # Condition 3: does the resource-attributes marker change the parenting, or is
    # TRACEPARENT alone (today's `runner.py:119`) already sufficient/insufficient
    # either way?
    parented_without = [r for r in without_marker if r["parent_span_id"]]
    print(
        f"condition (3) parenting without the marker: {bool(parented_without)} "
        f"({len(parented_without)}/{len(without_marker)}) -- "
        f"{'same as with the marker' if bool(parented) == bool(parented_without) else 'DIFFERENT'}"
    )

    # This spike's job is to produce the extract above for the Developer's section
    # and the ADR, not to assert a specific outcome — that outcome is exactly what is
    # unknown before it runs (that is the point of a declared gate).
    assert with_marker, "no observation reached Langfuse for the marked run: is the collector up?"


async def _run(tmp_path: Path, *, marker: bool) -> list[dict]:
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    traceparent = f"00-{trace_id}-{span_id}-01"
    assert trace_id_of(traceparent) == trace_id  # our own parser agrees with what we sent

    env = {**os.environ, **{k: v for k, v in _OTEL_DEFAULTS.items() if k not in os.environ}}
    env["TRACEPARENT"] = traceparent
    if marker:
        env["OTEL_RESOURCE_ATTRIBUTES"] = with_resource_marker(
            os.environ.get("OTEL_RESOURCE_ATTRIBUTES"),
            role_slug="gate_spike",
            trace_id=trace_id,
        )
    options = ClaudeAgentOptions(
        cwd=tmp_path / ("marker" if marker else "no_marker"),
        model=MODEL,
        tools=["Agent"],
        allowed_tools=["Agent"],
        permission_mode="bypassPermissions",
        max_turns=4,
        env=env,
    )
    (tmp_path / ("marker" if marker else "no_marker")).mkdir(exist_ok=True)
    async for message in query(prompt=PROMPT, options=options):
        if isinstance(message, ResultMessage):
            break
    return _wait_for_observations(trace_id)


def _report(rows: list[dict]) -> None:
    for row in rows:
        parent = row["parent_span_id"][:8] if row["parent_span_id"] else "-"
        print(
            f"  span={row['span_id'][:8]} parent={parent} "
            f"session={row['session_id']} name={row['name']}"
        )


def _wait_for_observations(trace_id: str) -> list[dict]:
    password = _clickhouse_password()
    deadline = time.monotonic() + WAIT_SECONDS
    rows: list[dict] = []
    while time.monotonic() < deadline:
        rows = _observations(trace_id, password)
        if rows:
            break
        time.sleep(3)
    return rows


def _observations(trace_id: str, password: str) -> list[dict]:
    sql = (
        "SELECT span_id, parent_span_id, session_id, name FROM events_core FINAL "
        f"WHERE trace_id = '{trace_id}' ORDER BY start_time ASC FORMAT JSONEachRow"
    )
    query_string = urllib.parse.urlencode({"user": "clickhouse", "password": password})
    request = urllib.request.Request(f"{CLICKHOUSE}?{query_string}", data=sql.encode())
    try:
        body = urllib.request.urlopen(request, timeout=10).read().decode()
    except (urllib.error.URLError, OSError):
        return []
    return [json.loads(line) for line in body.splitlines() if line]


def _clickhouse_password() -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("CLICKHOUSE_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"')
    return "clickhouse"
