"""What the use cases need from the outside, as protocols (ADR-0007).

Each port has one implementation today, over the product directory or the Claude
Agent SDK. They exist to keep the rules of the process out of the disk and the
harness, not as the product adapter: that one is now extractable, because the
second product arrived and it is this repo (README.md, 8; ADR-0017).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from ..domain.ci import CiReport, CiUnreadable, Containment
from ..domain.design import Design
from ..domain.performance import Run, Scenario
from ..domain.playbook import Playbook
from ..domain.presubmit import CheckResult
from ..domain.reaping import ReapedProcess
from ..domain.roles import Role
from ..domain.rules import LandWait, MergeFailure, RunKind, SpendRun, motive
from ..domain.stall import ChildActivity
from ..domain.suppressions import Forbidden
from ..domain.task import (
    DependencyState,
    FailureReason,
    MergeOutcome,
    PullRequest,
    TaskInput,
    TaskState,
)
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
    # Token usage of the delivering `ResultMessage`, normalised to the closed `kind`
    # set `agentic.role.tokens` groups by (`input`, `output`, `cache_read`,
    # `cache_write`) off the SDK's own `input_tokens`, `output_tokens`,
    # `cache_read_input_tokens`, `cache_creation_input_tokens`. Scoped to this one
    # `query()` call, never the Claude Code session's cumulative total across an
    # earlier call it resumed: a probe that resumed a session across two separate
    # `query()` calls showed the second call's `usage` carrying only what it itself
    # spent, not the first call's added in (observability/README.md, 14), so a retry's
    # or a cross-node resume's first round needs no baseline subtracted from it. Zeroed
    # (all four kinds present, at 0), never omitted, when the SDK reports none.
    usage: dict[str, int] = field(default_factory=dict)
    # Calls to the model in this run: one per `AssistantMessage` message the runner saw
    # before the delivery — the SDK does not otherwise distinguish a "call" from a
    # "turn" (`num_turns`, which counts turns, not calls, and the two differ on a run
    # with sub-agents in the foreground).
    calls: int = 0
    # `tool_use` blocks of this run, by tool name, counted the same way; a name outside
    # the known set (Bash, Read, Edit, Write, Grep, Glob, Agent, Skill,
    # StructuredOutput) counts as `other`.
    tool_calls: dict[str, int] = field(default_factory=dict)
    # Write attempts the SDK's permission layer denied during this run, for a role that
    # does not execute (glossary: Negación; `claude_sdk.runner._deny_writes`). `0` for a
    # role that executes, which never gets the callback wired at all.
    denials: int = 0
    # Whether the live turn-budget notice (`domain/turn_notice.py`; ADR-0004, seventh
    # addendum) fired during this run: the `PostToolUse` hook `hooks_for` wires for a
    # role `turn_notice.margin_for` covers crossed its close-out margin. `False` for
    # every other role, which never gets that hook wired at all (the same shape
    # `denials` has for a role that executes).
    turn_notice: bool = False
    # How many times the notice fired during this run (`domain/turn_notice.py`; ADR-0004,
    # seventh addendum, revised by its 2026-09-22 addendum): a fact beside `turn_notice`,
    # never replacing it (ADR-0008: added, never renamed). `0` for every run `turn_notice`
    # is `False` for, and for every run recorded before this field existed.
    turn_notice_firings: int = 0
    # `tool_calls[DELIVERY_TOOL]` (glossary: Emisión de entrega), exposed as its own
    # field because an attempt that never delivers still needs the number: `tool_calls`
    # itself is only returned on the happy path, while this travels through
    # `AttemptFailed` too (`ClaudeSdkRunner.run_once`'s own local counter, read before
    # every one of its six raise sites). `0` means this attempt saw no call to the
    # delivery tool at all, never "unknown".
    deliveries: int = 0
    # Denials the presubmit itself issued during this run (glossary: Puerta de
    # entrega), off the same closure shape `denials` has for `_deny_writes`
    # (`hooks._presubmit`'s own `on_denied`). A different fact from `deliveries`: a
    # presubmit denial is the gate working as designed, never a re-emission of an
    # invalid shape, and a different fact from `denials`, which is the SDK's own
    # permission layer denying a role that does not execute. `0` for every run whose
    # presubmit never refused, which is every run with no presubmit wired at all.
    presubmit_denials: int = 0
    # `TurnCount.value` at the end of the attempt (`domain/turn_notice.py`'s own counted
    # quantity, ADR-0031, issue #117): the main-thread `AssistantMessage` count the
    # notice's positions are stated in. `0` for a role the notice does not cover, since
    # the runner never wires the counter for one. Written on every attempt, cut as well
    # as delivering (`ClaudeSdkRunner.run_once`'s six raise sites read the same
    # `turns.value` this field carries), because a cut is the population that can
    # refute the assumption `domain/turn_notice.py`'s docstring states: an attempt cut
    # by `max_turns` whose recorded `main_thread_turns` sits below that role's own
    # `max_turns` refutes "count >= consumed turns".
    main_thread_turns: int = 0
    # The processes the reaper stopped when this attempt's session ended (GH-282,
    # ADR-0052): what a role left running in the background. Empty when there were none,
    # which is nearly every attempt, and a cancelled attempt has no result to carry it.
    reaped: list[ReapedProcess] = field(default_factory=list)


class AttemptFailed(Exception):
    """The attempt broke in the harness: an exception, `max_turns` exhausted, the money
    cap hit, no result. The use case decides whether to retry (README.md, 3), and it
    resumes `session_id` when the attempt left one: what the model read and did is
    still there (ADR-0004, addendum).

    `reason`, from the closed set `FailureReason`, is the raise site's own call, never
    guessed later by parsing `error`: it is what `agentic.role.failures` groups by.

    `error` passes through `rules.motive` before it becomes the exception's own message
    (`str(exc)`, what `run_with_retry` records as the attempt's `Failure.error` and what
    a second failure's `RoleFailure` carries verbatim): the choke point every raise site
    inherits instead of building the guarantee itself (issue #66) — a motive here never
    has an empty or tautological diagnostic part, whatever the site interpolated."""

    def __init__(
        self,
        error: str,
        cost_usd: float = 0.0,
        session_id: str = "",
        reason: FailureReason = "other",
        denials: int = 0,
        turn_notice: bool = False,
        turn_notice_firings: int = 0,
        deliveries: int = 0,
        presubmit_denials: int = 0,
        main_thread_turns: int = 0,
        reaped: Sequence[ReapedProcess] = (),
    ):
        super().__init__(motive(error, reason))
        self.cost_usd = cost_usd
        self.session_id = session_id
        self.reason = reason
        # Write attempts the SDK denied before this attempt broke (glossary: Negación);
        # `0` when the raise site had none to report, which is every site but the
        # runner's own (`RawResult.denials`' own docstring).
        self.denials = denials
        # Whether the live turn-budget notice fired before this attempt broke
        # (`RawResult.turn_notice`'s own docstring); `False` at every raise site but
        # the runner's own.
        self.turn_notice = turn_notice
        # How many times the notice fired before this attempt broke (`RawResult.
        # turn_notice_firings`'s own docstring); `0` at every raise site but the
        # runner's own.
        self.turn_notice_firings = turn_notice_firings
        # Calls to the delivery tool this attempt saw before it broke
        # (`RawResult.deliveries`' own docstring); `0` means none were seen, never
        # "unknown" -- every one of the runner's six raise sites sets it from the same
        # local counter, so no site defaults it by omission.
        self.deliveries = deliveries
        # Presubmit denials this attempt saw before it broke (`RawResult.presubmit_
        # denials`'s own docstring); `0` at every raise site but the runner's own.
        self.presubmit_denials = presubmit_denials
        # `TurnCount.value` at the point this attempt broke (`RawResult.main_thread_
        # turns`'s own docstring); `0` for a role the notice does not cover, and at
        # every raise site but the runner's own. Set at every one of the runner's six
        # raise sites, not only the successful return, because a cut is the population
        # that can refute the counted-quantity assumption `domain/turn_notice.py`
        # states.
        self.main_thread_turns = main_thread_turns
        # What the reaper stopped when this attempt's session ended (`RawResult.reaped`'s
        # own docstring); empty at every raise site but the runner's own.
        self.reaped = list(reaped)


class Presubmit(Protocol):
    """Asked when a role hands back its delivery, before the delivery counts: a reason
    refuses it, and the model gets the reason and delivers again (glossary: Puerta de
    entrega). Blocking: it may run the product's checks, within `timeout_s`."""

    timeout_s: float

    def __call__(self) -> str | None: ...


class CheckTool(Protocol):
    """The Developer's `run_check` tool (ADR-0065, GH-359): given the name the model chose,
    runs that declared check through the system and answers with the text the model reads.
    Blocking, within `timeout_s` for one call; the harness runs it off the event loop.
    `names` are the declared checks, all of them."""

    names: tuple[str, ...]
    timeout_s: float

    def __call__(self, name: str) -> str: ...


class SchemaGuard(Protocol):
    """Asked with the model's own `tool_input` before its structured-output call even
    reaches the CLI's own rejection (glossary: Guarda de esquema; ADR-0011, fifth
    addendum: a `PreToolUse` on that tool sees the exact same input the CLI's schema
    check and, downstream, `schemas.validate` would). Denies with `schemas.validate`'s
    own reason -- the only source of validity in the whole mechanism, never a second
    opinion this decides on its own -- so the model reads the missing keys inside the
    same turn instead of the CLI's generic message or nothing at all.

    Not `Presubmit`: that door is commit, clean tree, product checks, and this one
    never looks at any of it. `refusals` is the instance's own count, capped by the
    implementation (`RoleSchemaGuard`, `domain.schema_guard.REFUSALS`) the same way
    `CheckoutPresubmit.refusals` caps the Puerta de entrega's."""

    refusals: int

    def __call__(self, tool_input: dict[str, Any]) -> str | None: ...


class RoleRunner(Protocol):
    """Runs one attempt of a role and hands back what the model produced, raw.

    What the role may do (tools, permission rules, skills, whether it executes) is
    read off the `Role` itself. `resume` continues an earlier session of the same
    role: an interview is one session per participant (ADR-0014). `max_budget_usd`
    caps the money of this one run; `None` is no cap. `presubmit`, when given, is asked
    each time the model hands back its delivery, and refuses it with a reason. `guard`,
    when given, is asked with that same call's own `tool_input` before the presubmit,
    and may deny it first (`SchemaGuard`'s own docstring). `forbidden`, when given, is
    the product's own declared suppressions (issue #38): an Edit or a Write that adds
    one of them is denied with that rule's own `reason`, never the system's words.
    `None` or empty is the explicit no-op -- the cage is installed and denies nothing,
    the same shape `presubmit`/`guard` absent already have. `check_tool`, when given,
    is the `run_check` tool the Developer and its integrator may call (ADR-0065); every
    other role runs with no tool beyond its built-ins.

    `owner_task` is the parent task's id, never a subtask's (GH-282, ADR-0052): the mark
    every process of this attempt carries, so its leftovers can be found and stopped when
    the session ends."""

    async def run_once(
        self,
        role: Role,
        prompt: str,
        *,
        system_prompt: str,
        model: str,
        json_schema: dict[str, Any],
        cwd: Path,
        owner_task: str,
        resume: str | None = None,
        max_budget_usd: float | None = None,
        presubmit: Presubmit | None = None,
        guard: SchemaGuard | None = None,
        forbidden: Sequence[Forbidden] | None = None,
        check_tool: CheckTool | None = None,
    ) -> RawResult: ...


class TaskDocuments(Protocol):
    """The single per-task document (ADR-0001), stored in the product repo."""

    def save(self, state: TaskState) -> Path: ...

    def path_for(self, task_id: str) -> Path: ...

    def ids(self) -> list[str]:
        """Every task id with a document on disk, sorted -- so two sweeps over one
        corpus print the same order (Tech Lead plan, subtask `close-all`). `close
        --all` reads this instead of globbing `.agentic/tasks/` itself: the adapter
        already owns `document.relative_path`, and a glob in the composition root
        would put the document layout in two modules."""
        ...

    def ids_touched_since(self, since: datetime) -> list[str]:
        """The ids of the documents modified at or after `since` (file `mtime`, read
        before anything is parsed), sorted: what a poller reads to learn the recent
        spend without parsing the whole corpus (GH-177). Inside this port's own
        documents directory only."""
        ...

    def load(self, task_id: str) -> dict | None:
        """The document as its frontmatter and sections say, raw: what the system records
        beyond the state (plugins, system commit) comes along. None when there is none."""
        ...

    def write_into(self, task_id: str, root: Path) -> Path | None:
        """Write the task's document into the checkout at `root`, without touching
        wherever `save` itself keeps it (ADR-0011, sixth addendum: archiving commits
        the document on the archived task branch, never the product's main checkout).
        The path it wrote, or None when the task has no document to write."""
        ...

    def record_closure(self, task_id: str, values: Mapping[str, object]) -> Path:
        """Line-level edit of the closure's own top-level frontmatter keys
        (`document.record_closure`'s own docstring) into the document already on disk.
        Never goes through `save`, which re-renders `system_commit` from today's sha
        and `plugins` from today's submodule and would falsify the provenance of a
        finished Corrida (ADR-0001). Does not commit anything."""
        ...


class ProductKnowledge(Protocol):
    """The consolidated knowledge of the product (ADR-0006), stored with the human docs.

    What the knowledge must look like (capability names, what an approval carries)
    is `core.knowledge`'s rule, so it arrives through the port: `approval_check`
    is the check PM's output must pass, `consolidate` writes what an approved verdict
    leaves (ADR-0010)."""

    def read(self, root: Path | None = None) -> str:
        """The knowledge as found under `root`: the task's checkout, where the interview
        may already have edited the glossary (ADR-0014). The product root by default.
        What always goes whole, plus the area index (ADR-0001 of the knowledge module);
        the files that index points at are `index_files`. Side-effect free."""
        ...

    def index_files(self, root: Path | None, families: Collection[str]) -> dict[str, str]:
        """The files the index of `read` points at, for the criteria families given:
        path relative to `root` -> text, all under `.agentic/index/`. Pure;
        `Workspace.write_index` is who puts them on disk."""
        ...

    def design(self, root: Path | None = None) -> str:
        """The product's design standards (ADR-0055), whole under their heading, or "" when
        the product has no `DESIGN_STANDARDS.md`. There is no default."""
        ...

    def writing(self, root: Path | None = None) -> str:
        """The product's writing standards, whole under their heading, or "" when the product
        has no `WRITING_STANDARDS.md`. There is no default."""
        ...

    def criteria_files(self) -> tuple[str, ...]:
        """The names of the criteria documents the product owns, at its root, that the
        line cap measures. The process side asks and never spells them (ADR-0010)."""
        ...

    def over_cap(self, texts: dict[str, str]) -> list[str]:
        """One message per criteria document past the line cap. `texts` is file name ->
        text and holds only files the product has."""
        ...

    def regressions(self, base_texts: dict[str, str], head_texts: dict[str, str]) -> list[str]:
        """One message per criteria document over the line cap at the head and longer than
        at the base (GH-334). Both are file name -> text and hold only files that exist
        there; a file absent at the base counts 0."""
        ...

    def standards(self, root: Path | None = None, whole: bool = True) -> str:
        """The engineering standards (ADR-0012). `whole` is the caller's choice: every
        verifier reads them whole, the Developer and the integrator through the index
        (`index_files` carries the files that index points at)."""
        ...

    def brief(self, root: Path | None = None) -> str | None:
        """The product's `BRIEF.md`, or None when it has not written one yet
        (ADR-0025). Text only -- no verdict crosses this port; `cli.py` is the one
        module that joins this with `core.knowledge.domain.knowledge.brief_refusal`."""
        ...

    def approval_check(self, task_type: str) -> Callable[[dict], None]: ...

    def clarification_check(self) -> Callable[[dict], None]: ...

    def consolidate(self, verdict: dict, root: Path | None = None) -> list[Path]: ...


class Prompts(Protocol):
    """A role's prompt, raw, and where it came from: overlay or default (ADR-0005)."""

    def raw(self, role: Role) -> tuple[str, Path]: ...

    def playbook(self, role: Role) -> Playbook:
        """The role's playbook (ADR-0043): empty for a role the table does not name or a
        product that has none; a malformed one raises `ValueError`."""
        ...


class FlagVocabulary(Protocol):
    def load(self) -> dict[str, str]: ...


class ProductStack(Protocol):
    """How the product declares its code is built and verified (`core.knowledge`), as of
    one of its commits: the task's base, never the branch's. Its name is the exception,
    read at HEAD, because it says who the repository is and not what held at a commit."""

    def checks(self, revision: str) -> dict[str, str]:
        """Check name -> command, as the product's commit `revision` declares them."""
        ...

    def setup(self, revision: str) -> dict[str, str]:
        """Setup step -> command, run once to prepare a fresh checkout. Empty when the
        product declares none, which is a product that prepares nothing."""
        ...

    def witness(self, revision: str) -> str | None:
        """The command with which the product says where the code its checks just
        measured lives (`core.knowledge`, glossary: Testigo del checkout), run by the
        presubmit before them. Optional: `None` when the product declares none, which
        is the presubmit of today, byte for byte unchanged."""
        ...

    def parallel_checks(self, revision: str) -> bool:
        """Whether the product lets the presubmit run its declared checks concurrently
        (`core.knowledge`, ADR-0044). Opt-in: `False` when the product declares nothing,
        which is the serial presubmit of today."""
        ...

    def affected_checks(self, revision: str) -> dict[str, str]:
        """Check name -> its affected-only command (`core.knowledge`, ADR-0046), carrying
        the literal `{base}` token. Empty when the product declares none, which is the
        gate of today, byte for byte. Never read by the merge."""
        ...

    def affected_fallback(self, revision: str) -> tuple[str, ...]:
        """The globs whose change forces the full form of every check (ADR-0046). Empty
        when the product declares none."""
        ...

    def delivery_checks(self, revision: str) -> tuple[str, ...]:
        """The names of the checks the delivery gate runs, the fast tier, in the file's
        order (`core.knowledge`), as the commit `revision` declares them. Empty when the
        product declares no tiers, which is the gate of today, byte for byte."""
        ...

    def forbidden(self, revision: str) -> Sequence[Forbidden]:
        """The suppressions this product forbids a Developer's Edit or Write from
        adding (`core.knowledge`, issue #38), in file order: first declared rule wins.
        Empty when the product declares none, which is legitimate -- a Go product, or
        one that genuinely forbids nothing -- and not an error, the same shape
        `setup`'s absence already has."""
        ...

    def ci(self, revision: str) -> tuple[str, ...]:
        """The check-run names the product declares as its GitHub CI merge gate
        (`core.knowledge`, GH-216, ADR-0045), as the commit `revision` declares them.
        Opt-in: empty when the key is absent, which is the local-check path of today
        byte for byte. Raises `ValueError` when the key is malformed."""
        ...

    def product(self, revision: str = "HEAD") -> str:
        """The name the product goes by, the label every span and metric of a run over it
        carries (issue #47)."""
        ...

    def design(self, revision: str) -> Design | None:
        """The optional `design` block (ADR-0040, `core.knowledge`, glossary: Diseño
        del producto): the static prototype, its entry screen, and the optional
        `sample_app`/`compare` commands a design role reaches instead of a browser or
        an arbitrary URL. `None` when the product declares none, which is every
        product that has not handed the human a prototype yet -- never an error."""
        ...

    def performance(self, revision: str) -> dict[str, Scenario]:
        """Scenario name -> its `seed`, `measure`, volume and limits (`core.knowledge`,
        GH-318, ADR-0059), as the commit `revision` declares them, in file order. Empty when
        the key is absent, which is every product that has not declared a budget. Read only
        by `agentic perf`: never part of the checks, the presubmit or the merge gate. Raises
        `ValueError` when the key is malformed."""
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

    def open_issues(self) -> list[tuple[str, str]]:
        """Every open issue of the product repo, as `(url, body)` (ADR-0034): the read
        side of the stack review's own suppression, over a bounded `gh issue list`.
        Never filtered by a marker on the way in -- `domain.stack_review.open_proposal`
        does that locally, as a plain substring, because GitHub's own `--search` index
        is eventually consistent and does not treat the marker's punctuation as
        literal."""
        ...

    def open_issue_bodies(self) -> list[tuple[str, str]]:
        """Every open issue as `(url, body)` (GH-239): the landing's own name for the read
        `open_issues` makes, kept apart so the deduplication of its follow-up and flake
        issues does not depend on the stack review's verb. The caller matches its marker
        locally, as a plain substring."""
        ...


@dataclass(frozen=True)
class SampleAppHandle:
    """What `SampleApp.start` hands back on success (ADR-0040): the first non-empty
    lines the command printed within `design.SAMPLE_APP_READY_S`, for UX Lead's final
    walk to read verbatim, and a `token` opaque to everyone but the adapter that must
    stop it -- the domain never inspects it, only ever passes it back to `stop`."""

    lines: tuple[str, ...]
    token: Any


class SampleApp(Protocol):
    """The product's `sample_app` command (ADR-0040, glossary: App de muestra), as a
    long-lived process the system owns for the length of one `final_validation` node,
    never a role: no role gets a browser, a server or a network tool (§8). The shell
    adapter starts its own process group and collects output for up to
    `design.SAMPLE_APP_READY_S`; a fake stands in for tests."""

    def start(self, command: str, cwd: Path) -> SampleAppHandle | str:
        """A handle to walk, or a refusal: the last lines of output when the command
        exited, or did not print anything, before the deadline. Not a role failure --
        the node writes the refusal into the lead's own section instead."""
        ...

    def stop(self, handle: SampleAppHandle) -> None:
        """Ends the process group `start` began. Called from a `finally`: the app must
        not outlive the node, whatever PM's verdict was."""
        ...


@dataclass(frozen=True)
class Checkout:
    """A branch of the product checked out somewhere the roles can work (ADR-0011)."""

    branch: str
    path: Path
    # Commit the checkout started from: what a review diffs against.
    base: str
    # True only for the call that brought it into being, so the product's setup runs
    # once, when the checkout is born (ADR-0011, third addendum).
    created: bool = False


class MergeCallFailed(ValueError):
    """`Tracker.merge` failed (GH-196). `failure` is the closed class the adapter decided at
    the step that raised, never by reading the CLI's text; `str()` is the raw error, for the
    merge verb's log lines and nowhere else."""

    def __init__(self, failure: MergeFailure, message: str):
        super().__init__(message)
        self.failure = failure


class TrackerCallFailed(ValueError):
    """A `gh` call failed for a reason about the transport, not about the question (GH-266,
    GH-290): the base of `TrackerTimedOut` and `TrackerUnreachable`, so a catcher that ends
    one poll round names one class and cannot forget a subtype. A `ValueError`, so every
    handler that already treats an unreadable read as harmless absorbs it unchanged."""


class TrackerTimedOut(TrackerCallFailed):
    """A `gh` call ran out of time (GH-266). Declared here so `application/` and `cli.py`
    can name it without importing an adapter. The message names the call and the bound,
    never stderr or issue content."""


class TrackerUnreachable(TrackerCallFailed):
    """A `gh` call could not reach GitHub (GH-290). For a read, no request is known to have
    reached GitHub; for a write the response may have been lost after the change was made,
    so a write stays ambiguous. The message names the call and the marker `run_gh`
    recognised, never stderr."""


class GitError(RuntimeError):
    """A real git failure -- not a question `Workspace`'s own contract answers some
    other way, like `is_ancestor`'s `False` for an object this checkout does not have
    (GH-137: a stale local checkout, not a broken one). Defined here, in the port
    `application` layer owns, rather than in the adapter that raises it, because the
    poller's launch predicate (`application/poll.py`, which may not import adapters at
    all -- the architecture test) must catch it: "ante la duda, no lanza" covers a git
    failure the same as an unknown object or a `gh` error. The adapter module still
    exposes the same name, imported from here, so existing call sites and tests that
    import it from `adapters.git.workspace` keep working unchanged."""


@dataclass(frozen=True)
class Upstream:
    """Where new work starts from and what the poller judges a merge against (GH-180):
    the fetched tip of the product checkout's current branch."""

    # The ref the commit came from: the upstream's own name, or "HEAD" when there was
    # nothing better. Informational; callers use `sha`.
    ref: str
    # The commit. None only when even `HEAD` could not be read (an unborn branch, no git).
    sha: str | None
    # Why it is not the plain fetched upstream (no remote, fetch failed, local commits
    # left out), for the run's log and the poll round's output. None when nothing to say.
    note: str | None


@dataclass(frozen=True)
class UpstreamMerge:
    """What `Workspace.merge_upstream_into` reports (GH-239, the landing's sync): `clean`
    says the upstream is now in the checkout's history and committed; otherwise
    `conflicted` names the paths git left unmerged, for a Developer to resolve. `head` is
    the checkout's full HEAD after the attempt (unchanged when the merge conflicted).
    Not `domain.task.MergeOutcome`, which is the dependency merge's own shape."""

    clean: bool
    conflicted: tuple[str, ...]
    head: str


@dataclass(frozen=True)
class AddedAdr:
    """A file a non-merge commit of a branch added under `docs/adr` (GH-229): its `path`
    as the repository names it and the full sha of the `commit` that added it. A path a
    later commit of the branch deleted or renamed is still reported, for the commit that
    added it did: whether it exists at head is the caller's to read."""

    path: str
    commit: str


@dataclass(frozen=True)
class AddedLine:
    """A line a non-merge commit of a branch added (GH-229): the `text` without the
    patch's leading `+`, the `path` it was added to and the full sha of its `commit`."""

    path: str
    commit: str
    text: str


class Workspace(Protocol):
    """The product repository as git sees it (ADR-0011): one branch and worktree per
    task, one per subtask, all idempotent so a use case can ask again."""

    def repository_at(self, path: Path) -> Path | None:
        """The repository `path` belongs to, as its common directory: a worktree answers
        the repository it was made from and not its own `.git`, so two paths in the same
        repository always answer the same thing. That is what lets `preflight` tell
        whether the product is the very repo the system runs from (ADR-0017), a worktree
        of it included. None when `path` is in no repository, or when git is not there."""
        ...

    def task_checkout(self, task_id: str) -> Checkout: ...

    def subtask_checkout(self, task_id: str, subtask_id: str) -> Checkout: ...

    def head(self, path: Path) -> str: ...

    def write_index(self, path: Path, files: dict[str, str]) -> None:
        """Write the knowledge index `files` (path relative to `path` -> text, all under
        `.agentic/index/`) into the checkout at `path`. Each file lands atomically, the new
        ones before any stale file under that directory is deleted, and a file already
        holding the same content is left alone. The directory is excluded from git, so the
        index is never committed. A key outside it raises `ValueError`."""
        ...

    def full_head(self, path: Path) -> str:
        """The full object id of HEAD in `path`; read-only. `head` keeps its short form."""
        ...

    def tree(self, path: Path) -> str:
        """The id of `HEAD^{tree}` in the checkout at `path` (ADR-0065): what the code is,
        whichever commits led to it. Read-only; the key of a reusable result."""
        ...

    def record_setup_tree(self, path: Path) -> None:
        """Remember, in the checkout's own git dir (never in its files), the tree the
        product's setup has just run on. Called once, after every setup step passed. Best
        effort: when it cannot write, no record exists and no pass is stored; it never raises."""
        ...

    def setup_tree(self, path: Path) -> str | None:
        """The tree `record_setup_tree` wrote for the checkout at `path`, or `None` when
        there is no record or it does not name a tree object (`git cat-file -t`)."""
        ...

    def tree_changes(self, path: Path, old: str, new: str) -> tuple[str, ...]:
        """The paths whose content differs between the trees `old` and `new` (ADR-0065),
        read from the checkout at `path`: `git diff --name-only --no-renames`, a rename
        naming both of its paths. Raises `GitError` when git cannot say (an unknown tree)."""
        ...

    def is_clean(self, path: Path) -> bool:
        """No uncommitted change, tracked or untracked, under the checkout at `path`."""
        ...

    def differs_outside(self, path: Path, verified: str, head: str, excluded: str) -> bool:
        """Whether `head` differs from `verified` in anything but the path `excluded`
        (GH-358): `git diff --quiet <verified> <head> -- . ':!<excluded>'` is not quiet. The
        question behind a full-tier record covering a head: only the task's own document may
        have moved since the suite ran. Raises `GitError` for anything but git's two answers
        (an unknown commit, for one)."""
        ...

    def restore_clean(self, path: Path) -> None:
        """Puts the checkout at `path` back as its head holds it (GH-358): `git checkout --
        .`, then `git clean -fd`, never `-x`, so a tracked file a check edited and an
        untracked one it wrote are gone and the ignored environment stays."""
        ...

    def is_committed(self, path: Path) -> bool:
        """No change to a **tracked** file under `path`. The looser one, and the question
        behind the `+dirty` of a system version (ADR-0008): a scratch file git never
        heard of does not change what the code is. A delivery asks `is_clean` instead,
        because a new file a Developer forgot to add is missing work."""
        ...

    def merge(self, path: Path, branch: str) -> str | None:
        """Bring `branch` into the checkout at `path`: a subtask starts on the code of
        the subtasks it depends on, and the fan-in brings each approved one into the
        task branch. Merging again is a no-op. Returns why the merge did not happen, or
        None; a conflict leaves the checkout as it was, for a role to resolve on its
        own. It is a fact about the code, not a failure of the system, so the use case
        decides what to do with it."""
        ...

    def build_slice(self, path: Path, master: str, heads: Sequence[str], branch: str) -> str | None:
        """A slice of approved units on master (GH-297): in the checkout at `path`, creates
        `branch` from `master` (resetting one an earlier round left) and merges each of
        `heads` in the order given. Returns the new head's full sha, or `None` when any
        merge conflicts: then the merge is aborted, the checkout is back where it was and
        `branch` does not exist, so nothing half-merged is left. Raises `GitError` for a
        `master` or a checkout git refuses."""
        ...

    def integration_paths(
        self, path: Path, heads: Sequence[str], task_head: str, *, master: str | None = None
    ) -> frozenset[str] | None:
        """The paths of the integrator's own work (GH-297): `heads` merged, in a temporary
        worktree that is always removed, onto the merge-base of `task_head` and `master`
        (the fetched upstream of the product when not given); the paths that tree and
        `task_head` differ in, which includes what was resolved inside merge commits.
        `None` when that merge conflicts or has no base to start from."""
        ...

    def merge_keeping_conflicts(self, path: Path, branch: str) -> MergeOutcome:
        """Like `merge`, but a conflict is left in the checkout instead of undone: the
        dependency-merge path in `develop.py` is the only caller, and it hands the
        worktree to a Developer to resolve, never to a use case that assumes a clean
        tree back. `merge`'s own abort-on-conflict is untouched; this is a separately
        named operation (ADR-0004)."""
        ...

    def merge_upstream_into(self, checkout: Path, upstream_sha: str) -> UpstreamMerge:
        """Merge the commit `upstream_sha` (fetched already, `fetch_commit`) into the checkout,
        a conflict left in the tree like `merge_keeping_conflicts` does (GH-239). Raises
        `GitError` when git refused for any other reason than a conflict -- an unknown sha,
        a dirty tree -- since there is then nothing for a Developer to resolve."""
        ...

    def abort_merge(self, path: Path) -> None:
        """`git merge --abort` in the checkout at `path`, only when a merge is in progress
        (`MERGE_HEAD` exists), so it is idempotent. Raises `GitError` only when the abort
        itself fails. `landing_sync.merges_cleared` is the only caller (GH-267)."""
        ...

    def merge_head(self, path: Path) -> str | None:
        """The full sha of `MERGE_HEAD` in the checkout at `path`, or `None` when no merge
        is in progress; read-only."""
        ...

    def dependency_state(self, path: Path, branch: str, paths: Sequence[str]) -> DependencyState:
        """The facts a dependency merge leaves behind, for the pure verdict in
        `domain/rules.py` to decide from with no git call of its own: whether `branch`
        already landed in the checkout, whether a merge is still in progress, which of
        `paths` still hold a conflict marker in their own content, and the diff
        against `branch` over those paths as evidence for the review."""
        ...

    def commit_all(self, path: Path, message: str) -> str | None:
        """Commit everything changed under `path`; `None` when there was nothing."""
        ...

    def commit_paths(self, path: Path, relatives: Sequence[str], message: str) -> str | None:
        """Commit only `relatives` (paths relative to the checkout at `path`), whatever
        else is changed there; `None` when those paths have nothing to commit. The
        delivery's own document commit (GH-203) is the caller: `commit_all` would take a
        stray file along. A path deleted from the worktree is committed as a deletion, so
        a rename is the old path and the new one together (GH-229). Raises `GitError`."""
        ...

    def push(self, branch: str, *, lease: str | None = None) -> None:
        """Publish `branch` to `origin`. `None` is a plain push. A sha is a replace: a
        force push guarded by a lease on exactly that sha, so nothing pushed after the
        caller observed it is lost. The adapter never reads the remote again to pick the
        sha, and it never decides whether a replace is warranted: its caller does."""
        ...

    def remote_tip(self, path: Path, branch: str) -> str | None:
        """The full sha `origin` holds for `branch`, whose object is local afterwards (it
        is fetched into a private ref, never under `refs/heads/`); `None` only when the
        remote has no such branch. A failed read, an unreachable origin or a remote that
        moved between the read and the fetch raises `GitError`: it is never "absent"."""
        ...

    def show(self, ref: str, relative: str) -> str | None:
        """The text of `relative` at `ref` (a branch, `HEAD` or a commit), or `None` when
        the ref or the file does not exist there."""
        ...

    def commit_date(self, ref: str) -> str | None:
        """When the commit at `ref` was made, as an ISO-8601 instant in UTC with
        milliseconds (the form task documents carry), or `None` when `ref` is no commit."""
        ...

    def cleanup(self, task_id: str) -> None:
        """Remove the task's worktrees. The branches stay."""
        ...

    def discard(self, path: Path) -> None:
        """Remove one checkout, so asking for it again makes it anew. The branch stays,
        and so does whatever was committed on it."""
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

    def branches(self, prefix: str) -> list[str]:
        """Every branch name starting with `prefix`, verbatim (ADR-0011, sixth
        addendum): a dumb string verb with no notion of task or subtask. A repo at
        GH-88 answers `agentic/GH-1` *and* `agentic/GH-12` for the prefix
        `"agentic/GH-1"`, so a caller that means one task's own branches goes through
        `rules.branches_of_task`, never this verb's answer unfiltered."""
        ...

    def rename_branch(self, old: str, new: str) -> None:
        """`git branch -m old new`, carrying `branch.<b>.agenticBase` with it: the
        archiving verb (ADR-0011, sixth addendum). A no-op when `new` already exists
        and `old` does not -- the idempotent case a retried archive relies on."""
        ...

    def is_ignored(self, root: Path, relative: str) -> bool:
        """Whether the product's own `.gitignore` under `root` ignores `relative`
        (ADR-0011, sixth addendum): archiving commits the task document with
        `commit_all`'s own `git add -A`, so this is how it tells the caller the
        document did not land, without assuming every product versions
        `.agentic/tasks/` the way this repo and gym-saas both happen to."""
        ...

    def is_ancestor(self, path: Path, sha: str, ref: str = "HEAD") -> bool:
        """Whether `sha` already sits on `ref` (`HEAD` by default) at `path` (GH-137: a
        blocking issue's merge commit, checked before the poller launches what it
        blocked). False, never an exception, for a `sha` this checkout has no object for
        at all -- a merge commit `gh` reported that a stale local checkout has not
        fetched yet -- which is the launch predicate's own "ante la duda, no lanza", not
        this port's business to tell apart from "fetched, but not an ancestor". Raises
        `GitError` for anything else `git merge-base --is-ancestor` answers beside its
        own two documented exit codes (0: is an ancestor, 1: is not): that is a git
        failure, not an unresolved blocker, and must not read as "launch anyway"."""
        ...

    def changed_paths(self, base: str, head: str) -> tuple[str, ...]:
        """The paths `git diff --name-only <base>...<head>` names (GH-178): what the
        pull request changes against the commit it branched from. Raises `GitError`."""
        ...

    def added_adrs(self, master: str, head: str) -> tuple[AddedAdr, ...]:
        """Every path under `docs/adr` that a non-merge commit of `master..head` added,
        with the commit that added it (GH-229): the branch's own ADRs, whatever master
        took meanwhile and whatever a merge of master brought in. One read of the log.
        A rename counts as the new path being added. Raises `GitError`."""
        ...

    def added_lines(self, master: str, head: str, numbers: Sequence[str]) -> tuple[AddedLine, ...]:
        """Every line a non-merge commit of `master..head` added that contains one of
        `numbers` as a plain substring (GH-229), once however many numbers it holds. The
        second phase of the collision read: it is asked only for the numbers that
        collide, and it knows no reference grammar -- what a line means is the domain's.
        Each number is a string of exactly four digits; anything else raises `ValueError`
        before git runs. Raises `GitError`."""
        ...

    def tree_names(self, ref: str, directory: str) -> tuple[str, ...]:
        """The base names of what `ref` (a commit, a branch) holds directly under
        `directory`, empty when it has no such directory (GH-229). Raises `GitError` for
        a `ref` git does not know."""
        ...

    def fetch_commit(self, sha: str) -> bool:
        """Whether `sha` is a commit this checkout has, fetching it from `origin` when it
        is not (a pull request's head the local branch never saw). Never raises."""
        ...

    def checkout_path(self, name: str) -> Path:
        """Where the checkout `name` lives, `<product_dir>/.agentic/worktrees/<name>`
        (GH-315): the one place the layout is computed. Pure: no git, no filesystem."""
        ...

    def detached_checkout(self, name: str, sha: str) -> Path:
        """A throwaway worktree `.agentic/worktrees/<name>` detached at `sha` (GH-178);
        the caller removes it with `remove_checkout`. Raises `GitError`."""
        ...

    def remove_checkout(self, name: str) -> None:
        """Removes `.agentic/worktrees/<name>` whatever state it is in: registered or
        not, there or not. Never raises for one that is absent."""
        ...

    def checkout_names(self, prefix: str) -> list[str]:
        """The names of the directories under `.agentic/worktrees/` that start with
        `prefix` (GH-318: the `perf-*` a hard kill left behind), sorted; empty when there
        are none. Filesystem only: no git."""
        ...

    def contains(self, head: str, master: str) -> Containment:
        """Whether the commit `head` has the commit `master` among its ancestors (GH-216),
        after fetching both shas: `yes` or `no`, and `unknown` for anything that could not
        be read -- a sha that cannot be fetched, a git failure, a missing git. Never
        raises, and `unknown` is never `yes`: the caller counts it as stale."""
        ...

    def merge_base(self, left: str, right: str) -> str:
        """The full sha of the best common ancestor of the commits `left` and `right`
        (GH-334), after fetching both shas: where a pull request branched from the master it
        would merge into. Raises `GitError` when either sha cannot be fetched or git fails, so
        a caller can tell an unreadable base from a file the base does not hold."""
        ...

    def merge_conflicts(self, head: str, master: str) -> bool | None:
        """Whether the commit `head` conflicts when merged with the commit `master` (GH-255),
        after fetching both shas: `True` is a definite conflict, `False` a clean merge, and
        `None` anything that could not be read -- a sha that cannot be fetched, a git
        failure, a timeout, a missing git. Never raises, and never writes a worktree, a
        branch or the index. `None` is never a verdict: the caller treats it as unread."""
        ...

    def overlay(self, checkout: Path, sha: str, paths: Sequence[str]) -> None:
        """Copies `paths` as the commit `sha` holds them into `checkout` (GH-318), so a
        measurement can run scripts the code under test does not carry. Raises `GitError`
        for a path the commit lacks or one that leaves the checkout."""
        ...

    def upstream(self) -> Upstream:
        """The fetched upstream of the checkout's current branch (GH-180). Never returns
        None and never raises for a git failure or a missing git: every failure is an
        `Upstream` with a `note`. The only place that fetches (never `pull`, `checkout`,
        `reset` or a fast-forward: the human's branch is theirs, ADR-0002) and the only
        one that picks a fallback when the fetch fails."""
        ...


@dataclass(frozen=True)
class IssueState:
    """The structured facts of one GitHub issue the poller's blocker/launch predicates
    read (GH-137; invariant: no predicate reads prose). `state_reason` is `""` for an
    open issue, `"COMPLETED"` or `"NOT_PLANNED"` for a closed one -- `gh`'s own closed
    set, never widened here. `blocked_by` is `None` when `gh` does not expose the
    native dependency at all (an older CLI, GH issue's own JSON field request failing);
    `()` when `gh` does expose it and answers `blockedBy.nodes: []`, "exposed, with no
    native blockers". Whether either shape falls back to the issue body's own
    `Blocked by:` line is not this type's own concern (GH-142): `rules.blocker_numbers`
    is the one place that decides it.
    `closing_merge_commit` is the merge commit of the pull request that closed this
    issue, when `gh` names one; `None` for an issue still open, one closed without a
    linked pull request, or one whose pull request never merged.
    `labels` is the names of the issue's labels (GH-328); `None` when they could not be read,
    which no caller may take for "none": an unread label set is not a removed `needs-human`."""

    state: Literal["OPEN", "CLOSED"]
    state_reason: str
    blocked_by: tuple[int, ...] | None
    closing_merge_commit: str | None
    labels: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IssueComment:
    """One comment of an issue, with its timestamp (GH-137): `rules.next_attempt_at`'s
    own `since` reads a structured `createdAt`, never a parsed sentence, and `prior`
    counts comments after the last one a human (not the system) wrote."""

    body: str
    created_at: str


@dataclass(frozen=True)
class ListedPullRequest:
    """One pull request of the product as the playbook delivery reads it (ADR-0043):
    `state` is `OPEN`, `MERGED` or `CLOSED`, `merge_commit` the commit a merge made (empty
    otherwise) and `branch` the head it came from."""

    number: int
    url: str
    state: str
    body: str
    merge_commit: str
    branch: str


@dataclass(frozen=True)
class BranchPullRequest:
    """One pull request whose head is a given branch, as `Tracker.pull_requests_on`
    reports it: `state` is the platform's own, nothing is inferred from it here."""

    number: int
    state: Literal["OPEN", "CLOSED", "MERGED"]
    url: str


class Tracker(Protocol):
    """Where the human is told (ADR-0011): the pull request, the issue's comments and
    labels. A task that did not come from an issue has no issue to write to; the
    adapter ignores those calls."""

    def open_pull_request(
        self, task_id: str, branch: str, title: str, body: str, *, closes_issue: bool = True
    ) -> str:
        """Returns the pull request's URL. Linking it to the task's issue, when there
        is one, is the adapter's: only it knows what a task id says about the tracker.
        With `closes_issue=False` the body carries no closing keyword, so merging the
        pull request does not close the issue: a slice of the task (GH-297), which is
        not the task's own pull request."""
        ...

    def close_issue(self, task_id: str) -> None:
        """Closes the task's issue as completed (GH-344): the system's own answer to a
        pull request that merged without closing it. Raises `ValueError`/`OSError` when the
        platform cannot do it; a task with no issue is ignored, like the other calls."""
        ...

    def open_issue(self, title: str, body: str, *, label: str | None = None) -> str:
        """Open a new issue and return its URL (ADR-0034): the stack review's own way
        of telling the human, with no task id to key off of. It passes no `label`: the
        absence of the `agentic` label (README.md, 5) is what keeps a proposal from
        starting a run on its own, and `gh issue create --label` exits non-zero on a label
        the product repo does not have, which would throw away a paid interview for one
        word. The landing (GH-239, ADR-0048) is the caller that passes one, `agentic`, for
        the follow-ups and flakes it files, which the product repo always has: it is the
        queue's own label. Any state a reader needs goes in `title` instead."""
        ...

    def comment_on_issue(self, reference: str, text: str) -> None:
        """Comments `text` on the issue at `reference`: the URL or number `open_issue`
        answers, and the first element of each `Inbox.open_issue_bodies()` pair. Not
        `comment`, which writes on the task's own issue by its task id. Raises
        `ValueError` when the platform refuses or cannot be reached, as `open_issue`."""
        ...

    def rerun_failed_ci(self, reference: str, head: str) -> list[str]:
        """Re-runs the failed CI runs of commit `head` of the pull request at `reference`
        (GH-239) and returns the URL of each run it asked to re-run, none when no run on
        that head failed. Raises `ValueError` for a reference that is not a pull request
        URL, before any call, and when the platform cannot answer."""
        ...

    def close_pull_request(self, reference: str) -> None:
        """Closes the pull request at `reference` unmerged (GH-239): the terminal path
        replaces the system's own pull request with a fresh one. Raises `ValueError` for
        a reference that is not a pull request URL, before any call, and when the
        platform refuses."""
        ...

    def changed_files(self, pull_request: str) -> int:
        """How many files the pull request at `pull_request` (the URL `deliver` stored)
        shows as changed (`changedFiles`): the actual the plan's file estimate is compared
        with (ADR-0054). Raises `ValueError` for a reference that is not a pull request URL,
        before any call, and when the platform cannot answer or answers something unreadable;
        `TrackerTimedOut` is one, as every read."""
        ...

    def comment(self, task_id: str, text: str) -> None: ...

    def label(self, task_id: str, label: str) -> None: ...

    def unlabel(self, task_id: str, label: str) -> None: ...

    def ensure_label(self, name: str) -> None:
        """Makes sure the repository has a label called `name` (GH-294), so `open_issue`
        can pass it. Creates it when missing and never touches an existing one, its color
        and description included. Raises `ValueError` when the platform cannot answer."""
        ...

    def list_pull_requests(self) -> list[ListedPullRequest]:
        """Every pull request of the product, any state, with its body: the playbook
        delivery filters them locally for its marker block (never through the search
        index, which lags a body edit)."""
        ...

    def pull_requests_on(self, branch: str) -> list[BranchPullRequest]:
        """Every pull request, any state, whose head branch is `branch`. Raises
        `ValueError` when the platform cannot answer, answers something unreadable, or
        may have answered a truncated page: a failed lookup is never an empty list."""
        ...

    def update_pull_request_body(self, number: int, body: str) -> None: ...

    def issue_comments(self, task_id: str) -> list[str]:
        """The bodies of the issue's comments, oldest first, whoever wrote them; none
        when the task has no issue."""
        ...

    def pull_request(self, reference: str) -> PullRequest | None:
        """The facts of a task's own pull request: `reference` is the URL `deliver`
        stored in the document, always explicit -- never the bare form, which would
        resolve whatever branch `product_dir` has checked out, and a task restarted
        with `--fresh` has two pull requests with only the document knowing which is
        this run's. Its own `.state` is the pull request's state -- no separate
        `pull_request_state` verb exists, since this already answers it."""
        ...

    def ci_report(self, reference: str) -> CiReport | CiUnreadable:
        """The CI of a task's own pull request (GH-216): `reference` is the URL `deliver`
        stored in the document, explicit like `pull_request`'s. The head oid, its checks
        (check runs and commit statuses), the base ref and the creation time come from one
        query, so the checks always belong to the head the report names. Never raises:
        what cannot be read is a `CiUnreadable` with its cause class."""
        ...

    def branch_ci(self, branch: str) -> CiReport | CiUnreadable:
        """The CI of the latest commit of `branch` (GH-216), the master side of a red
        check's comparison. `base_ref`, `base_sha` and `opened_at` are None. Never raises:
        what cannot be read is a `CiUnreadable`."""
        ...

    def queue(self) -> list[int]:
        """Every open issue carrying the `agentic` label (README.md, 5), by number,
        ascending (GH-137): the poller's own candidate pool, so "primero el número más
        bajo" is decided here and not left to however `gh issue list` happens to order
        a page today."""
        ...

    def issue_state(self, number: int) -> IssueState:
        """The structured facts `poll_plan`'s predicates read off one issue -- a launch
        candidate or a blocker alike (GH-137)."""
        ...

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        """Every comment of the task's issue, oldest first, each with its own
        timestamp (GH-137) -- `issue_comments`'s bodies-only answer is not enough for
        `rules.next_attempt_at`, which needs `createdAt` too. Named apart from
        `issue_comments` on purpose, not only `comments`: the in-memory double already
        carries a `comments` attribute of its own (every posted comment, the fixture's
        long-standing test-inspection surface), and the two must never collide. Empty
        when the task has no issue, the same shape `issue_comments` already has."""
        ...

    def issue_created_at(self, task_id: str) -> str | None:
        """When the task's issue was created, as the tracker writes it (GH-273: the start
        of a task's cycle, which the score `cycle_h` measures from). `None` for a task id
        that is not `GH-N`, or an issue that carries no creation time. A tracker that
        cannot answer raises `ValueError` (`TrackerTimedOut` included), as every read."""
        ...

    def merge(self, reference: str, head_sha: str) -> str:
        """Merges the pull request at `reference`, the URL `deliver` stored (the same key
        `pull_request` takes), only if its head is still `head_sha` (GH-178: the sha the
        automatic-merge decision was taken on, never "whatever is there now"), and returns
        the method used. Raises `MergeCallFailed` (a `ValueError`) when the tracker refuses
        -- the head moved, a check or a rule blocks it, the pull request is not open, the
        reference is unreadable -- carrying the class of the step that failed."""
        ...


@dataclass(frozen=True)
class LockRecord:
    """What `.agentic/runs/<task>.lock` holds: who is running this task's thread right
    now (Tech Lead plan, subtask `run-lock`), so a second invocation over the same
    task can tell before it spends anything."""

    pid: int
    host: str
    started_at: str
    command: str
    boot_id: str | None = None
    stopped_at_shutdown: bool = False
    # The system version a run recorded when it raised under the graph
    # (`RunLock.keep_crashed`); `None` for a record with no such mark.
    crashed_version: str | None = None


class LockHeld(Exception):
    """`RunLock.acquire` found a process already holding the task's thread: `record` is
    what it wrote, and `str(exc)` names the fact alone (pid, host, since when) -- the
    lock's own scope, why a `langgraph dev` server is not covered, is the caller's to
    say, not this port's."""

    def __init__(self, record: LockRecord, message: str):
        super().__init__(message)
        self.record = record


class RunLock(Protocol):
    """Is a process holding this task's thread right now. One claim, not a general
    lock: a live pid on this host refuses, a pid recorded on another host refuses by
    name and is **never** probed -- a recycled pid elsewhere is a coin flip -- and a
    dead pid's lock is taken over. Scoped to CLI invocations: a node the graph runs
    through `langgraph dev` never asks it (`default_ports` fires once per node and
    never sees a `thread_id`)."""

    def read(self, task_id: str) -> LockRecord | None:
        """The lock's contents, without taking or releasing it: what `--dry-run` and a
        refusal report."""
        ...

    def acquire(self, task_id: str, command: str) -> LockRecord | None:
        """Take the lock for this process (`os.getpid()`, this host). Returns the
        record of a dead lock taken over, for the caller to print, or `None` when the
        lock was free. Raises `LockHeld` when a live pid on this host or any pid on
        another host already holds it."""
        ...

    def refusal(self, task_id: str) -> str | None:
        """The message `acquire` would raise `LockHeld` with right now, without
        writing anything and without probing anything `acquire` itself would not
        probe (a pid on another host is still never probed). `None` when `acquire`
        would succeed. The one place the liveness rule and its message live, so a
        read-only caller -- `--detach`'s parent, refusing on the human's terminal
        before it spawns -- cannot drift from what `acquire` actually enforces."""
        ...

    def release(self, task_id: str) -> None:
        """Release, if this process holds it (same pid, same host). A no-op when
        there is none, or when the lock now belongs to someone else -- a takeover
        that happened after this process's own claim was lost must never be undone
        by this process releasing on its way out."""
        ...

    def keep_at_shutdown(self, task_id: str) -> None:
        """What a run does instead of `release` when the system shutdown stopped it:
        rewrite this process's own record (same pid, same host) with
        `stopped_at_shutdown` set, keeping its boot id, so the next boot can retake the
        run. A no-op when there is none, or when the record belongs to another pid."""
        ...

    def keep_crashed(self, task_id: str, system_version: str) -> None:
        """What a run does instead of `release` when the graph raised: the sibling of
        `keep_at_shutdown`. Rewrite this process's own record (same pid, same host) with
        `crashed_version` set to `system_version`, keeping its boot id, so the poller can
        retake the run. A no-op when there is none, or when the record belongs to another
        pid. Exclusive with `release` and `keep_at_shutdown`: a run calls one of the three."""
        ...


@dataclass(frozen=True)
class RunRegistration:
    """One live `agentic` child invocation, as `RunRegistry.live` hands it back
    (GH-137): what the poller counts against `AGENTIC_MAX_CONCURRENT_RUNS`, across
    every product on this machine -- the account's own 429 is not scoped to one. `kind`
    says whether it counts (`rules.counts_against_cap`); a record that names none is
    `role`."""

    pid: int
    host: str
    product: str
    task_id: str
    kind: RunKind = "role"


@dataclass(frozen=True)
class LaunchAttempt:
    """What a launch left behind when it tried but did not reach a registered run
    (GH-137, `launches/<product>-<issue>.json`): `since` and `prior` feed
    `rules.next_attempt_at` directly, the same shape a resume's own schedule has, so
    one issue is never relaunched every turn while its own backoff has not run out."""

    since: datetime
    prior: int


class CheckResults(Protocol):
    """The machine's store of passing check results (ADR-0065), keyed by
    `domain.check_results.check_key`. Only the presubmit and the run-check tool use it."""

    def get(self, key: str, now: float) -> CheckResult | None:
        """The stored pass for `key`, or `None`. An entry that is missing, unreadable,
        not signed by this machine's secret or older than `MAX_AGE_S` at `now` is a miss;
        a read never raises."""
        ...

    def put(self, key: str, row: CheckResult, now: float) -> None:
        """Store `row`, a pass, under `key` as of `now`; a row that did not pass is not
        stored. The output is stored as it is given: cut it before. Entries older than
        `MAX_AGE_S` and the oldest above the entry limit are pruned."""
        ...


class RunRegistry(Protocol):
    """How many `agentic` invocations are alive on this machine right now, across every
    product (GH-137), and the poller's own record of a launch attempt that has not
    registered a run yet. Scoped to `Configuration.run_registry_dir`, outside every
    product -- the account's session limit a 429 names is the machine's, not a
    product's."""

    def register(self, product: str, task_id: str, kind: RunKind = "role") -> None:
        """Write this process's own entry (`os.getpid()`, this host) once, whole, under
        `kind`; it is never rewritten while the child runs. `run`/`resume`/`merge`/`land`
        call this inside their own per-task lock, right after they acquire it. Only the
        kinds for which `rules.counts_against_cap` is true occupy a slot of
        `AGENTIC_MAX_CONCURRENT_RUNS`."""
        ...

    def unregister(self) -> None:
        """Remove this process's own entry. A no-op if it never registered one."""
        ...

    def live(self) -> list[RunRegistration]:
        """Every entry whose pid answers on this host, right now. An entry recorded
        under a different host is never probed -- the same coin-flip `RunLock` already
        refuses to take -- and so never counts as live here either."""
        ...

    def exclusive(self) -> AbstractContextManager[None]:
        """The one lock the poller decides under (`runs/.lock`): counting `live()` and
        deciding how many launches fit is atomic across every poller on this machine,
        not only within one product's."""
        ...

    def launch_attempts(self, product: str, issue: int) -> LaunchAttempt | None:
        """The schedule of a launch attempt that has not cleared yet; `None` when this
        issue has never been attempted, or its attempt already cleared."""
        ...

    def record_launch(self, product: str, issue: int, when: datetime) -> None:
        """Note one more launch attempt of `issue`, timestamped `when` (the poller's own
        clock, never read here): `prior` grows by one, `since` becomes `when`."""
        ...

    def clear_launch(self, product: str, issue: int) -> None:
        """Forget the schedule once the launch registered a real run (`register` saw
        it) or once the issue is no longer a candidate at all. A no-op when there is
        none."""
        ...

    def publish_spend(
        self, root: Path, product: str, runs: Sequence[SpendRun], now: datetime
    ) -> list[str]:
        """Merge `runs` (this poller's own product, read from the task documents under
        `root`) into this poller's own spend file, keyed by the resolved `root` and
        replaced atomically (GH-177). Called under `exclusive()`. Returns the notes of
        what it could not read, never raises on a damaged file."""
        ...

    def spend(self, since: datetime) -> tuple[list[SpendRun], list[str]]:
        """Every product's published runs that ended at or after `since`, from the spend
        files under this registry only -- never from another product's directory --
        and the notes of the files or entries it skipped."""
        ...

    def land_waits(self, now: datetime) -> tuple[list[LandWait], list[str]]:
        """Every product's published Espera de aterrizaje (GH-362) that is still fresh at
        `now`, and a note per file it could not read. It filters for staleness only -- a
        record older than `rules.LAND_WAIT_TTL` or written under another boot -- and decides
        nothing: whether a record holds anything is `rules.poll_plan`'s."""
        ...

    def publish_land_wait(
        self,
        root: Path,
        product: str,
        issues: Sequence[int],
        since: datetime | None,
        now: datetime,
    ) -> None:
        """Write this poller's own wait record, keyed by the resolved `root`, whole and
        replaced atomically: the `issues` whose land waits for a slot, `since` when the wait
        began (`now` when `None`). Empty `issues` remove the record. Called under
        `exclusive()`; `waiting/` is created only when something is published."""
        ...


class Launcher(Protocol):
    """Spawns one child `agentic` invocation, detached (GH-137): the poller's own way of
    closing, resuming and launching, never running a role in its own process (the
    plan's own invariant). Wraps `cli._spawn_detached`: the real implementation picks
    its own log path the same way `cli._log_file_for` already does, so
    `application/poll.py` never has to invent one, and can be tested against a
    recording double instead of a real `Popen`."""

    def spawn(self, argv: list[str]) -> int:
        """Returns the child's pid, exactly as `cli.Spawner` already does once its own
        log path is picked."""
        ...


class ProcessReaper(Protocol):
    """Stops the processes a role left running (GH-282, ADR-0052): everything that carries
    the environment mark of a role the system ran, found by the mark and never by where
    it runs. Built with the product's key, so a caller names only the task. TERM first, a
    bounded grace, then KILL; what is still alive afterwards is a `survived` entry, never
    an exception. No method ever raises: an unexpected failure is an `error` entry. A
    process whose environment cannot be read is skipped, never signalled, and absent from
    the answer."""

    def reap_task(self, task_id: str) -> tuple[ReapedProcess, ...]:
        """Every marked process of the task and of its subtasks, whichever run or attempt."""
        ...

    def reap_run(self, task_id: str, run: int) -> tuple[ReapedProcess, ...]:
        """Only the processes marked by the system process `run`: a dead run's own."""
        ...

    def reap_attempt(self, task_id: str, run: int, token: str) -> tuple[ReapedProcess, ...]:
        """Only the processes of one attempt of a role."""
        ...

    def reap_within(self, paths: Sequence[Path]) -> tuple[ReapedProcess, ...]:
        """Processes whose cwd is inside one of `paths` (GH-315). Only unmarked processes
        (a marked one belongs to the mark reap); never the system, its ancestors or a
        process with a controlling terminal; a path that is not strictly inside the
        worktrees root the reaper was built with is dropped. Never raises."""
        ...

    def child_activity(
        self, task_id: str, run: int, token: str, parent_pid: int
    ) -> ChildActivity | None:
        """What the attempt's marked processes are doing, the one marked process whose
        parent is `parent_pid` (the CLI) left out: their CPU time in all and whether any is
        running or in disk wait. `None` when it could not be read; never raises."""
        ...


class PerfRunner(Protocol):
    """Runs one `seed`, `measure` or `setup` command of a performance scenario (GH-318,
    ADR-0059) in `checkout`, with `extra_env` on top of a scrubbed environment, and returns
    the `Run`. A non-zero exit and a timeout are a `Run` (`timed_out`), never an exception:
    the verdict reads them."""

    def run(
        self, checkout: Path, command: str, extra_env: Mapping[str, str], timeout_s: float
    ) -> Run: ...


class PerfLock(Protocol):
    """The machine-wide lock two measurements take so they never share a CPU (GH-318)."""

    def acquire(self, wait_s: float) -> bool:
        """Takes the lock, waiting at most `wait_s` seconds; `False` when it is held."""
        ...

    def release(self) -> None:
        """Gives the lock back; harmless when it was never taken."""
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
    sample_app: SampleApp
    reaper: ProcessReaper
    # Not a port: the run as a process, shared by every branch of a fan-out, with
    # nothing outside it to talk to (`limits.py`). It rides here because `Ports` is
    # what a use case receives.
    limits: RunLimits = field(default_factory=RunLimits)
    # QA Performance's node (GH-318, ADR-0060) measures through these two and a use case
    # cannot build them: `None` for a product no convened task runs on. A node that finds
    # none fails loudly instead of measuring with a stand-in.
    perf_runner: PerfRunner | None = None
    perf_lock: PerfLock | None = None
    # The machine's store of passing check results (ADR-0065). `None` stores and reuses
    # nothing, which is how every checkout ran before it existed.
    check_results: CheckResults | None = None
