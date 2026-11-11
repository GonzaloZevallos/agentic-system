"""GH-588: the poller's merge round follows the landing order. Only the head of a product's order
is a candidate; the others wait approved, and a clean merge-tree asks nothing of them."""

from __future__ import annotations

from tests.core.process.application.test_poll import _Locks
from tests.core.process.application.test_poll_merge import MASTER, _merges, _ports

OLD, MID, NEW = (
    "2026-11-10T08:00:00+00:00",
    "2026-11-10T09:00:00+00:00",
    "2026-11-10T10:00:00+00:00",
)


def _queued(ports, number: int, at: str, **more) -> None:
    ports.documents.record_closure(f"GH-{number}", {"landing_queued_at": at, **more})


def _three():
    ports = _ports(5, 6, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, MID)
    _queued(ports, 6, NEW)
    return ports


def test_only_the_oldest_approval_is_a_candidate_and_the_others_wait_behind_it():
    found, lines = _merges(_three())

    assert found == [7]
    assert lines == [
        "agentic: poll: auto-merge #5 -> waiting (clean) behind #7",
        "agentic: poll: auto-merge #6 -> waiting (clean) behind #7",
    ]


def test_the_next_pull_request_leads_once_the_head_has_landed():
    ports = _three()
    ports.documents.record_closure("GH-7", {"closed_at": "2026-11-11T00:00:00+00:00"})

    found, _ = _merges(ports)

    assert found == [5]


def test_a_conflict_on_a_waiting_pull_request_triggers_nothing_until_its_turn():
    ports = _three()
    ports.workspace.conflict_answers[("a" * 40, MASTER)] = True

    found, lines = _merges(ports)

    assert found == []
    assert lines[:2] == [
        "agentic: poll: auto-merge #5 -> waiting (conflicts) behind #7",
        "agentic: poll: auto-merge #6 -> waiting (conflicts) behind #7",
    ] or "waiting (conflicts) behind #7" in " ".join(lines)


def test_a_waiting_task_is_never_a_land_candidate_even_with_an_unstarted_pass():
    ports = _three()
    lands: list = []
    from agentic.core.process.application import poll

    poll.gather_merges(ports, frozenset(), MASTER, _Locks(), [], lands)

    assert all(candidate.issue == 7 for candidate in lands)


def test_an_escalated_head_does_not_block_the_next_pull_request():
    ports = _three()
    _queued(ports, 7, OLD, escalated="needs a decision")

    found, _ = _merges(ports)

    assert found == [5]


def test_an_unstamped_pull_request_goes_after_every_stamped_one():
    ports = _ports(5, 6)
    _queued(ports, 6, NEW)

    found, lines = _merges(ports)

    assert found == [6]
    assert lines == ["agentic: poll: auto-merge #5 -> waiting (clean) behind #6"]


def test_the_poller_anchors_the_head_once_and_never_a_waiting_task():
    ports = _three()

    _merges(ports)
    first = ports.documents.load("GH-7")

    assert first["head_since_head"] == "a" * 40
    assert "head_since_at" in first
    assert "head_since_head" not in ports.documents.load("GH-5")
    ports.documents.record_closure("GH-7", {"head_since_at": "2026-11-10T11:00:00+00:00"})
    _merges(ports)
    assert ports.documents.load("GH-7")["head_since_at"] == "2026-11-10T11:00:00+00:00"
