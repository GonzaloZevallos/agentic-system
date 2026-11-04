"""The verifiers of the integrated head, together (GH-469).

`copy_review`, `final_validation`, `qa_performance` and `full_checks` only read the head the
fan-in approved, so they no longer wait for one another. `verify` is the one place that
decides which are due (`rules.verifiers_due`), resolves the task's checkout once, checks the
tree is clean once, starts the due ones in a task group and folds what they return into one
patch (`rules.combined_return`) in the fixed verifier order, never the order they finish in.

`full_checks` runs first and alone when it does not wait on anything slow: the suite on this
machine (it cleans the tree it dirtied with `restore_clean` before any sibling starts, so no
role ever sees it) or a CI verdict already pushed for this head. A red or escalating run
returns without a model being paid for. Only when a CI verdict has to be waited for
(`full_checks_mode` is `wait`) does it join the others.

A verifier that raises never takes its siblings down: the others finish, their merged delta
is written to the document and the first exception, in verifier order, is raised. A verifier
that escalates leaves the others' deltas in the combined patch. When nothing returned and
nothing escalated, the capability files `final_validation` approved with are committed here,
once, on the head that was judged (`rules.close_docs_committable`); files owed on another
head are dropped."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

import anyio

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.roles import DEVELOPER_INTEGRATOR, PM_FINAL, QA_PERFORMANCE, TECH_WRITER_REVIEW, Role
from ..domain.rules import Verifier
from ..domain.task import TaskState, apply
from .checkouts import SetupFailed, task_checkout_in_thread
from .final_validation import commit_close_docs
from .full_checks import dirty_patch, full_checks_mode
from .ports import Checkout, Ports
from .run_role import finish

# What one verifier does, supplied by the adapter: it runs on the snapshot and the checkout it
# is given and returns the delta it produced (`task.delta`), never the whole state.
Run = Callable[[TaskState, Checkout], Awaitable[TaskState]]

_ROLE: Mapping[Verifier, Role] = {
    "full_checks": DEVELOPER_INTEGRATOR,
    "copy_review": TECH_WRITER_REVIEW,
    "final_validation": PM_FINAL,
    "qa_performance": QA_PERFORMANCE,
}


def _stops(state: TaskState, patch: TaskState) -> bool:
    """Whether `patch` ends the set: a unit was reopened or the task escalated."""
    after = apply(state, patch)
    return bool(after.get("escalated")) or not rules.all_approved(after)


async def verify(
    state: TaskState, cfg: Configuration, ports: Ports, run: Mapping[Verifier, Run]
) -> TaskState:
    due = rules.verifiers_due(state)
    if not due and not state.get("pending_close_docs"):
        return {}
    try:
        checkout = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        role = _ROLE[due[0]] if due else DEVELOPER_INTEGRATOR
        return finish(state, unprepared.patch(role), ports)
    if not ports.workspace.is_clean(checkout.path):
        return finish(state, dirty_patch(checkout.branch), ports)

    patches: dict[Verifier, TaskState] = {}
    snapshot = state
    concurrent = list(due)
    if "full_checks" in due and full_checks_mode(state, cfg, ports, checkout) != "wait":
        concurrent.remove("full_checks")
        patches["full_checks"] = await run["full_checks"](state, checkout)
        if _stops(state, patches["full_checks"]):
            return finish(state, rules.combined_return(state, patches), ports)
        snapshot = apply(state, patches["full_checks"])

    errors: dict[Verifier, Exception] = {}

    async def guarded(name: Verifier) -> None:
        # Captured, never raised into the group: a failing verifier must not cancel the
        # siblings that are still judging the same head.
        try:
            patches[name] = await run[name](snapshot, checkout)
        except Exception as error:
            errors[name] = error

    async with anyio.create_task_group() as group:
        for name in concurrent:
            group.start_soon(guarded, name)

    merged = rules.combined_return(state, patches) if patches else {}
    if errors:
        if patches:
            finish(state, merged, ports)
        raise errors[next(name for name in rules.VERIFIER_ORDER if name in errors)]

    after = apply(state, merged)
    if not _stops(state, merged):
        if rules.close_docs_committable(after):
            merged = apply(merged, commit_close_docs(after, ports, checkout.path))
        elif rules.close_docs_stale(after):
            merged = apply(merged, {"pending_close_docs": None})
    return finish(state, merged, ports)
