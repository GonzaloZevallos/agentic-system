"""Where mypy checks the in-memory doubles against the ports (GH-227, ADR-0021's addendum).

`ports_of_doubles` is never run to prove anything: it exists to be type-checked. Every
`Ports` field is filled with a real double class, so a double that stops matching its
protocol, or a new required `Ports` field with no double, is a mypy error. The protocols
that are not `Ports` fields are assigned one by one in `_never_called`, or named in
`UNCOVERED` with the reason mypy cannot see their stand-ins.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentic.core.process.application.limits import RunLimits
from agentic.core.process.application.ports import (
    Launcher,
    Ports,
    Presubmit,
    RunLock,
    RunRegistry,
    SchemaGuard,
)
from tests.fakes import (
    FakeLauncher,
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


def ports_of_doubles() -> Ports:
    return Ports(
        runner=ScriptedRunner(),
        documents=MemoryDocuments(),
        knowledge=MemoryKnowledge(),
        prompts=MemoryPrompts(),
        flags=MemoryFlags(),
        inbox=MemoryInbox(),
        workspace=MemoryWorkspace(),
        tracker=MemoryTracker(),
        stack=MemoryStack(),
        commands=MemoryCommands(),
        sample_app=MemorySampleApp(),
        limits=RunLimits(),
    )


def _never_called() -> None:
    registry: RunRegistry = MemoryRunRegistry()
    launcher: Launcher = FakeLauncher()
    del registry, launcher


# The protocols of `ports.py` that are not `Ports` fields and whose double is checked above.
COVERED: frozenset[type] = frozenset({RunRegistry, Launcher})

_INLINE = "stay unchecked because they live inline in test modules outside mypy's scope"

# The protocols whose stand-ins live inline in unchecked test modules. The reason names every
# stand-in by `tests/` path and line, so moving one into a checked module is a visible edit.
UNCOVERED: Mapping[type, str] = {
    Presubmit: (
        "stand-ins at tests/core/process/application/test_coverage_guard.py:69, "
        "tests/core/process/adapters/claude_sdk/test_hooks.py:221, "
        "tests/core/process/adapters/claude_sdk/test_runner.py:1000, "
        "tests/core/process/adapters/claude_sdk/test_runner.py:1029, "
        "tests/core/process/adapters/claude_sdk/test_runner.py:1064 and "
        f"tests/core/process/adapters/claude_sdk/test_runner.py:1266 {_INLINE}"
    ),
    SchemaGuard: (
        "stand-ins at tests/core/process/adapters/claude_sdk/test_hooks.py:244 and "
        f"tests/core/process/adapters/claude_sdk/test_runner.py:1083 {_INLINE}"
    ),
    RunLock: (
        "stand-ins at tests/test_cli.py:767, tests/test_cli.py:2765 and "
        f"tests/core/process/application/test_poll.py:50 {_INLINE}"
    ),
}
