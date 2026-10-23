"""GH-330, subtask `diagnose-verdict`: the diagnoser's closed verdict wired into
`landing_fix.diagnose`, on the GH-320 shape (`gh320_conflicting_facts`), driven through
`landing.land` on the in-memory ports and the fake launcher. A `sync` verdict closes nothing
and spawns nothing; the next round dispatches `sync_master` on the same head and pull
request. The Landing Diagnoser's output schema is checked at its own seam."""

from __future__ import annotations

import dataclasses
from typing import get_args

import pytest
from pydantic import ValidationError

from agentic.core.process.application import landing, landing_plan
from agentic.core.process.application.ports import RawResult, Upstream
from agentic.core.process.domain import schemas
from agentic.core.process.domain.task import (
    VERDICT_SYNC,
    VERDICT_UNLANDABLE,
    DiagnosisVerdict,
    TaskState,
)
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import _HeldLocks
from tests.core.process.landing_fixtures import (
    HEAD,
    MASTER,
    gh320_conflicting_facts,
    gh320_pull_request,
    gh320_slice_inputs,
)
from tests.fakes import FakeLauncher, MemoryTracker, ScriptedRunner, memory_ports

NOW = "2026-10-23T14:00:00+00:00"
TASK = "GH-320"
NEW = "d" * 40


def _world(monkeypatch):
    """GH-320 as the poller saw it: the pull request `CONFLICTING` on its head and on master,
    four approved units, conflicting syncs for this master sha already at their cap, so the
    next step is the diagnosis. The slice read is the fixture's `no_clean_slice`."""
    _, document = gh320_conflicting_facts()
    ports = memory_ports(tracker=MemoryTracker())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.tracker.prs[str(document["pull_request"])] = gh320_pull_request()
    units = {
        name: {"id": name, **unit}
        for name, unit in {
            **document["subtasks"],
            "integration": {"status": "approved", "reviewed_head": HEAD},
        }.items()
    }
    ports.documents.save({**document, "subtasks": units})
    monkeypatch.setattr(landing_plan, "slice_inputs", lambda *a, **k: gh320_slice_inputs())
    return ports


def _diagnoser(ports, *verdicts: str | None):
    """The ports with a diagnoser that delivers one result per verdict given."""
    results = [
        RawResult(
            {
                "markdown": "### Why it does not land\n\nOne conflicted line.",
                "reason": "merge origin/master and keep one copy of the delenv line",
                **({} if verdict is None else {"verdict": verdict}),
            },
            0.4,
            3,
            "s",
        )
        for verdict in verdicts
    ]
    return dataclasses.replace(ports, runner=ScriptedRunner(landing_diagnoser=results))


def _land(ports, tmp_path, launcher):
    lines: list[str] = []
    result = landing.land(
        TASK,
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lines.append,
        clock=lambda: NOW,
        launcher=launcher,
        argv0="agentic",
    )
    return result, lines


def _doc(ports) -> TaskState:
    return ports.documents.load(TASK)


def _diagnoses(ports) -> list[dict]:
    return [e for e in _doc(ports)["landing"] if e["step"] == "diagnose"]


def _nothing_closed(ports, launcher) -> None:
    assert ports.tracker.closed_pull_requests == []
    assert ports.tracker.comments == []
    assert launcher.spawned == []
    closure = ports.documents.closures.get(TASK, {})
    assert "landing_restarts" not in closure and "landing_cost_carried" not in closure
    assert not _doc(ports).get("landing_restarts")


# --- the schema ---------------------------------------------------------------------------


def test_the_diagnoser_output_requires_a_verdict_from_the_closed_set():
    base = {"markdown": "### Why\n\nx", "findings": [], "reason": "r"}

    for verdict in get_args(DiagnosisVerdict):
        assert schemas.LandingDiagnosis.model_validate({**base, "verdict": verdict})
    enum = schemas.LandingDiagnosis.model_json_schema()["properties"]["verdict"]["enum"]
    assert tuple(enum) == get_args(DiagnosisVerdict)
    assert "verdict" in schemas.LandingDiagnosis.model_json_schema()["required"]
    with pytest.raises(ValidationError):
        schemas.LandingDiagnosis.model_validate(base)
    with pytest.raises(ValidationError):
        schemas.LandingDiagnosis.model_validate({**base, "verdict": "merge"})


# --- a sync verdict -------------------------------------------------------------------------


def test_a_sync_verdict_is_saved_with_its_upstream_and_closes_and_spawns_nothing(
    monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)

    result, lines = _land(ports, tmp_path, launcher)

    assert (result.kind, result.step) == ("dispatched", "diagnose")
    [entry] = _diagnoses(ports)
    assert entry["delivered"] is True
    assert entry["verdict"] == VERDICT_SYNC and entry["upstream"] == MASTER
    _nothing_closed(ports, launcher)
    assert not any("restart" in line for line in lines)
    assert sum("sync" in line for line in lines) == 1  # the one line the decision logs


def test_the_next_round_dispatches_the_sync_on_the_same_head_and_pull_request(
    monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    reference = _doc(ports)["pull_request"]
    _land(ports, tmp_path, launcher)

    result, _ = _land(ports, tmp_path, launcher)

    assert (result.kind, result.step) == ("dispatched", "sync_master")
    sync = _doc(ports)["landing"][-1]
    assert (sync["step"], sync["head"], sync["upstream"]) == ("sync_master", HEAD, MASTER)
    assert _doc(ports)["pull_request"] == reference
    _nothing_closed(ports, launcher)


def test_a_re_review_that_returns_after_the_sync_takes_the_fix_path_with_no_restart(
    monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    _land(ports, tmp_path, launcher)
    _land(ports, tmp_path, launcher)  # the sync_master
    document = _doc(ports)
    # The sync pushed the merge commit; its re-review found one blocking row on the increment.
    entries = [dict(e) for e in document["landing"]]
    entries[-1]["resolved_at"] = NOW
    units = dict(document["subtasks"])
    units["integration"] = {**units["integration"], "reviewed_head": NEW}
    row = {
        "location": "tests/core/process/adapters/claude_sdk/test_runner.py:40",
        "claim": "the delenv line is kept twice",
        "severity": "blocking",
        "status": "open",
        "scope": "subtask",
        "subtask": "integration",
        "category": "standard-other",
    }
    ports.documents.save({**document, "landing": entries, "subtasks": units, "findings": [row]})
    ports.tracker.prs[str(document["pull_request"])] = dataclasses.replace(
        gh320_pull_request(), head_oid=NEW, mergeable="MERGEABLE"
    )
    spawned = len(launcher.spawned)

    result, _ = _land(ports, tmp_path, launcher)

    assert (result.kind, result.step) == ("dispatched", "fix")
    assert [e["step"] for e in _diagnoses(ports)] == ["diagnose"]
    assert ports.tracker.closed_pull_requests == []
    assert not any("--fresh" in argv for argv in launcher.spawned[spawned:])
    assert not _doc(ports).get("landing_restarts")


def test_the_saved_document_reads_as_a_sync_step_for_the_step_table(monkeypatch, tmp_path):
    """No hand-built entry: the real `diagnose` saved the verdict and the upstream, and the
    step table over that document answers `sync_master` for GH-320's facts."""
    facts, _ = gh320_conflicting_facts()
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC)
    _land(ports, tmp_path, FakeLauncher())

    step = landing_plan.step_for(_doc(ports), "merge_conflict", facts, gh320_slice_inputs)

    assert step.kind == "sync_master"


# --- a wrong guess and the unchanged paths ------------------------------------------------------


def test_a_sync_that_closes_with_lacks_is_followed_by_a_restart_after_the_next_diagnosis(
    monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    ports = _diagnoser(_world(monkeypatch), VERDICT_SYNC, VERDICT_SYNC)
    _land(ports, tmp_path, launcher)
    approved = _doc(ports)["subtasks"]
    _land(ports, tmp_path, launcher)  # the sync_master reopens the units
    document = _doc(ports)
    entries = [dict(e) for e in document["landing"]]
    entries[-1].update(lacks=MASTER, closed_at=NOW)  # `close_unsynced`: the head still lacks it
    ports.documents.save({**document, "landing": entries, "subtasks": approved})
    assert ports.tracker.closed_pull_requests == []

    result, _ = _land(ports, tmp_path, launcher)

    assert (result.kind, result.step) == ("dispatched", "diagnose")
    assert ports.tracker.closed_pull_requests == [str(document["pull_request"])]
    assert [argv[1:3] for argv in launcher.spawned if "--fresh" in argv] == [["run", "--issue"]]
    assert ports.documents.closures[TASK]["landing_restarts"] == 1


def test_an_unlandable_verdict_closes_the_pull_request_and_restarts_as_before(
    monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    ports = _diagnoser(_world(monkeypatch), VERDICT_UNLANDABLE)

    _land(ports, tmp_path, launcher)

    [entry] = _diagnoses(ports)
    assert entry["verdict"] == VERDICT_UNLANDABLE
    assert ports.tracker.closed_pull_requests == [str(_doc(ports)["pull_request"])]
    [(_, notice)] = ports.tracker.comments
    assert notice.startswith("Restarted by landing: ")
    assert [argv[1:3] for argv in launcher.spawned] == [["run", "--issue"]]
    assert "--fresh" in launcher.spawned[0]


def test_a_delivery_without_a_verdict_is_not_valid_so_nothing_is_closed_on_it(
    monkeypatch, tmp_path
):
    launcher = FakeLauncher()
    ports = _diagnoser(_world(monkeypatch), None, None)

    _land(ports, tmp_path, launcher)

    assert _diagnoses(ports)[0].get("delivered") is not True
    assert ports.tracker.closed_pull_requests == [] and launcher.spawned == []
