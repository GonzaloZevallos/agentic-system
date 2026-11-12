"""The line a task's pull request body carries for an approved API break (GH-610).

A product declares `api_break_line` in its stack (the prefix, the CI check and its step); the task
records the reason, in the plan (`api_break`) or in the owner's answer to `escalate_api_break`
(`api_break_decided`). Both must be there for the system to write anything: without either, no
body is read or touched and the pull request is what it always was."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..domain import landing
from .ports import Ports

_PULL_REQUEST_URL = re.compile(r"/pull/(\d+)$")


def pull_request_number(reference: str) -> int:
    """The number of a pull request URL as `Tracker.open_pull_request` answered it; `ValueError`
    for anything else, so a reference that is not one reaches no tracker call."""
    found = _PULL_REQUEST_URL.search(reference.rstrip("/"))
    if found is None:
        raise ValueError(f"not a pull request URL: {reference!r}")
    return int(found.group(1))


def required_line(
    ports: Ports, revision: str, record: Mapping[str, object]
) -> tuple[str, str] | None:
    """`(prefix, reason)` the body of the task's pull request must declare, or `None` when the
    stack at `revision` declares no `api_break_line` or the task `record` (its state or document)
    holds no reason. Raises `ValueError` when the stack's key is malformed."""
    declared = ports.stack.api_break_line(revision)
    if declared is None:
        return None
    entries = record.get("landing")
    reason = landing.recorded_api_break(
        str(record.get("api_break") or ""),
        [e for e in entries if isinstance(e, Mapping)] if isinstance(entries, list) else [],
    )
    return None if reason is None else (declared.prefix, reason)
