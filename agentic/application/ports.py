"""What the use cases need from the outside, as protocols (ADR-0007).

Each port has one implementation today, over the product directory or the Claude
Agent SDK. They exist to keep the rules of the process out of the disk and the
harness, not because a second product or harness is coming (README.md, 8).
"""

from __future__ import annotations

from collections.abc import Sequence
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
    """Runs one attempt of a role and hands back what the model produced, raw."""

    async def run_once(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        tools: Sequence[str] = (),
    ) -> RawResult: ...


class TaskDocuments(Protocol):
    """The single per-task document (ADR-0001), stored in the product repo."""

    def save(self, state: TaskState) -> Path: ...

    def path_for(self, task_id: str) -> Path: ...


class ProductKnowledge(Protocol):
    """The distilled knowledge of the product (ADR-0006), stored with the human docs."""

    def read(self) -> str: ...

    def write_definitions(self, definitions: dict[str, str]) -> list[Path]: ...

    def upsert_terms(self, terms: dict[str, dict[str, str]]) -> Path | None: ...


class Prompts(Protocol):
    """A role's prompt, raw, and where it came from: overlay or default (ADR-0005)."""

    def raw(self, role: Role) -> tuple[str, Path]: ...


class FlagVocabulary(Protocol):
    def load(self) -> dict[str, str]: ...


class Inbox(Protocol):
    """Where tasks come from: the product's GitHub issues (README.md, 5)."""

    def task(self, number: int) -> TaskInput: ...


@dataclass(frozen=True)
class Ports:
    runner: RoleRunner
    documents: TaskDocuments
    knowledge: ProductKnowledge
    prompts: Prompts
    flags: FlagVocabulary
    inbox: Inbox
