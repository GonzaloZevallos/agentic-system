"""The end of an evaluation run (GH-470, part 2): where `after_integrate` sends a task that
carries `evaluation` instead of `deliver`. Nothing is pushed, no pull request opens and the
tracker hears nothing; the task's checkouts are released and its document is stamped
`evaluated`, which the driver copies out of the root. No role runs and no model is paid."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PJM
from ..domain.task import EvaluatedPatch, TaskState, apply
from .ports import Ports
from .release import release_task


async def evaluated(state: TaskState, cfg: Configuration, ports: Ports) -> EvaluatedPatch:
    reaped = release_task(ports, state["id"])
    ended = rules.finished("evaluated", datetime.now(UTC).isoformat(timespec="seconds"))
    patch: EvaluatedPatch = {"current_role": PJM.name, **ended}
    if reaped:
        patch["reaped_at_cleanup"] = list(reaped)
    ports.documents.save(apply(state, patch))
    return patch
