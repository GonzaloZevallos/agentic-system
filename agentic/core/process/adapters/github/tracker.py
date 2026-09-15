"""`Tracker` over the `gh` CLI in the product checkout (ADR-0011): the pull request
of a task, and the comments and labels of its issue, which a resume also reads.

A task id `GH-<n>` came from issue n; any other id has no issue, and writing to it
is a no-op. `gh` resolves the repo from the checkout, as the inbox does.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

_ISSUE = re.compile(r"^GH-(\d+)$")

Run = Callable[..., subprocess.CompletedProcess[str]]


def issue_number(task_id: str) -> int | None:
    match = _ISSUE.match(task_id)
    return int(match.group(1)) if match else None


class GhTracker:
    def __init__(self, product_dir: Path, run: Run = subprocess.run):
        self.product_dir = product_dir
        self._run = run

    def open_pull_request(self, task_id: str, branch: str, title: str, body: str) -> str:
        if (number := issue_number(task_id)) is not None:
            body = f"Closes #{number}\n\n{body}"
        # Not a draft: the human merges it as it is (README.md, 9), or does not.
        return self._gh("pr", "create", "--head", branch, "--title", title, "--body", body)

    def comment(self, task_id: str, text: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh("issue", "comment", str(number), "--body", text)

    def label(self, task_id: str, label: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh("issue", "edit", str(number), "--add-label", label)

    def unlabel(self, task_id: str, label: str) -> None:
        if (number := issue_number(task_id)) is not None:
            self._gh("issue", "edit", str(number), "--remove-label", label)

    def issue_comments(self, task_id: str) -> list[str]:
        if (number := issue_number(task_id)) is None:
            return []
        comments = json.loads(self._gh("issue", "view", str(number), "--json", "comments"))
        return [c.get("body") or "" for c in comments.get("comments", [])]

    def _gh(self, *args: str) -> str:
        try:
            result = self._run(
                ["gh", *args], cwd=self.product_dir, capture_output=True, text=True, check=True
            )
        except FileNotFoundError as exc:
            raise ValueError("gh is not installed; delivering to GitHub needs the CLI") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"gh {' '.join(args[:2])} failed: {exc.stderr.strip()}") from exc
        return result.stdout.strip()
