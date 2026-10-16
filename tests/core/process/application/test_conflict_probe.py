"""GH-255, subtask `wiring`: the conflict probe where it runs -- `poll.assess_merge` (through
`gather_merges` and `landing.land`) and the `agentic merge` verb's `merge` branch. All on the
in-memory ports and `MemoryWorkspace`, whose `merge_conflicts` answers what a test scripted
in `conflict_answers` (unscripted: `None`, unread) and records every ask."""

from __future__ import annotations

import dataclasses

import pytest

from agentic.core.process.application import landing, merge, poll
from agentic.core.process.application.ports import Upstream, UpstreamMerge
from agentic.core.process.domain.ci import CiCheck, CiReport
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _finding,
    _HeldLocks,
    _Locks,
    _merges,
    _ports,
)
from tests.fakes import MemoryStack

LIVE = frozenset({"sync_master"})
NEW = "d" * 40
RUN = "https://github.com/o/r/actions/runs/1"


def _declaring(ports, ci=("web",)):
    return dataclasses.replace(ports, stack=MemoryStack(checks={"tests": "run tests"}, ci=ci))


def _red() -> CiReport:
    return CiReport(HEAD, "master", MASTER, "2026-10-15T00:00:00+00:00", (_check("FAILURE"),))


def _check(conclusion: str) -> CiCheck:
    return CiCheck("web", "COMPLETED", conclusion, RUN)


def _land(ports, tmp_path, lines=None):
    return landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        (lines if lines is not None else []).append,
        clock=lambda: "2026-10-15T00:00:00+00:00",
        live=LIVE,
        resume=lambda number: 777,
    )


def _git_free_refusals():
    """Each refusal that ranks ahead of the conflict and reads no git: the document that
    earns it, and the eval set the round holds."""
    unit = {"id": "only", "reviewed_head": HEAD}
    return {
        "not_ready": ({"status": "developing"}, frozenset()),
        "not_approved": ({"subtasks": {"only": {**unit, "status": "delivered"}}}, frozenset()),
        "open_findings_blocking": ({"findings": [_finding("blocking")]}, frozenset()),
        "eval_set": ({}, frozenset({5})),
    }


@pytest.mark.parametrize("refusal", list(_git_free_refusals()))
def test_a_refusal_ahead_of_the_conflict_makes_no_probe_in_the_poller_or_land(refusal, tmp_path):
    document, eval_set = _git_free_refusals()[refusal]
    ports = _ports(5, **document)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = True

    lines: list[str] = []
    poll.gather_merges(ports, eval_set, MASTER, _Locks(), lines)
    landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        eval_set,
        lambda line: None,
        live=LIVE,
    )

    assert lines == [f"agentic: poll: auto-merge #5 -> left to the human: {refusal}"]
    assert ports.workspace.conflict_probes == []


def test_a_diagnosed_task_is_not_probed_in_the_poller_or_land(tmp_path):
    diagnosed = {
        "head": HEAD,
        "step": "diagnose",
        "refusal": "ci_failed",
        "at": "t",
        "delivered": True,
    }
    ports = _declaring(_ports(5, checks=False, landing=[diagnosed]))
    ports.tracker.ci_reports = [_red()]
    ports.workspace.conflict_answers[(HEAD, MASTER)] = True

    _, lines = _merges(ports)
    _land(ports, tmp_path)

    assert ports.workspace.conflict_probes == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: ci_failed (web)"]


def test_a_conflicting_head_under_a_red_ci_is_a_conflict_that_never_reads_the_ci(tmp_path):
    ports = _declaring(_ports(5, checks=False))
    ports.tracker.ci_reports = [_red()]
    ports.workspace.conflict_answers[(HEAD, MASTER)] = True
    ports.workspace.upstream_merges[MASTER] = UpstreamMerge(clean=True, conflicted=(), head=NEW)

    found, lines = _merges(ports)

    assert found == []
    assert lines == ["agentic: poll: auto-merge #5 -> left to the human: merge_conflict"]
    assert ports.tracker.ci_reads == []
    assert ports.workspace.conflict_probes == [(HEAD, MASTER)]

    result = _land(ports, tmp_path)

    assert (result.kind, result.step) == ("dispatched", "sync_master")
    [entry] = (ports.documents.load("GH-5") or {})["landing"]
    assert (entry["head"], entry["step"], entry["refusal"]) == (
        HEAD,
        "sync_master",
        "merge_conflict",
    )
    assert ports.tracker.ci_reads == []
    assert len((ports.documents.load("GH-5") or {})["landing"]) == 1


def test_a_conflict_that_cannot_be_read_says_so_and_leaves_the_decision_as_it_was():
    ports = _declaring(_ports(5, checks=False))
    ports.tracker.ci_reports = [_red()]
    ports.workspace.conflict_answers[(HEAD, MASTER)] = None

    found, lines = _merges(ports)

    assert found == []
    assert lines == [
        f"agentic: poll: auto-merge #5 -> conflict unread ({HEAD})",
        "agentic: poll: auto-merge #5 -> left to the human: ci_failed (web)",
    ]
    assert ports.workspace.conflict_probes == [(HEAD, MASTER)]
    assert ports.tracker.ci_reads != []


def test_a_clean_probe_adds_no_line_and_the_candidate_stays_a_candidate():
    ports = _ports(5)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = False

    assert _merges(ports) == ([5], [])


def _merge_world(tmp_path, answer):
    ports = _ports(5)
    ports.workspace.conflict_answers[(HEAD, MASTER)] = answer
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda line: None,
    )
    return ports, result


def test_the_verb_refuses_a_conflict_it_reads_just_before_merging_and_writes_nothing(tmp_path):
    ports, result = _merge_world(tmp_path, True)
    before = {"id": "GH-5", **(ports.documents.load("GH-5") or {})}

    assert result == merge.MergeResult("refused", "merge_conflict")
    assert ports.tracker.merge_calls == []
    assert ports.tracker.comments == []
    document = ports.documents.load("GH-5") or {}
    assert not {"merge_failures", "auto_merge_head", "auto_merge_at"} & document.keys()
    assert document == {k: v for k, v in before.items() if k in document}


@pytest.mark.parametrize("answer", [False, None])
def test_the_verb_merges_as_before_when_the_probe_reads_clean_or_unread(tmp_path, answer):
    ports, result = _merge_world(tmp_path, answer)

    assert result.kind == "merged"
    assert ports.tracker.merge_calls == [(URL.format(5), HEAD)]
    assert ports.workspace.conflict_probes == [(HEAD, MASTER)]
