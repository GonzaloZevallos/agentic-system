"""In-memory ports. The use cases run against these; no graph, no SDK, no disk."""

from __future__ import annotations

import contextlib
import dataclasses
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from agentic.core.knowledge.domain import knowledge
from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application.limits import RunLimits
from agentic.core.process.application.ports import (
    AddedAdr,
    AddedLine,
    BranchPullRequest,
    Checkout,
    CommandResult,
    Commands,
    FlagVocabulary,
    GitError,
    Inbox,
    IssueComment,
    IssueState,
    LaunchAttempt,
    ListedPullRequest,
    MergeCallFailed,
    Ports,
    ProcessReaper,
    ProductKnowledge,
    ProductStack,
    Prompts,
    RawResult,
    RoleRunner,
    RunRegistration,
    SampleApp,
    SampleAppHandle,
    TaskDocuments,
    Tracker,
    Upstream,
    UpstreamMerge,
    Workspace,
)
from agentic.core.process.domain import rules
from agentic.core.process.domain.ci import CiReport, CiUnreadable, Containment
from agentic.core.process.domain.design import Design
from agentic.core.process.domain.playbook import Playbook
from agentic.core.process.domain.reaping import ReapedProcess
from agentic.core.process.domain.roles import PROMPTS_DIR, Role
from agentic.core.process.domain.rules import MergeFailure
from agentic.core.process.domain.suppressions import Forbidden
from agentic.core.process.domain.task import (
    DependencyState,
    MergeOutcome,
    PullRequest,
    TaskInput,
    TaskState,
)

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

    def __init__(self, **queues: list[RawResult | Exception]):
        self.queues = {k: list(v) for k, v in queues.items()}
        self.calls: list[dict[str, Any]] = []
        self.refused: list[str] = []

    async def run_once(self, role: Role, prompt: str, **kwargs: Any) -> RawResult:
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


def _subtask_of(cwd: Path | str | None) -> str:
    """The subtask a checkout belongs to: `.../GH-1.api` is `api`, the task's own
    checkout (the integration, or a lone subtask) is the empty string."""
    name = Path(cwd).name if cwd else ""
    return name.split(".", 1)[1] if "." in name else ""


class MemoryDocuments:
    def __init__(self) -> None:
        self.saved: list[TaskState] = []
        self.written_into: list[tuple[str, Path]] = []
        # task_id -> the closure values recorded for it, folded over successive calls
        # the way the filesystem adapter's line-level replace-or-append does.
        self.closures: dict[str, dict[str, object]] = {}

    def save(self, state: TaskState) -> Path:
        self.saved.append(state)
        return self.path_for(state["id"])

    def path_for(self, task_id: str) -> Path:
        return Path(f"/memory/.agentic/tasks/{task_id}.md")

    def ids(self) -> list[str]:
        return sorted({state["id"] for state in self.saved})

    def ids_touched_since(self, since: datetime) -> list[str]:
        return self.ids()

    def load(self, task_id: str) -> dict[str, object] | None:
        mine = [s for s in self.saved if s["id"] == task_id]
        if not mine:
            return None
        return {**mine[-1], **self.closures.get(task_id, {})}

    def record_closure(self, task_id: str, values: Mapping[str, object]) -> Path:
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

    def __init__(
        self,
        text: str = "",
        standards: str = "## Engineering standards\n\nHex.",
        brief: str | None = None,
        standards_index: str | None = None,
    ) -> None:
        self.text = text
        # What `standards(whole=False)` returns when a test pins the two shapes apart; None
        # keeps one text for both, as every test written before the index did.
        self.standards_index_text = standards_index
        self.index: dict[str, str] = {}
        self.standards_text = standards
        self.brief_text = brief
        self.definitions: dict[str, str] = {}
        self.terms: dict[str, dict[str, str]] = {}

    def read(self, root: Path | None = None) -> str:
        self.read_from = root
        return self.text

    def index_files(self, root: Path | None = None) -> dict[str, str]:
        return dict(self.index)

    def standards(self, root: Path | None = None, whole: bool = True) -> str:
        self.standards_from = root
        self.standards_whole = whole
        if not whole and self.standards_index_text is not None:
            return self.standards_index_text
        return self.standards_text

    def brief(self, root: Path | None = None) -> str | None:
        return self.brief_text

    def approval_check(self, task_type: str) -> Callable[[dict[str, object]], None]:
        return knowledge.approval_check(task_type)

    def clarification_check(self) -> Callable[[dict[str, object]], None]:
        return knowledge.clarification_check()

    def consolidate(self, verdict: dict[str, object], root: Path | None = None) -> list[Path]:
        definitions, terms = knowledge.consolidation(verdict)
        self.definitions.update(definitions)
        self.terms.update(terms)
        self.consolidated_in = root
        return [Path(f"/memory/docs/product/{slug}.md") for slug in definitions]


class MemoryPrompts:
    """The system's own prompt files, with optional overrides by slug."""

    def __init__(self, **overrides: str):
        self.overrides = overrides
        # Seeded by slug; a role with none has an empty playbook.
        self.playbooks: dict[str, Playbook] = {}

    def playbook(self, role: Role) -> Playbook:
        return self.playbooks.get(role.slug, Playbook(slug=role.slug))

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
        witness: str | None = None,
        forbidden: list[Forbidden] | None = None,
        design: Design | None = None,
        parallel_checks: bool = False,
        ci: dict[str, tuple[str, ...]] | tuple[str, ...] | None = None,
        affected_checks: dict[str, str] | None = None,
        affected_fallback: tuple[str, ...] = (),
    ) -> None:
        self.declared = {"tests": "pytest"} if checks is None else checks
        self.declared_setup = {} if setup is None else setup
        # None is a product that declares no name, which `preflight` refuses.
        self.declared_product = product
        # None is a product that declares no checkout witness (glossary: Testigo del
        # checkout), which is every product but this repo's own future tasks.
        self.declared_witness = witness
        # Empty is a product that forbids no suppression (issue #38), which is every
        # product but this repo's own future tasks -- the same legitimate absence
        # `witness` has.
        self.declared_forbidden = [] if forbidden is None else forbidden
        # None is a product that declares no design block (ADR-0040), the same
        # legitimate absence `witness` has: every product but the one a design task
        # prepares for.
        self.declared_design = design
        # False is a product that does not opt in to concurrent checks (ADR-0044), which
        # is every product until its own file says otherwise.
        self.declared_parallel_checks = parallel_checks
        # GH-216: the declared CI check names per revision; a revision with none is a
        # product that declares no `ci`, the local-check path of today. A test sets
        # entries on this mapping, or `ci_error` to make the read raise `ValueError` the
        # way a malformed key does. `ci_revisions` is each revision asked.
        # A bare tuple declares the same names at every revision, which is what the
        # merge and review tests need; a dict declares them per revision.
        self.declared_ci: dict[str, tuple[str, ...]] = {}
        self.declared_ci_all: tuple[str, ...] = ()
        if isinstance(ci, tuple):
            self.declared_ci_all = ci
        elif ci is not None:
            self.declared_ci = ci
        self.ci_error: str | None = None
        self.ci_revisions: list[str] = []
        # Empty is a product with no affected-only form (ADR-0046), every product until
        # its own file says otherwise.
        self.declared_affected_checks = {} if affected_checks is None else affected_checks
        self.declared_affected_fallback = affected_fallback
        self.revisions: list[str] = []

    def checks(self, revision: str) -> dict[str, str]:
        self.revisions.append(revision)
        return self.declared

    def setup(self, revision: str) -> dict[str, str]:
        self.revisions.append(revision)
        return self.declared_setup

    def witness(self, revision: str) -> str | None:
        # Not tracked in `revisions`, the same as `product`: existing tests count that
        # list to prove exactly `checks` and `setup` are asked (issue #22, 7), and a
        # witness read must not silently grow that count out from under them.
        return self.declared_witness

    def parallel_checks(self, revision: str) -> bool:
        # Not tracked in `revisions` either, `witness`'s own reason.
        return self.declared_parallel_checks

    def affected_checks(self, revision: str) -> dict[str, str]:
        # Not tracked in `revisions` either, `witness`'s own reason.
        return self.declared_affected_checks

    def affected_fallback(self, revision: str) -> tuple[str, ...]:
        # Not tracked in `revisions` either, `witness`'s own reason.
        return self.declared_affected_fallback

    def forbidden(self, revision: str) -> list[Forbidden]:
        # Not tracked in `revisions` either, `witness`'s own reason.
        return self.declared_forbidden

    def ci(self, revision: str) -> tuple[str, ...]:
        # Not tracked in `revisions` either, `witness`'s own reason.
        self.ci_revisions.append(revision)
        if self.ci_error is not None:
            raise ValueError(self.ci_error)
        return self.declared_ci.get(revision, self.declared_ci_all)

    def product(self, revision: str = "HEAD") -> str:
        if self.declared_product is None:
            raise ValueError("expected `product`, the name this product goes by")
        return self.declared_product

    def design(self, revision: str) -> Design | None:
        # Not tracked in `revisions` either, `witness`'s own reason.
        return self.declared_design


class MemoryCommands:
    """Answers each command from its queue, green when the queue is empty or missing;
    `ran` keeps (cwd, command).

    Safe to call from several threads (the parallel gate does): a lock guards `ran` and
    the queues. `in_flight` counts the calls inside `run` now and `high_water` the most
    there ever were at once. `holds` maps a command to a `threading.Event`: that call
    waits inside `run` until the test sets it (never a `sleep` as a proof). `raises`
    lists commands whose call raises `RuntimeError`. `completed` keeps every command
    whose call has left `run`, raised or not, in the order they left. With none of these
    options the behaviour is the plain one."""

    HOLD_LIMIT_S = 10.0  # a test that forgets to release must fail, not hang the suite

    def __init__(
        self,
        *,
        holds: dict[str, threading.Event] | None = None,
        raises: set[str] | None = None,
        **queues: list[CommandResult],
    ) -> None:
        self.queues = {k: list(v) for k, v in queues.items()}
        self.ran: list[tuple[Path, str]] = []
        self.holds = holds or {}
        self.raises = raises or set()
        self.in_flight = 0
        self.high_water = 0
        self.completed: list[str] = []
        self._changed = threading.Condition()

    def wait_until(self, predicate: Callable[[], bool], timeout_s: float = 5.0) -> bool:
        """Block until `predicate` holds over this fake's state; False on timeout."""
        with self._changed:
            return self._changed.wait_for(predicate, timeout_s)

    def run(self, cwd: Path, command: str, timeout_s: float) -> CommandResult:
        with self._changed:
            self.ran.append((cwd, command))
            self.in_flight += 1
            self.high_water = max(self.high_water, self.in_flight)
            self._changed.notify_all()
        try:
            if (hold := self.holds.get(command)) is not None:
                hold.wait(self.HOLD_LIMIT_S)
            if command in self.raises:
                raise RuntimeError(f"{command} blew up")
            with self._changed:
                queue = self.queues.get(command)
                return queue.pop(0) if queue else CommandResult(0, "ok")
        finally:
            with self._changed:
                self.in_flight -= 1
                self.completed.append(command)
                self._changed.notify_all()


class MemorySampleApp:
    """PM's early final validation's own sample app (ADR-0040), without a real process:
    `result` is handed back from `start` as scripted, a handle by default -- a test that
    wants a refusal passes a `str`. `stopped` keeps every handle `stop` was called with,
    in order, so a test can tell the app never outlived the node."""

    def __init__(self, result: SampleAppHandle | str | None = None) -> None:
        default = SampleAppHandle(lines=("Listening on http://localhost:4242",), token=None)
        self.result = result or default
        self.started: list[tuple[str, Path]] = []
        self.stopped: list[SampleAppHandle] = []

    def start(self, command: str, cwd: Path) -> SampleAppHandle | str:
        self.started.append((command, cwd))
        return self.result

    def stop(self, handle: SampleAppHandle) -> None:
        self.stopped.append(handle)


class MemoryInbox:
    def __init__(
        self,
        *,
        open_issues: list[tuple[str, str]] | None = None,
        task_errors: set[int] | None = None,
        **issues: TaskInput,
    ) -> None:
        self.issues = issues
        # `(url, body)` pairs `open_issues()` hands back: a stack review's own read
        # seam, never filtered here -- the caller matches the marker (ADR-0034).
        self._open_issues = open_issues or []
        # GH-142: a number here makes `task` raise `ValueError` (the shape a `gh`
        # failure or an unreadable body takes), the same pattern `MemoryTracker.
        # state_errors` already uses for `issue_state` -- a declared rejected input,
        # never an accidental `KeyError` off an inbox a scenario forgot to seed.
        self.task_errors = task_errors or set()

    def task(self, number: int) -> TaskInput:
        if number in self.task_errors:
            raise ValueError(f"issue #{number} has no body; the roles need a description")
        return self.issues[str(number)]

    def task_id(self, number: int) -> str:
        return f"GH-{number}"

    def issue_of(self, task_id: str) -> int | None:
        return int(task_id[3:]) if task_id.startswith("GH-") else None

    def open_issues(self) -> list[tuple[str, str]]:
        return self._open_issues

    def open_issue_bodies(self) -> list[tuple[str, str]]:
        return self._open_issues


@dataclasses.dataclass(frozen=True)
class BranchCommit:
    """A non-merge commit of the branch in `MemoryWorkspace.branch_commits`: the paths it
    `adds`, the `(path, text)` lines it added and the master sha whose tree it was made
    on (the one it last merged)."""

    sha: str
    adds: tuple[str, ...]
    lines: tuple[tuple[str, str], ...]
    master: str
    # The paths it deleted (a renumber commit moves a file: the old path is gone from its
    # tree, as git has it).
    removes: tuple[str, ...] = ()


class MemoryWorkspace:
    """Branches and worktrees as names. A Developer's commit is simulated by adding
    its checkout path to `moved` before the run: `head` then differs from the base.
    A path in `dirty` has untracked files and one in `uncommitted` has changes to
    tracked ones; only the second makes a version `+dirty`. `existing_branches` maps an
    existing branch to (head, base) for the checks of a resume and for `branches`,
    `rename_branch`; `checkouts` are the worktrees on disk."""

    ROOT = Path("/memory/worktrees")

    def __init__(self) -> None:
        # Path -> the repository it belongs to; a path outside it is in none.
        self.repositories: dict[Path, Path] = {}
        self.moved: set[Path] = set()
        # Checkout path -> the index files last written into it (`write_index`).
        self.indexes: dict[Path, dict[str, str]] = {}
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
        # GH-239: `merge_upstream_into`'s answer per upstream sha, its `GitError` text and
        # each call.
        self.upstream_merges: dict[str, UpstreamMerge] = {}
        self.upstream_merge_error: str | None = None
        self.upstream_merge_calls: list[tuple[Path, str]] = []
        # GH-267: a clean `merge_upstream_into` leaves `upstream_sha` an ancestor of the
        # head (as git does); a test sets this to script a merge that brings nothing in.
        self.merge_brings_nothing = False
        # GH-267: the MERGE_HEAD a checkout holds (a scripted conflict sets it, as git
        # does; a test may preset one), and each `abort_merge` call in `events` order.
        self.merge_heads: dict[Path, str] = {}
        self.merge_head_error: str | None = None
        self.abort_error: str | None = None
        self.aborted: list[Path] = []
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
        # GH-229: the pushes that went through, `(branch, lease)`, in order; `leases`
        # above also holds the ones a stale lease refused.
        self.pushes: list[tuple[str, str | None]] = []
        # GH-229: master as a tree a test moves with `advance_master`, `master_trees` being
        # every file master held at each of its shas; the branch's own non-merge commits,
        # which `added_adrs`, `added_lines` and `tree_names` answer from, and each ask.
        self.master_sha = "master0"
        # GH-229: when True, `commit_paths` is also a commit of the branch -- what the
        # worktree holds at each path it names becomes `show`'s text at the new head, and
        # `full_head` answers that head -- so a renumber is visible to the reads after it.
        self.track_commits = False
        # GH-229: branch commits get 40-hex ids when True, the form the merge decision
        # requires of a pull request head (`full_commit_id`).
        self.full_shas = False
        self.master_trees: dict[str, tuple[str, ...]] = {"master0": ()}
        self.branch_commits: list[BranchCommit] = []
        self.added_adrs_calls: list[tuple[str, str]] = []
        self.added_lines_calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.tree_names_calls: list[tuple[str, str]] = []
        # GH-262: each `base_of` ask (a `git config --get` on the real adapter), in order.
        self.base_of_calls: list[str] = []
        # GH-216: `contains`'s answer per `(head, master)` -- absent is `unknown`, which
        # the caller counts as stale -- and each ask, in order.
        self.containment: dict[tuple[str, str], Containment] = {}
        # What every pair not in `containment` answers: the merge tests script one answer.
        self.contains_answer: Containment = "unknown"
        self.contains_calls: list[tuple[str, str]] = []
        # GH-255: `merge_conflicts`'s answer per `(head, master)` -- an unscripted pair is
        # `None`, unread -- and each ask, in order. (`conflicts` is the merge-refusal set.)
        self.conflict_answers: dict[tuple[str, str], bool | None] = {}
        self.conflict_probes: list[tuple[str, str]] = []
        # The `lease` every `push` received, in order (the failed ones too); `remote_tips`
        # is what `remote_tip` answers per branch (absent is `None`, the remote has none),
        # `remote_tip_error` makes it raise `GitError`, and `remote_tip_calls` is each ask.
        self.leases: list[str | None] = []
        self.remote_tips: dict[str, str] = {}
        self.remote_tip_error: str | None = None
        self.remote_tip_calls: list[tuple[Path, str]] = []
        # `(path, relatives, message)` of every `commit_paths`, and `events`: that call and
        # every `push`, in order, so a test says which came first (GH-203).
        self.path_commits: list[tuple[Path, tuple[str, ...], str]] = []
        self.events: list[str] = []
        # Raises `GitError` from `push` for its first N calls / always from `commit_paths`
        # and `cleanup` when set: the failures a delivery must name or swallow.
        self.push_failures = 0
        self.commit_paths_error: str | None = None
        self.cleanup_error: str | None = None
        # `(ref, relative)` -> the text `show` answers; absent is `None`.
        self.files: dict[tuple[str, str], str] = {}
        self.cleaned: list[str] = []
        self._looks: dict[Path, int] = {}
        self.existing_branches: dict[str, tuple[str, str]] = {}
        # `rename_branch` calls, oldest first.
        self.renamed: list[tuple[str, str]] = []
        # Paths (as `is_ignored`'s own `relative` argument) a test declares ignored by
        # the product's `.gitignore`; empty is a product that ignores nothing.
        self.ignored: set[str] = set()
        self.ignored_checks: list[tuple[Path, str]] = []
        # `sha`s a test declares already landed; `is_ancestor` answers True for those
        # alone. Every call is kept too (GH-137's own blocker/launch predicates).
        self.ancestors: set[str] = set()
        self.ancestor_checks: list[tuple[Path, str, str]] = []
        # GH-204: `ref` -> the shas `is_ancestor` answers True for against that ref alone,
        # consulted before `ancestors` (a remote tip inside an archive but not inside HEAD).
        self.ancestors_by_ref: dict[str, set[str]] = {}
        # GH-137 blocking finding: a `sha` here makes `is_ancestor` raise `GitError`
        # instead of answering, the real-git-failure case a stale/unknown object must
        # not be confused with ("ante la duda, no lanza" needs a rejected-input test
        # for this, not only for the known-false case `ancestors` already covers).
        self.ancestor_errors: set[str] = set()
        # What `upstream()` answers (the poller's blocker tip, GH-180) and how many times
        # it was asked: the real one fetches, so "once per round, and only when a blocker
        # needs it" is a count.
        self.upstream_answer = Upstream("HEAD", None, None)
        self.upstream_calls = 0
        self.checkouts: list[Path] = []
        # GH-297: `build_slice`'s and `integration_paths`'s scripts and their calls.
        self.slice_conflicts: set[str] = set()
        self.slice_head = "5" * 40
        self.slice_builds: list[tuple[Path, str, tuple[str, ...], str]] = []
        self.integration_work: frozenset[str] = frozenset()
        self.integration_conflicts = False
        self.integration_reads: list[tuple[Path, tuple[str, ...], str, str | None]] = []
        # The worktrees this fake has made, as a real one has them on disk.
        self.on_disk: set[Path] = set()
        self.discarded: list[Path] = []
        # Every checkout asked for, in order: real ones take a lock on the same `.git`,
        # so who asks and when is what the fan-in exists to keep serial.
        self.checkouts_made: list[str] = []
        self.pruned = 0
        self.fetched_commits: list[str] = []
        self.diff_reads: list[tuple[str, str]] = []
        self.diff_paths: tuple[str, ...] = ()
        self.detached: list[str] = []
        self.removed_checkouts: list[str] = []

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

    def write_index(self, path: Path, files: dict[str, str]) -> None:
        self.indexes[path] = dict(files)

    def head(self, path: Path) -> str:
        # A path in `moved` advances on every look, as a branch someone commits to.
        if path not in self.moved:
            return "base"
        self._looks[path] = self._looks.get(path, 0) + 1
        return f"head{self._looks[path]}"

    def full_head(self, path: Path) -> str:
        """A look like `head`: on a moved path each call to either verb advances the same
        counter; production reads are side-effect free. With `track_commits`, the last
        commit of the branch."""
        if self.track_commits and self.branch_commits:
            return self.branch_commits[-1].sha
        return self.head(path)

    def is_clean(self, path: Path) -> bool:
        return path not in self.dirty and path not in self.uncommitted

    def is_committed(self, path: Path) -> bool:
        return path not in self.uncommitted

    def merge(self, path: Path, branch: str) -> str | None:
        self.merges.append((path, branch))
        return f"CONFLICT in {branch}" if branch in self.conflicts else None

    def build_slice(self, path: Path, master: str, heads: Sequence[str], branch: str) -> str | None:
        """GH-297: a head in `slice_conflicts` makes the build answer `None`, a clean one
        the sha in `slice_head` (a fixed one by default). Every call is in `slice_builds`."""
        self.slice_builds.append((path, master, tuple(heads), branch))
        if any(head in self.slice_conflicts for head in heads):
            return None
        return self.slice_head

    def integration_paths(
        self, path: Path, heads: Sequence[str], task_head: str, *, master: str | None = None
    ) -> frozenset[str] | None:
        """GH-297: what `integration_work` holds, `None` when `integration_conflicts` is set."""
        self.integration_reads.append((path, tuple(heads), task_head, master))
        return None if self.integration_conflicts else self.integration_work

    def merge_upstream_into(self, checkout: Path, upstream_sha: str) -> UpstreamMerge:
        """GH-239: a scripted answer per `upstream_sha` in `upstream_merges`, a clean merge
        otherwise; `upstream_merge_error` makes it raise `GitError` the way a refusal that
        is not a conflict does. Every call is in `upstream_merge_calls`."""
        self.upstream_merge_calls.append((checkout, upstream_sha))
        if self.upstream_merge_error is not None:
            raise GitError(self.upstream_merge_error)
        scripted = self.upstream_merges.get(upstream_sha)
        if scripted is not None:
            if not scripted.clean:
                self.merge_heads[checkout] = upstream_sha
            elif not self.merge_brings_nothing:
                self.ancestors.add(upstream_sha)
            return scripted
        if not self.merge_brings_nothing:
            self.ancestors.add(upstream_sha)
        return UpstreamMerge(clean=True, conflicted=(), head=self.full_head(checkout))

    def merge_keeping_conflicts(self, path: Path, branch: str) -> MergeOutcome:
        self.kept_merges.append((path, branch))
        if branch in self.refused_branches:
            return MergeOutcome(reason=f"refused: {branch}")
        if branch in self.conflicts:
            paths = self.conflict_paths.get(branch, ("x.py",))
            return MergeOutcome(reason=f"CONFLICT in {branch}", conflicted_paths=paths)
        return MergeOutcome(reason=None)

    def merge_head(self, path: Path) -> str | None:
        if self.merge_head_error is not None:
            raise GitError(self.merge_head_error)
        return self.merge_heads.get(path)

    def abort_merge(self, path: Path) -> None:
        if self.merge_heads.get(path) is None:
            return
        self.events.append("abort_merge")
        self.aborted.append(path)
        if self.abort_error is not None:
            raise GitError(self.abort_error)
        del self.merge_heads[path]

    def dependency_state(self, path: Path, branch: str, paths: Sequence[str]) -> DependencyState:
        self.dependency_state_calls.append((path, branch, tuple(paths)))
        queue = self.dependency_states.get(branch)
        if not queue:
            return DependencyState(False, False, ())
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def commit_all(self, path: Path, message: str) -> str | None:
        self.commits.append((path, message))
        return "c1"

    def commit_paths(self, path: Path, relatives: Sequence[str], message: str) -> str | None:
        self.events.append("commit_paths")
        if self.commit_paths_error is not None:
            raise GitError(self.commit_paths_error)
        self.path_commits.append((path, tuple(relatives), message))
        if self.track_commits:
            return self._commit_branch(path, relatives)
        return "c2"

    def _branch_sha(self) -> str:
        count = len(self.branch_commits)
        return f"{0xB000 + count:040x}" if self.full_shas else f"branch{count}"

    def _commit_branch(self, path: Path, relatives: Sequence[str]) -> str:
        """GH-229: the branch's next commit from what the worktree holds at `relatives`."""
        previous = self.branch_commits[-1].sha if self.branch_commits else ""
        adds: list[str] = []
        removes: list[str] = []
        lines: list[tuple[str, str]] = []
        texts: dict[str, str] = {}
        for relative in relatives:
            target = path / relative
            before = self.show(previous, relative)
            if not target.exists():
                removes.append(relative)
                continue
            text = target.read_text(encoding="utf-8")
            texts[relative] = text
            if before is None:
                adds.append(relative)
            known = set((before or "").splitlines())
            lines.extend((relative, line) for line in text.splitlines() if line not in known)
        sha = self._branch_sha()
        self.branch_commits.append(
            BranchCommit(sha, tuple(adds), tuple(lines), self.master_sha, tuple(removes))
        )
        for relative, text in texts.items():
            self.files[(sha, relative)] = text
        return sha

    def push(self, branch: str, *, lease: str | None = None) -> None:
        self.events.append("push")
        self.leases.append(lease)
        # The fake leaves `remote_tips` to the test, which moves the tip by hand.
        if lease is not None and self.remote_tips.get(branch) != lease:
            raise GitError("git push -u origin failed: stale info (lease)")
        if self.push_failures > 0:
            self.push_failures -= 1
            raise GitError("git push -u origin failed: scripted")
        self.pushed.append(branch)
        self.pushes.append((branch, lease))

    def remote_tip(self, path: Path, branch: str) -> str | None:
        self.remote_tip_calls.append((path, branch))
        if self.remote_tip_error is not None:
            raise GitError(self.remote_tip_error)
        return self.remote_tips.get(branch)

    def show(self, ref: str, relative: str) -> str | None:
        if (ref, relative) in self.files or not self.track_commits:
            return self.files.get((ref, relative))
        # GH-229: a path a commit did not touch is what the commit before it held.
        for index, commit in enumerate(self.branch_commits):
            if commit.sha == ref:
                if relative in commit.removes:
                    return None
                if index == 0:
                    return None
                return self.show(self.branch_commits[index - 1].sha, relative)
        return None

    def cleanup(self, task_id: str) -> None:
        if self.cleanup_error is not None:
            raise GitError(self.cleanup_error)
        self.cleaned.append(task_id)

    def branch_head(self, branch: str) -> str | None:
        return self.existing_branches[branch][0] if branch in self.existing_branches else None

    def base_of(self, branch: str) -> str | None:
        self.base_of_calls.append(branch)
        return self.existing_branches[branch][1] if branch in self.existing_branches else None

    def worktrees(self, task_id: str) -> list[Path]:
        return list(self.checkouts)

    def prune(self) -> None:
        self.pruned += 1

    # GH-178's merge verb: the pull request's head is always reachable, its diff is
    # `diff_paths`, and every worktree it asks for or removes is kept by name.
    def fetch_commit(self, sha: str) -> bool:
        self.fetched_commits.append(sha)
        return True

    # GH-229: the two-phase reads of a branch's own ADRs, over `branch_commits`.
    def advance_master(self, *files: str) -> str:
        """Master takes a commit that adds `files` (full paths): its new sha."""
        sha = f"master{len(self.master_trees)}"
        self.master_trees[sha] = (*self.master_trees[self.master_sha], *files)
        self.master_sha = sha
        return sha

    def add_branch_commit(
        self,
        *,
        adds: tuple[str, ...] = (),
        lines: tuple[tuple[str, str], ...] = (),
        master: str | None = None,
    ) -> str:
        """The branch commits `adds` and `lines`, on `master`'s tree (now by default)."""
        sha = self._branch_sha()
        on = self.master_sha if master is None else master
        self.branch_commits.append(BranchCommit(sha, adds, lines, on))
        return sha

    def added_adrs(self, master: str, head: str) -> tuple[AddedAdr, ...]:
        self.added_adrs_calls.append((master, head))
        return tuple(
            AddedAdr(path, commit.sha)
            for commit in self.branch_commits
            for path in commit.adds
            if path.startswith("docs/adr/")
        )

    def added_lines(self, master: str, head: str, numbers: Sequence[str]) -> tuple[AddedLine, ...]:
        for number in numbers:
            if not (len(number) == 4 and number.isascii() and number.isdigit()):
                raise ValueError(f"an ADR number is exactly four digits, not {number!r}")
        self.added_lines_calls.append((master, head, tuple(numbers)))
        return tuple(
            AddedLine(path, commit.sha, text)
            for commit in self.branch_commits
            for path, text in commit.lines
            if any(number in text for number in numbers)
        )

    def tree_names(self, ref: str, directory: str) -> tuple[str, ...]:
        self.tree_names_calls.append((ref, directory))
        if ref in self.master_trees:
            held = list(self.master_trees[ref])
        else:
            held = []
            for commit in self.branch_commits:
                if commit.sha == ref:
                    held = [*self.master_trees[commit.master]]
                    for earlier in self.branch_commits:
                        held.extend(earlier.adds)
                        held = [p for p in held if p not in earlier.removes]
                        if earlier.sha == ref:
                            break
                    break
            else:
                raise GitError(f"git ls-tree failed: not a valid object name {ref}")
        names = (path.rpartition("/") for path in held)
        return tuple(sorted(name for parent, _, name in names if parent == directory))

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        self.diff_reads.append((base, head))
        return self.diff_paths

    def detached_checkout(self, name: str, sha: str) -> Path:
        self.detached.append(name)
        return self.ROOT / name

    def remove_checkout(self, name: str) -> None:
        self.removed_checkouts.append(name)

    def branches(self, prefix: str) -> list[str]:
        return [name for name in self.existing_branches if name.startswith(prefix)]

    def rename_branch(self, old: str, new: str) -> None:
        self.renamed.append((old, new))
        if old in self.existing_branches:
            self.existing_branches[new] = self.existing_branches.pop(old)

    def is_ignored(self, root: Path, relative: str) -> bool:
        self.ignored_checks.append((root, relative))
        return relative in self.ignored

    def is_ancestor(self, path: Path, sha: str, ref: str = "HEAD") -> bool:
        self.ancestor_checks.append((path, sha, ref))
        if sha in self.ancestor_errors:
            raise GitError(f"git merge-base --is-ancestor {sha} {ref} failed: scripted")
        if ref in self.ancestors_by_ref:
            return sha in self.ancestors_by_ref[ref]
        return sha in self.ancestors

    def contains(self, head: str, master: str) -> Containment:
        self.contains_calls.append((head, master))
        return self.containment.get((head, master), self.contains_answer)

    def merge_conflicts(self, head: str, master: str) -> bool | None:
        self.conflict_probes.append((head, master))
        return self.conflict_answers.get((head, master))

    def upstream(self) -> Upstream:
        self.upstream_calls += 1
        return self.upstream_answer


class MemoryTracker:
    def __init__(self, url: str = "https://github.com/o/r/pull/7") -> None:
        self.url = url
        # `changed_files`: the count it answers per reference (default 12), the references it
        # was asked for, and an error to raise instead (ADR-0054).
        self.changed_files_count = 12
        self.changed_files_asked: list[str] = []
        self.changed_files_error: str | None = None
        self.pull_requests: list[tuple[str, str, str]] = []
        # Whether each `open_pull_request` call (failed ones counted) asked to close the issue.
        self.closes_issue_calls: list[bool] = []
        self.comments: list[tuple[str, str]] = []
        self.labels: list[tuple[str, str]] = []
        self.unlabeled: list[tuple[str, str]] = []
        # GH-294: the labels the repository has, the ones `ensure_label` created, and the
        # `ValueError` text that makes it fail the way `gh` does (nothing is created).
        self.existing_labels: set[str] = set()
        self.created_labels: list[str] = []
        self.ensure_label_error: str | None = None
        # Bodies of the issue's comments before the run, oldest first.
        self.earlier: list[str] = []
        # `(title, body)` of every issue `open_issue` opened, in order.
        self.opened_issues: list[tuple[str, str]] = []
        self.open_issue_calls = 0
        self.open_issue_fails_on: set[int] = set()
        self.issue_numbers_from: int | None = None
        # GH-239: the `label` of each of them (None: unlabelled), the `(reference, head)` of
        # every `rerun_failed_ci` and the run links it answers, its `ValueError` text and
        # every reference `close_pull_request` closed.
        self.opened_labels: list[str | None] = []
        self.rerun_calls: list[tuple[str, str]] = []
        self.failed_runs: list[str] = []
        self.rerun_error: str | None = None
        self.closed_pull_requests: list[str] = []
        # GH-137: the poller's own candidate pool, and the structured facts per issue
        # number a test scripts with `states`.
        self._queue: list[int] = []
        self.states: dict[int, IssueState] = {}
        # GH-137 blocking finding: a number here makes `issue_state` raise `ValueError`
        # (`gh`'s own failure shape, `GhTracker._gh`) instead of answering, so a
        # blocker predicate's "an error of `gh` counts as unresolved" has a rejected
        # input to reject, not only the happy structured answer `states` gives.
        self.state_errors: set[int] = set()
        # `timestamped_comments`'s own answer, keyed by task id -- separate from
        # `earlier`/`issue_comments`, which stay bodies-only, and from `self.comments`
        # above, which is every comment *this fake posted*, not the issue's own.
        self.timestamped: dict[str, list[IssueComment]] = {}
        # `issue_created_at`'s own answer, keyed by task id (none when absent), and the ids
        # whose read raises `ValueError`, `gh`'s own failure shape.
        self.issue_created: dict[str, str] = {}
        self.issue_created_errors: set[str] = set()
        # The playbook delivery's reads and writes: every PR `pull_requests` lists, and
        # `(number, body)` of each body update.
        self.listed: list[ListedPullRequest] = []
        self.updated_bodies: list[tuple[int, str]] = []
        # GH-178: `pull_request`'s canned answers by reference (the URL the document
        # stores); `head_oid`, when set, is what every answered pull request reports as
        # its head, so a test moves the head without rebuilding the pull request.
        self.prs: dict[str, PullRequest | None] = {}
        self.head_oid: str | None = None
        # `merge`'s own record: `(reference, head_sha)` of every call, the method it answers,
        # and `merge_error`, which makes it raise `ValueError` the way a refused `gh pr
        # merge` does. GH-196: `merge_failure` makes that error a `MergeCallFailed` of that
        # class, the adapter's own shape; with it unset the error stays a plain `ValueError`.
        # `events` is every `pull_request`, `merge` and `comment` call in order.
        self.merge_calls: list[tuple[str, str]] = []
        self.merge_method = "squash"
        self.merge_error: str | None = None
        self.merge_failure: MergeFailure | None = None
        self.events: list[str] = []
        # GH-203: `open_pull_request` raises `ValueError` (`gh`'s own failure shape) for its
        # first N calls, and `comment` raises it for as many as it is told to.
        self.open_failures = 0
        # The body of every `open_pull_request` call, the failed ones too.
        self.attempted_bodies: list[str] = []
        self.open_error = "gh pr create failed: Body is too long"
        self.comment_failures = 0
        # `pull_requests_on`: the pull requests scripted per head branch, `lookup_error`
        # to make it raise `ValueError` (`gh`'s own failure shape), and each branch asked.
        self.on_branch: dict[str, list[BranchPullRequest]] = {}
        self.lookup_error: str | None = None
        self.lookups: list[str] = []
        # Called with the branch after the lookup answers, so a test can move the remote
        # between the read and the push.
        self.on_lookup: Callable[[str], None] | None = None
        # GH-216: `ci_report`'s answer per reference (the PR URL) and `branch_ci`'s per
        # branch. A key that is absent answers `CiUnreadable("gh_unreadable")`, never a
        # green report; `ci_reads` and `branch_reads` are each ask, in order.
        # A list is the answer per round instead: each ask pops the first, the last one
        # stays, and an empty list is unreadable.
        self.ci_reports: dict[str, CiReport | CiUnreadable] | list[CiReport | CiUnreadable] = {}
        self.branch_reports: dict[str, CiReport | CiUnreadable] = {}
        self.ci_reads: list[str] = []
        self.branch_reads: list[str] = []

    def ci_report(self, reference: str) -> CiReport | CiUnreadable:
        self.ci_reads.append(reference)
        if isinstance(self.ci_reports, list):
            if not self.ci_reports:
                return CiUnreadable("gh_unreadable")
            return self.ci_reports.pop(0) if len(self.ci_reports) > 1 else self.ci_reports[0]
        return self.ci_reports.get(reference, CiUnreadable("gh_unreadable"))

    def branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        self.branch_reads.append(branch)
        return self.branch_reports.get(branch, CiUnreadable("gh_unreadable"))

    def pull_request(self, reference: str) -> PullRequest | None:
        pull_request_number(reference)  # the adapter's own strictness: a bad reference raises
        self.events.append("pull_request")
        pr = self.prs.get(reference)
        if pr is not None and self.head_oid is not None:
            return dataclasses.replace(pr, head_oid=self.head_oid)
        return pr

    def merge(self, reference: str, head_sha: str) -> str:
        pull_request_number(reference)  # rejected before anything is recorded, as the adapter does
        self.events.append("merge")
        self.merge_calls.append((reference, head_sha))
        if self.merge_error is not None:
            if self.merge_failure is not None:
                raise MergeCallFailed(self.merge_failure, self.merge_error)
            raise ValueError(self.merge_error)
        return self.merge_method

    def open_pull_request(
        self, task_id: str, branch: str, title: str, body: str, *, closes_issue: bool = True
    ) -> str:
        self.attempted_bodies.append(body)
        self.closes_issue_calls.append(closes_issue)
        if self.open_failures > 0:
            self.open_failures -= 1
            raise ValueError(self.open_error)
        self.pull_requests.append((branch, title, body))
        return self.url

    def open_issue(self, title: str, body: str, *, label: str | None = None) -> str:
        """`open_issue_fails_on` lists the calls (0-based, failed ones counted) that raise
        `ValueError`, `gh`'s own failure shape; `issue_numbers_from` makes each opened issue
        answer its own URL, numbered in order, instead of the shared one (ADR-0054)."""
        call = self.open_issue_calls
        self.open_issue_calls += 1
        if call in self.open_issue_fails_on:
            raise ValueError("gh: could not create the issue")
        self.opened_issues.append((title, body))
        self.opened_labels.append(label)
        if self.issue_numbers_from is None:
            return self.url
        return (
            f"https://github.com/o/r/issues/{self.issue_numbers_from + len(self.opened_issues) - 1}"
        )

    def rerun_failed_ci(self, reference: str, head: str) -> list[str]:
        """GH-239: the runs of `head` that failed, rerun. Scripted by `failed_runs`; a bad
        reference raises before anything is recorded, as the adapter does, and
        `rerun_error` makes it raise `ValueError` (`gh`'s own failure shape)."""
        pull_request_number(reference)
        self.events.append("rerun_failed_ci")
        self.rerun_calls.append((reference, head))
        if self.rerun_error is not None:
            raise ValueError(self.rerun_error)
        return list(self.failed_runs)

    def close_pull_request(self, reference: str) -> None:
        pull_request_number(reference)
        self.events.append("close_pull_request")
        self.closed_pull_requests.append(reference)

    def changed_files(self, pull_request: str) -> int:
        pull_request_number(pull_request)
        self.changed_files_asked.append(pull_request)
        if self.changed_files_error is not None:
            raise ValueError(self.changed_files_error)
        return self.changed_files_count

    def comment(self, task_id: str, text: str) -> None:
        self.events.append("comment")
        if self.comment_failures > 0:
            self.comment_failures -= 1
            raise ValueError("gh issue failed: scripted")
        self.comments.append((task_id, text))

    def label(self, task_id: str, label: str) -> None:
        self.labels.append((task_id, label))

    def unlabel(self, task_id: str, label: str) -> None:
        self.unlabeled.append((task_id, label))

    def ensure_label(self, name: str) -> None:
        if self.ensure_label_error is not None:
            raise ValueError(self.ensure_label_error)
        if name not in self.existing_labels:
            self.existing_labels.add(name)
            self.created_labels.append(name)

    def list_pull_requests(self) -> list[ListedPullRequest]:
        return list(self.listed)

    def pull_requests_on(self, branch: str) -> list[BranchPullRequest]:
        self.lookups.append(branch)
        if self.lookup_error is not None:
            raise ValueError(self.lookup_error)
        if self.on_lookup is not None:
            self.on_lookup(branch)
        return list(self.on_branch.get(branch, []))

    def update_pull_request_body(self, number: int, body: str) -> None:
        self.updated_bodies.append((number, body))

    def issue_comments(self, task_id: str) -> list[str]:
        return [*self.earlier, *(text for _, text in self.comments)]

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        return self.timestamped.get(task_id, [])

    def issue_created_at(self, task_id: str) -> str | None:
        if task_id in self.issue_created_errors:
            raise ValueError(f"gh issue view {task_id} failed: scripted")
        return self.issue_created.get(task_id)

    def queue(self) -> list[int]:
        return sorted(self._queue)

    def issue_state(self, number: int) -> IssueState:
        if number in self.state_errors:
            raise ValueError(f"gh issue view {number} failed: scripted")
        return self.states[number]


class MemoryRunRegistry:
    """GH-137: no real pid, no real file. `live` answers whatever a test pre-loads in
    `entries`, since this fake never actually forks a second process to be alive."""

    def __init__(self, entries: list[RunRegistration] | None = None) -> None:
        self.entries: list[RunRegistration] = list(entries or [])
        self.registered: list[tuple[str, str]] = []
        self.registered_kinds: list[rules.RunKind] = []
        self.unregistered = 0
        self.exclusive_entries = 0
        self.attempts: dict[tuple[str, int], LaunchAttempt] = {}
        self.cleared: list[tuple[str, int]] = []
        # GH-177: what each root published, and every call, so a test can assert none
        # happened (pacing off) or read what a round handed over.
        self.published: dict[Path, list[rules.SpendRun]] = {}
        self.publish_calls: list[tuple[Path, str, list[rules.SpendRun], datetime]] = []
        self.spend_calls: list[datetime] = []
        self.spend_notes: list[str] = []

    def register(self, product: str, task_id: str, kind: rules.RunKind = "role") -> None:
        self.registered.append((product, task_id))
        self.registered_kinds.append(kind)

    def unregister(self) -> None:
        self.unregistered += 1

    def live(self) -> list[RunRegistration]:
        return list(self.entries)

    @contextlib.contextmanager
    def exclusive(self) -> Iterator[None]:
        self.exclusive_entries += 1
        yield

    def launch_attempts(self, product: str, issue: int) -> LaunchAttempt | None:
        return self.attempts.get((product, issue))

    def record_launch(self, product: str, issue: int, when: datetime) -> None:
        existing = self.attempts.get((product, issue))
        prior = existing.prior if existing is not None else 0
        self.attempts[(product, issue)] = LaunchAttempt(when, prior + 1)

    def clear_launch(self, product: str, issue: int) -> None:
        self.attempts.pop((product, issue), None)
        self.cleared.append((product, issue))

    def publish_spend(
        self, root: Path, product: str, runs: Sequence[rules.SpendRun], now: datetime
    ) -> list[str]:
        self.publish_calls.append((root, product, list(runs), now))
        self.published[root] = rules.merged_spend(self.published.get(root, []), runs, now)
        return []

    def spend(self, since: datetime) -> tuple[list[rules.SpendRun], list[str]]:
        self.spend_calls.append(since)
        runs = [r for mine in self.published.values() for r in mine if r.ended_at >= since]
        return runs, list(self.spend_notes)


class FakeReaper:
    """GH-282: records every reap it was asked for, as `(scope, task_id, run, token)`, and
    answers the tuple it was configured with, the same for every scope, never touching a
    process. `reaped` is the answer of `reap_task`, `reaped_runs` of `reap_run` and
    `reaped_attempts` of `reap_attempt`; each defaults to nothing reaped."""

    def __init__(
        self,
        reaped: tuple[ReapedProcess, ...] = (),
        *,
        reaped_runs: tuple[ReapedProcess, ...] = (),
        reaped_attempts: tuple[ReapedProcess, ...] = (),
    ) -> None:
        self.reaped = reaped
        self.reaped_runs = reaped_runs
        self.reaped_attempts = reaped_attempts
        self.calls: list[tuple[str, str, int | None, str | None]] = []

    def reap_task(self, task_id: str) -> tuple[ReapedProcess, ...]:
        self.calls.append(("task", task_id, None, None))
        return self.reaped

    def reap_run(self, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
        self.calls.append(("run", task_id, run, None))
        return self.reaped_runs

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        self.calls.append(("attempt", task_id, run, token))
        return self.reaped_attempts


class FakeLauncher:
    """GH-137: records every `argv` a poller would have spawned, in order, instead of
    ever calling `_spawn_detached`. `fails`, when given, is the set of 0-based call
    indices (counting every `spawn`, including the ones that fail) that raise
    `FileNotFoundError` instead of returning a pid -- `poll.execute`'s own `except
    OSError` seam (GH-140), never added to `spawned` since a real `Popen` never got the
    chance to run either."""

    def __init__(self, pid: int = 4242, fails: set[int] | None = None) -> None:
        self.pid = pid
        self.spawned: list[list[str]] = []
        self._fails = fails or set()
        self._calls = 0

    def spawn(self, argv: list[str]) -> int:
        index = self._calls
        self._calls += 1
        if index in self._fails:
            raise FileNotFoundError(2, "No such file or directory")
        self.spawned.append(list(argv))
        return self.pid


def memory_ports(
    runner: RoleRunner | None = None,
    *,
    documents: TaskDocuments | None = None,
    knowledge: ProductKnowledge | None = None,
    prompts: Prompts | None = None,
    flags: FlagVocabulary | None = None,
    inbox: Inbox | None = None,
    workspace: Workspace | None = None,
    tracker: Tracker | None = None,
    stack: ProductStack | None = None,
    commands: Commands | None = None,
    sample_app: SampleApp | None = None,
    reaper: ProcessReaper | None = None,
    limits: RunLimits | None = None,
) -> Ports:
    return Ports(
        runner=runner if runner is not None else ScriptedRunner(),
        documents=documents if documents is not None else MemoryDocuments(),
        knowledge=knowledge if knowledge is not None else MemoryKnowledge(),
        prompts=prompts if prompts is not None else MemoryPrompts(),
        flags=flags if flags is not None else MemoryFlags(),
        inbox=inbox if inbox is not None else MemoryInbox(),
        workspace=workspace if workspace is not None else MemoryWorkspace(),
        tracker=tracker if tracker is not None else MemoryTracker(),
        stack=stack if stack is not None else MemoryStack(),
        commands=commands if commands is not None else MemoryCommands(),
        sample_app=sample_app if sample_app is not None else MemorySampleApp(),
        reaper=reaper if reaper is not None else FakeReaper(),
        **({} if limits is None else {"limits": limits}),
    )


# Recorded role outputs, as the harness would hand them back.

# A capability file as PM must return it: it opens with the jobs it serves.
REPORTS = "# Reports\n\n## Jobs\n\n- When I audit, I want CSV, so I can file it.\n\nCSV export."


def questions(
    n: int = 1,
    done: bool = False,
    cost: float = 0.05,
    session: str = "q",
    invariants: Sequence[object] = (),
) -> RawResult:
    text = "### Closing\n\nDecided." if done else f"❓ **Q{n}** - **Scope**: which?\n\n➡️ a"
    return RawResult(
        {"markdown": text, "done": done, "invariants": list(invariants)}, cost, 2, session
    )


def answers(
    n: int = 1,
    for_human: Sequence[object] = (),
    withdrawn: Sequence[object] = (),
    cost: float = 0.1,
    session: str = "a",
) -> RawResult:
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


def tech_answers(
    n: int = 1, for_pm: Sequence[object] = (), cost: float = 0.1, session: str = "t"
) -> RawResult:
    return RawResult(
        {"markdown": f"**Q{n}**: accepted", "questions_for_pm": list(for_pm)}, cost, 1, session
    )


def consulted(
    n: int = 1,
    for_human: Sequence[object] = (),
    withdrawn: Sequence[object] = (),
    definitions: Sequence[object] | None = None,
    glossary: Sequence[object] | None = None,
    cost: float = 0.15,
    session: str = "pm",
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


def planned(
    *subtasks: dict[str, object],
    cost: float = 0.3,
    session: str = "tl",
    invariants: Sequence[object] = (),
    integration: str = "",
    estimated_cost_usd: float = 20.0,
    estimated_files: int = 5,
) -> RawResult:
    items: list[dict[str, object]] = [
        {"id": "api", "title": "API", "brief": "Build it.", "seams": ["GET /x"]},
        *subtasks,
    ]
    markdown = "### Solution\n\nHex.\n\n### Subtasks\n\n" + "\n\n".join(
        f"#### {i['id']}\n\n{i['brief']}" for i in items
    )
    return RawResult(
        {
            "markdown": markdown,
            "subtasks": items,
            "invariants": list(invariants),
            "integration": integration,
            "estimated_cost_usd": estimated_cost_usd,
            "estimated_files": estimated_files,
        },
        cost,
        2,
        session,
    )


def planned_parts(
    *parts: dict[str, object],
    cost: float = 0.3,
    session: str = "tl",
    invariants: Sequence[object] = ("Every part ships alone.",),
    estimated_cost_usd: float = 400.0,
    estimated_files: int = 90,
) -> RawResult:
    """A Tech Lead plan that does not fit (ADR-0054): parts and no subtasks. With no part
    given, two chained ones."""
    items: list[dict[str, object]] = list(parts) or [
        {"title": "Catalogue", "body": "Extract the strings.", "depends_on": []},
        {"title": "Screens", "body": "Translate the screens.", "depends_on": [1]},
    ]
    markdown = "### Solution\n\nCut it.\n\n### Parts\n\n" + "\n\n".join(
        f"#### {i['title']}\n\n{i['body']}" for i in items
    )
    return RawResult(
        {
            "markdown": markdown,
            "subtasks": [],
            "parts": items,
            "invariants": list(invariants),
            "integration": "",
            "estimated_cost_usd": estimated_cost_usd,
            "estimated_files": estimated_files,
        },
        cost,
        2,
        session,
    )


def delivered(summary: str = "done", cost: float = 0.5, session: str = "dev") -> RawResult:
    return RawResult(
        {"markdown": "### Changes\n\nx.py", "summary": summary, "assumptions": []},
        cost,
        10,
        session,
    )


def finding(
    severity: str = "blocking",
    category: str = "spec-behaviour",
    claim: str = "wrong",
    scope: str = "subtask",
) -> dict[str, str]:
    return {
        "location": "x.py:1",
        "claim": claim,
        "evidence": "$ pytest tests/test_x.py\nFAILED",
        "severity": severity,
        "scope": scope,
        "category": category,
    }


def reviewed(
    kind: str = "approved",
    reason: str = "fine",
    cost: float = 0.2,
    category: str = "spec-behaviour",
    findings: list[dict[str, str]] | None = None,
    settled: Sequence[object] = (),
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


def classified(type_: str = "feature", confidence: str = "high", cost: float = 0.01) -> RawResult:
    return RawResult(
        {"markdown": "Clear.", "type": type_, "confidence": confidence, "reason": "r"}, cost
    )


def defined(n: int = 1, cost: float = 0.1) -> RawResult:
    return RawResult(
        {
            "markdown": f"### Behaviour\n\nv{n}\n\n### For the next role\n\nDefinition v{n}.",
            "flags": {"touches_ui": False, "touches_copy": True},
        },
        cost,
    )


def verdict(
    kind: str,
    reason: str = "r",
    definitions: Sequence[object] | None = None,
    glossary: Sequence[object] | None = None,
    cost: float = 0.2,
    flags: dict[str, bool] | None = None,
) -> RawResult:
    if definitions is None:
        definitions = [{"capability": "reports", "content": REPORTS}] if kind == "approved" else []
    return RawResult(
        {
            "markdown": f"### Verdict\n\n{kind}\n\n### For the next role\n\n{reason}",
            "verdict": kind,
            "reason": reason,
            "flags": {"touches_ui": False, "touches_copy": False} if flags is None else flags,
            "definitions": definitions,
            "glossary": glossary or [],
        },
        cost,
    )


# The design branch's own roles (ADR-0040): UX Designer/UI Designer write a plain
# `DesignDraft` (nothing but `markdown`); UX Lead/UI Lead write a `DesignReview`
# (a verdict plus every finding); PM's consolidation writes a `Consolidation` (a
# verdict, the questions UX's blocking findings become, and the rest as observations).


def design_draft(markdown: str = "### Flow\n\nUnchanged.", cost: float = 0.1) -> RawResult:
    return RawResult({"markdown": markdown}, cost)


def design_findings(
    kind: str, reason: str = "r", items: Sequence[object] | None = None, cost: float = 0.1
) -> RawResult:
    return RawResult(
        {
            "markdown": f"### Walk\n\nOk.\n\n### Verdict\n\n{kind}\n\n{reason}",
            "verdict": kind,
            "reason": reason,
            "items": items or [],
        },
        cost,
    )


def final_validation(
    kind: str = "approved",
    reason: str = "r",
    blocking: Sequence[object] | None = None,
    cost: float = 0.2,
) -> RawResult:
    """PM's early, UI-only final validation (ADR-0040): same `verdict`/`reason` shape,
    `blocking` in place of `definitions`/`observations` -- one-line claims the
    integration review must settle, empty on `approved`."""
    return RawResult(
        {
            "markdown": f"### Verdict\n\n{kind}\n\n{reason}",
            "verdict": kind,
            "reason": reason,
            "blocking": list(blocking or []),
        },
        cost,
    )


def consolidation(
    kind: str = "approved",
    reason: str = "r",
    questions: Sequence[object] | None = None,
    observations: Sequence[object] | None = None,
    cost: float = 0.2,
) -> RawResult:
    return RawResult(
        {
            "markdown": f"### Verdict\n\n{kind}\n\n{reason}\n\n### PRD\n\nConsolidated.",
            "verdict": kind,
            "reason": reason,
            "questions_for_human": list(questions or []),
            "observations": observations or [],
        },
        cost,
    )


# The stack review path (ADR-0034, issue #40): the author's own proposal.


def stack_proposal(
    verdict_text: str = "keep the stack",
    stack_diff: str = "- a diff",
    standards: str = "## Engineering standards\n\nHex.",
    adr_draft: str = "an ADR draft",
    cost: float = 0.3,
    session: str = "stack",
) -> RawResult:
    return RawResult(
        {
            "markdown": "### Verdict\n\nDecided.",
            "verdict": verdict_text,
            "stack_diff": stack_diff,
            "standards": standards,
            "adr_draft": adr_draft,
        },
        cost,
        2,
        session,
    )


# The bug path (issue #65): the Product Analyst's diagnosis and PM's ruling on it.


def diagnosed(
    ruling: str = "bug",
    capability: str = "dashboard",
    for_pm: Sequence[object] = (),
    cost: float = 0.1,
) -> RawResult:
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
    kind: str = "approved",
    ruling: str = "bug",
    reason: str = "r",
    definitions: Sequence[object] | None = None,
    glossary: Sequence[object] | None = None,
    cost: float = 0.2,
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
