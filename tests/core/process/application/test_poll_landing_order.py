"""GH-588: the poller's merge round follows the landing order. Only the head of a product's order
is a candidate; the others wait approved, and a clean merge-tree asks nothing of them."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.process.adapters.filesystem.documents import FilesystemTaskDocuments
from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueState, RunRegistration, Upstream
from agentic.core.process.application.stack_facts import StackMemo
from agentic.core.process.domain import adr_numbers, ci, landing_order, rules
from agentic.core.process.domain import landing as landing_rules
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_landing_fix import _stranded_ports
from tests.core.process.application.test_poll import _Locks, _no_sleep
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    REOPENED,
    URL,
    _merges,
    _ports,
    _pr,
    _ready,
)
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryStack

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


def _head_of(number: int) -> str:
    return f"{number:x}".rjust(40, "c")


def _distinct(ports, *numbers: int) -> None:
    """Give each pull request a head of its own (and clean merge-tree readings against
    `MASTER`), so a conflict can be scripted on one of them."""
    for number in numbers:
        head = _head_of(number)
        ports.tracker.prs[URL.format(number)] = dataclasses.replace(_pr(), head_oid=head)
        document = ports.documents.load(f"GH-{number}")
        document["subtasks"] = {"only": {"id": "only", "status": "approved", "reviewed_head": head}}
        document["merge_checks"] = {"head": head, "master": MASTER, "passed": True}
        ports.documents.save(document)
        ports.workspace.conflict_answers[(head, MASTER)] = False


def _round(ports, master=MASTER, **more):
    lines: list[str] = []
    lands: list[rules.LandCandidate] = []
    found = poll.gather_merges(
        ports, frozenset(), master, _Locks(), lines, lands, dry_run=False, memo=StackMemo(), **more
    )
    return found, lands, lines


def _steps(monkeypatch) -> list[str]:
    """The landing steps `gather_merges` asked, in order."""
    asked: list[str] = []
    original = poll._landing_step

    def spy(*args, **kwargs):
        step = original(*args, **kwargs)
        if step is not None:
            asked.append(step.kind)
        return step

    monkeypatch.setattr(poll, "_landing_step", spy)
    return asked


def test_a_conflict_on_a_waiting_pull_request_triggers_nothing_until_its_turn(monkeypatch):
    ports = _three()
    _distinct(ports, 5, 6, 7)
    ports.workspace.conflict_answers[(_head_of(5), MASTER)] = True
    asked = _steps(monkeypatch)

    found, lands, lines = _round(ports)

    assert found == [7]
    assert lands == []
    assert asked == []
    assert lines == [
        "agentic: poll: auto-merge #5 -> waiting (conflicts) behind #7",
        "agentic: poll: auto-merge #6 -> waiting (clean) behind #7",
    ]


def test_an_unreadable_merge_tree_on_a_waiting_pull_request_is_logged_and_asks_nothing(
    monkeypatch,
):
    ports = _three()
    _distinct(ports, 5, 6, 7)
    ports.workspace.conflict_answers[(_head_of(5), MASTER)] = None
    asked = _steps(monkeypatch)

    found, lands, lines = _round(ports)

    assert found == [7]
    assert lands == [] and asked == []
    assert "agentic: poll: auto-merge #5 -> waiting (unread) behind #7" in lines


def test_three_pull_requests_with_master_moving_twice_evaluate_only_the_head_each_round(
    monkeypatch,
):
    ports = _three()
    _distinct(ports, 5, 6, 7)
    asked = _steps(monkeypatch)
    evaluated: list[int] = []
    for master in (MASTER, "d" * 40, "e" * 40):
        ports.workspace.upstream_answer = Upstream("origin/master", master, None)
        for number in (5, 6, 7):
            ports.workspace.conflict_answers[(_head_of(number), master)] = False
        found, lands, lines = _round(ports, master)
        evaluated += found
        assert lands == []  # a clean merge-tree: no integrate pass for anybody
        assert len(lines) == 3 - len(evaluated)  # everybody else waits
        ports.documents.record_closure(f"GH-{found[0]}", {"closed_at": "2026-11-11T00:00:00+00:00"})

    assert evaluated == [7, 5, 6]  # one evaluation each, in the order of the stamps
    assert asked == []  # zero sync_master


def test_the_head_alone_is_synced_when_it_conflicts_and_the_waiting_ones_are_not(monkeypatch):
    ports = _three()
    _distinct(ports, 5, 6, 7)
    for number in (5, 6, 7):
        ports.workspace.conflict_answers[(_head_of(number), MASTER)] = True
    asked = _steps(monkeypatch)

    found, lands, lines = _round(ports)

    assert found == []
    assert asked == ["sync_master"]  # exactly one, the head's
    assert lands == [rules.LandCandidate(7, "land_model")]
    assert "agentic: poll: auto-merge #5 -> waiting (conflicts) behind #7" in lines
    assert "agentic: poll: auto-merge #6 -> waiting (conflicts) behind #7" in lines


NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _stuck(ports, number: int) -> None:
    """Task `number` as GH-227 left it: its unit back at its review, the landing entry on disk,
    and no child (a reopened pass: `resume_owed`)."""
    document = ports.documents.load(f"GH-{number}")
    document["status"] = "developing"
    document["landing"] = [{**REOPENED}]
    document["subtasks"] = {"only": {"id": "only", "status": "delivered", "reviewed_head": HEAD}}
    ports.documents.save(document)


def _unstarted(ports, number: int) -> None:
    document = ports.documents.load(f"GH-{number}")
    document["status"] = "developing"
    document["landing"] = [{**REOPENED}]
    ports.documents.save(document)


def test_a_waiting_task_with_an_unstarted_pass_is_neither_found_nor_a_land_candidate():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    _unstarted(ports, 5)
    assert landing_rules.landing_pass(ports.documents.load("GH-5")) == "unstarted"

    found, lands, lines = _round(ports, now=NOW)

    assert found == [7]
    assert lands == []
    # not ready for review: its merge-tree is not read, and it waits all the same
    assert lines == ["agentic: poll: auto-merge #5 -> waiting (unread) behind #7"]


def test_a_waiting_task_that_owes_a_resume_is_not_a_land_candidate():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    _stuck(ports, 7)
    _stuck(ports, 5)
    assert landing_rules.resume_owed(ports.documents.load("GH-5"), NOW)

    lands: list[rules.LandCandidate] = []
    # a child works on the older pass: it leads, and the younger one waits behind it
    found = poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        _Locks(alive={"GH-7"}),
        [],
        lands,
        now=NOW,
        dry_run=False,
        memo=StackMemo(),
    )

    assert found == []
    assert lands == []


def test_a_stranded_diagnose_that_waits_is_not_a_land_candidate():
    ports = _stranded_ports()
    _ready(ports, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)

    found, lands, lines = _round(ports, now=NOW)

    assert found == [7]  # the older pull request leads
    assert lands == []  # the stranded diagnose waits for its turn like any other
    assert "agentic: poll: auto-merge #7 -> waiting" not in " ".join(lines)


def test_a_stranded_diagnose_is_the_systems_and_leads_when_it_is_the_oldest():
    ports = _stranded_ports()
    _ready(ports, 7)
    _queued(ports, 7, NEW)
    _queued(ports, 5, OLD)

    found, lands, lines = _round(ports, now=NOW)

    assert found == []
    assert lands == [rules.LandCandidate(5, "land_model")]  # `land` frees it (GH-257)
    assert "agentic: poll: auto-merge #7 -> waiting (clean) behind #5" in lines


def test_an_unstarted_pass_lands_once_its_turn_comes():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    _unstarted(ports, 5)
    ports.documents.record_closure("GH-7", {"closed_at": "2026-11-11T00:00:00+00:00"})

    _, lands, _ = _round(ports, now=NOW)

    assert lands == [rules.LandCandidate(5, "land_model")]  # the head: its turn


def test_a_head_with_an_open_pass_but_a_dead_child_escalated_does_not_block_the_next():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    _stuck(ports, 7)
    document = ports.documents.load("GH-7")
    document["escalated"] = "waits for a decision"
    ports.documents.save(document)

    found, lands, _ = _round(ports, now=NOW)

    assert found == [5]
    assert 7 not in [candidate.issue for candidate in lands]


def test_a_head_with_an_open_pass_that_needs_a_human_does_not_block_the_next():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    _stuck(ports, 7)
    ports.tracker.states[7] = IssueState("OPEN", "", (), None, ("needs-human",))

    found, lands, _ = _round(ports, now=NOW)

    assert found == [5]
    assert 7 not in [candidate.issue for candidate in lands]


def test_a_not_ready_head_with_a_live_pass_stays_ahead_of_an_older_stamp():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    _stuck(ports, 5)

    found, lands, lines = _round(ports, now=NOW)

    assert found == []
    assert lands == [rules.LandCandidate(5, "land_model")]  # the live pass: it resumes
    assert lines[-1] == "agentic: poll: auto-merge #7 -> waiting (clean) behind #5"


def test_an_older_pull_request_whose_stack_cannot_be_read_keeps_the_younger_one_waiting(
    monkeypatch,
):
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)
    original = poll.assess_merge

    def assessed(ports_, task_id, *args, **kwargs):
        if task_id == "GH-7":
            raise poll.StackUnreadable("base gone")
        return original(ports_, task_id, *args, **kwargs)

    monkeypatch.setattr(poll, "assess_merge", assessed)

    found, lands, lines = _round(ports, now=NOW)

    assert found == [] and lands == []
    assert sorted(lines) == [
        "agentic: poll: auto-merge #5 -> waiting (clean) behind #7",
        "agentic: poll: auto-merge #7 -> left to the human: stack_unreadable",
    ]


def test_an_owned_task_that_is_not_the_head_is_not_anchored():
    ports = _ports(5, 7)
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW, escalated="waits for a decision")

    _round(ports, now=NOW)

    assert "head_since_head" not in ports.documents.load("GH-5")
    assert ports.documents.load("GH-7")["head_since_head"] == HEAD


COLLISION = adr_numbers.Renaming((("docs/adr/0001-a.md", "docs/adr/0002-a.md"),), {})
# A collision the rule cannot settle (D-be76583a: `duplicate_own_number` is always asked).
AMBIGUOUS = adr_numbers.Ambiguous("duplicate_own_number", "0049 twice", (("a", "b"),))


def _collide(monkeypatch, collision, *colliding: str) -> None:
    """The pull requests of `colliding` task ids are refused with `adr_number_taken` on
    `collision`: what `assess_merge` reads from the diff and the base, scripted."""
    original = poll.assess_merge

    def assessed(ports, task_id, *args, **kwargs):
        result = original(ports, task_id, *args, **kwargs)
        if task_id not in colliding:
            return result
        return dataclasses.replace(
            result,
            decision=rules.MergeDecision("refuse", "adr_number_taken"),
            facts=dataclasses.replace(result.facts, adr_collision=collision),
        )

    monkeypatch.setattr(poll, "assess_merge", assessed)


def test_an_adr_collision_on_the_head_renumbers_and_on_a_waiting_pull_request_waits(monkeypatch):
    ports = _three()
    _collide(monkeypatch, COLLISION, "GH-7", "GH-5")
    asked = _steps(monkeypatch)

    _, lands, lines = _round(ports, now=NOW)

    assert asked == ["renumber"]  # the head's only
    assert lands == [rules.LandCandidate(7, "land_model")]
    assert "agentic: poll: auto-merge #5 -> waiting (clean) behind #7" in lines


def test_an_ambiguous_adr_collision_on_the_head_escalates(monkeypatch):
    ports = _three()
    _collide(monkeypatch, AMBIGUOUS, "GH-7")
    asked = _steps(monkeypatch)

    _, lands, _ = _round(ports, now=NOW)

    assert asked == ["escalate_adr"]
    assert lands == [rules.LandCandidate(7, "land_checks")]


def test_an_ambiguous_adr_collision_the_rule_settles_is_applied_on_the_head_only(monkeypatch):
    """GH-588 x D-be76583a: with the rule on, the head of the order is asked to apply the
    renumbering and the task behind it is not asked anything."""
    ports = _three()
    settled = adr_numbers.Ambiguous("unclassified_token", "app/main.py:228", (("a", "b"),))
    _collide(monkeypatch, settled, "GH-7", "GH-5")
    asked = _steps(monkeypatch)

    _round(ports, now=NOW)

    assert asked == ["apply_adr_decision"]  # the head's only


def test_a_head_that_escalated_an_adr_collision_does_not_block_the_next(monkeypatch):
    ports = _three()
    entry = {
        "head": HEAD,
        "step": "escalate_adr",
        "refusal": "adr_number_taken",
        "at": "2026-11-11T09:00:00+00:00",
    }
    ports.documents.record_closure("GH-7", {"escalated": "ADR collision"})
    document = ports.documents.load("GH-7")
    document["landing"] = [entry]
    ports.documents.save(document)
    _collide(monkeypatch, AMBIGUOUS, "GH-7")

    found, lands, lines = _round(ports, now=NOW)

    assert found == [5]  # the next stamp leads
    assert lands == []
    assert "agentic: poll: auto-merge #6 -> waiting (clean) behind #5" in lines


def _missing_ci(ports):
    ports = dataclasses.replace(ports, stack=MemoryStack(checks={"tests": "t"}, ci=("web",)))
    ports.tracker.ci_reports = []
    return ports


def test_a_head_waiting_for_ci_is_anchored_at_its_first_turn_and_not_again():
    ports = _missing_ci(_ports(5, 7, checks=False))
    _queued(ports, 7, OLD)
    _queued(ports, 5, NEW)

    _, _, lines = _round(ports, now=NOW)
    first = ports.documents.load("GH-7")
    _round(ports, now=NOW + timedelta(minutes=5))

    assert first["head_since_at"] == NOW.isoformat()
    assert first["head_since_head"] == HEAD
    assert ports.documents.load("GH-7")["head_since_at"] == NOW.isoformat()
    assert "head_since_at" not in ports.documents.load("GH-5")


def test_a_lost_anchor_write_is_made_again_later_never_earlier(monkeypatch):
    ports = _three()
    real = ports.documents.record_closure
    monkeypatch.setattr(
        ports.documents, "record_closure", lambda *a, **k: (_ for _ in ()).throw(OSError("lost"))
    )
    _round(ports, now=NOW)
    assert "head_since_at" not in ports.documents.load("GH-7")

    monkeypatch.setattr(ports.documents, "record_closure", real)
    later = NOW + timedelta(minutes=10)
    _round(ports, now=later)

    assert ports.documents.load("GH-7")["head_since_at"] == later.isoformat()


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


def _dead(task_id: str, **more):
    from agentic.core.process.application.ports import LockRecord

    return {
        task_id: LockRecord(777, "h", "2026-11-10T07:00:00Z", "agentic run", "boot-old", **more)
    }


def test_a_delivered_task_whose_run_was_cut_by_a_signal_still_leads_the_order():
    # GH-616: a dead lock with no mark is not a hold; only `agentic stop` writes one.
    found, _ = _merges(_three(), lock=_Locks(_dead("GH-7")))

    assert found == [7]


def test_a_delivered_task_stopped_by_agentic_stop_waits_and_the_next_one_leads():
    found, _ = _merges(_three(), lock=_Locks(_dead("GH-7", stopped_deliberately=True)))

    assert found == [5]


# GH-681: the fast lane. Pull request #7 is the head and a live child of the product works on it;
# #5 and #6 are clean and green, each with a path of its own and master already in its branch.
HEAD_SEEN = (NOW - timedelta(minutes=1)).isoformat()


def _lane_ports():
    ports = _three()
    _distinct(ports, 5, 6, 7)
    ports.documents.record_closure("GH-7", {"head_seen_at": HEAD_SEEN, "head_turn_at": HEAD_SEEN})
    for number, path in ((7, "head.py"), (5, "five.py"), (6, "six.py")):
        ports.workspace.ancestors_by_ref[_head_of(number)] = {MASTER}
        ports.workspace.touched_by[(MASTER, _head_of(number))] = (path,)
    return ports


def _lane_round(ports, tmp_path, *, now=NOW, master=MASTER, integrating=("GH-7",), **more):
    lines: list[str] = []
    found = poll.gather_merges(
        ports,
        frozenset(),
        master,
        _Locks(),
        lines,
        [],
        now=now,
        busy=frozenset({"GH-7"}),
        cfg=Configuration(product_dir=tmp_path),
        integrating={task: "land_model" for task in integrating},
        dry_run=False,
        memo=StackMemo(),
        **more,
    )
    return found, lines


def _evaluated(ports, tmp_path, *, now=NOW, master=MASTER, integrating=("GH-7",)):
    return poll.evaluate_landing_order(
        ports,
        frozenset(),
        master,
        _Locks(),
        frozenset({"GH-7"}),
        Configuration(product_dir=tmp_path),
        now,
        integrating={task: "land_model" for task in integrating},
        memo=StackMemo(),
    )


WAITING = "agentic: poll: auto-merge #{} -> waiting (clean) behind #7"
NOT_READY = "agentic: poll: auto-merge #{} -> waiting (clean, not merge ready) behind #7"
FAST = "agentic: poll: auto-merge #5 -> fast lane behind busy #7 (land_model, merge)"


class TestFastLane:
    def test_a_clean_green_member_is_found_with_the_fast_lane_line_beside_an_integrating_head(
        self, tmp_path
    ):
        ports = _lane_ports()

        found, lines = _lane_round(ports, tmp_path)

        assert found == [5]  # the head's own child is busy: only the lane member is found
        assert lines == [FAST, WAITING.format(6)]  # one lane member; #6 is ready and plain

    def test_a_conflicting_member_stays_waiting_and_the_next_one_takes_the_lane(self, tmp_path):
        ports = _lane_ports()
        ports.workspace.conflict_answers[(_head_of(5), MASTER)] = True

        found, lines = _lane_round(ports, tmp_path)

        assert found == [6]
        assert lines[0] == "agentic: poll: auto-merge #5 -> waiting (conflicts) behind #7"
        assert lines[1] == FAST.replace("#5", "#6")

    def test_members_waiting_on_ci_stay_waiting_and_open_no_lane(self, tmp_path):
        ports = _missing_ci(_lane_ports())

        found, lines = _lane_round(ports, tmp_path)

        assert found == []
        assert lines == [NOT_READY.format(5), NOT_READY.format(6)]

    def test_a_member_lacking_master_is_no_obstacle_and_gets_the_lane(self, tmp_path):
        ports = _lane_ports()
        ports.workspace.ancestors_by_ref[_head_of(5)] = set()

        found, lines = _lane_round(ports, tmp_path)

        assert found == [5]
        assert "lacks master" not in "".join(lines)

    def test_overlapping_paths_say_which_head_they_overlap(self, tmp_path):
        ports = _lane_ports()
        ports.workspace.touched_by[(MASTER, _head_of(5))] = ("head.py",)

        _, lines = _lane_round(ports, tmp_path)

        assert lines[0] == (
            "agentic: poll: auto-merge #5 -> waiting (clean, paths overlap #7) behind #7"
        )

    def test_a_head_not_integrating_opens_no_lane_and_says_so(self, tmp_path):
        ports = _lane_ports()  # a live `merge` or `land_checks` child is not in `integrating`

        found, lines = _lane_round(ports, tmp_path, integrating=())

        assert found == []
        assert lines == [
            "agentic: poll: auto-merge #5 -> waiting (clean, head not integrating) behind #7",
            "agentic: poll: auto-merge #6 -> waiting (clean, head not integrating) behind #7",
        ]

    def test_a_head_holding_no_turn_reads_nothing_and_says_it_is_not_integrating(self, tmp_path):
        ports = _lane_ports()
        lines: list[str] = []

        found = poll.gather_merges(
            ports,
            frozenset(),
            MASTER,
            _Locks(),
            lines,
            [],
            now=NOW,
            cfg=Configuration(product_dir=tmp_path),
            dry_run=False,
            memo=StackMemo(),
        )

        assert found == [7]
        assert lines == [
            "agentic: poll: auto-merge #5 -> waiting (clean, head not integrating) behind #7",
            "agentic: poll: auto-merge #6 -> waiting (clean, head not integrating) behind #7",
        ]
        assert ports.workspace.ancestor_checks == []

    def test_a_member_lacking_the_moved_master_gets_the_lane_in_the_next_round(self, tmp_path):
        ports = _lane_ports()
        first = _lane_round(ports, tmp_path)[0]
        moved = "d" * 40
        ports.documents.record_closure("GH-5", {"closed_at": "2026-11-11T00:00:00+00:00"})
        ports.workspace.upstream_answer = Upstream("origin/master", moved, None)
        document = ports.documents.load("GH-6")
        document["merge_checks"] = {"head": _head_of(6), "master": moved, "passed": True}
        ports.documents.save(document)
        ports.workspace.conflict_answers[(_head_of(6), moved)] = False

        found, lines = _lane_round(ports, tmp_path, now=NOW + timedelta(minutes=1), master=moved)

        assert first == [5]
        assert found == [6]
        assert lines == [FAST.replace("#5", "#6")]

    def test_a_head_with_one_sync_left_opens_no_lane(self, tmp_path):
        ports = _lane_ports()
        spent = [
            {"step": "sync_master", "head": _head_of(7), "at": f"2026-11-10T10:0{n}:00+00:00"}
            for n in range(landing_rules.LANDING_SYNCS_MAX - 2)
        ]
        ports.documents.record_closure("GH-7", {"landing": spent})
        assert _evaluated(ports, tmp_path).lane == 5  # two left: the lane opens

        ports.documents.record_closure("GH-7", {"landing": [*spent, {**spent[0], "at": "t9"}]})
        round_ = _evaluated(ports, tmp_path)

        assert round_.lane is None
        assert set(round_.lane_reasons.values()) == {"head_sync_budget"}
        found, lines = _lane_round(ports, tmp_path)
        assert found == []
        assert lines == [
            "agentic: poll: auto-merge #5 -> waiting (clean, head sync budget) behind #7",
            "agentic: poll: auto-merge #6 -> waiting (clean, head sync budget) behind #7",
        ]

    def test_a_head_first_seen_in_this_round_opens_the_lane_in_the_same_round(self, tmp_path):
        ports = _lane_ports()
        ports.documents.record_closure("GH-7", {"head_seen_at": None, "head_turn_at": None})

        first_found, first_lines = _lane_round(ports, tmp_path)
        second_found, second_lines = _lane_round(ports, tmp_path, now=NOW + timedelta(minutes=1))

        # GH-827: the round observes its head, so a busy head first seen now already holds the turn.
        assert first_found == second_found == [5]
        assert first_lines[0] == second_lines[0] == FAST

    def test_a_member_under_a_merge_refusal_backoff_prints_todays_line(self, tmp_path):
        ports = _lane_ports()
        ports.documents.record_closure(
            "GH-5",
            {
                "merge_refusal": {
                    "reason": "ci_untrusted_no_dispatch",
                    "head": _head_of(5),
                    "master": MASTER,
                    "at": NOW.isoformat(),
                }
            },
        )

        found, lines = _lane_round(ports, tmp_path)

        assert lines[0] == "agentic: poll: auto-merge #5 -> waiting (clean, backed off) behind #7"
        assert found == [6]

    def test_the_lane_changes_no_member_and_writes_nothing_of_its_own(self, tmp_path):
        with_lane, without = _lane_ports(), _lane_ports()

        _lane_round(with_lane, tmp_path)
        _lane_round(without, tmp_path, integrating=())
        a = _evaluated(_lane_ports(), tmp_path)
        b = _evaluated(_lane_ports(), tmp_path, integrating=())

        assert (a.lane, b.lane) == (5, None)
        assert [(e.member, e.turned) for e in a.entries] == [
            (e.member, e.turned) for e in b.entries
        ]
        # Its own writing is the informative admission (GH-681 part 2), on the member alone.
        for number in (5, 6, 7):
            written = dict(with_lane.documents.load(f"GH-{number}"))
            admitted = written.pop("lane_admitted", None)
            assert written == without.documents.load(f"GH-{number}")
            assert (admitted is not None) is (number == 5)
            assert "landing" not in written

    @pytest.mark.parametrize(
        ("kind", "opens"),
        [("land_model", True), ("role", True), ("merge", False), ("land_checks", False)],
    )
    def test_a_live_child_of_the_product_on_the_head_decides_through_the_poller(
        self, tmp_path, kind, opens
    ):
        ports = _lane_ports()
        launcher = FakeLauncher()
        live = RunRegistration(1, "h", "product", "GH-7", kind=kind)

        lines = poll.run_once(
            Configuration(product_dir=tmp_path),
            ports,
            MemoryRunRegistry(entries=[live]),
            launcher,
            NOW,
            sleep=_no_sleep,
            argv0="agentic",
            lock=_Locks(),
            current_boot="boot-now",
        )

        assert (FAST.replace("land_model", kind) in lines) is opens
        if not opens:
            assert (
                "agentic: poll: auto-merge #5 -> waiting (clean, head not integrating) behind #7"
                in lines
            )


def _idle_round(ports, tmp_path, locks):
    """A round with no busy child: the head is at work only when `locks` refuses it."""
    poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        locks,
        [],
        [],
        now=NOW + timedelta(minutes=5),
        busy=frozenset(),
        cfg=Configuration(product_dir=tmp_path),
        integrating={"GH-7": "land_model"},
        dry_run=False,
        memo=StackMemo(),
    )


class TestFastLaneRecord:
    """GH-681 part 2: what `gather_merges` writes of the lane's informative record. The writers'
    own cases are in `test_landing_lane.py`; these are the wiring: when the round writes at all."""

    def test_the_lane_member_is_admitted_once_in_its_own_document(self, tmp_path):
        ports = _lane_ports()

        _lane_round(ports, tmp_path)
        first = ports.documents.load("GH-5")["lane_admitted"]
        _lane_round(ports, tmp_path, now=NOW + timedelta(minutes=1))

        assert first == {
            "head_issue": 7,
            "pr_head": _head_of(5),
            "master": MASTER,
            "decision": "merge",
            "at": NOW.isoformat(),
        }
        assert ports.documents.load("GH-5")["lane_admitted"] == first
        assert not any(ports.documents.load(f"GH-{n}").get("lane_admitted") for n in (6, 7))

    def test_a_held_round_writes_no_lane_key_anywhere(self, tmp_path):
        ports = _lane_ports()

        found, _ = _lane_round(ports, tmp_path, held=True)

        assert found == [5]
        for number in (5, 6, 7):
            document = ports.documents.load(f"GH-{number}")
            assert not {"lane_admitted", "lane_recorded", "lane_merges"} & set(document)

    def test_a_merge_after_the_admission_is_recorded_on_the_head_once_it_is_idle(self, tmp_path):
        ports = _lane_ports()
        _lane_round(ports, tmp_path)
        ports.documents.record_closure(
            "GH-5",
            {
                "auto_merge_head": _head_of(5),
                "auto_merge_master": MASTER,
                "auto_merged_at": (NOW + timedelta(minutes=2)).isoformat(),
            },
        )

        # A head with a child at work is not written (`busy`): the next round that finds it idle is.
        _lane_round(ports, tmp_path, now=NOW + timedelta(minutes=4))
        assert "lane_merges" not in ports.documents.load("GH-7")
        _idle_round(ports, tmp_path, _Locks())

        merges = ports.documents.load("GH-7")["lane_merges"]
        assert [(m["issue"], m["pr_head"]) for m in merges] == [(5, _head_of(5))]
        assert ports.documents.load("GH-5")["lane_recorded"] is not None
        assert "landing" not in ports.documents.load("GH-7")

    def test_a_member_that_starts_waiting_on_ci_loses_the_lane_and_its_admission(self, tmp_path):
        ports = _lane_ports()
        _lane_round(ports, tmp_path)
        assert ports.documents.load("GH-5")["lane_admitted"] is not None
        head_before = dict(ports.documents.load("GH-7"))

        found, _ = _lane_round(_missing_ci(ports), tmp_path, now=NOW + timedelta(minutes=1))

        assert found == []
        assert not ports.documents.load("GH-5").get("lane_admitted")
        assert ports.documents.load("GH-7") == head_before

    def test_a_head_the_lock_refuses_is_not_written_even_with_no_busy_child(self, tmp_path):
        ports = _lane_ports()
        _lane_round(ports, tmp_path)
        ports.documents.record_closure(
            "GH-5",
            {
                "auto_merge_head": _head_of(5),
                "auto_merge_master": MASTER,
                "auto_merged_at": (NOW + timedelta(minutes=2)).isoformat(),
            },
        )

        _idle_round(ports, tmp_path, _Locks(alive={"GH-7"}))

        assert "lane_merges" not in ports.documents.load("GH-7")
        assert "lane_recorded" not in ports.documents.load("GH-5")


def _unseen_head(ports) -> None:
    """The head as a child run's `save` leaves it: no `head_seen_at`, no `head_turn_at`."""
    ports.documents.record_closure("GH-7", {"head_seen_at": None, "head_turn_at": None})


class TestLaneAssessedBesideAHeadTheRoundObserves:
    """The fast lane reads the head as `advance` returned it (GH-827): a head whose
    `head_seen_at` a child run erased is still seen as head in this round."""

    def test_a_busy_head_with_no_head_seen_at_still_assesses_every_clean_member(self, tmp_path):
        ports = _lane_ports()
        _unseen_head(ports)

        round_ = _evaluated(ports, tmp_path)

        assert round_.head == 7
        assert round_.lane == 5
        # #6 is obstacle-free but one lane per round goes to #5 (`landing_order._lane`): none of the
        # seven `LaneReason`s fits it, so it has none (GH-827 Decisions, invariant 1).
        assert round_.lane_reasons == {}

    def test_every_member_is_in_the_lane_or_has_a_reason_when_the_head_cannot_open_it(
        self, tmp_path
    ):
        ports = _lane_ports()
        _unseen_head(ports)
        ports.workspace.touched_by[(MASTER, _head_of(5))] = ("head.py",)
        ports.workspace.touched_by[(MASTER, _head_of(6))] = ("head.py",)

        round_ = _evaluated(ports, tmp_path)

        assert round_.lane is None
        assert round_.lane_reasons == {5: "paths_overlap", 6: "paths_overlap"}

    def test_the_round_after_a_child_run_saved_a_stale_state_assesses_the_members(self, tmp_path):
        memory = _lane_ports()
        _unseen_head(memory)
        documents = FilesystemTaskDocuments(tmp_path)
        for task_id in memory.documents.ids():
            state = memory.documents.load(task_id)
            documents.save({"id": task_id, "title": task_id, "description": "d"})
            documents.record_closure(task_id, {k: v for k, v in state.items() if k != "id"})
        ports = dataclasses.replace(memory, documents=documents)
        stale = documents.load("GH-7")  # the child loaded it before the poller wrote head_seen_at

        first = _evaluated(ports, tmp_path)
        _lane_round(ports, tmp_path)  # round N: the poller records the turn of the head
        assert documents.load("GH-7")["head_seen_at"] is not None
        documents.save(stale)  # the child saves: the whole frontmatter, head_seen_at gone
        assert not documents.load("GH-7").get("head_seen_at")
        second = _evaluated(ports, tmp_path, now=NOW + timedelta(minutes=1))

        assert first.lane == second.lane == 5
        assert second.lane_reasons == {}

    def test_a_hand_started_child_is_an_integrating_child_like_the_pollers_own(self, tmp_path):
        ports = _lane_ports()
        _unseen_head(ports)
        launcher = FakeLauncher()
        hand_started = RunRegistration(1, "h", "product", "GH-7", kind="role")

        lines = poll.run_once(
            Configuration(product_dir=tmp_path),
            ports,
            MemoryRunRegistry(entries=[hand_started]),
            launcher,
            NOW,
            sleep=_no_sleep,
            argv0="agentic",
            lock=_Locks(),
            current_boot="boot-now",
        )

        assert FAST.replace("land_model", "role") in lines

    @pytest.mark.parametrize("child", [None, "merge"])
    def test_a_head_that_does_not_integrate_reads_nothing_and_every_member_says_so(
        self, tmp_path, monkeypatch, child
    ):
        ports = _lane_ports()
        _unseen_head(ports)
        reads: list[str] = []
        workspace, tracker = ports.workspace, ports.tracker
        for owner, name in ((workspace, "is_ancestor"), (workspace, "touched_paths")):
            original = getattr(owner, name)

            def spy(*args, _name=name, _original=original, **kwargs):
                reads.append(_name)
                return _original(*args, **kwargs)

            monkeypatch.setattr(owner, name, spy)
        pull_request = tracker.pull_request

        def spy_pull_request(reference):
            pull_request_number(reference)
            reads.append("pull_request")
            return pull_request(reference)

        monkeypatch.setattr(tracker, "pull_request", spy_pull_request)
        lane_only = poll._fast_lane

        def counted(*args, **kwargs):
            reads.clear()
            result = lane_only(*args, **kwargs)
            lane_reads.extend(reads)
            return result

        lane_reads: list[str] = []
        monkeypatch.setattr(poll, "_fast_lane", counted)

        round_ = poll.evaluate_landing_order(
            ports,
            frozenset(),
            MASTER,
            _Locks(),
            frozenset({"GH-7"}) if child else frozenset(),
            Configuration(product_dir=tmp_path),
            NOW,
            integrating={},
            memo=StackMemo(),
        )

        assert lane_reads == []
        assert round_.lane is None
        assert round_.lane_reasons == {5: "head_not_integrating", 6: "head_not_integrating"}

    def test_a_clean_member_that_is_no_lane_candidate_says_it_is_not_merge_ready(self, tmp_path):
        ports = _missing_ci(_lane_ports())
        _unseen_head(ports)

        found, lines = _lane_round(ports, tmp_path, integrating=())

        assert found == []
        assert lines == [NOT_READY.format(5), NOT_READY.format(6)]

    def test_a_candidate_behind_a_head_with_no_child_says_the_head_is_not_integrating(
        self, tmp_path
    ):
        ports = _lane_ports()
        lines: list[str] = []

        poll.gather_merges(
            ports,
            frozenset(),
            MASTER,
            _Locks(),
            lines,
            [],
            now=NOW,
            cfg=Configuration(product_dir=tmp_path),
            dry_run=False,
            memo=StackMemo(),
        )

        assert lines == [
            "agentic: poll: auto-merge #5 -> waiting (clean, head not integrating) behind #7",
            "agentic: poll: auto-merge #6 -> waiting (clean, head not integrating) behind #7",
        ]


# The poller-lane subtask: the lane admits the decisions of `LANE_DECISIONS`, not only `merge`.
OLD_MASTER = "e" * 40


def _stale(ports, *numbers: int) -> None:
    """The checks of these pull requests are recorded on an older master: the real assessment
    decides `run_checks` for them (no `ci:`), the pair (head, current master) lacks evidence."""
    for number in numbers:
        document = ports.documents.load(f"GH-{number}")
        document["merge_checks"] = {"head": _head_of(number), "master": OLD_MASTER, "passed": True}
        ports.documents.save(document)
        ports.workspace.ancestors_by_ref[_head_of(number)] = set()  # holds no current master


def _round_at(ports, tmp_path, *, busy, integrating, now=NOW, master=MASTER):
    lines: list[str] = []
    found = poll.gather_merges(
        ports,
        frozenset(),
        master,
        _Locks(),
        lines,
        [],
        now=now,
        busy=frozenset(busy),
        cfg=Configuration(product_dir=tmp_path),
        integrating=integrating,
        dry_run=False,
        memo=StackMemo(),
    )
    return found, lines


RUN = "agentic: poll: auto-merge #{} -> fast lane behind busy #7 (land_model, run_checks)"


class TestLaneBesideAReintegratingHead:
    def test_two_clean_pull_requests_behind_a_reintegrating_head_merge_in_order(self, tmp_path):
        ports = _lane_ports()
        _stale(ports, 5, 6)
        head_child = {"GH-7": "land_model"}

        first, first_lines = _round_at(ports, tmp_path, busy={"GH-7"}, integrating=head_child)
        ports.documents.record_closure("GH-5", {"closed_at": "2026-11-11T00:00:00+00:00"})
        moved = "d" * 40
        ports.workspace.upstream_answer = Upstream("origin/master", moved, None)
        for number in (6, 7):
            ports.workspace.conflict_answers[(_head_of(number), moved)] = False
        second, second_lines = _round_at(
            ports,
            tmp_path,
            busy={"GH-7"},
            integrating=head_child,
            now=NOW + timedelta(minutes=1),
            master=moved,
        )

        assert first == [5] and first_lines[0] == RUN.format(5)
        assert second == [6] and second_lines[0] == RUN.format(6)
        assert "head_yields" not in ports.documents.load("GH-7")

    def test_the_head_that_delivers_is_the_next_one_merged_and_never_yields(self, tmp_path):
        ports = _lane_ports()
        _stale(ports, 5, 6)

        found, _ = _round_at(ports, tmp_path, busy=(), integrating={})

        assert found == [7]
        assert "head_yields" not in ports.documents.load("GH-7")

    def test_a_live_lane_merge_is_not_cut_when_the_head_delivers_and_the_head_follows(
        self, tmp_path
    ):
        ports = _lane_ports()
        _stale(ports, 5, 6)
        started, _ = _round_at(ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"})

        # the head delivered; the lane's own merge child (not integrating) is still alive
        later = NOW + timedelta(minutes=1)
        delivered, lines = _round_at(ports, tmp_path, busy={"GH-5"}, integrating={}, now=later)
        assert started == [5]
        assert 5 not in delivered and "(land_model" not in "".join(lines)
        assert (
            "agentic: poll: auto-merge #6 -> waiting (clean, head not integrating) behind #7"
            in lines
        )

        # the lane merge ended: the head is the product's candidate again
        ports.documents.record_closure("GH-5", {"closed_at": "2026-11-11T00:00:00+00:00"})
        last, _ = _round_at(
            ports, tmp_path, busy=(), integrating={}, now=later + timedelta(minutes=1)
        )
        assert last == [7]
        assert "head_yields" not in ports.documents.load("GH-7")

    def test_a_ci_run_jump_starts_stays_live_and_the_head_follows_it(self, tmp_path):
        ports = _lane_ports()
        _stale(ports, 5, 6)
        ports.stack.declared_ci_all = ("web",)
        ports.tracker.ci_reports = {
            f"https://github.com/o/r/pull/{number}": ci.CiReport(
                _head_of(number),
                "master",
                "9" * 40,
                "2026-11-03T09:00:00+00:00",
                (ci.CiCheck("web", "COMPLETED", "SUCCESS", "https://github.com/o/r/runs/1/job/5"),),
            )
            for number in (5, 6, 7)
        }

        started, started_lines = _round_at(
            ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"}
        )
        assert started == [5]
        assert started_lines[0] == (
            "agentic: poll: auto-merge #5 -> fast lane behind busy #7 (land_model, ci_run)"
        )
        assert ports.documents.load("GH-5")["lane_admitted"]["decision"] == "ci_run"

        # the head delivered; the lane's own ci_run merge child is still alive: nothing is cut
        later = NOW + timedelta(minutes=1)
        delivered, lines = _round_at(ports, tmp_path, busy={"GH-5"}, integrating={}, now=later)
        assert 5 not in delivered and "(land_model" not in "".join(lines)
        assert (
            "agentic: poll: auto-merge #6 -> waiting (clean, head not integrating) behind #7"
            in lines
        )

        # the jump ended: the head is the product's candidate again, and never yielded
        ports.documents.record_closure("GH-5", {"closed_at": "2026-11-11T00:00:00+00:00"})
        last, _ = _round_at(
            ports, tmp_path, busy=(), integrating={}, now=later + timedelta(minutes=1)
        )
        assert last == [7]
        assert "head_yields" not in ports.documents.load("GH-7")

    def test_a_clean_stacked_child_behind_an_integrating_head_never_jumps(self, tmp_path):
        ports = _lane_ports()
        _stale(ports, 5, 6)
        ports.tracker.states[9] = IssueState("OPEN", "", (), None)
        ports.documents.record_closure(
            "GH-5",
            {
                "stack_parent": {
                    "issue": 9,
                    "task_id": "GH-9",
                    "head": "c" * 40,
                    "started_at": "2026-11-16T10:00:00+00:00",
                }
            },
        )

        found, lines = _round_at(ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"})

        assert found == [6]
        assert lines[0] == (
            "agentic: poll: auto-merge #5 -> waiting (unread, stacked parent unmerged) behind #7"
        )

    def test_a_backed_off_and_a_refused_member_say_why_in_the_line(self, tmp_path):
        ports = _lane_ports()
        ports.documents.record_closure(
            "GH-5",
            {
                "merge_refusal": {
                    "reason": "ci_untrusted_no_dispatch",
                    "head": _head_of(5),
                    "master": MASTER,
                    "at": NOW.isoformat(),
                }
            },
        )
        document = ports.documents.load("GH-6")
        document["merge_checks"] = {"head": _head_of(6), "master": MASTER, "passed": False}
        ports.documents.save(document)

        found, lines = _round_at(ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"})

        assert found == []
        assert lines[0] == "agentic: poll: auto-merge #5 -> waiting (clean, backed off) behind #7"
        assert lines[1] == NOT_READY.format(6)

    def test_the_admission_and_the_merge_record_carry_the_admitted_decision(self, tmp_path):
        ports = _lane_ports()
        _stale(ports, 5, 6)
        _round_at(ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"})
        assert ports.documents.load("GH-5")["lane_admitted"]["decision"] == "run_checks"

        ports.documents.record_closure(
            "GH-5",
            {
                "auto_merged_at": (NOW + timedelta(seconds=30)).isoformat(),
                "auto_merge_head": _head_of(5),
                "auto_merge_master": MASTER,
                "closed_at": "2026-11-11T00:00:00+00:00",
            },
        )
        _round_at(
            ports,
            tmp_path,
            busy=(),
            integrating={},
            now=NOW + timedelta(minutes=1),
        )

        merges = ports.documents.load("GH-7")["lane_merges"]
        assert [(m["issue"], m["decision"]) for m in merges] == [(5, "run_checks")]


def test_every_lane_reason_has_its_text():
    assert set(poll.LANE_TEXT) == set(get_args(landing_order.LaneReason))


def test_a_stored_decision_outside_the_closed_list_is_dropped_and_an_old_entry_has_none():
    from agentic.core.process.domain import document

    admitted = {"head_issue": 7, "pr_head": "a" * 40, "master": "b" * 40, "at": NOW.isoformat()}
    merge = {"issue": 5, "pr_head": "a" * 40, "master": "b" * 40, "merged_at": NOW.isoformat()}

    kept = document._lane_fields(
        {
            "lane_admitted": {**admitted, "decision": "ci_run"},
            "lane_merges": [{**merge, "decision": "refuse"}, {**merge, "decision": "run_checks"}],
        }
    )
    old = document._lane_fields({"lane_admitted": admitted, "lane_merges": [merge]})

    assert kept["lane_admitted"]["decision"] == "ci_run"
    assert ["decision" in m for m in kept["lane_merges"]] == [False, True]
    assert "decision" not in old["lane_admitted"] and "decision" not in old["lane_merges"][0]


class TestLaneWithCiBesideAReintegratingHead:
    """The product declares `ci:`: a clean pull request whose head lacks the current master is
    judged `ci_run` by the real assessment, and the lane admits it, its fresh run being the
    merge child's own gate on the pair."""

    @staticmethod
    def _ci_ports():
        ports = _lane_ports()
        _stale(ports, 5, 6)
        ports = dataclasses.replace(ports, stack=MemoryStack(checks={"tests": "x"}, ci=("web",)))
        # each head's pull-request CI is green, on a master older than the current one
        ports.tracker.ci_reports = {
            URL.format(number): ci.CiReport(
                _head_of(number),
                "master",
                OLD_MASTER,
                "2026-11-03T09:00:00+00:00",
                (
                    ci.CiCheck(
                        "web", "COMPLETED", "SUCCESS", f"https://github.com/o/r/runs/{number}"
                    ),
                ),
            )
            for number in (5, 6, 7)
        }
        return ports

    def test_a_ci_run_jump_starts_and_stays_live_while_the_head_delivers(self, tmp_path):
        ports = self._ci_ports()

        started, lines = _round_at(
            ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"}
        )

        assert started == [5]
        assert lines[0] == (
            "agentic: poll: auto-merge #5 -> fast lane behind busy #7 (land_model, ci_run)"
        )
        assert ports.documents.load("GH-5")["lane_admitted"]["decision"] == "ci_run"

        # the head delivered while the jump's own merge child (not integrating) is alive: it is
        # neither cut nor followed by a second jump
        later = NOW + timedelta(minutes=1)
        delivered, lines = _round_at(ports, tmp_path, busy={"GH-5"}, integrating={}, now=later)
        assert 5 not in delivered and 6 not in delivered
        assert "(land_model" not in "".join(lines)
        assert (
            "agentic: poll: auto-merge #6 -> waiting (clean, head not integrating) behind #7"
            in lines
        )
        assert "head_yields" not in ports.documents.load("GH-7")

        # the jump ended: the head is the product's own candidate again
        ports.documents.record_closure("GH-5", {"closed_at": "2026-11-11T00:00:00+00:00"})
        last, _ = _round_at(
            ports, tmp_path, busy=(), integrating={}, now=later + timedelta(minutes=1)
        )
        assert last == [7]

    def test_a_refused_ci_decision_opens_no_jump(self, tmp_path):
        ports = self._ci_ports()
        for number in (5, 6):
            ports.documents.record_closure(
                f"GH-{number}",
                {
                    "merge_refusal": {
                        "reason": "ci_untrusted_no_dispatch",
                        "head": _head_of(number),
                        "master": MASTER,
                        "at": NOW.isoformat(),
                    }
                },
            )

        found, lines = _round_at(ports, tmp_path, busy={"GH-7"}, integrating={"GH-7": "land_model"})

        assert found == []
        assert not any("fast lane" in line for line in lines)
