"""The child process `tests/test_stop_signal.py` signals (GH-355): not a test, a script.

It runs `cli._signal_guarded` the way `agentic run` does -- the real `ShellCommands`, the real
`ShellReaper`, a real `FilesystemRunLock`, a real `StopCause` with a marking flag -- over a
temporary git repository, around one scenario that is busy on a `sleep 300` when the test
sends it a real SIGTERM from outside:

- `gate`: a fake role hook running the real delivery gate (`hooks._delivery_gate`);
- `full_checks`: the real `full_checks` node, on the graph `build_graph` compiles, with the
  SQLite checkpointer of production;
- `setup`: the real `subtask_checkout_in_thread`, whose product setup is the sleep;
- `escapes`: a check that ignores SIGTERM and leaves a grandchild outside its process group,
  beside a fake role that keeps a marked child and ignores the cancellation;
- `blocked`: a node that runs a command straight on the event loop, no thread.

Invoked as `python -m tests.stop_signal_child <mode> <scenario> <product> <workdir> <deadline>`.
Mode `run` is the process under test; mode `resume` is the resume the stop must leave possible:
it reads what the stopped run left and runs the same step again with a command that ends.
The workdir holds the pid files the scenario writes (outside the checkout, which must stay
clean) and the JSON report. `run` writes its report only when it exits by the SystemExit the
guard raises; the hard deadline's `os._exit` leaves none, which is the point of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import anyio

from agentic import cli, composition
from agentic.composition import default_ports
from agentic.core.process.adapters.claude_sdk import hooks
from agentic.core.process.adapters.langgraph.graph import build_graph
from agentic.core.process.adapters.shell import EventStopFlag
from agentic.core.process.application.checkouts import subtask_checkout, subtask_checkout_in_thread
from agentic.core.process.application.ports import Ports
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain import reaping
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack

TASK = "GH-1"
SUBTASKS = ("api", "ui")


def _pidfile_check(workdir: Path) -> str:
    return f"echo $$ > {workdir}/check.pid; exec sleep 300"


def _ignoring_check(workdir: Path) -> str:
    """Ignores SIGTERM (the disposition is inherited by what it starts) and leaves a
    grandchild in a session of its own, which only the mark can still find."""
    return (
        "trap '' TERM; "
        f"setsid sh -c 'echo $$ > {workdir}/grandchild.pid; exec sleep 300' & "
        f"echo $$ > {workdir}/check.pid; wait"
    )


def _stack(scenario: str, workdir: Path, resuming: bool) -> MemoryStack:
    sleeping = _ignoring_check(workdir) if scenario == "escapes" else _pidfile_check(workdir)
    if scenario == "setup":
        return MemoryStack(
            checks={}, setup={"install": f"touch {workdir}/setup.ran" if resuming else sleeping}
        )
    slow = "true" if resuming else sleeping
    if scenario == "full_checks":
        return MemoryStack(checks={"fast": "true", "slow": slow}, delivery_checks=("fast",))
    return MemoryStack(checks={"slow": slow})


def _subtasks() -> dict[str, Any]:
    return {
        sid: {
            "id": sid,
            "title": sid,
            "brief": "b",
            "seams": ["s"],
            "depends_on": [],
            "status": "approved",
            "branch": f"agentic/{TASK}.{sid}",
            "base": "base",
        }
        for sid in SUBTASKS
    }


def _state(**extra: Any) -> dict[str, Any]:
    return {"id": TASK, "title": "t", "description": "d", "subtasks": _subtasks(), **extra}


def _parts(scenario: str, product: Path, workdir: Path, resuming: bool, flag: EventStopFlag) -> Any:
    cfg = Configuration(product_dir=product)
    ports = replace(default_ports(cfg, flag), stack=_stack(scenario, workdir, resuming))
    return cfg, ports


def _config(product: Path) -> dict[str, Any]:
    return {
        "configurable": {"product_dir": str(product), "thread_id": TASK},
        "recursion_limit": 40,
    }


def _graph(ports: Ports) -> Any:
    def ports_for(_cfg: Configuration) -> Ports:
        return ports

    return build_graph(ports_for, arm_for=composition._arm_for)


async def _run_gate(ports: Ports, workdir: Path, box: dict[str, Any]) -> None:
    """What a role's delivery does: the hook the SDK calls, over the real presubmit."""
    task = ports.workspace.task_checkout(TASK)
    gate = CheckoutPresubmit(ports, task.path, "", ports.stack.checks("HEAD"), 600)
    box["gate"] = gate
    hook = hooks._delivery_gate(None, gate)
    await hook(cast(Any, {}), None, cast(Any, None))


def _marked_role(cfg: Configuration, workdir: Path) -> subprocess.Popen[bytes]:
    """A role's child process: it carries the mark of this run's attempt."""
    key = reaping.product_key(str(cfg.product_dir.resolve()))
    mark = reaping.mark(key, TASK, os.getpid(), uuid4().hex)
    return subprocess.Popen(
        ["sh", "-c", f"echo $$ > {workdir}/role.pid; exec sleep 300"],
        env={**os.environ, reaping.MARK_NAME: mark},
        start_new_session=True,
    )


async def _scenario(
    scenario: str,
    cfg: Configuration,
    ports: Ports,
    product: Path,
    workdir: Path,
    box: dict[str, Any],
) -> None:
    if scenario == "gate":
        await _run_gate(ports, workdir, box)
    elif scenario == "escapes":
        role = _marked_role(cfg, workdir)
        box["role"] = role

        async def role_that_ignores_cancellation() -> None:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(1000)

        async with anyio.create_task_group() as tg:
            tg.start_soon(role_that_ignores_cancellation)
            tg.start_soon(_run_gate, ports, workdir, box)
    elif scenario == "blocked":
        task = ports.workspace.task_checkout(TASK)
        # The bug itself: a product command run straight on the loop, no thread.
        ports.commands.run(task.path, ports.stack.checks("HEAD")["slow"], 600)
    elif scenario == "setup":
        # The task's checkout exists already, so the setup that runs is the subtask's.
        ports.workspace.task_checkout(TASK)
        await subtask_checkout_in_thread(_state(), SUBTASKS[0], cfg, ports)
    elif scenario == "full_checks":
        await _run_graph(cfg, ports, product, box)
    else:
        raise SystemExit(f"unknown scenario {scenario}")


async def _run_graph(cfg: Configuration, ports: Ports, product: Path, box: dict[str, Any]) -> None:
    task = ports.workspace.task_checkout(TASK)
    head = ports.workspace.full_head(task.path)
    async with composition.checkpointer(cfg, TASK) as saver:
        app = _graph(ports).compile(checkpointer=saver)
        seeded = _state(type="feature", branch=f"agentic/{TASK}", task_head=head)
        # The fan-in has run: the next node the graph reaches is `full_checks`.
        await app.aupdate_state(_config(product), seeded, as_node="integrate")
        await app.ainvoke(None, config=_config(product))


def run(scenario: str, product: Path, workdir: Path, deadline_s: float) -> None:
    flag = EventStopFlag()
    cfg, ports = _parts(scenario, product, workdir, False, flag)
    lock = cli._lock_for(cfg)
    stop = cli.StopCause(flag=flag, reaper=ports.reaper)
    box: dict[str, Any] = {}

    async def work() -> None:
        with cli._locked(lock, TASK, stop):
            await _scenario(scenario, cfg, ports, product, workdir, box)

    try:
        anyio.run(partial(cli._signal_guarded, work, stop, deadline_s=deadline_s))
    except SystemExit:
        gate = box.get("gate")
        report = {"flakes": gate.flakes() if gate is not None else []}
        (workdir / "report.json").write_text(json.dumps(report))
        raise


async def _resume(scenario: str, product: Path, workdir: Path) -> dict[str, Any]:
    cfg, ports = _parts(scenario, product, workdir, True, EventStopFlag())
    report: dict[str, Any] = {}
    if scenario == "setup":
        checkout = subtask_checkout(_state(), SUBTASKS[0], cfg, ports)
        report["created"] = checkout.created
        report["setup_ran"] = (workdir / "setup.ran").exists()
        return report
    async with composition.checkpointer(cfg, TASK) as saver:
        app = _graph(ports).compile(checkpointer=saver, interrupt_after=["full_checks"])
        before = await app.aget_state(_config(product))
        saved = await saver.aget_tuple(_config(product))
        report["next"] = list(before.next)
        report["errors"] = [t.error for t in before.tasks if t.error]
        report["pending_writes"] = len(saved.pending_writes or []) if saved else 0
        report["before"] = before.values.get("full_checks")
        after = await app.ainvoke(None, config=_config(product))
        report["after"] = after.get("full_checks")
    return report


def resume(scenario: str, product: Path, workdir: Path) -> None:
    report = anyio.run(_resume, scenario, product, workdir)
    (workdir / "resume.json").write_text(json.dumps(report, default=str))


if __name__ == "__main__":
    mode, name, product_arg, workdir_arg, deadline_arg = sys.argv[1:6]
    if mode == "run":
        run(name, Path(product_arg), Path(workdir_arg), float(deadline_arg))
    else:
        resume(name, Path(product_arg), Path(workdir_arg))
