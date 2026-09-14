"""In-memory ports. The use cases run against these; no graph, no SDK, no disk."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentic.core.knowledge.domain import knowledge
from agentic.core.process.application.ports import Ports, RawResult
from agentic.core.process.domain.roles import PROMPTS_DIR, Role
from agentic.core.process.domain.task import TaskInput, TaskState

VOCAB = {"touches_ui": "UI", "touches_copy": "copy"}


class ScriptedRunner:
    """Answers `run_once` from per-role queues and records every call it saw.
    A queued exception is raised instead of returned."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: list[dict] = []

    async def run_once(self, role: Role, prompt: str, **kwargs) -> RawResult:
        self.calls.append({"slug": role.slug, "prompt": prompt, **kwargs})
        item = self.queues[role.slug].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def slugs(self) -> list[str]:
        return [c["slug"] for c in self.calls]


class MemoryDocuments:
    def __init__(self):
        self.saved: list[TaskState] = []

    def save(self, state: TaskState) -> Path:
        self.saved.append(state)
        return self.path_for(state["id"])

    def path_for(self, task_id: str) -> Path:
        return Path(f"/memory/.agentic/tasks/{task_id}.md")

    @property
    def last(self) -> TaskState:
        return self.saved[-1]


class MemoryKnowledge:
    """Keeps what a distillation would write. The checks are the real ones: the
    process tests care that a bad approval fails, not how the file is named."""

    def __init__(self, text: str = ""):
        self.text = text
        self.definitions: dict[str, str] = {}
        self.terms: dict[str, dict[str, str]] = {}

    def read(self) -> str:
        return self.text

    def approval_check(self, task_type: str) -> Callable[[dict], None]:
        return knowledge.approval_check(task_type)

    def distill(self, verdict: dict) -> list[Path]:
        definitions, terms = knowledge.distillation(verdict)
        self.definitions.update(definitions)
        self.terms.update(terms)
        return [Path(f"/memory/docs/product/{slug}.md") for slug in definitions]


class MemoryPrompts:
    """The system's own prompt files, with optional overrides by slug."""

    def __init__(self, **overrides: str):
        self.overrides = overrides

    def raw(self, role: Role) -> tuple[str, Path]:
        if role.slug in self.overrides:
            return self.overrides[role.slug], Path(f"/memory/{role.slug}.md")
        path = PROMPTS_DIR / f"{role.slug}.md"
        return path.read_text(), path


class MemoryFlags:
    def __init__(self, vocabulary: dict[str, str] | None = None):
        self.vocabulary = VOCAB if vocabulary is None else vocabulary

    def load(self) -> dict[str, str]:
        return self.vocabulary


class MemoryInbox:
    def __init__(self, **issues: TaskInput):
        self.issues = issues

    def task(self, number: int) -> TaskInput:
        return self.issues[str(number)]


def memory_ports(runner: ScriptedRunner | None = None, **overrides) -> Ports:
    fields = {
        "runner": runner or ScriptedRunner(),
        "documents": MemoryDocuments(),
        "knowledge": MemoryKnowledge(),
        "prompts": MemoryPrompts(),
        "flags": MemoryFlags(),
        "inbox": MemoryInbox(),
    }
    return Ports(**{**fields, **overrides})


# Recorded role outputs, as the harness would hand them back.


def classified(type_="feature", confidence="high", cost=0.01) -> RawResult:
    return RawResult(
        {"markdown": "Clear.", "type": type_, "confidence": confidence, "reason": "r"}, cost
    )


def defined(n=1, cost=0.1) -> RawResult:
    return RawResult(
        {
            "markdown": f"### Behaviour\n\nv{n}\n\n### For the next role\n\nDefinition v{n}.",
            "flags": {"touches_ui": False, "touches_copy": True},
        },
        cost,
    )


def verdict(kind, reason="r", definitions=None, glossary=None, cost=0.2) -> RawResult:
    if definitions is None:
        definitions = (
            [{"capability": "reports", "content": "# Reports"}] if kind == "approved" else []
        )
    return RawResult(
        {
            "markdown": f"### Verdict\n\n{kind}\n\n### For the next role\n\n{reason}",
            "verdict": kind,
            "reason": reason,
            "flags": {"touches_ui": True, "touches_copy": False},
            "definitions": definitions,
            "glossary": glossary or [],
        },
        cost,
    )
