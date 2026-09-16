"""What the use cases need from the outside, as protocols (ADR-0007).

Each port has one implementation today, over the product directory or the Claude
Agent SDK. They exist to keep the rules of the process out of the disk and the
harness, not because a second product or harness is coming (README.md, 8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..domain.roles import Role
from ..domain.task import TaskInput, TaskState
from .limits import RunLimits


@dataclass(frozen=True)
class RawResult:
    """One attempt of a role, as the harness saw it end. Validation is the use case's."""

    structured_output: Any
    cost_usd: float = 0.0
    turns: int = 0
    # Claude Code session of the attempt: `claude -r <id>` reopens it.
    session_id: str = ""
    # `cost_usd` split by model: the parent's and its sub-agents' (ADR-0013, addendum).
    cost_by_model: dict[str, float] = field(default_factory=dict)


class AttemptFailed(Exception):
    """The attempt broke in the harness: an exception, `max_turns` exhausted, the money
    cap hit, no result. The use case decides whether to retry (README.md, 3), and it
    resumes `session_id` when the attempt left one: what the model read and did is
    still there (ADR-0004, addendum)."""

    def __init__(self, error: str, cost_usd: float = 0.0, session_id: str = ""):
        super().__init__(error)
        self.cost_usd = cost_usd
        self.session_id = session_id


class Presubmit(Protocol):
    """Asked when a role hands back its delivery, before the delivery counts: a reason
    refuses it, and the model gets the reason and delivers again (glossary: Puerta de
    entrega). Blocking: it may run the product's checks, within `timeout_s`."""

    timeout_s: float

    def __call__(self) -> str | None: ...


class RoleRunner(Protocol):
    """Runs one attempt of a role and hands back what the model produced, raw.

    What the role may do (tools, permission rules, skills, whether it executes) is
    read off the `Role` itself. `resume` continues an earlier session of the same
    role: an interview is one session per participant (ADR-0014). `max_budget_usd`
    caps the money of this one run; `None` is no cap. `presubmit`, when given, is asked
    each time the model hands back its delivery, and refuses it with a reason."""

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
        presubmit: Presubmit | None = None,
    ) -> RawResult: ...


class TaskDocuments(Protocol):
    """The single per-task document (ADR-0001), stored in the product repo."""

    def save(self, state: TaskState) -> Path: ...

    def path_for(self, task_id: str) -> Path: ...

    def load(self, task_id: str) -> dict | None:
        """The document as its frontmatter and sections say, raw: what the system records
        beyond the state (plugins, system commit) comes along. None when there is none."""
        ...


class ProductKnowledge(Protocol):
    """The consolidated knowledge of the product (ADR-0006), stored with the human docs.

    What the knowledge must look like (capability names, what an approval carries)
    is `core.knowledge`'s rule, so it arrives through the port: `approval_check`
    is the check PM's output must pass, `consolidate` writes what an approved verdict
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

    def consolidate(self, verdict: dict, root: Path | None = None) -> list[Path]: ...


class Prompts(Protocol):
    """A role's prompt, raw, and where it came from: overlay or default (ADR-0005)."""

    def raw(self, role: Role) -> tuple[str, Path]: ...


class FlagVocabulary(Protocol):
    def load(self) -> dict[str, str]: ...


class ProductStack(Protocol):
    """How the product declares its code is built and verified (`core.knowledge`), as of
    one of its commits: the task's base, never the branch's."""

    def checks(self, revision: str) -> dict[str, str]:
        """Check name -> command, as the product's commit `revision` declares them."""
        ...


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    # stdout and stderr, interleaved as the command wrote them.
    output: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Commands(Protocol):
    """Runs a product's command in a checkout, as a shell would, for the system itself:
    the presubmit. Never fails for a non-zero exit; that is the result."""

    def run(self, cwd: Path, command: str, timeout_s: float) -> CommandResult: ...


class Inbox(Protocol):
    """Where tasks come from: the product's GitHub issues (README.md, 5)."""

    def task(self, number: int) -> TaskInput: ...

    def task_id(self, number: int) -> str:
        """The id of the task issue `number` becomes, without reading the issue."""
        ...

    def issue_of(self, task_id: str) -> int | None:
        """The issue a task came from; None for a task that did not come from one."""
        ...


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

    def is_clean(self, path: Path) -> bool:
        """No uncommitted change, tracked or untracked, under the checkout at `path`."""
        ...

    def merge(self, path: Path, branch: str) -> str | None:
        """Bring `branch` into the checkout at `path`: a subtask starts on the code of
        the subtasks it depends on, and the fan-in brings each approved one into the
        task branch. Merging again is a no-op. Returns why the merge did not happen, or
        None; a conflict leaves the checkout as it was, for a role to resolve on its
        own. It is a fact about the code, not a failure of the system, so the use case
        decides what to do with it."""
        ...

    def commit_all(self, path: Path, message: str) -> str | None:
        """Commit everything changed under `path`; `None` when there was nothing."""
        ...

    def push(self, branch: str) -> None: ...

    def cleanup(self, task_id: str) -> None:
        """Remove the task's worktrees. The branches stay."""
        ...

    def branch_head(self, branch: str) -> str | None:
        """The short commit `branch` points at; None when the branch does not exist."""
        ...

    def base_of(self, branch: str) -> str | None:
        """The commit `branch` started from, as recorded when the system created it."""
        ...

    def worktrees(self, task_id: str) -> list[Path]:
        """The task's checkouts that are on disk."""
        ...

    def prune(self) -> None:
        """Forget checkouts whose directory is gone, so asking for them again recreates
        them: a resume after the human removed a worktree."""
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

    def unlabel(self, task_id: str, label: str) -> None: ...

    def issue_comments(self, task_id: str) -> list[str]:
        """The bodies of the issue's comments, oldest first, whoever wrote them; none
        when the task has no issue."""
        ...


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
    stack: ProductStack
    commands: Commands
    # Not a port: the run as a process, shared by every branch of a fan-out, with
    # nothing outside it to talk to (`limits.py`). It rides here because `Ports` is
    # what a use case receives.
    limits: RunLimits = field(default_factory=RunLimits)
