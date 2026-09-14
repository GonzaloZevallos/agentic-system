"""What the process adapters need from tracing, as a port (ADR-0010).

The graph opens a span per role and stamps the use case's patch on it; the SDK
runner hands the current trace parent to the Claude Code subprocess. Who records
the spans is `core.measurement`'s business: `composition` wires it in, and nothing
under `core.process` imports it. `NoTelemetry` is the default when nobody does.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Protocol

from ..domain.task import TaskState


class RoleSpan(Protocol):
    def finish(self, patch: TaskState) -> None: ...


class Telemetry(Protocol):
    def role_span(self, role_name: str) -> AbstractContextManager[RoleSpan]: ...

    def traceparent(self) -> str | None: ...


class _NoSpan:
    def finish(self, patch: TaskState) -> None:
        return None


class NoTelemetry:
    def role_span(self, role_name: str) -> AbstractContextManager[RoleSpan]:
        return nullcontext(_NoSpan())

    def traceparent(self) -> str | None:
        return None
