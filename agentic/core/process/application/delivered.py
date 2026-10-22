"""The files a task's pull request changed, read for the record (ADR-0053): what the plan's
file estimate is compared with. A read that fails never fails the close or the merge that
asked for it; the task just carries no `actual_files`."""

from __future__ import annotations

from collections.abc import Callable

from ..domain import rules
from .ports import Ports


def files_patch(reference: str, ports: Ports, log: Callable[[str], None]) -> dict[str, int]:
    """`{"actual_files": n}` for the pull request at `reference`, written only through
    `rules.delivered_files`; empty when the tracker cannot answer."""
    try:
        return rules.delivered_files(ports.tracker.changed_files(reference))
    except (ValueError, OSError) as exc:
        log(f"agentic: changed files of {reference} unreadable, actual_files omitted: {exc}")
        return {}
