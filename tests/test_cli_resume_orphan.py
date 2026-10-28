"""GH-403: the retake of an orphan is `agentic resume --from-document`; the cli resume function
takes the document branch whatever the checkpoint holds, so the poller's child never refuses."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic import cli
from agentic.core.process.domain.task import TaskState
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports


class _Saver:
    """A checkpointer that opens an empty store: what the thread holds is `_thread`'s."""

    @asynccontextmanager
    async def __call__(self, cfg, task_id):
        yield InMemorySaver()


class _Graph:
    """The compiled graph `_resume` asks for the thread's snapshot: `values` and `next` are
    what a run left in its checkpoint, so each case needs no real run to get there."""

    def __init__(self, values: dict, pending: tuple[str, ...]) -> None:
        self.snapshot = SimpleNamespace(values=values, next=pending, interrupts=())

    def compile(self, checkpointer):
        return self

    async def aget_state(self, config):
        return self.snapshot


def _thread(monkeypatch, values: dict, pending: tuple[str, ...]) -> None:
    monkeypatch.setattr(cli, "graph", _Graph(values, pending))


def _ports():
    ports = memory_ports()
    doc: TaskState = {"id": "GH-1", "title": "Export", "description": "CSV.", "status": "planned"}
    ports.documents.save(doc)
    return ports


@pytest.mark.anyio
async def test_from_document_over_a_checkpoint_with_a_pending_node_takes_the_document(
    tmp_path, capsys, monkeypatch
):
    _thread(monkeypatch, {"id": "GH-1", "status": "developing"}, ("developer",))
    cfg = Configuration(product_dir=tmp_path)
    await cli._resume(
        "GH-1", cfg, _ports(), from_document=True, dry_run=True, checkpointer=_Saver()
    )
    assert "from:      document" in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_checkpoint_with_nothing_next_and_status_planned_needs_the_flag(
    tmp_path, capsys, monkeypatch
):
    # The rejected input: a thread with values, nothing pending, status planned. Without
    # `--from-document` it is "not paused" (today's refusal); with it, the document wins.
    _thread(monkeypatch, {"id": "GH-1", "status": "planned"}, ())
    cfg = Configuration(product_dir=tmp_path)
    with pytest.raises(cli.CannotStart, match=r"is not paused: its run ended \(planned\)"):
        await cli._resume("GH-1", cfg, _ports(), dry_run=True, checkpointer=_Saver())
    await cli._resume(
        "GH-1", cfg, _ports(), from_document=True, dry_run=True, checkpointer=_Saver()
    )
    assert "from:      document" in capsys.readouterr().out


@pytest.mark.anyio
async def test_from_document_with_no_checkpoint_values_resumes_from_the_document(
    tmp_path, capsys, monkeypatch
):
    _thread(monkeypatch, {}, ())
    cfg = Configuration(product_dir=tmp_path)
    await cli._resume(
        "GH-1", cfg, _ports(), from_document=True, dry_run=True, checkpointer=_Saver()
    )
    assert "from:      document" in capsys.readouterr().out
