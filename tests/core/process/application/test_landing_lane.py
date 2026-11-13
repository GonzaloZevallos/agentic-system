"""GH-681: `landing_lane.lane_facts` reads the facts the fast lane needs and decides nothing: the
head's paths and syncs left, and for each candidate whether it holds master and what it touches.
A read that fails is `None`, never `False`, `0` or an empty set."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.application import landing_lane
from agentic.core.process.application.landing_lane import LaneHead, lane_facts
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import landing_order
from agentic.core.process.domain.task import PullRequest
from tests.fakes import MemoryDocuments, MemoryTracker, memory_ports

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


# --- GH-681 part 2: the informative record (admit, record_lane_merges, clear_admissions) ------

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)
MERGED = (NOW + timedelta(minutes=2)).isoformat()


class _Documents(MemoryDocuments):
    """`MemoryDocuments` that counts loads and can refuse the write of some documents."""

    def __init__(self) -> None:
        super().__init__()
        self.loaded: list[str] = []
        self.refuse: set[str] = set()

    def load(self, task_id):
        self.loaded.append(task_id)
        return super().load(task_id)

    def record_closure(self, task_id, values):
        if task_id in self.refuse:
            raise OSError("disk full")
        return super().record_closure(task_id, values)


def _lane_ports(*issues: int):
    ports = memory_ports(documents=_Documents())
    for issue in issues:
        ports.documents.save({"id": f"GH-{issue}", "title": "t"})
    return ports


def _work(*busy: str):
    return lambda task_id: task_id in busy


def _admit(ports, now=NOW, busy=(), out=None, pr_head=MEMBER_HEAD):
    landing_lane.admit(
        ports,
        "GH-5",
        ports.documents.load("GH-5"),
        7,
        pr_head,
        MASTER,
        now,
        _work(*busy),
        [] if out is None else out,
    )


def _merged(ports, head=MEMBER_HEAD, master=MASTER, at=MERGED):
    ports.documents.record_closure(
        "GH-5", {"auto_merge_head": head, "auto_merge_master": master, "auto_merged_at": at}
    )


def _record(ports, busy=(), open_ids=("GH-5", "GH-7"), now=LATER):
    out: list[str] = []
    landing_lane.record_lane_merges(ports, open_ids, now, _work(*busy), out)
    return out


def _admission(ports):
    return ports.documents.load("GH-5").get("lane_admitted")


def test_an_admission_is_written_once_and_keeps_its_instant_on_the_same_sha():
    ports = _lane_ports(5, 7)
    _admit(ports)
    assert _admission(ports) == {
        "head_issue": 7,
        "pr_head": MEMBER_HEAD,
        "master": MASTER,
        "at": NOW.isoformat(),
    }
    _admit(ports, now=LATER)
    assert _admission(ports)["at"] == NOW.isoformat()
    _admit(ports, now=LATER, pr_head=OTHER_OID)
    assert _admission(ports)["at"] == LATER.isoformat()


def test_an_admission_is_not_written_while_the_member_is_at_work():
    ports = _lane_ports(5, 7)
    _admit(ports, busy=("GH-5",))
    assert _admission(ports) is None


def test_a_failed_admission_write_is_one_line_and_never_an_error():
    ports = _lane_ports(5, 7)
    ports.documents.refuse.add("GH-5")
    out: list[str] = []
    _admit(ports, out=out)
    assert len(out) == 1 and "GH-5" in out[0]


def test_an_admission_is_cleared_when_the_member_is_no_longer_the_lane():
    ports = _lane_ports(5, 7)
    _admit(ports)
    out: list[str] = []
    landing_lane.clear_admissions(ports, [("GH-5", 5)], 5, _work(), out)
    assert _admission(ports) is not None  # still the lane (even when its step is a ci wait)
    landing_lane.clear_admissions(ports, [("GH-5", 5)], None, _work("GH-5"), out)
    assert _admission(ports) is not None  # at work: not written
    landing_lane.clear_admissions(ports, [("GH-5", 5)], None, _work(), out)
    assert _admission(ports) is None and out == []


def test_clearing_never_takes_an_admission_a_merge_followed():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    landing_lane.clear_admissions(ports, [("GH-5", 5)], None, _work(), [])
    assert _admission(ports) is not None


def test_a_cleared_admission_is_never_credited_when_the_member_later_merges_as_head():
    ports = _lane_ports(5, 7)
    _admit(ports)
    landing_lane.clear_admissions(ports, [("GH-5", 5)], None, _work(), [])
    _merged(ports)
    assert _record(ports) == []
    assert "lane_merges" not in ports.documents.load("GH-7")
    assert "lane_recorded" not in ports.documents.load("GH-5")


def test_a_merge_on_the_admitted_sha_is_appended_once_and_then_marks_the_member():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    assert _record(ports) == []
    merge = {"issue": 5, "pr_head": MEMBER_HEAD, "master": MASTER, "merged_at": MERGED}
    assert ports.documents.load("GH-7")["lane_merges"] == [merge]
    assert ports.documents.load("GH-5")["lane_recorded"] == LATER.isoformat()
    _record(ports)
    assert ports.documents.load("GH-7")["lane_merges"] == [merge]


def test_a_lost_mark_is_finished_without_a_second_entry_on_the_head():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    _record(ports)
    ports.documents.record_closure("GH-5", {"lane_recorded": None})
    _record(ports)
    assert len(ports.documents.load("GH-7")["lane_merges"]) == 1
    assert ports.documents.load("GH-5")["lane_recorded"] is not None


def test_a_merge_on_another_sha_marks_not_lane_and_writes_nothing_on_the_head():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports, head=OTHER_OID)
    _record(ports)
    assert ports.documents.load("GH-5")["lane_recorded"] == "not_lane"
    assert "lane_merges" not in ports.documents.load("GH-7")


def test_a_merge_made_by_hand_on_github_writes_nothing():
    ports = _lane_ports(5, 7)
    _admit(ports)
    ports.documents.record_closure("GH-5", {"closed_at": MERGED})  # no `auto_merged_at`
    assert _record(ports) == []
    assert "lane_recorded" not in ports.documents.load("GH-5")
    assert "lane_merges" not in ports.documents.load("GH-7")


@pytest.mark.parametrize("busy", [("GH-7",), ("GH-5",)])
def test_a_head_or_member_at_work_is_left_for_a_later_round_without_a_line(busy):
    """`at_work` is `busy` or a run lock that refuses: the poller builds one predicate."""
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    assert _record(ports, busy=busy) == []
    assert "lane_merges" not in ports.documents.load("GH-7")
    assert "lane_recorded" not in ports.documents.load("GH-5")
    _record(ports)
    assert len(ports.documents.load("GH-7")["lane_merges"]) == 1


def test_a_head_write_that_fails_leaves_the_member_unmarked_and_says_one_line_each_round():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    ports.documents.refuse.add("GH-7")
    for _ in range(2):
        out = _record(ports)
        assert len(out) == 1 and "#7" in out[0]
    assert "lane_recorded" not in ports.documents.load("GH-5")
    ports.documents.refuse.clear()
    assert _record(ports) == []
    assert len(ports.documents.load("GH-7")["lane_merges"]) == 1


def test_an_absent_head_document_marks_not_lane_with_one_line():
    ports = _lane_ports(5)
    _admit(ports)
    _merged(ports)
    out = _record(ports, open_ids=("GH-5",))
    assert len(out) == 1 and "#7" in out[0]
    assert ports.documents.load("GH-5")["lane_recorded"] == "not_lane"


def test_a_closed_member_is_still_recorded_from_the_documents_touched_in_the_window():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    ports.documents.record_closure("GH-5", {"closed_at": MERGED})
    _record(ports, open_ids=("GH-7",))
    assert len(ports.documents.load("GH-7")["lane_merges"]) == 1


def test_a_member_document_older_than_the_window_is_not_loaded():
    ports = _lane_ports(5, 7)
    _admit(ports)
    _merged(ports)
    ports.documents.untouched.add("GH-5")
    ports.documents.loaded.clear()
    _record(ports, open_ids=("GH-7",))
    assert "GH-5" not in ports.documents.loaded
    assert "lane_merges" not in ports.documents.load("GH-7")
