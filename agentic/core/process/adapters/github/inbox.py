"""`Inbox` over `gh issue view` in the product checkout."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ...domain.task import TaskInput
from .tracker import issue_number


class GhInbox:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

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
