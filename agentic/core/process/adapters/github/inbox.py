"""`Inbox` over `gh issue view` in the product checkout."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from ...domain.task import TaskInput
from .tracker import issue_number

Run = Callable[..., subprocess.CompletedProcess[str]]

# `gh issue list`'s own page size cap high enough that a product's open issues never
# spill past one call, low enough that a runaway poller cannot ask for the world.
_OPEN_ISSUES_LIMIT = 100


class GhInbox:
    def __init__(self, product_dir: Path, *, run: Run = subprocess.run):
        self.product_dir = product_dir
        # Only `open_issues` uses this seam; `task` below keeps its own inline call
        # (ADR-0034: old form stays old form until another task unifies the two).
        self._run = run

    def task(self, number: int) -> TaskInput:
        """Task id, title and description from a GitHub issue of the product repo.

        `gh` resolves the repo from the product checkout, so the system needs no
        GitHub configuration of its own. The id carries the issue number so the
        document and the issue point at each other.
        """
        try:
            result = subprocess.run(
                ["gh", "issue", "view", str(number), "--json", "title,body"],
                cwd=self.product_dir,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise ValueError("gh is not installed; --issue needs the GitHub CLI") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"gh could not read issue #{number}: {exc.stderr.strip()}") from exc
        issue = json.loads(result.stdout)
        title, body = issue["title"].strip(), (issue.get("body") or "").strip()
        if not body:
            raise ValueError(f"issue #{number} has no body; the roles need a description")
        return {"id": self.task_id(number), "title": title, "description": body}

    def task_id(self, number: int) -> str:
        return f"GH-{number}"

    def issue_of(self, task_id: str) -> int | None:
        return issue_number(task_id)

    def open_issues(self) -> list[tuple[str, str]]:
        """Every open issue's `(url, body)` (ADR-0034), never filtered by `--search`:
        the eventually consistent index is the failure mode the local, pure marker
        match in `domain.stack_review.open_proposal` exists to avoid."""
        try:
            result = self._run(
                [
                    "gh",
                    "issue",
                    "list",
                    "--state",
                    "open",
                    "--json",
                    "url,body",
                    "--limit",
                    str(_OPEN_ISSUES_LIMIT),
                ],
                cwd=self.product_dir,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise ValueError("gh is not installed; a stack review needs the CLI") from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"gh issue list failed: {exc.stderr.strip()}") from exc
        return [(i["url"], i.get("body") or "") for i in json.loads(result.stdout)]

    def open_issue_bodies(self) -> list[tuple[str, str]]:
        """The landing's read of the same list as `open_issues` (GH-239)."""
        return self.open_issues()
