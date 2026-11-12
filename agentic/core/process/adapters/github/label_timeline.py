"""When a label was last added to an issue, read from the issue's timeline (GH-599).

`GhLabelTimeline` implements the narrow `LabelTimeline` port, apart from `GhTracker`: the close
and the system merge read it to record a task's lead time. It never raises -- a failed read
is `None`, which the caller turns into `unreadable`, never into a failed close or merge.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ...application.ports import TrackerCallFailed
from .run import Run, bounded_run, print_line, run_gh

_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      timelineItems(itemTypes: [LABELED_EVENT], last: 100) {
        nodes { ... on LabeledEvent { createdAt label { name } } }
      }
    }
  }
}
"""


def last_addition(nodes: Iterable[Any], label: str) -> str | None:
    """The latest `createdAt` among the events that added `label`; a node that is not whole
    is skipped. ISO instants in one zone sort as text."""
    found: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        named = node.get("label")
        created = node.get("createdAt")
        if isinstance(named, dict) and named.get("name") == label and isinstance(created, str):
            found.append(created)
    return max(found) if found else None


class GhLabelTimeline:
    def __init__(
        self,
        product_dir: Path,
        run: Run = bounded_run,
        log: Callable[[str], None] = print_line,
    ):
        self.product_dir = product_dir
        self._run = run
        self._log = log

    def label_added_at(self, issue: int, label: str) -> str | None:
        args = (
            "api",
            "graphql",
            "-f",
            f"query={_QUERY}",
            "-F",
            "owner={owner}",
            "-F",
            "repo={repo}",
            "-F",
            f"number={issue}",
        )
        try:
            raw = run_gh(
                self._run,
                args,
                cwd=self.product_dir,
                log=self._log,
                not_installed="gh is not installed",
                failed=lambda stderr: "gh api graphql failed",
            )
            nodes = json.loads(raw)["data"]["repository"]["issue"]["timelineItems"]["nodes"]
            return last_addition(nodes, label)
        except (
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
            TrackerCallFailed,
            KeyError,
            TypeError,
        ):
            return None
