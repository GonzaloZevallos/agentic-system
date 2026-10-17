"""The wait for a spawned child to show up in the run registry (issue #137, GH-270): the
poller's after each spawn, and the `land` child's after it spawned the resume it owes. It
lives apart so the landing can use it without importing the poller."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.rules import RunKind
from .ports import RunRegistry

REGISTER_WAIT_S = 60.0
REGISTER_POLL_INTERVAL_S = 1.0


def await_registration(
    registry: RunRegistry,
    task_id: str,
    timeout_s: float,
    sleep: Callable[[float], None],
    *,
    kind: RunKind | None = None,
    product: str | None = None,
) -> bool:
    """Up to `timeout_s`, in `REGISTER_POLL_INTERVAL_S` steps, until a child of `task_id`
    shows up in `registry.live()` (Human decision, issue #137): so a second poll round never
    counts a slot this round's own spawn has not registered into yet, which is what let
    a round exceed `AGENTIC_MAX_CONCURRENT_RUNS` before this. `kind` and `product`, when
    given, narrow the match to that kind and that product (GH-270): the resume a land spawned
    is a `role` of its own product, never the land's own entry nor another product's task of
    the same id; with neither, any entry of the task id matches, as before. Returns whether it
    showed up in time -- best-effort only, the same shape `cli._registered`'s own write
    already has: a child that never registers within the timeout is not retried here,
    and the caller goes on rather than hang forever on one that crashed before it could."""
    waited = 0.0
    while True:
        if any(
            r.task_id == task_id
            and (kind is None or r.kind == kind)
            and (product is None or r.product == product)
            for r in registry.live()
        ):
            return True
        if waited >= timeout_s:
            return False
        sleep(REGISTER_POLL_INTERVAL_S)
        waited += REGISTER_POLL_INTERVAL_S
