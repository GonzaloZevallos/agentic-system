"""Does Langfuse price a Claude Code call that went through the collector?

Sends one span shaped exactly like `claude_code.llm_request` (the token counts under
Claude Code's own names) to the local collector, then polls Langfuse's ClickHouse until
the observation shows usage and a cost above zero. Run it after recreating the
collector (`docker compose up -d --force-recreate otel-collector` in
`observability/langfuse/`); a collector still on the old config leaves the cost at 0
and the probe says so.

The span claims `service.name: claude-code`, so the collector keeps it out of Grafana.
Standard library only: `uv run observability/collector/probe.py`.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COLLECTOR = "http://localhost:4318/v1/traces"
CLICKHOUSE = "http://localhost:8123/"
ENV_FILE = Path(__file__).resolve().parents[1] / "langfuse" / ".env"
MODEL = "claude-opus-5-5"
TOKENS = {
    "input_tokens": 1000,
    "output_tokens": 100,
    "cache_read_tokens": 5000,
    "cache_creation_tokens": 200,
}
# Four distinct values, so the check does not depend on the keys Langfuse files them
# under (`input`, `cache_read_input_tokens`...): the printed usage shows those.
WAIT_SECONDS = 120


def main() -> int:
    trace_id = secrets.token_hex(16)
    try:
        send_span(trace_id)
    except (urllib.error.URLError, OSError) as exc:
        return fail(f"no collector at {COLLECTOR}: {exc}. Is `docker compose up` running?")
    print(f"sent claude_code.llm_request, trace {trace_id}; waiting for Langfuse to ingest it")

    password = clickhouse_password()
    deadline = time.monotonic() + WAIT_SECONDS
    row = None
    while time.monotonic() < deadline:
        try:
            row = observation(trace_id, password)
        except (urllib.error.URLError, OSError) as exc:
            return fail(f"no ClickHouse at {CLICKHOUSE}: {exc}. Is Langfuse up?")
        if row is not None and row["cost"] > 0:
            break
        time.sleep(3)

    if row is None:
        return fail("Langfuse never stored the span; check the collector logs")
    usage, cost = row["usage"], row["cost"]
    print(f"usage_details: {json.dumps(usage)}\ntotal_cost: {cost}")
    missing = set(TOKENS.values()) - set(usage.values())
    if missing:
        return fail(
            f"usage lacks the counts {sorted(missing)}: the collector is not translating them. "
            "Recreate it from observability/langfuse/: "
            "docker compose up -d --force-recreate otel-collector"
        )
    if not cost > 0:
        return fail(f"usage arrived but cost is {cost}: Langfuse has no price for {MODEL}")
    print("ok: Langfuse prices Claude Code calls")
    return 0


def send_span(trace_id: str) -> None:
    now = time.time_ns()
    attributes = [
        {"key": "model", "value": {"stringValue": MODEL}},
        {"key": "gen_ai.system", "value": {"stringValue": "anthropic"}},
        {"key": "gen_ai.request.model", "value": {"stringValue": MODEL}},
        {"key": "probe", "value": {"boolValue": True}},
        *({"key": k, "value": {"intValue": str(v)}} for k, v in TOKENS.items()),
    ]
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "claude-code"}}]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "agentic-probe"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": secrets.token_hex(8),
                                "name": "claude_code.llm_request",
                                "kind": 3,
                                "startTimeUnixNano": str(now - 1_000_000_000),
                                "endTimeUnixNano": str(now),
                                "attributes": attributes,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    request = urllib.request.Request(
        COLLECTOR, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(request, timeout=10).read()


def observation(trace_id: str, password: str) -> dict | None:
    """{usage, cost} of the probe's observation once Langfuse wrote it, else None."""
    sql = (
        "SELECT usage_details, total_cost FROM events_core FINAL "
        f"WHERE trace_id = '{trace_id}' AND name = 'claude_code.llm_request' "
        "ORDER BY event_ts DESC LIMIT 1 FORMAT JSONEachRow"
    )
    query = urllib.parse.urlencode({"user": "clickhouse", "password": password})
    request = urllib.request.Request(f"{CLICKHOUSE}?{query}", data=sql.encode())
    line = urllib.request.urlopen(request, timeout=10).read().decode().strip()
    if not line:
        return None
    raw = json.loads(line)
    usage = raw["usage_details"]
    if isinstance(usage, str):
        usage = json.loads(usage or "{}")
    return {"usage": {k: int(v) for k, v in usage.items()}, "cost": float(raw["total_cost"] or 0)}


def clickhouse_password() -> str:
    """`CLICKHOUSE_PASSWORD` from the Langfuse `.env`, else the compose default."""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("CLICKHOUSE_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"')
    return "clickhouse"


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
