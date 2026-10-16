"""`Inbox` over `gh issue view` in the product checkout."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from ...domain.task import TaskInput
from .run import bounded_run, print_line, run_gh
from .tracker import Run, issue_number

# `gh issue list`'s own page size cap high enough that a product's open issues never
# spill past one call, low enough that a runaway poller cannot ask for the world.
_OPEN_ISSUES_LIMIT = 100


class GhInbox:
    def __init__(
        self,
        product_dir: Path,
        *,
        run: Run = bounded_run,
        log: Callable[[str], None] = print_line,
    ):
        self.product_dir = product_dir
        self._run = run
        self._log = log

    def task(self, number: int) -> TaskInput:
        """Task id, title and description from a GitHub issue of the product repo.

        `gh` resolves the repo from the product checkout, so the system needs no
        GitHub configuration of its own. The id carries the issue number so the
        document and the issue point at each other.
        """
        raw = run_gh(
            self._run,
            ("issue", "view", str(number), "--json", "title,body"),
            cwd=self.product_dir,
            log=self._log,
            not_installed="gh is not installed; --issue needs the GitHub CLI",
            failed=lambda stderr: f"gh could not read issue #{number}: {stderr}",
        )
        issue = json.loads(raw)
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
        raw = run_gh(
            self._run,
            (
                "issue",
                "list",
                "--state",
                "open",
                "--json",
                "url,body",
                "--limit",
                str(_OPEN_ISSUES_LIMIT),
            ),
            cwd=self.product_dir,
            log=self._log,
            not_installed="gh is not installed; a stack review needs the CLI",
            failed=lambda stderr: f"gh issue list failed: {stderr}",
        )
        return [(i["url"], i.get("body") or "") for i in json.loads(raw)]

    def open_issue_bodies(self) -> list[tuple[str, str]]:
        """The landing's read of the same list as `open_issues` (GH-239)."""
        return self.open_issues()
