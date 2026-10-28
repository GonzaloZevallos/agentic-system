"""GH-376: the poller's sweep of `agentic-ci/<id>` refs, against `MemoryWorkspace` and
`MemoryDocuments`. The only deleter of those refs: it removes a ref only when the task
ended and has no live run, and nothing it meets ends the round."""

from __future__ import annotations

from datetime import UTC, datetime

from agentic.core.process.application import ci_ref_sweep, poll
from agentic.core.process.application.ports import GitError, Ports, RunRegistration
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_poll import _Locks
from tests.fakes import (
    FakeLauncher,
    MemoryDocuments,
    MemoryRunRegistry,
    MemoryWorkspace,
    memory_ports,
)

ENDED = {"closed_at": "2026-10-20T00:00:00Z"}


def _ports(
    refs: dict[str, dict[str, object] | None], workspace: MemoryWorkspace | None = None
) -> tuple[Ports, MemoryWorkspace]:
    workspace = workspace or MemoryWorkspace()
    ports = memory_ports(workspace=workspace)
    for task_id, extra in refs.items():
        workspace.ci_refs[f"agentic-ci/{task_id}"] = "a" * 40
        if extra is not None:
            ports.documents.save({"id": task_id, **extra})
    return ports, workspace


def test_an_ended_task_with_no_live_run_loses_its_ref_and_the_rest_are_kept():
    ports, workspace = _ports(
        {
            "GH-1": ENDED,  # ended and dead: deleted
            "GH-2": ENDED,  # ended but a run is alive: kept
            "GH-3": {"status": "escalated"},  # not ended: kept
            "GH-4": {"status": "discarded", "finished_at": "2026-10-20T00:00:00Z"},
        }
    )
    workspace.ci_refs["agentic/GH-1"] = "b" * 40  # never touched: not under the prefix
    lines = ci_ref_sweep.sweep(ports, {"GH-2"})
    assert lines == ["agentic: poll: swept 2 CI ref(s)"]
    assert set(workspace.ci_refs) == {"agentic-ci/GH-2", "agentic-ci/GH-3", "agentic/GH-1"}


def test_a_ref_with_no_document_is_kept_and_nothing_to_do_says_nothing():
    ports, workspace = _ports({"GH-9": None, "GH-3": {"status": "escalated"}})
    assert ci_ref_sweep.sweep(ports, set()) == []
    assert set(workspace.ci_refs) == {"agentic-ci/GH-9", "agentic-ci/GH-3"}


class _UnreadableDocuments(MemoryDocuments):
    def load(self, task_id: str) -> dict[str, object] | None:
        if task_id == "GH-5":
            raise ValueError("front matter is not YAML")
        return super().load(task_id)


def test_a_ref_whose_document_cannot_be_read_is_kept_with_one_line_and_the_others_go():
    workspace = MemoryWorkspace()
    ports = memory_ports(workspace=workspace, documents=_UnreadableDocuments())
    for task_id in ("GH-5", "GH-6"):
        workspace.ci_refs[f"agentic-ci/{task_id}"] = "a" * 40
    ports.documents.save({"id": "GH-6", **ENDED})
    lines = ci_ref_sweep.sweep(ports, set())
    assert len(lines) == 2
    assert lines[0] == "agentic: poll: swept 1 CI ref(s)"
    assert "kept agentic-ci/GH-5" in lines[1] and "front matter is not YAML" in lines[1]
    assert set(workspace.ci_refs) == {"agentic-ci/GH-5"}


def test_a_listing_that_fails_is_one_line_and_deletes_nothing():
    ports, workspace = _ports({"GH-1": ENDED})
    workspace.ci_ref_error = "git ls-remote failed: could not resolve host"
    lines = ci_ref_sweep.sweep(ports, set())
    assert len(lines) == 1 and "could not resolve host" in lines[0]
    assert ("delete", "agentic-ci/GH-1") not in workspace.ci_ref_calls


class _FailsOnOne(MemoryWorkspace):
    def delete_remote_ref(self, ref: str) -> None:
        if ref == "agentic-ci/GH-1":
            self.ci_ref_calls.append(("delete", ref))
            raise GitError("push rejected")
        super().delete_remote_ref(ref)


def test_a_delete_that_fails_is_one_line_and_the_sweep_goes_on_to_the_next_ref():
    ports, workspace = _ports({"GH-1": ENDED, "GH-2": ENDED}, workspace=_FailsOnOne())
    lines = ci_ref_sweep.sweep(ports, set())
    assert lines[0] == "agentic: poll: swept 1 CI ref(s)"
    assert len(lines) == 2 and "agentic-ci/GH-1" in lines[1] and "push rejected" in lines[1]
    assert set(workspace.ci_refs) == {"agentic-ci/GH-1"}


def test_a_poller_round_sweeps_with_the_ids_the_registry_says_are_alive(tmp_path):
    ports, workspace = _ports({"GH-1": ENDED, "GH-2": ENDED})
    registry = MemoryRunRegistry([RunRegistration(1, "h", "p", "GH-2")])
    lines = poll.run_once(
        Configuration(product_dir=tmp_path),
        ports,
        registry,
        FakeLauncher(),
        datetime(2026, 10, 27, tzinfo=UTC),
        argv0="agentic",
        sleep=lambda _: None,
        lock=_Locks(),
        current_boot="boot-now",
    )
    assert "agentic: poll: swept 1 CI ref(s)" in lines
    assert set(workspace.ci_refs) == {"agentic-ci/GH-2"}
