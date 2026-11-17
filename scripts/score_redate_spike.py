"""The GH-819 spike: does re-posting a score id with a timestamp on another day leave two rows?

Run by a person with the local Langfuse keys, never in CI and never against a product:
`uv run python scripts/score_redate_spike.py`. It writes only to the probe environment
`agentic-gate-probe`, on a throwaway id, and prints every step so the output can be pasted
whole. Reads `AGENTIC_LANGFUSE_HOST`, `_PUBLIC_KEY`, `_SECRET_KEY` and the ClickHouse trio.
Step 1 posts the score, step 2 posts the same id one day later, step 3 counts rows with
`final` (a re-post keeps the first timestamp, GH-819). It then runs the delete, poll-until-404
(60 s at most), repost and count sequence and prints the timestamp the row ends with, so the
output shows whether retire then repost re-dates it."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

ENVIRONMENT = "agentic-gate-probe"
POLL_LIMIT_S = 60.0


def _env(name: str) -> str:
    value = os.environ.get(f"AGENTIC_LANGFUSE_{name}", "")
    if not value:
        raise SystemExit(f"AGENTIC_LANGFUSE_{name} is not set; nothing was posted")
    return value


def _call(method: str, path: str, body: bytes | None = None) -> int:
    token = base64.b64encode(f"{_env('PUBLIC_KEY')}:{_env('SECRET_KEY')}".encode()).decode()
    request = urllib.request.Request(
        _env("HOST").rstrip("/") + path,
        data=body,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)


def _post(score_id: str, trace_id: str, moment: datetime) -> int:
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    event = {
        "id": uuid.uuid4().hex,
        "type": "score-create",
        "timestamp": stamp,
        "body": {
            "id": score_id,
            "traceId": trace_id,
            "name": "cycle_h",
            "value": 1,
            "dataType": "NUMERIC",
            "environment": ENVIRONMENT,
            "timestamp": stamp,
        },
    }
    return _call("POST", "/api/public/ingestion", json.dumps({"batch": [event]}).encode())


def _count(score_id: str) -> int:
    params = urllib.parse.urlencode({"param_id": score_id})
    sql = "select count() from scores final where id = {id:String} format JSON"
    request = urllib.request.Request(
        f"{_env('CLICKHOUSE_URL').rstrip('/')}/?{params}",
        data=sql.encode(),
        method="POST",
        headers={
            "X-ClickHouse-User": _env("CLICKHOUSE_USER"),
            "X-ClickHouse-Key": _env("CLICKHOUSE_PASSWORD"),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(json.load(response)["data"][0]["count()"])


def _timestamp(score_id: str) -> str:
    params = urllib.parse.urlencode({"param_id": score_id})
    sql = "select toString(timestamp) as t from scores final where id = {id:String} format JSON"
    request = urllib.request.Request(
        f"{_env('CLICKHOUSE_URL').rstrip('/')}/?{params}",
        data=sql.encode(),
        method="POST",
        headers={
            "X-ClickHouse-User": _env("CLICKHOUSE_USER"),
            "X-ClickHouse-Key": _env("CLICKHOUSE_PASSWORD"),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        rows = json.load(response)["data"]
    return str(rows[0]["t"]) if rows else "(no row)"


def _settle() -> None:
    time.sleep(8)  # ingestion is asynchronous: wait for the flush before counting


def main() -> None:
    score_id, trace_id = uuid.uuid4().hex, uuid.uuid4().hex
    first = datetime.now(UTC) - timedelta(days=3)
    print(f"1. post {score_id} at {first.isoformat()}: HTTP {_post(score_id, trace_id, first)}")
    _settle()
    second = first + timedelta(days=1)
    print(f"2. post the same id at {second.isoformat()}: HTTP {_post(score_id, trace_id, second)}")
    _settle()
    rows = _count(score_id)
    print(f"3. select count() from scores final where id = {score_id!r}: {rows}")
    print(f"   timestamp kept by the first row: {_timestamp(score_id)}")
    path = f"/api/public/scores/{score_id}"
    print(f"4. DELETE {path}: HTTP {_call('DELETE', path)}")
    deadline = time.monotonic() + POLL_LIMIT_S
    while (status := _call("GET", path)) != 404:
        print(f"   GET -> {status}")
        if time.monotonic() > deadline:
            raise SystemExit("result: the delete never reached 404 within 60 s; stop and escalate")
        time.sleep(2)
    print("   GET -> 404")
    print(f"5. post again at {second.isoformat()}: HTTP {_post(score_id, trace_id, second)}")
    _settle()
    print(f"6. count after the repost: {_count(score_id)}")
    stamp = _timestamp(score_id)
    print(f"   timestamp after the repost: {stamp}")
    moved = stamp.startswith(second.strftime("%Y-%m-%d"))
    if not moved:
        raise SystemExit("result: NOT re-dated; change the approach and escalate")
    print("result: retire then repost re-dates the score")


if __name__ == "__main__":
    main()
