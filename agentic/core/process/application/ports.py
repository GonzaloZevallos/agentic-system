"""What the use cases need from the outside, as protocols (ADR-0007).

Each port has one implementation today, over the product directory or the Claude
Agent SDK. They exist to keep the rules of the process out of the disk and the
harness, not because a second product or harness is coming (README.md, 8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..domain.roles import Role
from ..domain.task import TaskInput, TaskState


@dataclass(frozen=True)
class RawResult:
    """One attempt of a role, as the harness saw it end. Validation is the use case's."""

    structured_output: Any
    cost_usd: float = 0.0
    turns: int = 0
    # Claude Code session of the attempt: `claude -r <id>` reopens it.
    session_id: str = ""


class AttemptFailed(Exception):
    """The attempt broke in the harness: an exception, `max_turns` exhausted, no result.
    The use case decides whether to retry (README.md, 3)."""

    def __init__(self, error: str, cost_usd: float = 0.0):
        super().__init__(error)
        self.cost_usd = cost_usd


class RoleRunner(Protocol):
    """Runs one attempt of a role and hands back what the model produced, raw.

    What the role may do (tools, permission rules, skills, whether it executes) is
    read off the `Role` itself. `resume` continues an earlier session of the same
    role: an interview is one session per participant (ADR-0014). `max_budget_usd`
    caps the money of this one run; `None` is no cap."""

    async def run_once(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        resume: str | None = None,
        max_budget_usd: float | None = None,
    ) -> RawResult: ...


class TaskDocuments(Protocol):
    """The single per-task document (ADR-0001), stored in the product repo."""

    def save(self, state: TaskState) -> Path: ...

    def path_for(self, task_id: str) -> Path: ...


class ProductKnowledge(Protocol):
    """The distilled knowledge of the product (ADR-0006), stored with the human docs.

    What the knowledge must look like (capability names, what an approval carries)
    is `core.knowledge`'s rule, so it arrives through the port: `approval_check`
    is the check PM's output must pass, `distill` writes what an approved verdict
    leaves (ADR-0010)."""

    def read(self, root: Path | None = None) -> str:
        """The knowledge as found under `root`: the task's checkout, where the interview
        may already have edited the glossary (ADR-0014). The product root by default."""
        ...

    def standards(self, root: Path | None = None) -> str:
        """The engineering standards (ADR-0012), as the roles that build read them."""
        ...

    def approval_check(self, task_type: str) -> Callable[[dict], None]: ...

    def clarification_check(self) -> Callable[[dict], None]: ...

    def distill(self, verdict: dict, root: Path | None = None) -> list[Path]: ...


class Prompts(Protocol):
    """A role's prompt, raw, and where it came from: overlay or default (ADR-0005)."""

    def raw(self, role: Role) -> tuple[str, Path]: ...


class FlagVocabulary(Protocol):
    def load(self) -> dict[str, str]: ...


class Inbox(Protocol):
    """Where tasks come from: the product's GitHub issues (README.md, 5)."""

    def task(self, number: int) -> TaskInput: ...


@dataclass(frozen=True)
class Checkout:
    """A branch of the product checked out somewhere the roles can work (ADR-0011)."""

    branch: str
    path: Path
    # Commit the checkout started from: what a review diffs against.
    base: str


class Workspace(Protocol):
    """The product repository as git sees it (ADR-0011): one branch and worktree per
    task, one per subtask, all idempotent so a use case can ask again."""

    def task_checkout(self, task_id: str) -> Checkout: ...

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout: ...

    def head(self, path: Path) -> str: ...

    def merge(self, path: Path, branch: str) -> None:
        """Bring `branch` into the checkout at `path`: a subtask starts on the code of
        the subtasks it depends on. Merging again is a no-op."""
        ...

    def commit_all(self, path: Path, message: str) -> str | None:
        """Commit everything changed under `path`; `None` when there was nothing."""
        ...

    def push(self, branch: str) -> None: ...

    def cleanup(self, task_id: str) -> None:
        """Remove the task's worktrees. The branches stay."""
        ...


class Tracker(Protocol):
    """Where the human is told (ADR-0011): the pull request, the issue's comments and
    labels. A task that did not come from an issue has no issue to write to; the
    adapter ignores those calls."""

    def open_pull_request(self, task_id: str, branch: str, title: str, body: str) -> str:
        """Returns the pull request's URL. Linking it to the task's issue, when there
        is one, is the adapter's: only it knows what a task id says about the tracker."""
        ...

    def comment(self, task_id: str, text: str) -> None: ...

    def label(self, task_id: str, label: str) -> None: ...


@dataclass(frozen=True)
class Ports:
    runner: RoleRunner
    documents: TaskDocuments
    knowledge: ProductKnowledge
    prompts: Prompts
    flags: FlagVocabulary
    inbox: Inbox
    workspace: Workspace
    tracker: Tracker
