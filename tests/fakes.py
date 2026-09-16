"""In-memory ports. The use cases run against these; no graph, no SDK, no disk."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentic.core.knowledge.domain import knowledge
from agentic.core.process.application.ports import Checkout, CommandResult, Ports, RawResult
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import PROMPTS_DIR, Role
from agentic.core.process.domain.task import TaskInput, TaskState

VOCAB = {"touches_ui": "UI", "touches_copy": "copy"}


class ScriptedRunner:
    """Answers `run_once` from per-role queues and records every call it saw.
    A queued exception is raised instead of returned. A gate is asked before the
    result is handed back, as the harness does when the model delivers, and again after
    each refusal, as a model that fixes and delivers again; `refused` keeps the reasons.

    A queue named `<slug>__<subtask>` answers only that subtask's branch and wins over
    the plain `<slug>` one. Branches of the fan-out run concurrently, so which of two
    reviews pops first is the event loop's business: a test that cares which subtask
    was returned scripts it by name, not by position."""

    def __init__(self, **queues):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: list[dict] = []
        self.refused: list[str] = []

    async def run_once(self, role: Role, prompt: str, **kwargs) -> RawResult:
        self.calls.append({"slug": role.slug, "prompt": prompt, "role": role, **kwargs})
        if (gate := kwargs.get("gate")) is not None:
            while (reason := gate()) is not None:
                self.refused.append(reason)
        key = f"{role.slug}__{_subtask_of(kwargs.get('cwd'))}"
        queue = self.queues.get(key) or self.queues.get(role.slug)
        if not queue:
            raise AssertionError(f"no scripted result left for {role.slug} ({key})")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def prompts(self, slug: str) -> list[str]:
        return [c["prompt"] for c in self.calls if c["slug"] == slug]

    @property
    def slugs(self) -> list[str]:
        return [c["slug"] for c in self.calls]

    def ran(self, slug: str) -> list[str]:
        """Which subtask each run of `slug` worked on, in order: the shape of a wave
        without depending on how two concurrent branches interleaved."""
        return [_subtask_of(c.get("cwd")) for c in self.calls if c["slug"] == slug]


def _subtask_of(cwd) -> str:
    """The subtask a checkout belongs to: `.../GH-1.api` is `api`, the task's own
    checkout (the integration, or a lone subtask) is the empty string."""
    name = Path(cwd).name if cwd else ""
    return name.split(".", 1)[1] if "." in name else ""


class MemoryDocuments:
    def __init__(self):
        self.saved: list[TaskState] = []

    def save(self, state: TaskState) -> Path:
        self.saved.append(state)
        return self.path_for(state["id"])

    def path_for(self, task_id: str) -> Path:
        return Path(f"/memory/.agentic/tasks/{task_id}.md")

    def load(self, task_id: str) -> dict | None:
        mine = [s for s in self.saved if s["id"] == task_id]
        return dict(mine[-1]) if mine else None

    @property
    def last(self) -> TaskState:
        return self.saved[-1]


class MemoryKnowledge:
    """Keeps what a consolidation would write. The checks are the real ones: the
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

    def consolidate(self, verdict: dict, root: Path | None = None) -> list[Path]:
        definitions, terms = knowledge.consolidation(verdict)
        self.definitions.update(definitions)
        self.terms.update(terms)
        self.consolidated_in = root
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


class MemoryStack:
    """The product's declared checks, whatever the revision; `revisions` says which were
    asked."""

    def __init__(self, checks: dict[str, str] | None = None):
        self.declared = {"tests": "pytest"} if checks is None else checks
        self.revisions: list[str] = []

    def checks(self, revision: str) -> dict[str, str]:
        self.revisions.append(revision)
        return self.declared


class MemoryCommands:
    """Answers each command from its queue, green when the queue is empty or missing;
    `ran` keeps (cwd, command)."""

    def __init__(self, **queues: list[CommandResult]):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.ran: list[tuple[Path, str]] = []

    def run(self, cwd: Path, command: str, timeout_s: float) -> CommandResult:
        self.ran.append((cwd, command))
        queue = self.queues.get(command)
        return queue.pop(0) if queue else CommandResult(0, "ok")


class MemoryInbox:
    def __init__(self, **issues: TaskInput):
        self.issues = issues

    def task(self, number: int) -> TaskInput:
        return self.issues[str(number)]

    def task_id(self, number: int) -> str:
        return f"GH-{number}"

    def issue_of(self, task_id: str) -> int | None:
        return int(task_id[3:]) if task_id.startswith("GH-") else None


class MemoryWorkspace:
    """Branches and worktrees as names. A Developer's commit is simulated by adding
    its checkout path to `moved` before the run: `head` then differs from the base.
    A path in `dirty` has uncommitted changes. `branches` maps an existing branch to
    (head, base) for the checks of a resume; `checkouts` are the worktrees on disk."""

    ROOT = Path("/memory/worktrees")

    def __init__(self):
        self.moved: set[Path] = set()
        self.dirty: set[Path] = set()
        self.commits: list[tuple[Path, str]] = []
        self.merges: list[tuple[Path, str]] = []
        # Branches whose merge does not apply, as a real conflict would.
        self.conflicts: set[str] = set()
        self.pushed: list[str] = []
        self.cleaned: list[str] = []
        self._looks: dict[Path, int] = {}
        self.branches: dict[str, tuple[str, str]] = {}
        self.checkouts: list[Path] = []
        # Every checkout asked for, in order: real ones take a lock on the same `.git`,
        # so who asks and when is what the fan-in exists to keep serial.
        self.checkouts_made: list[str] = []
        self.pruned = 0

    def task_checkout(self, task_id: str) -> Checkout:
        branch = rules.task_branch(task_id)
        self.checkouts_made.append(branch)
        return Checkout(branch, self.ROOT / task_id, "base0")

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout:
        branch = rules.subtask_branch(task_id, subtask_id)
        self.checkouts_made.append(branch)
        return Checkout(branch, self.ROOT / f"{task_id}.{subtask_id}", "base1")

    def head(self, path: Path) -> str:
        # A path in `moved` advances on every look, as a branch someone commits to.
        if path not in self.moved:
            return "base"
        self._looks[path] = self._looks.get(path, 0) + 1
        return f"head{self._looks[path]}"

    def is_clean(self, path: Path) -> bool:
        return path not in self.dirty

    def merge(self, path: Path, branch: str) -> str | None:
        self.merges.append((path, branch))
        return f"CONFLICT in {branch}" if branch in self.conflicts else None

    def commit_all(self, path: Path, message: str) -> str | None:
        self.commits.append((path, message))
        return "c1"

    def push(self, branch: str) -> None:
        self.pushed.append(branch)

    def cleanup(self, task_id: str) -> None:
        self.cleaned.append(task_id)

    def branch_head(self, branch: str) -> str | None:
        return self.branches[branch][0] if branch in self.branches else None

    def base_of(self, branch: str) -> str | None:
        return self.branches[branch][1] if branch in self.branches else None

    def worktrees(self, task_id: str) -> list[Path]:
        return list(self.checkouts)

    def prune(self) -> None:
        self.pruned += 1


class MemoryTracker:
    def __init__(self, url: str = "https://github.com/o/r/pull/7"):
        self.url = url
        self.pull_requests: list[tuple[str, str, str]] = []
        self.comments: list[tuple[str, str]] = []
        self.labels: list[tuple[str, str]] = []
        self.unlabeled: list[tuple[str, str]] = []
        # Bodies of the issue's comments before the run, oldest first.
        self.earlier: list[str] = []

    def open_pull_request(self, task_id: str, branch: str, title: str, body: str) -> str:
        self.pull_requests.append((branch, title, body))
        return self.url

    def comment(self, task_id: str, text: str) -> None:
        self.comments.append((task_id, text))

    def label(self, task_id: str, label: str) -> None:
        self.labels.append((task_id, label))

    def unlabel(self, task_id: str, label: str) -> None:
        self.unlabeled.append((task_id, label))

    def issue_comments(self, task_id: str) -> list[str]:
        return [*self.earlier, *(text for _, text in self.comments)]


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
        "stack": MemoryStack(),
        "commands": MemoryCommands(),
    }
    return Ports(**{**fields, **overrides})


# Recorded role outputs, as the harness would hand them back.

# A capability file as PM must return it: it opens with the jobs it serves.
REPORTS = "# Reports\n\n## Jobs\n\n- When I audit, I want CSV, so I can file it.\n\nCSV export."


def questions(n=1, done=False, cost=0.05, session="q") -> RawResult:
    text = "### Closing\n\nDecided." if done else f"❓ **Q{n}** - **Scope**: which?\n\n➡️ a"
    return RawResult({"markdown": text, "done": done}, cost, 2, session)


def answers(n=1, for_human=(), withdrawn=(), cost=0.1, session="a") -> RawResult:
    return RawResult(
        {
            "markdown": f"**Q{n}**: accepted",
            "questions_for_human": list(for_human),
            "withdrawn": list(withdrawn),
        },
        cost,
        1,
        session,
    )


def tech_answers(n=1, for_pm=(), cost=0.1, session="t") -> RawResult:
    return RawResult(
        {"markdown": f"**Q{n}**: accepted", "questions_for_pm": list(for_pm)}, cost, 1, session
    )


def consulted(
    n=1, for_human=(), withdrawn=(), definitions=None, glossary=None, cost=0.15, session="pm"
) -> RawResult:
    return RawResult(
        {
            "markdown": f"**Q{n}**: the stored date",
            "questions_for_human": list(for_human),
            "withdrawn": list(withdrawn),
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


def finding(severity="blocking", category="spec-behaviour", claim="wrong", scope="subtask"):
    return {
        "location": "x.py:1",
        "claim": claim,
        "evidence": "$ pytest tests/test_x.py\nFAILED",
        "severity": severity,
        "scope": scope,
        "category": category,
    }


def reviewed(kind="approved", reason="fine", cost=0.2, category="spec-behaviour") -> RawResult:
    """The verdict is derived: a return is a review with a blocking finding."""
    findings = [finding(category=category, claim=reason)] if kind == "returned" else []
    return RawResult(
        {"markdown": "### Findings\n\nNone.", "reason": reason, "findings": findings},
        cost,
        3,
        "rv",
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
