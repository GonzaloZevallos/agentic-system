"""The merge verb's `perf_unverified` refusal (GH-318, ADR-0060): the comment carries one
fixed evidence line built from the run's verdict and shas, closes with the human's hand-off,
is written once per (head, reason), and nothing about it changes a task that is not convened."""

from __future__ import annotations

import pytest

from agentic.core.process.application import landing_plan, merge
from agentic.core.process.application.ports import Upstream
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _ports

OTHER = "c" * 40
PLAN = [
    {
        "name": "list",
        "volume": 100,
        "volume_unit": "rows",
        "basis": "b",
        "budgets": [
            {"metric": "wall_ms", "limit": 200, "unit": "ms", "source": "issue", "basis": "b"}
        ],
    }
]


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


def _run(ports, tmp_path):
    return merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
    )


def _qa(verdict, head):
    return {"role": "QA Performance", "cost_usd": 0.1, "perf_verdict": verdict, "perf_head": head}


def _refusals(ports):
    return [body for _, body in ports.tracker.comments]


def test_an_unverified_run_is_refused_with_its_verdict_and_left_to_the_human(tmp_path):
    ports = _ports(5, performance=PLAN, runs=[_qa("unverified", HEAD)])

    result = _run(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "perf_unverified")
    [comment] = _refusals(ports)
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} perf_unverified"
    assert "verdict unverified" in comment
    assert comment.endswith(merge.LEFT_TO_HUMAN)


def test_an_approval_on_another_head_names_both_shas(tmp_path):
    ports = _ports(5, performance=PLAN, runs=[_qa("approved", OTHER)])

    assert _run(ports, tmp_path).reason == "perf_unverified"
    [comment] = _refusals(ports)
    assert f"measured {OTHER}, head {HEAD}" in comment


def test_a_convened_task_with_no_run_at_all_says_so(tmp_path):
    ports = _ports(5, performance=PLAN)

    assert _run(ports, tmp_path).reason == "perf_unverified"
    assert "verdict none" in _refusals(ports)[0]


def test_the_same_head_and_reason_is_commented_once(tmp_path):
    ports = _ports(5, performance=PLAN, runs=[_qa("unverified", HEAD)])

    _run(ports, tmp_path)
    _run(ports, tmp_path)

    assert len(_refusals(ports)) == 1


def test_an_approval_on_this_head_merges_with_no_perf_comment(tmp_path):
    ports = _ports(5, performance=PLAN, runs=[_qa("approved", HEAD)])

    assert _run(ports, tmp_path).kind == "merged"
    assert all("perf_unverified" not in body for body in _refusals(ports))
