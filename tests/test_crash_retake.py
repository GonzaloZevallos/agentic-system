"""GH-308, the requested edge test: a run whose graph raises during planning (the shape of
GH-295, `ValueError: ... no value for placeholder`) leaves the task where the next poll
round retakes it. Nothing here writes the lock by hand: the real `cli._run` path leaves
the record, through `_locked`, over a real `FilesystemRunLock`; the round then reads it."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import cli
from agentic.core.process.adapters.filesystem import FilesystemRunLock
from agentic.core.process.application import poll
from agentic.shared.configuration import Configuration
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

BOOT = "boot-1"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class _RaisesDuringPlanning:
    """A graph double whose `ainvoke` raises the way GH-295's planning did."""

    def compile(self, checkpointer):
        return self

    async def aget_state(self, config):
        return SimpleNamespace(values={}, next=(), interrupts=())

    async def aupdate_state(self, config, values, as_node=None):
        pass

    async def ainvoke(self, task, config):
        raise ValueError("tech_lead_platform.md: no value for placeholder {{cost_history}}")


class _Saver:
    def __init__(self):
        self.saver = InMemorySaver()

    def __call__(self, cfg, task_id):
        saver = self.saver

        class _Open:
            async def __aenter__(self):
                return saver

            async def __aexit__(self, *exc):
                return None

        return _Open()


async def _crash_a_run(monkeypatch, tmp_path, version: str):
    """Drives the cli run path into a graph exception; returns the lock it left."""
    monkeypatch.setattr(cli, "graph", _RaisesDuringPlanning())
    monkeypatch.setattr(cli.telemetry, "system_version", lambda: version)
    lock = FilesystemRunLock(tmp_path / "runs", boot_id=lambda: BOOT)
    task = {"id": "GH-295", "title": "t", "description": "d"}
    with pytest.raises(ValueError, match="no value for placeholder"):
        await cli._run(
            task,
            Configuration(product_dir=tmp_path),
            memory_ports(),
            checkpointer=_Saver(),
            run_lock=lock,
            stop=cli.StopCause(),
        )
    return lock


def _round(lock, ports, launcher, version):
    return poll.run_once(
        Configuration(product_dir=lock.runs_dir.parent),
        ports,
        MemoryRunRegistry(),
        launcher,
        NOW,
        "agentic",
        sleep=lambda seconds: None,
        lock=lock,
        current_boot=BOOT,
        system_version=lambda: version,
    )


@pytest.mark.anyio
async def test_a_run_that_raised_during_planning_is_resumed_by_the_next_poll_round(
    monkeypatch, tmp_path
):
    lock = await _crash_a_run(monkeypatch, tmp_path, "abc1234")
    ports = memory_ports()
    # What the nodes had already written before the exception: open, nothing escalated.
    ports.documents.save({"id": "GH-295", "status": "open", "current_role": "PjM"})
    launcher = FakeLauncher()

    lines = _round(lock, ports, launcher, "abc1234")

    assert launcher.spawned == [
        [
            "agentic",
            "resume",
            "--issue",
            "295",
            "--yes",
            "--detach",
            "--product-dir",
            str(tmp_path),
        ]
    ]
    assert lines[0] == "agentic: poll: retake #295 -> pid 4242"
    [(task_id, body)] = ports.tracker.comments
    assert task_id == "GH-295"
    assert body.startswith("Retaken after crash: ")
    assert "resuming under abc1234 " in body
    assert "(retake 1 of 2)" in body


@pytest.mark.anyio
async def test_the_same_round_leaves_a_crash_with_no_known_version_to_the_human(
    monkeypatch, tmp_path
):
    """The input the retake must reject: a system version that says nothing."""
    lock = await _crash_a_run(monkeypatch, tmp_path, "unknown")
    ports = memory_ports()
    ports.documents.save({"id": "GH-295", "status": "open", "current_role": "PjM"})
    launcher = FakeLauncher()

    lines = _round(lock, ports, launcher, "unknown")

    assert launcher.spawned == [] and ports.tracker.comments == []
    assert "#295 crashed, system version unknown" in lines[0]
