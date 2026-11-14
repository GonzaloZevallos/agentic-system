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
    CiLogs,
    Commands,
    ExperimentFile,
    FlagVocabulary,
    FreeSpace,
    Inbox,
    LabelTimeline,
    Launcher,
    LoadReader,
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
    SpecReader,
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
    FakeCiLogs,
    FakeExperimentFile,
    FakeFreeSpace,
    FakeLabelTimeline,
    FakeLauncher,
    FakeLoadReader,
    FakePerfLock,
    FakePerfRunner,
    FakeReaper,
    FakeSpecReader,
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
_load_reader: LoadReader = FakeLoadReader()
_perf_lock: PerfLock = FakePerfLock()
_check_scheduler: CheckScheduler = FakeCheckScheduler()
_scratch: Scratch = MemoryScratch()
_free_space: FreeSpace = FakeFreeSpace()
_ci_logs: CiLogs = FakeCiLogs()
_experiment_file: ExperimentFile = FakeExperimentFile()
_spec_reader: SpecReader = FakeSpecReader({})
_stop_flag: StopFlag = FakeStopFlag()
_board: Board = FakeBoard()
_board_sent: BoardSent = FakeBoardSent()
_label_timeline: LabelTimeline = FakeLabelTimeline()


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
        spec_reader=_spec_reader,
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
    "LoadReader": FakeLoadReader,
    "PerfLock": FakePerfLock,
    "CheckScheduler": FakeCheckScheduler,
    "Scratch": MemoryScratch,
    "FreeSpace": FakeFreeSpace,
    "CiLogs": FakeCiLogs,
    "ExperimentFile": FakeExperimentFile,
    "SpecReader": FakeSpecReader,
    "StopFlag": FakeStopFlag,
    "Board": FakeBoard,
    "BoardSent": FakeBoardSent,
    "LabelTimeline": FakeLabelTimeline,
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
    "RunProcess": Uncovered(
        (("tests/core/process/application/test_stop.py", "_Process"),),
        _INLINE,
    ),
    "Stage0Store": Uncovered(
        (("tests/core/process/application/test_stage0_rotate.py", "Store"),), _INLINE
    ),
    "Stage0Users": Uncovered(
        (("tests/core/process/application/test_stage0_rotate.py", "Users"),), _INLINE
    ),
    "CommitCiReader": Uncovered(
        (("tests/core/process/adapters/git/test_master_window.py", "FakeReader"),), _INLINE
    ),
    "MasterWindow": Uncovered(
        (("tests/core/process/application/test_stage0_rotate.py", "Window"),), _INLINE
    ),
    "Stage0Candidate": Uncovered(
        (("tests/core/process/application/test_stage0_rotate.py", "Candidate"),), _INLINE
    ),
    "Stage0Escalation": Uncovered(
        (("tests/core/process/application/test_stage0_rotate.py", "Escalation"),), _INLINE
    ),
    "FailurePolicy": Uncovered(
        (("tests/core/process/application/test_stage0_rotate.py", "Policy"),), _INLINE
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


def prompts_serve_spec_blocks(
    factory: Callable[[Path, Mapping[str, str]], Prompts], tmp: Path
) -> None:
    """What every `Prompts` shares for the Spec blocks: `factory(tmp, table)` builds a port
    whose blocks are the values of `table` (slug -> block name), with each block's text being
    `text of <name>`. The table holds one invalid name (`../x`). A known name returns its text; a
    name outside the table and the invalid one held by the table both raise `ValueError`; and
    `spec_block_names` is the table's names, sorted, the invalid one included. It
    asserts neither the snapshot nor the overlay: those are the filesystem adapter's own.
    Raises `AssertionError` for a port that answers otherwise."""
    table = {"pm": "zeta", "developer": "alpha", "qa": "../x"}
    prompts = factory(tmp, table)
    for name in ("zeta", "alpha"):
        if (text := prompts.spec_block(name)) != f"text of {name}":
            raise AssertionError(f"Prompts.spec_block({name!r}) answered {text!r}")
    for bad in ("outside", "../x"):
        try:
            prompts.spec_block(bad)
        except ValueError:
            continue
        raise AssertionError(f"Prompts.spec_block({bad!r}) answered instead of raising ValueError")
    if prompts.spec_block_names() != ("../x", "alpha", "zeta"):
        raise AssertionError(f"Prompts.spec_block_names answered {prompts.spec_block_names()!r}")
