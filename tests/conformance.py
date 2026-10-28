"""Where mypy checks the in-memory doubles against the ports (GH-227, ADR-0021's addendum).

Each covered protocol of `ports.py` is assigned its double here, once, with the protocol as
the annotation: a double that stops matching its protocol is a mypy error on that line.
Nothing here runs to prove anything; `ports_of_doubles` exists so the module-level
assignments are used, and so a new required `Ports` field with no double is an error too.

`COVERED` and `UNCOVERED` are the inventory `tests/test_conformance_scope.py` holds to
`ports.py`: every protocol is in one of them. The `UNCOVERED` stand-ins live in test modules
outside mypy's scope, so they are named by file and class, never by line.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from agentic.core.process.application.limits import RunLimits
from agentic.core.process.application.ports import (
    Board,
    BoardSent,
    CheckResults,
    CheckScheduler,
    Commands,
    FlagVocabulary,
    FreeSpace,
    Inbox,
    Launcher,
    PerfLock,
    PerfRunner,
    Ports,
    ProcessReaper,
    ProductKnowledge,
    ProductStack,
    Prompts,
    RoleRunner,
    RunRegistry,
    RunStopped,
    SampleApp,
    Scratch,
    StopFlag,
    TaskDocuments,
    Tracker,
    Workspace,
)
from agentic.core.process.domain.roles import Role
from tests.fakes import (
    FakeBoard,
    FakeBoardSent,
    FakeCheckScheduler,
    FakeFreeSpace,
    FakeLauncher,
    FakePerfLock,
    FakePerfRunner,
    FakeReaper,
    FakeStopFlag,
    MemoryCheckResults,
    MemoryCommands,
    MemoryDocuments,
    MemoryFlags,
    MemoryInbox,
    MemoryKnowledge,
    MemoryPrompts,
    MemoryRunRegistry,
    MemorySampleApp,
    MemoryScratch,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    ScriptedRunner,
)

_runner: RoleRunner = ScriptedRunner()
_documents: TaskDocuments = MemoryDocuments()
_knowledge: ProductKnowledge = MemoryKnowledge()
_prompts: Prompts = MemoryPrompts()
_flags: FlagVocabulary = MemoryFlags()
_stack: ProductStack = MemoryStack()
_commands: Commands = MemoryCommands()
_inbox: Inbox = MemoryInbox()
_sample_app: SampleApp = MemorySampleApp()
_workspace: Workspace = MemoryWorkspace()
_tracker: Tracker = MemoryTracker()
_registry: RunRegistry = MemoryRunRegistry()
_check_results: CheckResults = MemoryCheckResults()
_launcher: Launcher = FakeLauncher()
_reaper: ProcessReaper = FakeReaper()
_perf_runner: PerfRunner = FakePerfRunner()
_perf_lock: PerfLock = FakePerfLock()
_check_scheduler: CheckScheduler = FakeCheckScheduler()
_scratch: Scratch = MemoryScratch()
_free_space: FreeSpace = FakeFreeSpace()
_stop_flag: StopFlag = FakeStopFlag()
_board: Board = FakeBoard()
_board_sent: BoardSent = FakeBoardSent()


def ports_of_doubles() -> Ports:
    return Ports(
        runner=_runner,
        documents=_documents,
        knowledge=_knowledge,
        prompts=_prompts,
        flags=_flags,
        inbox=_inbox,
        workspace=_workspace,
        tracker=_tracker,
        stack=_stack,
        commands=_commands,
        sample_app=_sample_app,
        reaper=_reaper,
        check_scheduler=_check_scheduler,
        limits=RunLimits(),
        check_results=_check_results,
        scratch=_scratch,
        free_space=_free_space,
    )


# Protocol name -> the double of `tests/fakes.py` assigned to it above.
COVERED: dict[str, type] = {
    "RoleRunner": ScriptedRunner,
    "TaskDocuments": MemoryDocuments,
    "ProductKnowledge": MemoryKnowledge,
    "Prompts": MemoryPrompts,
    "FlagVocabulary": MemoryFlags,
    "ProductStack": MemoryStack,
    "Commands": MemoryCommands,
    "Inbox": MemoryInbox,
    "SampleApp": MemorySampleApp,
    "Workspace": MemoryWorkspace,
    "Tracker": MemoryTracker,
    "RunRegistry": MemoryRunRegistry,
    "CheckResults": MemoryCheckResults,
    "Launcher": FakeLauncher,
    "ProcessReaper": FakeReaper,
    "PerfRunner": FakePerfRunner,
    "PerfLock": FakePerfLock,
    "CheckScheduler": FakeCheckScheduler,
    "Scratch": MemoryScratch,
    "FreeSpace": FakeFreeSpace,
    "StopFlag": FakeStopFlag,
    "Board": FakeBoard,
    "BoardSent": FakeBoardSent,
}


class Uncovered(NamedTuple):
    """A protocol with no double in `tests/fakes.py`: its stand-ins, as (path, class name)
    pairs, and why mypy cannot see them."""

    stand_ins: tuple[tuple[str, str], ...]
    reason: str


_INLINE = (
    "the stand-ins live inline in test modules outside mypy's scope, "
    "so only their member names are checked"
)

UNCOVERED: dict[str, Uncovered] = {
    "Presubmit": Uncovered(
        (
            ("tests/core/process/application/test_coverage_guard.py", "CountingPresubmit"),
            ("tests/core/process/adapters/claude_sdk/test_hooks.py", "FakePresubmit"),
            ("tests/core/process/adapters/claude_sdk/test_runner.py", "FakePresubmit"),
        ),
        _INLINE,
    ),
    "CheckTool": Uncovered(
        (("tests/core/process/adapters/claude_sdk/test_run_check_tool.py", "SlowTool"),),
        _INLINE,
    ),
    "SchemaGuard": Uncovered(
        (
            ("tests/core/process/adapters/claude_sdk/test_hooks.py", "FakeGuard"),
            ("tests/core/process/adapters/claude_sdk/test_runner.py", "FakeGuard"),
        ),
        _INLINE,
    ),
    "RunLock": Uncovered(
        (
            ("tests/test_cli.py", "FakeLock"),
            ("tests/test_cli.py", "FakeKeyedLock"),
            ("tests/core/process/application/test_poll.py", "_Locks"),
        ),
        _INLINE,
    ),
}


def perf_runner_stops_with_the_flag(runner: PerfRunner, stop: StopFlag, checkout: Path) -> None:
    """The `PerfRunner` contract of the stop (GH-385): with the run's flag set, `run` raises
    `RunStopped` and never answers a `Run`. `runner` must be built on `stop`. Raises
    `AssertionError` for a runner that ignores the flag."""
    extra_env: Mapping[str, str] = {}
    stop.set()
    try:
        runner.run(checkout, "true", extra_env, 10.0)
    except RunStopped:
        return
    raise AssertionError("PerfRunner.run answered with the run's stop flag set")


def perf_lock_stops_with_the_flag(lock: PerfLock, stop: StopFlag) -> None:
    """The `PerfLock` contract of the stop (GH-385): with the run's flag set, `acquire` raises
    `RunStopped` and does not take the lock. `lock` must be built on `stop`. Raises
    `AssertionError` for a lock that ignores the flag."""
    stop.set()
    try:
        taken = lock.acquire(0.0)
    except RunStopped:
        return
    lock.release()
    raise AssertionError(f"PerfLock.acquire answered {taken} with the run's stop flag set")


def prompts_serve_the_text_of_construction(
    build: Callable[[Path], Prompts], system_dir: Path, role: Role
) -> None:
    """The `Prompts` snapshot contract (GH-409): `raw` returns the text the role's file held
    when `build` ran, whatever the file says afterwards. `build(system_dir)` must read the
    system defaults from `system_dir`. Raises `AssertionError` for a port that reads live."""
    path = system_dir / f"{role.slug}.md"
    path.write_text("Original text for {{date}}.", encoding="utf-8")
    prompts = build(system_dir)
    path.write_text("A new text that wants {{docs_review}}.", encoding="utf-8")
    text, _ = prompts.raw(role)
    if text != "Original text for {{date}}.":
        raise AssertionError(f"Prompts.raw answered {text!r}, not the text of construction")


def prompts_keep_an_unreadable_file_for_its_role(
    build: Callable[[Path], Prompts], system_dir: Path, role: Role, other: Role
) -> None:
    """The other half of the snapshot contract: a file that cannot be decoded does not stop
    `build`, and `raw` for that role raises while `other` still answers. Raises
    `AssertionError` for a port that raises at construction or never raises."""
    (system_dir / f"{role.slug}.md").write_bytes(b"\xff\xfe not utf-8 \xff")
    (system_dir / f"{other.slug}.md").write_text("Fine.", encoding="utf-8")
    try:
        prompts = build(system_dir)
    except (OSError, UnicodeDecodeError) as error:
        raise AssertionError(f"building the port raised {error!r}") from error
    if prompts.raw(other)[0] != "Fine.":
        raise AssertionError("an unreadable file of one role spoiled another role")
    try:
        prompts.raw(role)
    except UnicodeDecodeError:
        return
    raise AssertionError("Prompts.raw answered for a file that is not UTF-8")
