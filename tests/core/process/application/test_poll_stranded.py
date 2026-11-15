"""GH-798: the poller flags an open issue whose task ended `tracker_only` after an estimate
resume with no re-plan (`rules.stranded_tracker_only`), once, and at most one per round."""

from __future__ import annotations

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueComment, IssueState
from tests.core.process.application.test_poll import _seed_body
from tests.core.process.application.test_poll_orphaned import _go
from tests.fakes import MemoryDocuments, memory_ports

MARKER = "<!-- agentic-stranded-tracker-only -->"


def _stranded(number: int) -> dict:
    return {
        "id": f"GH-{number}",
        "status": "tracker_only",
        "tracker_items": [{"kind": "comment", "text": "x"}],
        "pause_log": [
            {"kind": "escalation", "value": "estimate", "at": "2026-11-06T16:23:35+00:00"},
            {"kind": "resumed", "at": "2026-11-06T16:29:17+00:00"},
        ],
        "runs": [{"role": "Tech Lead", "started_at": "2026-11-06T16:00:00+00:00"}],
    }


def _ports(*numbers: int, state: str = "OPEN", docs: dict[int, dict] | None = None):
    documents = MemoryDocuments()
    ports = memory_ports(documents=documents)
    ports.tracker._queue = [n for n in numbers if state == "OPEN"]
    for n in numbers:
        ports.tracker.states[n] = IssueState(state, "", (), None)
        _seed_body(ports, n)
        documents.save((docs or {}).get(n, _stranded(n)))
    return ports


def _flagged(ports) -> list[tuple[str, str]]:
    return [(t, text) for t, text in ports.tracker.comments if MARKER in text]


def test_a_stranded_open_issue_gets_the_label_and_one_comment_with_the_marker():
    ports = _ports(7)
    lines, _ = _go(ports)
    assert ("GH-7", "needs-human") in ports.tracker.labels
    [(task, text)] = _flagged(ports)
    assert task == "GH-7"
    assert "agentic run --fresh --issue 7" in text
    assert text.rstrip().endswith(MARKER)
    assert any("#7" in line and "stranded" in line for line in lines)


def test_an_issue_that_already_carries_the_marker_is_not_commented_again():
    ports = _ports(7)
    ports.tracker.timestamped["GH-7"] = [IssueComment(f"earlier\n{MARKER}", "2026-11-07T00:00:00Z")]
    _go(ports)
    assert _flagged(ports) == []
    assert ports.tracker.labels == []


def test_at_most_one_issue_is_flagged_per_round_and_the_next_round_flags_the_other():
    ports = _ports(7, 8)
    _go(ports)
    assert [t for t, _ in _flagged(ports)] == ["GH-7"]
    # The fake's `comment()` does not feed `timestamped_comments`: hand the poller its own
    # comment back, as the issue would.
    ports.tracker.timestamped["GH-7"] = [
        IssueComment(text, "2026-11-07T00:00:00Z") for task, text in _flagged(ports)
    ]
    _go(ports)
    assert [t for t, _ in _flagged(ports)] == ["GH-7", "GH-8"]
    assert poll.STRANDED_FLAGS_PER_ROUND == 1


def test_a_closed_issue_is_never_touched():
    ports = _ports(7, state="CLOSED")
    _go(ports)
    assert ports.tracker.comments == [] and ports.tracker.labels == []


def test_a_tracker_only_task_without_an_estimate_resume_is_left_alone():
    doc = _stranded(7)
    doc["pause_log"][0]["value"] = "questions"
    ports = _ports(7, docs={7: doc})
    _go(ports)
    assert ports.tracker.comments == [] and ports.tracker.labels == []


def test_a_failed_label_still_lets_the_next_round_retry_and_never_raises():
    ports = _ports(7)
    ports.tracker.label_failures = 1
    _go(ports)
    assert _flagged(ports) == []
    _go(ports)
    assert [t for t, _ in _flagged(ports)] == ["GH-7"]
