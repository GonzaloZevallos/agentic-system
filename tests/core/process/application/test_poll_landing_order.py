"""GH-588: the poller's merge round follows the landing order. Only the head of a product's order
is a candidate; the others wait approved, and a clean merge-tree asks nothing of them."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from agentic.core.process.application import poll
from agentic.core.process.application.ports import IssueState, Upstream
from agentic.core.process.domain import adr_numbers, rules
from agentic.core.process.domain import landing as landing_rules
from tests.core.process.application.test_landing_fix import _stranded_ports
from tests.core.process.application.test_poll import _Locks
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
from tests.fakes import MemoryStack

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
    found = poll.gather_merges(ports, frozenset(), master, _Locks(), lines, lands, **more)
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
        ports, frozenset(), MASTER, _Locks(alive={"GH-7"}), [], lands, now=NOW
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
