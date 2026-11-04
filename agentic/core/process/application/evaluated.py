"""The end of an evaluation run (GH-470, part 2): where `after_integrate` sends a task that
carries `evaluation` instead of `deliver`. Nothing is pushed, no pull request opens and the
tracker hears nothing; the task's checkouts are released and its document is stamped
`evaluated`, which the driver copies out of the root. No role runs and no model is paid."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import PJM
from ..domain.task import TaskState
from .ports import Ports
from .release import release_task
from .run_role import finish


async def evaluated(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    reaped = release_task(ports, state["id"])
    ended = rules.finished("evaluated", datetime.now(UTC).isoformat(timespec="seconds"))
    return finish(
        state,
        {
            "current_role": PJM.name,
            **ended,
            **({"reaped_at_cleanup": list(reaped)} if reaped else {}),
        },
        ports,
    )
