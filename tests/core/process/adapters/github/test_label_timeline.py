"""`GhLabelTimeline`: the last addition of a label, and a read that never raises (GH-599)."""

import json
import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.github.label_timeline import GhLabelTimeline, last_addition


def _nodes(*events):
    return [{"createdAt": at, "label": {"name": name}} for at, name in events]


def test_last_addition_is_the_latest_of_the_label_and_ignores_others():
    nodes = _nodes(
        ("2026-11-01T10:00:00Z", "agentic"),
        ("2026-11-03T10:00:00Z", "bug"),
        ("2026-11-02T10:00:00Z", "agentic"),
    )
    assert last_addition(nodes, "agentic") == "2026-11-02T10:00:00Z"
    assert last_addition(nodes, "missing") is None
    assert last_addition([], "agentic") is None


def test_last_addition_skips_malformed_nodes():
    nodes = [{"createdAt": "2026-11-01T10:00:00Z"}, None, {"label": {"name": "agentic"}}]
    assert last_addition(nodes, "agentic") is None


class Run:
    def __init__(self, stdout="", error=None, code=0):
        self.stdout, self.error, self.code, self.calls = stdout, error, code, []

    def __call__(self, args, cwd, capture_output, text, check):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.code, self.stdout, "boom")


def _answer(*events):
    nodes = _nodes(*events)
    return json.dumps({"data": {"repository": {"issue": {"timelineItems": {"nodes": nodes}}}}})


def test_it_reads_labeled_events_over_graphql_and_returns_the_last_addition():
    run = Run(_answer(("2026-11-01T10:00:00Z", "agentic"), ("2026-11-04T10:00:00Z", "agentic")))
    assert GhLabelTimeline(Path("/p"), run).label_added_at(7, "agentic") == "2026-11-04T10:00:00Z"
    (args,) = run.calls
    assert args[:3] == ["gh", "api", "graphql"]
    assert any("LABELED_EVENT" in part and "last: 100" in part for part in args)
    assert "number=7" in args


@pytest.mark.parametrize(
    "run",
    [
        Run(error=OSError("no gh")),
        Run(error=subprocess.TimeoutExpired(["gh"], 60)),
        Run(code=1),
        Run(stdout="not json"),
        Run(stdout="[]"),
        Run(stdout=json.dumps({"data": None})),
        Run(stdout=json.dumps({"data": {"repository": {"issue": {"timelineItems": 3}}}})),
    ],
)
def test_every_failure_of_the_read_is_none_and_nothing_raises(run):
    assert GhLabelTimeline(Path("/p"), run).label_added_at(7, "agentic") is None
