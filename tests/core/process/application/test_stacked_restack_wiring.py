"""GH-897, subtask `wiring`: the restack row written at the two places a `sync_master` closes
(`landing_sync._synced`, both branches, and `deliver`) and the PR line, over the memory ports and
the filesystem documents adapter. A document without `stack_parent` is pinned by
`test_stacked_baseline`; here the stacked side, and the absence of any key when not stacked."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import deliver as deliver_node
from agentic.core.process.application import landing_sync
from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.landing_plan import LandingContext
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import task
from agentic.core.process.domain.landing import LandingStep
from agentic.core.process.domain.task import StackParent
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_landing_sync import (
    HEAD,
    MASTER,
    _behind,
    _diffs,
    _document,
)
from tests.core.process.application.test_poll import _cfg
from tests.core.process.domain.test_landing import _facts
from tests.fakes import MemoryStack, memory_ports

AT = "2026-11-17T00:00:00+00:00"
STACKED = StackParent(issue=9, task_id="GH-9", head="c" * 40, started_at="2026-11-16T10:00:00")


class _Clock:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 11, 17, tzinfo=UTC)


def _entry(**more) -> dict:
    return {
        "head": HEAD,
        "step": "sync_master",
        "refusal": "merge_conflict",
        "at": "2026-10-14T00:00:00+00:00",
        "upstream": MASTER,
        "cost_base": 1.0,
        "runs_base": 1,
        **more,
    }


def _landed(tmp_path, monkeypatch, *, brings_nothing: bool, **document) -> dict:
    """The sync step run over a document that already holds its open entry (a stacked task is
    planned by another route, so `land` is not driven here; `sync_master` is the seam)."""
    monkeypatch.setattr(landing_sync, "_now", lambda: AT)
    document = {
        "landing": [_entry()],
        "cost_usd": 3.5,
        "runs": [{"tokens": {"input": 100}}, {"tokens": {"input": 7}}, {"tokens": {"input": 5}}],
        **document,
    }
    ports, _ = _behind(tmp_path, **document)
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))
    ports.workspace.merge_brings_nothing = brings_nothing
    context = LandingContext(
        "GH-5",
        _cfg(tmp_path),
        ports,
        LandingStep("sync_master", "merge_conflict"),
        HEAD,
        Upstream("origin/master", MASTER, None),
        _facts(),
        [].append,
        clock=lambda: AT,
    )
    landing_sync.sync_master(context)
    return dict(_document(ports))


@pytest.mark.parametrize("brings_nothing", [False, True], ids=["resolved", "unsynced"])
def test_a_stacked_sync_closes_with_one_row(tmp_path, monkeypatch, brings_nothing):
    saved = _landed(tmp_path, monkeypatch, brings_nothing=brings_nothing, stack_parent=STACKED)

    assert saved["stack_restacks"] == [
        {
            "at": AT,
            "upstream": MASTER,
            "conflict": True,
            "cost_usd": 2.5,
            "tokens": {"input": 12},
        }
    ]


@pytest.mark.parametrize("brings_nothing", [False, True], ids=["resolved", "unsynced"])
def test_a_sync_without_stack_parent_saves_neither_key(tmp_path, monkeypatch, brings_nothing):
    saved = _landed(tmp_path, monkeypatch, brings_nothing=brings_nothing)

    assert "stack_restacks" not in saved and "stack_pauses" not in saved


def test_an_unreadable_stack_parent_still_gets_its_row(tmp_path, monkeypatch):
    saved = _landed(tmp_path, monkeypatch, brings_nothing=False, stack_parent="garbage")

    assert [row["upstream"] for row in saved["stack_restacks"]] == [MASTER]


def test_an_earlier_row_of_another_upstream_is_kept(tmp_path, monkeypatch):
    earlier = {"at": "2026-11-01T00:00:00+00:00", "upstream": "d" * 40, "conflict": False}
    earlier["cost_usd"] = 1.5
    saved = _landed(
        tmp_path, monkeypatch, brings_nothing=False, stack_parent=STACKED, stack_restacks=[earlier]
    )

    assert [row["upstream"] for row in saved["stack_restacks"]] == ["d" * 40, MASTER]


def test_a_row_already_saved_for_the_same_close_is_not_added_again(tmp_path, monkeypatch):
    row = {"at": AT, "upstream": MASTER, "conflict": True, "cost_usd": 2.5}
    saved = _landed(
        tmp_path, monkeypatch, brings_nothing=False, stack_parent=STACKED, stack_restacks=[row]
    )

    assert [r["upstream"] for r in saved["stack_restacks"]] == [MASTER]
    assert len(saved["stack_restacks"]) == 1


def test_the_row_survives_a_save_then_record_landing_and_a_reload(tmp_path, monkeypatch):
    saved = _landed(tmp_path, monkeypatch, brings_nothing=False, stack_parent=STACKED)
    saved.pop("runs")  # bare fixture runs lack the cost the renderer reads; not under test
    documents = FilesystemTaskDocuments(tmp_path)

    documents.save({"id": "GH-5", "title": "t", "description": "d", **saved})
    documents.record_closure("GH-5", {"landing": saved["landing"]})

    loaded = documents.load("GH-5")
    assert loaded["stack_restacks"] == saved["stack_restacks"]
    assert loaded["landing"] == saved["landing"]


def _state(**more):
    return {
        "id": "GH-12",
        "title": "Dashboard",
        "description": "How is my gym doing.\n\n<!-- agentic-closes: #12 -->",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
        **more,
    }


def _open_sync():
    return _entry(runs_base=0)


async def _delivered(tmp_path, state, ancestors=True):
    monkeypatch_clock = pytest.MonkeyPatch()
    monkeypatch_clock.setattr(deliver_node, "datetime", _Clock)
    try:
        ports = memory_ports(stack=MemoryStack())
        if ancestors:
            ports.workspace.ancestors.add(MASTER)
        patch = await deliver(state, Configuration(product_dir=tmp_path), ports)
    finally:
        monkeypatch_clock.undo()
    return ports, patch


@pytest.mark.anyio
@pytest.mark.parametrize("ancestors", [True, False], ids=["resolved", "unsynced"])
async def test_deliver_of_a_stacked_task_patches_one_row_even_applied_twice(tmp_path, ancestors):
    state = _state(
        stack_parent=STACKED,
        landing=[_open_sync()],
        cost_usd=3.5,
        runs=[{"tokens": {"input": 7}}, {"tokens": {"input": 5}}, {}],
    )

    _, patch = await _delivered(tmp_path, state, ancestors)

    [row] = patch["stack_restacks"]
    assert row == {
        "at": AT,
        "upstream": MASTER,
        "conflict": True,
        "cost_usd": 2.5,
        "tokens": {"input": 12},
    }
    once = task.apply(state, patch)
    assert task.apply(once, patch)["stack_restacks"] == once["stack_restacks"] == [row]


@pytest.mark.anyio
async def test_deliver_of_an_already_closed_last_entry_adds_no_row(tmp_path):
    closed = {**_open_sync(), "resolved_at": "2026-10-15T00:00:00+00:00", "cost_usd": 1.0}
    state = _state(stack_parent=STACKED, landing=[closed], cost_usd=3.5)

    _, patch = await _delivered(tmp_path, state)

    assert "stack_restacks" not in patch


@pytest.mark.anyio
async def test_deliver_of_an_unstacked_task_patches_neither_key(tmp_path):
    state = _state(landing=[_open_sync()], cost_usd=3.5)

    _, patch = await _delivered(tmp_path, state)

    assert "stack_restacks" not in patch and "stack_pauses" not in patch
    assert patch["landing"][0]["resolved_at"] == AT


async def _body(tmp_path, **more) -> str:
    ports = memory_ports(stack=MemoryStack())
    await deliver(_state(**more), Configuration(product_dir=tmp_path), ports)
    return ports.tracker.pull_requests[0][2]


@pytest.mark.anyio
async def test_the_pr_line_follows_a_readable_stack_parent_only(tmp_path):
    plain = await _body(tmp_path)

    stacked = await _body(tmp_path, stack_parent=STACKED)
    unreadable = await _body(tmp_path, stack_parent={"issue": "nine"})

    assert stacked == plain.replace(
        "Closes #12\n\n", "Closes #12\n\nStacked on #9: merges only after it.\n\n"
    )
    assert "Stacked on" in stacked and "Stacked on" not in unreadable
    assert unreadable == plain
