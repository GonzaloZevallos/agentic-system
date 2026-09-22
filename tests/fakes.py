"""In-memory ports. The use cases run against these; no graph, no SDK, no disk."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentic.core.knowledge.domain import knowledge
from agentic.core.process.application.ports import Checkout, CommandResult, Ports, RawResult
from agentic.core.process.domain import rules
from agentic.core.process.domain.roles import PROMPTS_DIR, Role
from agentic.core.process.domain.task import DependencyState, MergeOutcome, TaskInput, TaskState

VOCAB = {"touches_ui": "UI", "touches_copy": "copy"}


class ScriptedRunner:
    """Answers `run_once` from per-role queues and records every call it saw.
    A queued exception is raised instead of returned. A presubmit is asked before the
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
        if (presubmit := kwargs.get("presubmit")) is not None:
            while (reason := presubmit()) is not None:
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
        self.written_into: list[tuple[str, Path]] = []
        # task_id -> the closure values recorded for it, folded over successive calls
        # the way the filesystem adapter's line-level replace-or-append does.
        self.closures: dict[str, dict[str, str]] = {}

    def save(self, state: TaskState) -> Path:
        self.saved.append(state)
        return self.path_for(state["id"])

    def path_for(self, task_id: str) -> Path:
        return Path(f"/memory/.agentic/tasks/{task_id}.md")

    def load(self, task_id: str) -> dict | None:
        mine = [s for s in self.saved if s["id"] == task_id]
        if not mine:
            return None
        return {**mine[-1], **self.closures.get(task_id, {})}

    def record_closure(self, task_id: str, values: dict[str, str]) -> Path:
        if self.load(task_id) is None:
            raise FileNotFoundError(task_id)
        self.closures[task_id] = {**self.closures.get(task_id, {}), **values}
        return self.path_for(task_id)

    def write_into(self, task_id: str, root: Path) -> Path | None:
        self.written_into.append((task_id, root))
        if self.load(task_id) is None:
            return None
        return root / ".agentic" / "tasks" / f"{task_id}.md"

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
        return knowledge.clarification_check()

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
    """The product's declared stack, whatever the revision; `revisions` says which were
    asked, of either key."""

    def __init__(
        self,
        checks: dict[str, str] | None = None,
        setup: dict[str, str] | None = None,
        product: str | None = "product",
    ):
        self.declared = {"tests": "pytest"} if checks is None else checks
        self.declared_setup = {} if setup is None else setup
        # None is a product that declares no name, which `preflight` refuses.
        self.declared_product = product
        self.revisions: list[str] = []

    def checks(self, revision: str) -> dict[str, str]:
        self.revisions.append(revision)
        return self.declared

    def setup(self, revision: str) -> dict[str, str]:
        self.revisions.append(revision)
        return self.declared_setup

    def product(self, revision: str = "HEAD") -> str:
        if self.declared_product is None:
            raise ValueError("expected `product`, the name this product goes by")
        return self.declared_product


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
    A path in `dirty` has untracked files and one in `uncommitted` has changes to
    tracked ones; only the second makes a version `+dirty`. `existing_branches` maps an
    existing branch to (head, base) for the checks of a resume and for `branches`,
    `rename_branch`; `checkouts` are the worktrees on disk."""

    ROOT = Path("/memory/worktrees")

    def __init__(self):
        # Path -> the repository it belongs to; a path outside it is in none.
        self.repositories: dict[Path, Path] = {}
        self.moved: set[Path] = set()
        self.dirty: set[Path] = set()
        self.uncommitted: set[Path] = set()
        self.commits: list[tuple[Path, str]] = []
        self.merges: list[tuple[Path, str]] = []
        # Branches whose merge does not apply, as a real conflict would.
        self.conflicts: set[str] = set()
        # `merge_keeping_conflicts` calls, and the paths a scripted conflict leaves
        # (`x.py` when a branch is in `conflicts` but not given here). A branch in
        # `refused_branches` conflicts with nothing to resolve: a refusal with no
        # conflicted path at all, the `unmergeable`-immediately case.
        self.kept_merges: list[tuple[Path, str]] = []
        self.conflict_paths: dict[str, tuple[str, ...]] = {}
        self.refused_branches: set[str] = set()
        # `dependency_state` calls, and the states scripted per branch, one queue each:
        # successive calls pop the next scripted state, the last one repeating once the
        # queue is down to it (a branch's pre-run call and its post-delivery scan often
        # need to answer differently). A branch with none reads as not yet landed and
        # nothing in progress, so the fake's default is "attempt the merge", the same
        # thing every dependency does today.
        self.dependency_state_calls: list[tuple[Path, str, tuple[str, ...]]] = []
        self.dependency_states: dict[str, list[DependencyState]] = {}
        self.pushed: list[str] = []
        self.cleaned: list[str] = []
        self._looks: dict[Path, int] = {}
        self.existing_branches: dict[str, tuple[str, str]] = {}
        # `rename_branch` calls, oldest first.
        self.renamed: list[tuple[str, str]] = []
        # Paths (as `is_ignored`'s own `relative` argument) a test declares ignored by
        # the product's `.gitignore`; empty is a product that ignores nothing.
        self.ignored: set[str] = set()
        self.ignored_checks: list[tuple[Path, str]] = []
        self.checkouts: list[Path] = []
        # The worktrees this fake has made, as a real one has them on disk.
        self.on_disk: set[Path] = set()
        self.discarded: list[Path] = []
        # Every checkout asked for, in order: real ones take a lock on the same `.git`,
        # so who asks and when is what the fan-in exists to keep serial.
        self.checkouts_made: list[str] = []
        self.pruned = 0

    def repository_at(self, path: Path) -> Path | None:
        return self.repositories.get(Path(path))

    def task_checkout(self, task_id: str) -> Checkout:
        branch = rules.task_branch(task_id)
        return self._made(branch, self.ROOT / task_id, "base0")

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout:
        branch = rules.subtask_branch(task_id, subtask_id)
        return self._made(branch, self.ROOT / f"{task_id}.{subtask_id}", "base1")

    def _made(self, branch: str, path: Path, base: str) -> Checkout:
        # As the real one: only the ask that made the worktree says so, so the setup
        # runs once, and a discarded one is made again.
        created = path not in self.on_disk
        self.checkouts_made.append(branch)
        self.on_disk.add(path)
        return Checkout(branch, path, base, created=created)

    def discard(self, path: Path) -> None:
        self.discarded.append(path)
        self.on_disk.discard(path)

    def head(self, path: Path) -> str:
        # A path in `moved` advances on every look, as a branch someone commits to.
        if path not in self.moved:
            return "base"
        self._looks[path] = self._looks.get(path, 0) + 1
        return f"head{self._looks[path]}"

    def is_clean(self, path: Path) -> bool:
        return path not in self.dirty and path not in self.uncommitted

    def is_committed(self, path: Path) -> bool:
        return path not in self.uncommitted

    def merge(self, path: Path, branch: str) -> str | None:
        self.merges.append((path, branch))
        return f"CONFLICT in {branch}" if branch in self.conflicts else None

    def merge_keeping_conflicts(self, path: Path, branch: str) -> MergeOutcome:
        self.kept_merges.append((path, branch))
        if branch in self.refused_branches:
            return MergeOutcome(reason=f"refused: {branch}")
        if branch in self.conflicts:
            paths = self.conflict_paths.get(branch, ("x.py",))
            return MergeOutcome(reason=f"CONFLICT in {branch}", conflicted_paths=paths)
        return MergeOutcome(reason=None)

    def dependency_state(self, path: Path, branch: str, paths) -> DependencyState:
        self.dependency_state_calls.append((path, branch, tuple(paths)))
        queue = self.dependency_states.get(branch)
        if not queue:
            return DependencyState(False, False, ())
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def commit_all(self, path: Path, message: str) -> str | None:
        self.commits.append((path, message))
        return "c1"

    def push(self, branch: str) -> None:
        self.pushed.append(branch)

    def cleanup(self, task_id: str) -> None:
        self.cleaned.append(task_id)

    def branch_head(self, branch: str) -> str | None:
        return self.existing_branches[branch][0] if branch in self.existing_branches else None

    def base_of(self, branch: str) -> str | None:
        return self.existing_branches[branch][1] if branch in self.existing_branches else None

    def worktrees(self, task_id: str) -> list[Path]:
        return list(self.checkouts)

    def prune(self) -> None:
        self.pruned += 1

    def branches(self, prefix: str) -> list[str]:
        return [name for name in self.existing_branches if name.startswith(prefix)]

    def rename_branch(self, old: str, new: str) -> None:
        self.renamed.append((old, new))
        if old in self.existing_branches:
            self.existing_branches[new] = self.existing_branches.pop(old)

    def is_ignored(self, root: Path, relative: str) -> bool:
        self.ignored_checks.append((root, relative))
        return relative in self.ignored


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


def questions(n=1, done=False, cost=0.05, session="q", invariants=()) -> RawResult:
    text = "### Closing\n\nDecided." if done else f"❓ **Q{n}** - **Scope**: which?\n\n➡️ a"
    return RawResult(
        {"markdown": text, "done": done, "invariants": list(invariants)}, cost, 2, session
    )


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


def planned(*subtasks: dict, cost=0.3, session="tl", invariants=(), integration="") -> RawResult:
    items = [
        {"id": "api", "title": "API", "brief": "Build it.", "seams": ["GET /x"]},
        *subtasks,
    ]
    return RawResult(
        {
            "markdown": "### Solution\n\nHex.",
            "subtasks": items,
            "invariants": list(invariants),
            "integration": integration,
        },
        cost,
        2,
        session,
    )


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


def reviewed(
    kind="approved",
    reason="fine",
    cost=0.2,
    category="spec-behaviour",
    findings=None,
    settled=(),
) -> RawResult:
    """The verdict is derived: a return is a review with a blocking finding."""
    if findings is None:
        findings = [finding(category=category, claim=reason)] if kind == "returned" else []
    return RawResult(
        {
            "markdown": "### Findings\n\nNone.",
            "reason": reason,
            "findings": findings,
            "settled": list(settled),
        },
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


# The bug path (issue #65): the Product Analyst's diagnosis and PM's ruling on it.


def diagnosed(ruling="bug", capability="dashboard", for_pm=(), cost=0.1) -> RawResult:
    return RawResult(
        {
            "markdown": f"### Reproduction\n\nRan it.\n\n### Ruling\n\n{ruling}",
            "ruling": ruling,
            "capability": capability,
            "questions_for_pm": list(for_pm),
        },
        cost,
        3,
        "pa",
    )


def ruled(
    kind="approved", ruling="bug", reason="r", definitions=None, glossary=None, cost=0.2
) -> RawResult:
    return RawResult(
        {
            "markdown": f"### Verdict\n\n{kind}: {ruling}\n\n### For the next role\n\n{reason}",
            "verdict": kind,
            "ruling": ruling,
            "reason": reason,
            "definitions": definitions or [],
            "glossary": glossary or [],
        },
        cost,
    )
