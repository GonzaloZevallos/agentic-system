"""GH-681: `landing_lane.lane_facts` reads the facts the fast lane needs and decides nothing: the
head's paths and syncs left, and for each candidate whether it holds master and what it touches.
A read that fails is `None`, never `False`, `0` or an empty set."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from agentic.core.process.application import landing_lane
from agentic.core.process.application.landing_lane import LaneHead, lane_facts
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import landing_order
from agentic.core.process.domain.task import PullRequest
from tests.fakes import MemoryTracker, memory_ports

PRODUCT = Path("/p")
MASTER = "b" * 40
HEAD_OID = "1" * 40
OTHER_OID = "2" * 40
MEMBER_HEAD = "3" * 40
URL = "https://github.com/o/r/pull/{}"


def _pr(oid: str | None = HEAD_OID) -> PullRequest:
    return PullRequest(
        state="OPEN", created_at="2026-11-10T00:00:00Z", merged_at=None, commits=(), head_oid=oid
    )


def _ports():
    ports = memory_ports(tracker=MemoryTracker())
    ports.tracker.prs[URL.format(7)] = _pr()
    return ports


def _member(issue: int) -> landing_order.Member:
    return landing_order.Member(issue=issue, queued_at=None, live_pass=False, waits_on_human=False)


def _head(*, integrating: bool = True, landing=()) -> LaneHead:
    return LaneHead(7, URL.format(7), {"landing": list(landing)}, integrating)


def _sync(head: str, at: str = "2026-11-10T10:00:00+00:00") -> dict[str, object]:
    return {"step": "sync_master", "head": head, "at": at}


def _facts(ports, *, head: LaneHead | None = None, candidates=None):
    members = (_member(7), _member(5), _member(6))
    given = {5: MEMBER_HEAD} if candidates is None else candidates
    result = lane_facts(ports, PRODUCT, MASTER, members, head or _head(), given)
    return {m.issue: m for m in result}


def test_a_candidates_facts_and_the_heads_are_read():
    ports = _ports()
    ports.workspace.ancestors_by_ref[MEMBER_HEAD] = {MASTER}
    ports.workspace.touched_by[(MASTER, HEAD_OID)] = ("a.py", "b.py")
    ports.workspace.touched_by[(MASTER, MEMBER_HEAD)] = ("c.py",)

    by_issue = _facts(ports, head=_head(landing=[_sync(HEAD_OID)]))

    head, member = by_issue[7], by_issue[5]
    assert head.integrating is True
    assert head.paths == frozenset({"a.py", "b.py"})
    assert head.head_syncs_left == landing_rules.LANDING_SYNCS_MAX - 1
    assert (member.merge_now, member.clean, member.holds_master) == (True, True, True)
    assert member.paths == frozenset({"c.py"})
    assert ports.workspace.ancestor_checks == [(PRODUCT, MASTER, MEMBER_HEAD)]


def test_a_member_that_is_not_a_candidate_is_returned_unchanged():
    ports = _ports()

    by_issue = _facts(ports)

    assert by_issue[6] == _member(6)
    assert [
        m.issue for m in lane_facts(ports, PRODUCT, MASTER, (_member(7), _member(5)), _head(), {})
    ] == [
        7,
        5,
    ]


def test_an_ancestor_read_that_fails_is_unknown_and_reads_nothing_else_of_that_member():
    ports = _ports()
    ports.workspace.ancestor_ref_errors.add(MEMBER_HEAD)

    member = _facts(ports)[5]

    assert member.holds_master is None
    assert member.paths is None
    assert (member.merge_now, member.clean) == (True, True)
    assert ports.workspace.touched_reads == [(MASTER, HEAD_OID)]  # the head's alone
    assert ports.workspace.fetched_commits == []
    assert ports.workspace.contains_calls == []


def test_a_member_that_lacks_master_reads_no_paths():
    ports = _ports()  # MEMBER_HEAD is not scripted as holding MASTER

    member = _facts(ports)[5]

    assert member.holds_master is False
    assert member.paths is None
    assert (MASTER, MEMBER_HEAD) not in ports.workspace.touched_reads


def test_a_member_whose_paths_cannot_be_read_has_none_and_holds_master_stays_true():
    ports = _ports()
    ports.workspace.ancestors_by_ref[MEMBER_HEAD] = {MASTER}
    ports.workspace.touched_head_errors.add(MEMBER_HEAD)
    ports.workspace.touched_by[(MASTER, HEAD_OID)] = ("a.py",)

    by_issue = _facts(ports)

    assert by_issue[5].holds_master is True
    assert by_issue[5].paths is None
    assert by_issue[7].paths == frozenset({"a.py"})


def test_a_head_pull_request_that_cannot_be_read_gives_no_paths_and_no_syncs_left(monkeypatch):
    ports = _ports()
    ports.workspace.ancestors_by_ref[MEMBER_HEAD] = {MASTER}
    spent_calls: list[object] = []
    monkeypatch.setattr(landing_lane.landing_plan, "spent", lambda *a, **k: spent_calls.append(a))
    bad = LaneHead(7, "not-a-pull-request-url", {}, True)  # the tracker raises `ValueError`

    by_issue = _facts(ports, head=bad)

    assert by_issue[7].integrating is True
    assert by_issue[7].paths is None
    assert by_issue[7].head_syncs_left is None
    assert spent_calls == []
    assert ports.workspace.touched_reads == []
    assert by_issue[5].holds_master is None  # no member is read without the head's paths
    assert ports.workspace.ancestor_checks == []


def test_a_head_pull_request_that_is_missing_or_has_no_oid_is_unread():
    for pr in (None, _pr(None)):
        ports = _ports()
        if pr is None:
            del ports.tracker.prs[URL.format(7)]
        else:
            ports.tracker.prs[URL.format(7)] = pr

        head = _facts(ports)[7]

        assert (head.paths, head.head_syncs_left) == (None, None)
        assert ports.workspace.touched_reads == []


def test_a_head_diff_that_fails_gives_no_paths_and_fetches_no_commit():
    ports = _ports()
    ports.workspace.touched_error = "scripted"

    by_issue = _facts(ports, head=_head(landing=[_sync(HEAD_OID)]))

    assert by_issue[7].paths is None
    assert by_issue[7].head_syncs_left == landing_rules.LANDING_SYNCS_MAX - 1  # the budget is read
    assert by_issue[5].holds_master is None
    assert ports.workspace.fetched_commits == []
    assert ports.workspace.contains_calls == []
    assert ports.workspace.ancestor_checks == []


def test_a_head_that_does_not_integrate_reads_nothing_at_all():
    ports = _ports()

    by_issue = _facts(ports, head=_head(integrating=False))

    assert by_issue[7].integrating is False
    assert (by_issue[7].paths, by_issue[7].head_syncs_left) == (None, None)
    assert (by_issue[5].merge_now, by_issue[5].clean) == (True, True)
    assert (by_issue[5].holds_master, by_issue[5].paths) == (None, None)
    assert ports.tracker.events == []
    assert ports.workspace.touched_reads == []
    assert ports.workspace.ancestor_checks == []


def test_a_head_that_moved_between_rounds_diffs_the_new_sha():
    ports = _ports()
    ports.workspace.touched_by[(MASTER, HEAD_OID)] = ("old.py",)
    ports.workspace.touched_by[(MASTER, OTHER_OID)] = ("new.py",)

    first = _facts(ports)[7]
    ports.tracker.prs[URL.format(7)] = _pr(OTHER_OID)
    second = _facts(ports)[7]

    assert first.paths == frozenset({"old.py"})
    assert second.paths == frozenset({"new.py"})
    assert (MASTER, OTHER_OID) in ports.workspace.touched_reads


def test_syncs_left_counts_per_task_inside_the_epoch_and_not_per_sha():
    ports = _ports()
    spent = [_sync(HEAD_OID, f"2026-11-10T10:0{n}:00+00:00") for n in range(4)]
    spent.append(_sync(OTHER_OID, "2026-11-10T09:00:00+00:00"))

    head = _facts(ports, head=_head(landing=spent))[7]

    assert head.head_syncs_left == landing_rules.LANDING_SYNCS_MAX - 5


def test_a_resolved_land_slice_leaves_only_the_syncs_after_it():
    ports = _ports()
    before = [_sync(HEAD_OID) for _ in range(3)]
    sliced = {"step": "land_slice", "head": HEAD_OID, "resolved_at": "2026-11-10T11:00:00+00:00"}
    after = [_sync(HEAD_OID), _sync(HEAD_OID)]

    head = _facts(ports, head=_head(landing=[*before, sliced, *after]))[7]

    assert head.head_syncs_left == landing_rules.LANDING_SYNCS_MAX - 2


def test_a_document_that_cannot_be_counted_gives_no_syncs_left_but_keeps_the_paths(monkeypatch):
    ports = _ports()
    ports.workspace.touched_by[(MASTER, HEAD_OID)] = ("a.py",)

    def boom(*args, **kwargs):
        raise TypeError("scripted")

    monkeypatch.setattr(landing_lane.landing_plan, "spent", boom)

    head = _facts(ports)[7]

    assert head.head_syncs_left is None
    assert head.paths == frozenset({"a.py"})


def test_the_result_is_a_copy_in_the_order_given():
    ports = _ports()
    members = (_member(7), _member(5))

    result = lane_facts(ports, PRODUCT, MASTER, members, _head(), {5: MEMBER_HEAD})

    assert [m.issue for m in result] == [7, 5]
    assert members == (_member(7), _member(5))
    assert result[0] == dataclasses.replace(
        _member(7),
        integrating=True,
        paths=frozenset(),
        head_syncs_left=landing_rules.LANDING_SYNCS_MAX,
    )
