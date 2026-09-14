"""In-memory ports. The use cases run against these; no graph, no SDK, no disk."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentic.core.knowledge.domain import knowledge
from agentic.core.process.application.ports import Checkout, Ports, RawResult
from agentic.core.process.domain import rules
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
        queue = self.queues.get(role.slug)
        if not queue:
            raise AssertionError(f"no scripted result left for {role.slug}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def prompts(self, slug: str) -> list[str]:
        return [c["prompt"] for c in self.calls if c["slug"] == slug]

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

    def __init__(self, text: str = "", standards: str = "## Engineering standards\n\nHex."):
        self.text = text
        self.standards_text = standards
        self.definitions: dict[str, str] = {}
        self.terms: dict[str, dict[str, str]] = {}

    def read(self, root: Path | None = None) -> str:
        self.read_from = root
        return self.text

    def standards(self, root: Path | None = None) -> str:
        return self.standards_text

    def approval_check(self, task_type: str) -> Callable[[dict], None]:
        return knowledge.approval_check(task_type)

    def clarification_check(self) -> Callable[[dict], None]:
        return knowledge.clarification_check

    def distill(self, verdict: dict, root: Path | None = None) -> list[Path]:
        definitions, terms = knowledge.distillation(verdict)
        self.definitions.update(definitions)
        self.terms.update(terms)
        self.distilled_in = root
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


class MemoryWorkspace:
    """Branches and worktrees as names. A Developer's commit is simulated by adding
    its checkout path to `moved` before the run: `head` then differs from the base."""

    ROOT = Path("/memory/worktrees")

    def __init__(self):
        self.moved: set[Path] = set()
        self.commits: list[tuple[Path, str]] = []
        self.merges: list[tuple[Path, str]] = []
        self.pushed: list[str] = []
        self.cleaned: list[str] = []
        self._looks: dict[Path, int] = {}

    def task_checkout(self, task_id: str) -> Checkout:
        return Checkout(rules.task_branch(task_id), self.ROOT / task_id, "base0")

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout:
        branch = rules.subtask_branch(task_id, subtask_id)
        return Checkout(branch, self.ROOT / f"{task_id}.{subtask_id}", "base1")

    def head(self, path: Path) -> str:
        # A path in `moved` advances on every look, as a branch someone commits to.
        if path not in self.moved:
            return "base"
        self._looks[path] = self._looks.get(path, 0) + 1
        return f"head{self._looks[path]}"

    def merge(self, path: Path, branch: str) -> None:
        self.merges.append((path, branch))

    def commit_all(self, path: Path, message: str) -> str | None:
        self.commits.append((path, message))
        return "c1"

    def push(self, branch: str) -> None:
        self.pushed.append(branch)

    def cleanup(self, task_id: str) -> None:
        self.cleaned.append(task_id)


class MemoryTracker:
    def __init__(self, url: str = "https://github.com/o/r/pull/7"):
        self.url = url
        self.pull_requests: list[tuple[str, str, str]] = []
        self.comments: list[tuple[str, str]] = []
        self.labels: list[tuple[str, str]] = []

    def open_pull_request(self, task_id: str, branch: str, title: str, body: str) -> str:
        self.pull_requests.append((branch, title, body))
        return self.url

    def comment(self, task_id: str, text: str) -> None:
        self.comments.append((task_id, text))

    def label(self, task_id: str, label: str) -> None:
        self.labels.append((task_id, label))


def memory_ports(runner: ScriptedRunner | None = None, **overrides) -> Ports:
    fields = {
        "runner": runner or ScriptedRunner(),
        "documents": MemoryDocuments(),
        "knowledge": MemoryKnowledge(),
        "prompts": MemoryPrompts(),
        "flags": MemoryFlags(),
        "inbox": MemoryInbox(),
        "workspace": MemoryWorkspace(),
        "tracker": MemoryTracker(),
    }
    return Ports(**{**fields, **overrides})


# Recorded role outputs, as the harness would hand them back.

# A capability file as PM must return it: it opens with the jobs it serves.
REPORTS = "# Reports\n\n## Jobs\n\n- When I audit, I want CSV, so I can file it.\n\nCSV export."


def questions(n=1, done=False, cost=0.05, session="q") -> RawResult:
    text = "### Closing\n\nDecided." if done else f"❓ **Q{n}** - **Scope**: which?\n\n➡️ a"
    return RawResult({"markdown": text, "done": done}, cost, 2, session)


def answers(n=1, for_human=(), cost=0.1, session="a") -> RawResult:
    return RawResult(
        {"markdown": f"**Q{n}**: accepted", "questions_for_human": list(for_human)},
        cost,
        1,
        session,
    )


def tech_answers(n=1, for_pm=(), cost=0.1, session="t") -> RawResult:
    return RawResult(
        {"markdown": f"**Q{n}**: accepted", "questions_for_pm": list(for_pm)}, cost, 1, session
    )


def consulted(
    n=1, for_human=(), definitions=None, glossary=None, cost=0.15, session="pm"
) -> RawResult:
    return RawResult(
        {
            "markdown": f"**Q{n}**: the stored date",
            "questions_for_human": list(for_human),
            "definitions": definitions or [],
            "glossary": glossary or [],
        },
        cost,
        1,
        session,
    )


def planned(*subtasks: dict, cost=0.3, session="tl") -> RawResult:
    items = [
        {"id": "api", "title": "API", "brief": "Build it.", "seams": ["GET /x"]},
        *subtasks,
    ]
    return RawResult({"markdown": "### Solution\n\nHex.", "subtasks": items}, cost, 2, session)


def delivered(summary="done", cost=0.5, session="dev") -> RawResult:
    return RawResult(
        {"markdown": "### Changes\n\nx.py", "summary": summary, "assumptions": []},
        cost,
        10,
        session,
    )


def reviewed(kind="approved", reason="fine", cost=0.2) -> RawResult:
    return RawResult(
        {"markdown": f"### Verdict\n\n{kind}", "verdict": kind, "reason": reason}, cost, 3, "rv"
    )


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
        definitions = [{"capability": "reports", "content": REPORTS}] if kind == "approved" else []
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
