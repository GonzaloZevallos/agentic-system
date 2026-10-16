"""GH-267, subtask `abort`: no landing step leaves a merge in progress (the abort is
`landing_sync.merges_cleared`'s), and `land` leaves a merge that is not the system's alone.
Over the memory ports; the real-git counterpart is
`tests/core/process/adapters/git/test_workspace_abort.py`."""

from __future__ import annotations

from agentic.core.process.application import landing
from agentic.core.process.application.ports import Upstream, UpstreamMerge
from tests.core.process.application.test_landing_sync import (
    HEAD,
    LIVE,
    MASTER,
    MASTER_CLEAN,
    _behind,
    _document,
    _unit,
)
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import _HeldLocks

STRANGER = "e" * 40
CONFLICT = UpstreamMerge(clean=False, conflicted=("shared.py",), head=HEAD)


def _land(ports, tmp_path, log, resumed):
    def resume(number: int) -> int:
        resumed.append(number)
        return 777

    return landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        log.append,
        clock=lambda: "2026-10-14T00:00:00+00:00",
        live=LIVE,
        resume=resume,
    )


def _saves(ports):
    """Every `documents.save` as an `events` mark, so an abort can be ordered against it."""
    save = ports.documents.save

    def recording(document):
        ports.workspace.events.append("save")
        save(document)

    ports.documents.save = recording


def test_a_conflicting_sync_aborts_the_merge_before_the_unit_is_reopened(tmp_path):
    ports, _ = _behind(tmp_path)
    ports.workspace.upstream_merges[MASTER] = CONFLICT
    _saves(ports)
    checkout = ports.workspace.task_checkout("GH-5").path
    resumed: list[int] = []

    result = _land(ports, tmp_path, [], resumed)

    assert result.kind == "dispatched" and resumed == [5]
    assert ports.workspace.merge_head(checkout) is None
    events = ports.workspace.events
    assert events.index("abort_merge") < events.index("save")
    reason = _unit(ports)["reason"]
    assert f"git merge {MASTER}" in reason and "shared.py" in reason
    assert "aborted" in reason


def test_a_sync_git_refuses_aborts_what_it_left_and_reopens_from_a_clean_tree(tmp_path):
    ports, _ = _behind(tmp_path)
    ports.workspace.upstream_merge_error = "refused"
    checkout = ports.workspace.task_checkout("GH-5").path
    ports.workspace.merge_heads[checkout] = MASTER  # what the refused merge left behind
    _saves(ports)

    _land(ports, tmp_path, [], [])

    events = ports.workspace.events
    assert events.index("abort_merge") < events.index("save")
    assert "syncing" in _unit(ports)["reason"]


def test_an_abort_that_fails_reopens_and_spawns_nothing(tmp_path):
    ports, _ = _behind(tmp_path)
    ports.workspace.upstream_merges[MASTER] = CONFLICT
    ports.workspace.abort_error = "cannot abort"
    resumed: list[int] = []
    log: list[str] = []

    result = _land(ports, tmp_path, log, resumed)

    assert result.kind == "stopped" and "cannot abort" in (result.reason or "")
    assert resumed == []
    assert _unit(ports)["status"] == "approved"  # never reopened on a tree mid-merge


def test_a_clean_sync_clears_a_stale_merge_of_the_system_and_goes_on(tmp_path):
    ports, _ = _behind(tmp_path)
    checkout = ports.workspace.task_checkout("GH-5").path
    ports.workspace.merge_heads[checkout] = HEAD  # a killed pass's merge of the pull head
    ports.workspace.upstream_merges[MASTER] = MASTER_CLEAN

    result = _land(ports, tmp_path, [], [])

    assert result.kind == "dispatched" and ports.workspace.aborted == [checkout]
    assert ports.workspace.merge_head(checkout) is None
    assert ports.workspace.pushed  # the sync went through


def test_land_leaves_a_merge_that_is_not_the_systems_alone(tmp_path):
    ports, _ = _behind(tmp_path)
    checkout = ports.workspace.task_checkout("GH-5").path
    ports.workspace.merge_heads[checkout] = STRANGER
    before = _document(ports).get("landing")
    log: list[str] = []
    resumed: list[int] = []

    result = _land(ports, tmp_path, log, resumed)

    assert (result.kind, result.reason, result.step) == (
        "skipped",
        "foreign_merge",
        "sync_master",
    )
    assert _document(ports).get("landing") == before  # nothing recorded
    assert ports.workspace.aborted == [] and ports.workspace.upstream_merge_calls == []
    assert resumed == []
    assert ports.workspace.merge_head(checkout) == STRANGER
    assert any("is not the system's, left alone" in line and STRANGER in line for line in log)


def test_land_goes_on_when_the_merge_in_progress_is_a_recorded_upstream(tmp_path):
    previous = {
        "head": "9" * 40,
        "step": "sync_master",
        "refusal": "x",
        "at": "t",
        "upstream": STRANGER,
    }
    ports, _ = _behind(tmp_path, landing=[previous])
    checkout = ports.workspace.task_checkout("GH-5").path
    ports.workspace.merge_heads[checkout] = STRANGER

    result = _land(ports, tmp_path, [], [])

    assert result.kind == "dispatched" and ports.workspace.aborted == [checkout]


def test_land_reports_a_merge_head_it_cannot_read_as_stopped(tmp_path):
    ports, _ = _behind(tmp_path)
    ports.workspace.merge_head_error = "not a git repository"

    result = _land(ports, tmp_path, [], [])

    assert result.kind == "stopped" and "not a git repository" in (result.reason or "")
