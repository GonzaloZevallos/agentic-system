"""The `full_checks` node (GH-358): the whole suite, once, on the integrated head.

A product that declares `delivery_checks` gates each delivery on the fast tier only; this node
is where the rest of `checks` runs, with no affected form substituted, before the pull request
opens (`rules.after_integrate` sends the task here once every unit is approved). No role runs
and nothing is paid: it is the system running the product's own commands.

In order: the task checkout (a setup that fails escalates as `deliver`'s does), a clean tree
required, every declared check run once through the same `CheckoutPresubmit` the merge gate
uses, the tree put back as its head holds it whatever the checks did (`restore_clean`, never
`-x`), the record and the `Full checks` section written. A red run reopens the integration
with one row per failed check (`rules.full_checks_return`); a green one writes a passed record
and nothing else, and the fan-in goes on."""

from __future__ import annotations

import time
from typing import Any

from pydantic import TypeAdapter

from agentic.shared.configuration import Configuration

from ..domain import presubmit, rules
from ..domain.check_schedule import CheckPriority
from ..domain.roles import DEVELOPER_INTEGRATOR
from ..domain.task import WAITING_ON_HUMAN, FullCheckEntry, FullChecksRecord, TaskState
from .checkouts import SetupFailed, task_base, task_checkout
from .ports import Ports
from .presubmit import CheckoutPresubmit
from .run_role import finish

SECTION = "Full checks"
_NOTHING_BEFORE = ""
_ENTRIES = TypeAdapter(list[FullCheckEntry])


async def full_checks(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    try:
        task = task_checkout(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    workspace = ports.workspace
    if not workspace.is_clean(task.path):
        return finish(state, _dirty(task.branch), ports)
    base = task_base(state, ports)
    declared = ports.stack.checks(base)
    gate = CheckoutPresubmit(
        ports,
        task.path,
        _NOTHING_BEFORE,
        declared,
        cfg.check_timeout_s,
        priority=CheckPriority.INTEGRATION,
        wait_max_s=cfg.check_wait_max_s,
        witness=ports.stack.witness(base),
        parallel=ports.stack.parallel_checks(base),
    )
    head = workspace.full_head(task.path)
    started = time.monotonic()
    try:
        # Called once, with no refusal loop and no affected plan: the suite as declared.
        gate()
    finally:
        # A check that edits a tracked file or writes an untracked one must not refuse the
        # next delivery of a reopened unit, nor the landing's own clean-tree checks.
        workspace.restore_clean(task.path)
    merged = presubmit.merge_checks_record(
        head=head,
        master=base,
        duration_s=round(time.monotonic() - started, 1),
        declared=declared,
        results=gate.results,
        witness=gate.witness_result,
    )
    record = _record(state, merged, declared, gate)
    text = presubmit.failed_checks_markdown(merged, head, neutralise=False)
    if record["passed"]:
        patch: TaskState = {"full_checks": record}
        text = f"Passed on `{head}`: {', '.join(declared)}."
    else:
        patch = rules.full_checks_return(state, record, cfg.max_changes_requested_per_pair)
        text = text or f"Failed on `{head}`: no check output was recorded."
    patch["sections"] = _section(state, record["version"], text)
    return finish(state, patch, ports)


def _record(
    state: TaskState, merged: dict[str, object], declared: dict[str, str], gate: CheckoutPresubmit
) -> FullChecksRecord:
    previous = state.get("full_checks")
    record: FullChecksRecord = {
        "version": previous["version"] + 1 if previous else 1,
        "head": str(merged["head"]),
        "passed": merged["passed"] is True,
        "duration_s": float(str(merged["duration_s"])),
        "declared": dict(declared),
        "results": _without_output(_ENTRIES.validate_python(merged["checks"])),
        "witness": dict(gate.witness_result) if gate.witness_result is not None else None,
    }
    if not record["passed"]:
        record["failed_step"] = str(merged["failed_step"])
    return record


def _without_output(entries: list[FullCheckEntry]) -> list[FullCheckEntry]:
    """The name and verdict of each check: the record holds no command output (the `tail` the
    merge record keeps is free text); the `Full checks` section is where it is read."""
    return [{"name": entry["name"], "passed": entry["passed"]} for entry in entries]


def _section(state: TaskState, version: int, text: str) -> dict[str, str]:
    """The new section, and its previous run kept as history under the superseded name."""
    sections = {SECTION: text}
    previous = state.get("sections", {}).get(SECTION)
    if previous is not None:
        sections[rules.superseded(SECTION, version - 1)] = previous
    return sections


def _dirty(branch: str) -> dict[str, Any]:
    """The task checkout holds uncommitted work the suite would be run over: no command
    runs, and nothing is cleaned, which could throw away what a human left there."""
    return rules.logged(
        {
            "current_role": DEVELOPER_INTEGRATOR.name,
            "status": WAITING_ON_HUMAN,
            **rules.escalation(
                "setup",
                f"the task checkout of {branch!r} has uncommitted changes, so the full suite "
                "would not be a run of the integrated head. Commit or discard them and resume.",
            ),
        }
    )
