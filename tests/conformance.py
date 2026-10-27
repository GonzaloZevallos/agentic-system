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

from typing import NamedTuple

from agentic.core.process.application.limits import RunLimits
from agentic.core.process.application.ports import (
    CheckResults,
    CheckScheduler,
    Commands,
    FlagVocabulary,
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
    SampleApp,
    StopFlag,
    TaskDocuments,
    Tracker,
    Workspace,
)
from tests.fakes import (
    FakeCheckScheduler,
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
_stop_flag: StopFlag = FakeStopFlag()


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
    "StopFlag": FakeStopFlag,
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
