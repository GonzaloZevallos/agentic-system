"""GH-860: the poller's decision reads a stacked child's facts through `stack_facts`, once the
pair is landable, memoised per window. In-memory ports with counting wrappers: no `gh`, no git."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.application import (
    checkouts,
    landing,
    merge,
    poll,
    stack_hold,
    stacked_start,
)
from agentic.core.process.application.ports import IssueState, LockRecord, Upstream
from agentic.core.process.application.stack_facts import StackMemo
from agentic.core.process.domain import landing_order, rules, stacking
from agentic.core.process.domain.task import VERDICT_UNLANDABLE, StackParent
from agentic.shared.configuration import Configuration
from tests.core.process.adapters.git import test_workspace
from tests.core.process.application.test_poll import _cfg, _Locks
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _HeldLocks,
    _ports,
    _pr,
)

# the product fixture of the git adapter's tests, reused as it is (a fixture is found by its name)
product = test_workspace.product
git = test_workspace.git

PARENT = "c" * 40
CLOSING = "f" * 40


def _record(issue: int = 9) -> StackParent:
    return StackParent(
        issue=issue, task_id=f"GH-{issue}", head=PARENT, started_at="2026-11-16T10:00:00"
    )


def _stacked(*numbers: int, issue: int = 9, **more):
    ports = _ports(*numbers, stack_parent=_record(issue), **more)
    ports.tracker.states[issue] = IssueState("OPEN", "", (), None)
    return ports


def _count(ports) -> dict[str, int]:
    """Wraps the three reads a stacking fact costs; the dict is what each was asked."""
    calls = {"issue_state": 0, "fetch_commit": 0, "is_ancestor": 0}
    tracker, workspace = ports.tracker, ports.workspace
    issue_state, fetch, ancestor = (
        tracker.issue_state,
        workspace.fetch_commit,
        workspace.is_ancestor,
    )

    def counted(name, original):
        def wrapper(*args, **kwargs):
            # The landing order reads the labels of every task's issue: only the parent's counts.
            if name != "issue_state" or args[0] == 9:
                calls[name] += 1
            return original(*args, **kwargs)

        return wrapper

    tracker.issue_state = counted("issue_state", issue_state)
    workspace.fetch_commit = counted("fetch_commit", fetch)
    workspace.is_ancestor = counted("is_ancestor", ancestor)
    return calls


def _assess(ports, number: int = 5, memo: StackMemo | None = None):
    return poll.assess_merge(
        ports,
        f"GH-{number}",
        ports.documents.load(f"GH-{number}"),
        number,
        frozenset(),
        MASTER,
        cfg=_cfg(Path("/p")),
        memo=memo or StackMemo(),
    )


def test_a_stacked_child_that_reaches_the_row_is_read_and_refused_per_the_table():
    ports = _stacked(5)
    assessed = _assess(ports)
    assert assessed.decision.reason == "stack_parent_unmerged"
    assert assessed.facts.stack.parent_state == "open"
    assert assessed.facts.stack.parent_in_upstream is False


def test_a_stacked_child_whose_closed_parent_left_its_head_out_is_orphaned():
    ports = _stacked(5)
    ports.tracker.states[9] = IssueState("CLOSED", "COMPLETED", (), CLOSING)
    ports.workspace.ancestors_by_ref[CLOSING] = set()
    assert _assess(ports).decision.reason == "stack_base_orphaned"


def test_a_landed_parent_the_child_holds_merges():
    ports = _stacked(5)
    ports.workspace.ancestors = {PARENT}
    assessed = _assess(ports)
    assert assessed.decision.kind == "merge"
    assert assessed.facts.stack.child_has_parent is True


def _forbid_stack_facts(monkeypatch) -> None:
    def refuse(*args, **kwargs):
        raise AssertionError("stack_facts was called")

    monkeypatch.setattr(poll, "stack_facts", refuse)


def test_a_document_without_the_record_makes_no_stacking_read(monkeypatch):
    ports = _ports(5)
    _forbid_stack_facts(monkeypatch)
    calls = _count(ports)
    assert _assess(ports).decision.kind == "merge"
    assert calls["issue_state"] == 0


def test_an_unlandable_verdict_makes_no_stacking_read(monkeypatch):
    diagnosis = {
        "head": HEAD,
        "step": "diagnose",
        "refusal": "merge_failed",
        "delivered": True,
        "verdict": VERDICT_UNLANDABLE,
    }
    ports = _stacked(5, landing=[diagnosis])
    _forbid_stack_facts(monkeypatch)
    calls = _count(ports)
    _assess(ports)
    assert calls["issue_state"] == 0


def test_two_children_of_one_parent_in_a_round_share_one_issue_read():
    ports = _stacked(5, 6)
    calls = _count(ports)
    memo = StackMemo()
    assert _assess(ports, 5, memo).decision.reason == "stack_parent_unmerged"
    assert _assess(ports, 6, memo).decision.reason == "stack_parent_unmerged"
    assert calls["issue_state"] == 1


def test_the_poller_round_gives_its_children_one_memo(tmp_path):
    ports = _stacked(5, 6)
    calls = _count(ports)
    lines: list[str] = []
    found = poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        _Locks(),
        lines,
        cfg=_cfg(tmp_path),
        dry_run=False,
        memo=StackMemo(),
    )
    assert found == []
    assert calls["issue_state"] == 1
    # GH-838: a held child is never head, so the line says why it waits.
    assert lines == [
        f"agentic: poll: auto-merge #{n} -> waiting (unread, stacked parent unmerged) with no head"
        for n in (5, 6)
    ]


def test_a_land_reads_the_parent_once_for_its_order_note_and_once_for_its_own_decision(tmp_path):
    """`landing.land` assesses twice, `_note_order` (the other children of the order: GH-6, the task
    itself being at work) and `_land` (GH-5), each with its own fresh memo, both of one parent: a
    memo shared between them would make the second read free."""
    ports = _stacked(5, 6)
    calls = _count(ports)
    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
        turn="head",
    )
    assert result.kind == "skipped"
    assert calls["issue_state"] == 2


def test_the_verb_reads_the_parent_once_and_refuses_it_as_unmerged(tmp_path):
    """GH-876: `merge_task` reads the parent through `stack_facts`: one `issue_state` per call."""
    ports = _stacked(5)
    calls = _count(ports)
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
        lambda: datetime(2026, 11, 16, tzinfo=UTC).isoformat(),
    )
    assert "stack_parent_unmerged" in repr(result)
    assert calls["issue_state"] == 1


# GH-885: the wait on an unfetchable head, written by the round.
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def _unfetchable(ports, missing: str = PARENT) -> None:
    ports.workspace.fetch_commit = lambda sha: sha != missing


def _round(ports, tmp_path, lock=None, busy=frozenset(), held=False, lands=None, now=NOW):
    lines: list[str] = []
    poll.gather_merges(
        ports,
        frozenset(),
        MASTER,
        lock or _Locks(),
        lines,
        lands,
        now=now,
        busy=busy,
        cfg=_cfg(tmp_path),
        held=held,
        dry_run=False,
        memo=StackMemo(),
    )
    return lines


def _written(ports, number: int = 5):
    return ports.documents.closures.get(f"GH-{number}", {}).get("stack_unfetchable", "unwritten")


def test_a_stacked_child_with_an_unfetchable_parent_head_writes_since(tmp_path):
    ports = _stacked(5)
    _unfetchable(ports)
    _round(ports, tmp_path)
    assert _written(ports) == {"head": HEAD, "since": NOW.isoformat()}


def test_a_waiting_child_writes_since_and_runs_no_step(tmp_path):
    ports = _stacked(5, 6)
    _unfetchable(ports)
    lands: list = []
    _round(ports, tmp_path, lands=lands)
    assert _written(ports, 5) == {"head": HEAD, "since": NOW.isoformat()}
    assert _written(ports, 6) == {"head": HEAD, "since": NOW.isoformat()}
    assert lands == []


def test_a_second_round_with_the_record_in_place_writes_nothing(tmp_path):
    ports = _stacked(5)
    _unfetchable(ports)
    _round(ports, tmp_path)
    writes: list = []
    record = ports.documents.record_closure
    ports.documents.record_closure = lambda t, v: writes.append(dict(v)) or record(t, v)
    _round(ports, tmp_path, now=NOW.replace(hour=10))
    assert not [w for w in writes if "stack_unfetchable" in w]
    assert _written(ports) == {"head": HEAD, "since": NOW.isoformat()}


def test_a_held_round_writes_nothing(tmp_path):
    ports = _stacked(5)
    _unfetchable(ports)
    _round(ports, tmp_path, held=True)
    assert _written(ports) == "unwritten"


def test_a_busy_task_writes_nothing(tmp_path):
    ports = _stacked(5)
    _unfetchable(ports)
    _round(ports, tmp_path, busy=frozenset({"GH-5"}))
    assert _written(ports) == "unwritten"


def test_a_locked_task_writes_nothing(tmp_path):
    ports = _stacked(5)
    _unfetchable(ports)
    _round(ports, tmp_path, lock=_Locks(alive={"GH-5"}))
    assert _written(ports) == "unwritten"


class _LockAppearing(_Locks):
    """Reads free through the assessment, alive from the first read after it was asked once
    more: the lock appears between the assessment and the write."""

    def __init__(self):
        super().__init__()
        self.asked = 0

    def refusal(self, task_id):
        self.asked += 1
        return "held" if self.asked > 1 else None


def test_a_lock_appearing_between_assessment_and_write_blocks_the_write(tmp_path):
    ports = _stacked(5)
    _unfetchable(ports)
    lock = _LockAppearing()
    _round(ports, tmp_path, lock=lock)
    assert lock.asked >= 2
    assert _written(ports) == "unwritten"


def test_an_escalated_child_with_a_record_writes_null_once_and_nothing_after(tmp_path):
    record = {"head": HEAD, "since": NOW.isoformat()}
    ports = _stacked(5, escalated={"class": "questions"}, stack_unfetchable=record)
    _unfetchable(ports)
    _round(ports, tmp_path)
    assert _written(ports) is None
    ports.documents.closures["GH-5"]["marker"] = 1
    _round(ports, tmp_path)
    assert ports.documents.closures["GH-5"] == {"stack_unfetchable": None, "marker": 1}


def test_a_document_without_stack_parent_reads_no_heads_and_writes_nothing(tmp_path, monkeypatch):
    ports = _ports(5)
    _forbid_stack_facts(monkeypatch)
    writes: list = []
    record = ports.documents.record_closure
    ports.documents.record_closure = lambda t, v: writes.append(dict(v)) or record(t, v)
    assert _assess(ports).unfetchable is None
    _round(ports, tmp_path)
    assert not [w for w in writes if "stack_unfetchable" in w]
    assert _written(ports) == "unwritten"


def test_the_assessment_carries_expiry_only_when_a_head_was_read_missing():
    record = {"head": HEAD, "since": (NOW - poll.stacking.STACK_UNFETCHABLE_WAIT).isoformat()}
    missing = _stacked(5, stack_unfetchable=record)
    _unfetchable(missing)
    stack = poll.assess_merge(
        missing,
        "GH-5",
        missing.documents.load("GH-5"),
        5,
        frozenset(),
        MASTER,
        cfg=_cfg(Path("/p")),
        memo=StackMemo(),
        now=NOW,
    ).facts.stack
    assert (stack.heads_fetched, stack.unfetchable_expired) == (False, True)
    assert stack.unfetchable_since == record["since"]
    fetched = _stacked(5, stack_unfetchable=record)
    stack = _assess(fetched).facts.stack
    assert (stack.heads_fetched, stack.unfetchable_expired, stack.unfetchable_since) == (
        True,
        None,
        None,
    )


def test_a_failing_write_is_said_in_one_line_and_does_not_end_the_round(tmp_path):
    ports = _stacked(5, 6)
    _unfetchable(ports)

    def broken(task_id, values):
        raise OSError("disk")

    ports.documents.record_closure = broken
    lines = _round(ports, tmp_path)
    assert sum("stack_unfetchable not recorded" in line for line in lines) == 2
    assert any("auto-merge #5" in line for line in lines)


# --- GH-838 part 2: `stack_held` of a landing-order member ---------------------------------------


def _stack_member(ports, assessed, number: int = 5):
    return poll._member(
        ports,
        None,
        _Locks(),
        f"GH-{number}",
        number,
        ports.documents.load(f"GH-{number}"),
        assessed,
    )


def test_a_stacked_child_whose_parent_is_unmerged_is_a_held_member():
    ports = _stacked(5)
    assessed = _assess(ports)
    assert assessed.facts.stack is not None
    assert _stack_member(ports, assessed).stack_held is True


def test_a_stacked_child_whose_ancestry_could_not_be_read_is_held():
    ports = _stacked(5)
    ports.workspace.ancestor_errors.add(PARENT)
    assessed = _assess(ports)
    assert assessed.facts.stack is not None
    assessed = dataclasses.replace(
        assessed,
        facts=dataclasses.replace(
            assessed.facts,
            stack=dataclasses.replace(assessed.facts.stack, parent_in_upstream=None),
        ),
    )
    assert _stack_member(ports, assessed).stack_held is True


def test_a_task_with_no_stack_record_is_not_held_and_reads_nothing():
    ports = _ports(5)
    assessed = _assess(ports)
    assert assessed.facts.stack is None
    calls = _count(ports)
    assert _stack_member(ports, assessed).stack_held is False
    assert calls == {"issue_state": 0, "fetch_commit": 0, "is_ancestor": 0}


def test_a_member_built_without_an_assessment_is_not_held():
    ports = _stacked(5)
    assert _stack_member(ports, None).stack_held is False


def test_the_member_makes_no_read_of_its_own():
    ports = _stacked(5)
    assessed = _assess(ports)
    calls = _count(ports)
    assert _stack_member(ports, assessed).stack_held is True
    assert calls == {"issue_state": 0, "fetch_commit": 0, "is_ancestor": 0}


def test_every_lane_reason_has_poller_text():
    assert set(poll.LANE_TEXT) == set(get_args(landing_order.LaneReason))
    assert poll.LANE_TEXT["stack_parent_unmerged"] == "stacked parent unmerged"


# --- GH-1163: the whole stacked life on real git -------------------------------------------------

REPO_NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _ready_parent(repo: Path) -> str:
    """Parent GH-5: a pushed task branch with one commit, which is the head its review approved."""
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    git("push", "-q", "-u", "origin", "master", cwd=repo)
    git("checkout", "-q", "-b", "agentic/GH-5", cwd=repo)
    (repo / "parent.txt").write_text("parent\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "parent", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    git("push", "-q", "origin", "agentic/GH-5", cwd=repo)
    git("checkout", "-q", "master", cwd=repo)
    return head


def _real_ports(repo: Path, parent_head: str):
    workspace = GitWorkspace(repo, Configuration(product_dir=repo).git_identity)
    ports = dataclasses.replace(_ports(), workspace=workspace)
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, ())
    ports.documents.save(
        {
            "id": "GH-5",
            "status": "ready_for_review",
            "subtasks": {"a": {"id": "a", "status": "approved", "reviewed_head": parent_head}},
        }
    )
    return ports


def _deliver_child(ports, record, head: str, master: str):
    """The child's document as delivery leaves it: approved at `head`, checks green on the pair."""
    ports.tracker.prs[URL.format(6)] = _pr()
    ports.tracker.head_oid = head
    ports.documents.save(
        {
            "id": "GH-6",
            "status": "ready_for_review",
            "pull_request": URL.format(6),
            "stack_parent": record,
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": head}},
            "merge_checks": {"head": head, "master": master, "passed": True},
        }
    )
    return poll.assess_merge(
        ports,
        "GH-6",
        ports.documents.load("GH-6"),
        6,
        frozenset(),
        master,
        cfg=Configuration(product_dir=ports.workspace.product_dir),
        memo=StackMemo(),
    )


def test_a_stacked_child_launches_on_the_ready_parent_and_lands_through_a_restack(product):
    cfg = Configuration(product_dir=product)
    parent_head = _ready_parent(product)
    ports = _real_ports(product, parent_head)
    decision = stacked_start.decide("GH-6", 5, None, ports, REPO_NOW.isoformat(), cfg=cfg)
    assert decision.ref == stacking.Parent(parent_head)
    record = decision.patch["stack_parent"]
    child = checkouts.task_checkout({"id": "GH-6", "stack_parent": record}, cfg, ports)
    base = ports.workspace.base_of(rules.task_branch("GH-6"))
    assert base is not None and git("rev-parse", base, cwd=product) == parent_head

    (child.path / "child.txt").write_text("child\n")
    ports.workspace.commit_all(child.path, "child")
    head = ports.workspace.full_head(child.path)
    master = ports.workspace.upstream().sha
    assert master is not None
    assessed = _deliver_child(ports, record, head, master)
    assert assessed.decision.reason == "stack_parent_unmerged"

    before = master
    git("merge", "-q", "--no-ff", "-m", "Merge parent", "agentic/GH-5", cwd=product)
    git("push", "-q", "origin", "master", cwd=product)
    landed = git("rev-parse", "HEAD", cwd=product)
    ports.tracker.states[5] = IssueState("CLOSED", "COMPLETED", (), landed, ())
    master = ports.workspace.upstream().sha
    assert master == landed
    # a child that was started at the recorded head carries it: nothing to restack
    assert _deliver_child(ports, record, head, master).decision.kind == "merge"

    # a child whose history lost the recorded head (rebuilt on the old master) is due a restack
    tree = git("rev-parse", f"{head}^{{tree}}", cwd=product)
    rebuilt = git("commit-tree", tree, "-p", before, "-m", "child rebuilt", cwd=product)
    git("reset", "-q", "--hard", rebuilt, cwd=child.path)
    assessed = _deliver_child(ports, record, rebuilt, master)
    assert assessed.decision.reason == "stack_restack_due"

    synced = ports.workspace.merge_upstream_into(child.path, master)
    assert synced.clean
    assessed = _deliver_child(ports, record, synced.head, master)
    assert assessed.facts.stack is not None and assessed.facts.stack.child_has_parent is True
    assert assessed.decision == rules.MergeDecision("merge", None)


def test_a_branchless_stacked_child_whose_parent_left_ready_is_held_and_spends_no_retake(product):
    cfg = Configuration(product_dir=product)
    parent_head = _ready_parent(product)
    ports = _real_ports(product, parent_head)
    decision = stacked_start.decide("GH-6", 5, None, ports, REPO_NOW.isoformat(), cfg=cfg)
    # the start wrote the record and died before the branch: a dead lock is all that is left
    ports.documents.save({"id": "GH-6", "status": "developing", **decision.patch})
    ports.tracker._queue = [6]
    ports.tracker.states[6] = IssueState("OPEN", "", (), None)
    assert ports.workspace.branch_head(rules.task_branch("GH-6")) is None
    ports.documents.save({"id": "GH-5", "status": "developing"})  # the parent left Ready
    locks = _Locks(
        {"GH-6": LockRecord(777, "h", "2026-11-27T08:00:00Z", "agentic run", "boot-old")}
    )
    queued = {6: ports.documents.load("GH-6")}
    hold = stack_hold.compute(ports, cfg, locks, queued, ports.workspace.upstream, StackMemo(), [])
    assert hold.held == {6: "parent_not_ready"}

    lines = stack_hold.write_episodes(
        ports, locks, hold, queued, REPO_NOW, dry_run=False, space_held=False
    )
    assert lines == []
    notes = [text for task, text in ports.tracker.comments if task == "GH-6"]
    assert len(notes) == 1 and stack_hold.marker(5, "parent_not_ready", 1) in notes[0]

    # without the hold, the dead lock of the unstarted child is the retake loop this part closes
    assert poll.gather_retakes(ports, locks, "boot-now", now=REPO_NOW)[0] == [6]
    spent: dict = {}
    retakes, said = poll.gather_retakes(
        ports,
        locks,
        "boot-now",
        now=REPO_NOW,
        spent=spent,
        skip=frozenset(hold.held),
    )
    assert (retakes, said, spent) == ([], [], {})
    assert [text for task, text in ports.tracker.comments if task == "GH-6"] == notes
