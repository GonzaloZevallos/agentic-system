"""Publishes `observability/grafana/dashboard.json` to Grafana Cloud (ADR-0032,
subtask `dashboard-publisher`, GH-123).

The file is a template of import (a `${DS_PROMETHEUS}` uid) and the body of the POST at
once: a template variable named `DS_PROMETHEUS` resolves it, so `POST
/api/dashboards/db` -- which never substitutes anything, unlike the UI importer -- gets
a dashboard whose datasource is already correct. The repo carries no instance state:
`DS_PROMETHEUS`'s `current` in the file is the sentinel `{"selected": false, "text": "",
"value": ""}`, and this script injects the real uid and the real name it reads from the
instance itself.

Two verifications, because a 200 from `POST /api/dashboards/db` is not proof the
dashboard has data (that is exactly the defect this task fixes another way): before
publishing, `GET /api/datasources/uid/$GRAFANA_DS_UID` must be 200, or nothing is
published; after publishing, the stored dashboard must resolve every `datasource.uid`
and `DS_PROMETHEUS`'s own `current.value`, or the script exits non-zero. No path exits
0 without both.

The decision is two pure functions, `body()` and `published_refusal()`, both fixed by
`tests/test_dashboard.py` (the checked scope this task's tests run under); everything
below them is transport that no test in this repo can reach without network. Standard
library only, following `observability/collector/probe.py`'s precedent for operational
Python outside `agentic/` (out of `[tool.mypy] files` by construction, ADR-0021: named
in the pull request, not silenced here).

`uv run scripts/publish_dashboard.py`; reads `GRAFANA_URL`, `GRAFANA_TOKEN` and
`GRAFANA_DS_UID` from the environment (`observability/grafana/README.md` names them,
without a value). The token is never printed, in a cut message or otherwise; URLs are.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

USAGE = "uv run scripts/publish_dashboard.py"
ENV_VARS = ("GRAFANA_URL", "GRAFANA_TOKEN", "GRAFANA_DS_UID")

REPO = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = REPO / "observability" / "grafana" / "dashboard.json"
DASHBOARD_UID = "agentic-system"


def datasource_uids(obj: object) -> list[str]:
    """Every `datasource.uid` reached from `obj` by a plain recursive walk: panel-level,
    target-level and templating-variable-level references alike -- the three shapes
    `tests/test_dashboard.py::_exprs` never walks, which is why the v8 defect shipped
    green. One definition, consumed by `published_refusal` below and by the file guard
    in `tests/test_dashboard.py`, which imports it by path: two walks would be two
    anti-vacuidades that can drift apart, which is the failure this task exists to fix
    in the first place."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            datasource = node.get("datasource")
            if isinstance(datasource, dict) and "uid" in datasource:
                found.append(datasource["uid"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found


def body(dashboard: dict, ds_uid: str, ds_name: str) -> dict:
    """The POST body: `dashboard` without its `id` (Grafana assigns/keeps one; sending
    one back would target a different dashboard than `uid` names), `overwrite: true`,
    and `DS_PROMETHEUS`'s `current` set to what the v9 hotfix proved resolves both
    import paths -- `text` the datasource's visible name, `value` its uid, `selected`
    true. Raises if `dashboard` carries no `DS_PROMETHEUS` variable: publishing that
    body would reproduce the v8 defect with a clean exit code."""
    dashboard = copy.deepcopy(dashboard)
    dashboard.pop("id", None)
    for variable in dashboard.get("templating", {}).get("list", []):
        if variable.get("name") == "DS_PROMETHEUS":
            variable["current"] = {"selected": True, "text": ds_name, "value": ds_uid}
            break
    else:
        raise ValueError("dashboard has no DS_PROMETHEUS template variable")
    return {"dashboard": dashboard, "overwrite": True}


def published_refusal(stored: dict, ds_uid: str) -> str | None:
    """`None` when the stored dashboard is safe to leave published; a reason otherwise.
    Not an object-equality check -- Grafana adds `id`/`version` and normalises, so that
    would always fail -- but exactly the property this task exists to guarantee: every
    `datasource.uid` reached from `stored` equals `ds_uid`, and `DS_PROMETHEUS`'s own
    `current.value` does too.

    Refuses, rather than returning `None`, when it found nothing to check at all: zero
    datasource references, or no `DS_PROMETHEUS` variable in `stored["templating"]`
    (ADR-0023) -- including when `stored` is the whole `{"dashboard": ..., "meta":
    ...}` envelope instead of the dashboard itself, since `templating` then is not a
    top-level key of `stored`. Callers pass `stored["dashboard"]` explicitly so that
    mistake is a loud `KeyError` in the caller, not a silent `None` here."""
    uids = datasource_uids(stored)
    if not uids:
        return "no datasource.uid reference found in the stored dashboard to verify"
    wrong = sorted({uid for uid in uids if uid != ds_uid})
    if wrong:
        return f"stored dashboard has datasource.uid(s) != {ds_uid!r}: {wrong!r}"

    variables = stored.get("templating", {}).get("list", [])
    ds_variables = [v for v in variables if v.get("name") == "DS_PROMETHEUS"]
    if not ds_variables:
        return "stored dashboard has no DS_PROMETHEUS template variable in templating.list"

    current_value = ds_variables[0].get("current", {}).get("value")
    if current_value != ds_uid:
        return f"DS_PROMETHEUS current.value is {current_value!r}, expected {ds_uid!r}"
    return None


def _request(url: str, token: str, data: bytes | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data is not None else "GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {}


def main() -> int:
    missing = [name for name in ENV_VARS if not os.environ.get(name)]
    if missing:
        print(f"missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    grafana_url = os.environ["GRAFANA_URL"].rstrip("/")
    token = os.environ["GRAFANA_TOKEN"]
    ds_uid = os.environ["GRAFANA_DS_UID"]

    ds_url = f"{grafana_url}/api/datasources/uid/{ds_uid}"
    status, datasource = _request(ds_url, token)
    if status != 200:
        print(f"GET {ds_url} -> {status}, not publishing (uid {ds_uid!r})", file=sys.stderr)
        return 1
    ds_name = datasource["name"]

    dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    payload = body(dashboard, ds_uid, ds_name)

    publish_url = f"{grafana_url}/api/dashboards/db"
    status, _ = _request(publish_url, token, json.dumps(payload).encode("utf-8"))
    if status != 200:
        print(f"POST {publish_url} -> {status}, nothing verified", file=sys.stderr)
        return 1

    verify_url = f"{grafana_url}/api/dashboards/uid/{DASHBOARD_UID}"
    status, stored = _request(verify_url, token)
    if status != 200:
        print(f"GET {verify_url} -> {status} after publishing", file=sys.stderr)
        return 1
    refusal = published_refusal(stored["dashboard"], ds_uid)
    if refusal is not None:
        print(f"published but not verified: {refusal}", file=sys.stderr)
        return 1

    print(f"published {publish_url}, verified against {verify_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
