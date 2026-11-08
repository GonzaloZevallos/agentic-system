"""GH-500: a `merge` refused for `ci_untrusted_no_dispatch` wrote nothing, so the poller started
it again every round. The verb now writes `merge_refusal: {reason, head, master, at}` and the
poller, on the same head and master, holds the merge back (`Deferred(backoff)`) until either
moves or `EXEMPT_REFUSAL_BACKOFF` runs out. Through the `merge` verb over the real filesystem
document adapter, `poll.gather_merges` and `rules.poll_plan`."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import merge, poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import exempt_queue, rules
from tests.core.process.application.test_merge_ci_run import _world
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _Locks
from tests.fakes import MERGE_BASE

NOW = datetime(2026, 11, 4, 12, 20, tzinfo=UTC)
MOVED = "e" * 40


def _untrusted(tmp_path: Path):
    """A `ci:` product whose head changes the CI's own definition and declares no dispatch,
    with the task document on the real filesystem adapter."""
    ports = _world()
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)
    documents = FilesystemTaskDocuments(tmp_path)
    documents.save({"title": "t", "description": "d", **(ports.documents.load("GH-5") or {})})
    return dataclasses.replace(ports, documents=documents)


def _refuse(ports, tmp_path: Path, at: datetime = NOW, master: str = MASTER):
    return merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", master, None),
        frozenset(),
        lambda _line: None,
        lambda: at.isoformat(),
    )


def _reloaded(tmp_path: Path) -> dict:
    """The document as a fresh adapter reads it from disk."""
    return dict(FilesystemTaskDocuments(tmp_path).load("GH-5") or {})


def _plan(ports, now: datetime, master: str = MASTER) -> rules.PollPlan:
    """What the next round decides: `gather_merges` splits the backoff from the merges."""
    backed_off: list[int] = []
    merges = poll.gather_merges(
        ports, frozenset(), master, _Locks(), [], now=now, backed_off=backed_off
    )
    return rules.poll_plan(
        closes=[],
        resumes=[],
        launches=[],
        available_slots=1,
        now=now,
        merges=merges,
        backed_off_merges=backed_off,
    )


def test_the_refusal_is_written_with_the_pair_it_decided_with_and_read_back_from_disk(tmp_path):
    ports = _untrusted(tmp_path)

    result = _refuse(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "ci_untrusted_no_dispatch")
    assert ports.workspace.diff_reads == [(MERGE_BASE, HEAD)]  # GH-547: the merge's own range
    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]
    assert _reloaded(tmp_path)["merge_refusal"] == {
        "reason": "ci_untrusted_no_dispatch",
        "head": HEAD,
        "master": MASTER,
        "at": NOW.isoformat(),
    }


def test_on_the_same_head_and_master_the_next_round_defers_the_merge_as_backoff(tmp_path):
    ports = _untrusted(tmp_path)
    _refuse(ports, tmp_path)
    later = NOW + timedelta(minutes=5)

    plan = _plan(ports, later)

    assert plan.actions == []
    assert plan.deferred == [rules.Deferred("merge", 5, "backoff")]
    assert poll.deferred_line(plan.deferred[0]) == "agentic: poll: merge #5 waits: backoff"


def test_the_refusal_is_not_honoured_once_the_head_moves(tmp_path):
    ports = _untrusted(tmp_path)
    _refuse(ports, tmp_path)
    url = URL.format(5)
    ports.tracker.prs[url] = dataclasses.replace(ports.tracker.prs[url], head_oid=MOVED)
    stored = ports.documents.load("GH-5") or {}
    stored["subtasks"] = {"only": {"id": "only", "status": "approved", "reviewed_head": MOVED}}
    ports.documents.save(stored)
    backed_off: list[int] = []
    lines: list[str] = []

    poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        _Locks(),
        lines,
        now=NOW + timedelta(minutes=5),
        backed_off=backed_off,
    )

    # The new head is decided afresh (here it waits on its own CI), never held by the old row.
    assert backed_off == []
    assert any(line.startswith("agentic: poll: auto-merge #5 -> waiting: ci") for line in lines)


def test_the_merge_is_planned_again_once_master_moves(tmp_path):
    ports = _untrusted(tmp_path)
    _refuse(ports, tmp_path)
    ports.workspace.upstream_answer = Upstream("origin/master", MOVED, None)

    plan = _plan(ports, NOW + timedelta(minutes=5), master=MOVED)

    assert [(a.kind, a.issue) for a in plan.actions] == [("merge", 5)]


def test_the_merge_is_planned_again_when_the_backoff_runs_out(tmp_path):
    ports = _untrusted(tmp_path)
    _refuse(ports, tmp_path)

    plan = _plan(ports, NOW + exempt_queue.EXEMPT_REFUSAL_BACKOFF)

    assert [(a.kind, a.issue) for a in plan.actions] == [("merge", 5)]


def test_a_refusal_other_than_the_one_that_wrote_nothing_is_not_recorded(tmp_path):
    ports = _untrusted(tmp_path)
    ports.workspace.diff_paths = ()
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.conflicts.add(MASTER)  # the fresh merge conflicts: `merge_conflict` row only

    result = _refuse(ports, tmp_path)

    assert result.reason == "merge_conflict"
    written = _reloaded(tmp_path)
    assert "merge_refusal" not in written and "merge_conflict" in written
