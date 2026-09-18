"""The diagnosis of a bug (README.md, 2 and 4; issue #65): the Product Analyst
reproduces it in the task's checkout, reading the code and running the product's
checks, compares what it saw with the written definition of the capability, and
rules: bug, expected behaviour, or undefined, when the product never wrote down what
it does there. PM validates the ruling after it, or decides it.

`define` without the interview: nothing to interview PM about before looking, and
what the Product Analyst cannot settle travels to PM as questions in its section. On a
return from PM it runs again over its own previous section and PM's, as on a feature.
It writes nothing on disk and commits nothing: reproducing is reading and running,
not executing (glossary: Ejecutar)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import presubmit
from ..domain.roles import PRODUCT_ANALYST_BUG, diagnoser_for
from ..domain.task import TaskState
from .checkouts import SetupFailed, task_checkout
from .ports import Ports
from .presubmit import declared_checks
from .run_role import finish, run_role


async def diagnose(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    # The task's checkout is born here on a bug, as it is in `define` on a feature: the
    # Product Analyst reproduces in it, and this is where the product's setup can fail.
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(PRODUCT_ANALYST_BUG), ports)
    checks = declared_checks(state, ports)
    output, patch = await run_role(
        state,
        diagnoser_for(checks),
        cfg,
        ports,
        cwd=task.path,
        checks=presubmit.describe_checks(checks),
    )
    patch["branch"] = task.branch
    if output is not None:
        # `defined`: the status the routing and a resume already read as "there is
        # something PM has to validate", whatever the path called it.
        patch["status"] = "defined"
    return finish(state, patch, ports)
