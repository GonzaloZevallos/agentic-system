"""GH-334: a task whose pull request closed unmerged after slices landed for it. The poller
reads the pull request once, asks git (never `landed_slices` alone) whether every unit's
`reviewed_head` reached master, and either closes the document (`closed_at`,
`closed_by_slices`, never a `delivery_outcome`) or asks for a fresh run, saying which."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.application import poll, tracker_only
from agentic.core.process.application.ports import RunRegistration, Upstream
from agentic.core.process.domain.task import PullRequest
from tests.core.process.application.test_poll import ESCALATED_AT, _cfg, _Locks, _round
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _pr, _ready
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryTracker, memory_ports

SLICE_PR = "https://github.com/o/r/pull/350"
A, B = "1" * 40, "2" * 40


def _landed(**more):
    return {
        "subtasks": {
            "a": {"id": "a", "status": "approved", "reviewed_head": A},
            "b": {"id": "b", "status": "approved", "reviewed_head": B},
            "integration": {"id": "integration", "status": "approved", "reviewed_head": HEAD},
        },
        "landed_slices": [{"units": ["a"], "pull_request": SLICE_PR, "at": "t"}],
        **more,
    }


def _ports(ancestors: set[str], **more):
    ports = memory_ports(tracker=MemoryTracker())
    ports.workspace.upstream_answer = Upstream("origin/master", MASTER, None)
    ports.workspace.ancestors = ancestors
    _ready(ports, 5, checks=False, **_landed(**more))
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    return ports


def _gather(ports, tmp_path, reruns=None):
    lines: list[str] = []
    found = poll.gather_merges(
        ports, frozenset(), MASTER, _Locks(), lines, cfg=_cfg(tmp_path), reruns=reruns
    )
    return found, lines


def _reads(ports) -> int:
    return ports.tracker.events.count("pull_request")


def test_every_unit_an_ancestor_closes_the_task_without_a_delivery_outcome(tmp_path):
    ports = _ports({A, B, HEAD})
    found, lines = _gather(ports, tmp_path)
    assert found == []
    assert lines == ["agentic: poll: #5 -> closed: every unit landed through slices (#350)"]
    closure = ports.documents.closures["GH-5"]
    assert set(closure) == {"closed_at", "closed_by_slices"}
    assert closure["closed_by_slices"] == [SLICE_PR]
    assert "delivery_outcome" not in closure


def test_the_close_reads_the_pull_request_once_and_not_again_next_round(tmp_path):
    ports = _ports({A, B})
    _gather(ports, tmp_path)
    assert _reads(ports) == 1
    assert _gather(ports, tmp_path) == ([], [])  # the document is closed now: skipped


def test_a_closed_pull_request_without_landed_slices_stays_silent_and_reads_no_git(tmp_path):
    ports = _ports({A, B})
    ports.documents.save({**ports.documents.load("GH-5"), "landed_slices": []})
    assert _gather(ports, tmp_path) == ([], [])
    assert ports.workspace.ancestor_checks == []
    assert "GH-5" not in ports.documents.closures


def test_an_unread_ancestry_neither_closes_nor_asks_for_a_run(tmp_path):
    ports = _ports({A})
    ports.workspace.ancestor_errors = {B}
    reruns: list[int] = []
    assert _gather(ports, tmp_path, reruns) == ([], [])
    assert reruns == []
    assert "GH-5" not in ports.documents.closures


def test_a_unit_that_did_not_land_asks_for_a_fresh_run_once(tmp_path):
    ports = _ports({A})
    reruns: list[int] = []
    found, lines = _gather(ports, tmp_path, reruns)
    assert (found, reruns) == ([], [5])
    assert lines == ["agentic: poll: #5 -> fresh run: slices left b"]
    assert _reads(ports) == 1
    assert "GH-5" not in ports.documents.closures


def test_an_escalated_task_gets_no_fresh_run(tmp_path):
    ports = _ports({A}, escalated="Landing failed")
    reruns: list[int] = []
    assert _gather(ports, tmp_path, reruns) == ([], [])
    assert reruns == []


def test_a_round_spawns_one_fresh_run_and_the_next_round_none(tmp_path):
    ports = _ports({A})
    launcher = FakeLauncher()
    registry = MemoryRunRegistry()
    cfg = _cfg(tmp_path)
    first = _round(cfg, ports, registry, launcher, ESCALATED_AT)
    assert launcher.spawned == [
        ["agentic", "run", "--issue", "5", "--fresh", "--detach", "--product-dir", str(tmp_path)]
    ]
    assert "agentic: poll: #5 -> fresh run: slices left b" in first
    again = _round(cfg, ports, registry, launcher, ESCALATED_AT)
    assert len(launcher.spawned) == 1
    assert again.count("agentic: poll: #5 -> fresh run: slices left b") == 1


def test_a_fresh_run_waits_for_a_slot(tmp_path):
    ports = _ports({A})
    live = [RunRegistration(1, "h", "other", "GH-1")]
    launcher = FakeLauncher()
    _round(
        _cfg(tmp_path, max_concurrent_runs=1),
        ports,
        MemoryRunRegistry(entries=live),
        launcher,
        ESCALATED_AT,
    )
    assert launcher.spawned == []


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def test_with_a_real_repo_the_renamed_unit_branches_still_resolve_and_the_task_closes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "f").write_text("0")
    _git(repo, "add", "f")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "agentic/GH-5.a")
    (repo / "a").write_text("a")
    _git(repo, "add", "a")
    _git(repo, "commit", "-q", "-m", "unit a")
    unit_a = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "agentic/GH-5.b", "master")
    (repo / "b").write_text("b")
    _git(repo, "add", "b")
    _git(repo, "commit", "-q", "-m", "unit b")
    unit_b = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "master")
    _git(repo, "merge", "-q", "--no-ff", "-m", "slice", unit_a)
    _git(repo, "merge", "-q", "--no-ff", "-m", "slice", unit_b)
    master = _git(repo, "rev-parse", "HEAD")
    # The archive renames the branches and deletes no object.
    _git(repo, "branch", "-m", "agentic/GH-5.a", "archive/run1-x/agentic/GH-5.a")
    _git(repo, "branch", "-m", "agentic/GH-5.b", "archive/run1-x/agentic/GH-5.b")
    ports = memory_ports(
        tracker=MemoryTracker(), workspace=GitWorkspace(repo, {"name": "t", "email": "t@t"})
    )
    ports.documents.save(
        {
            "id": "GH-5",
            "status": "ready_for_review",
            "pull_request": URL.format(5),
            "subtasks": {
                "a": {"id": "a", "status": "approved", "reviewed_head": unit_a},
                "b": {"id": "b", "status": "approved", "reviewed_head": unit_b},
            },
            "landed_slices": [{"units": ["a", "b"], "pull_request": SLICE_PR, "at": "t"}],
        }
    )
    ports.tracker.prs[URL.format(5)] = PullRequest(
        state="CLOSED", created_at="2026-10-05T00:00:00Z", merged_at=None, commits=(), head_oid=HEAD
    )
    lines: list[str] = []
    poll.gather_merges(ports, frozenset(), master, _Locks(), lines, cfg=_cfg(repo), reruns=[])
    assert lines == ["agentic: poll: #5 -> closed: every unit landed through slices (#350)"]
    assert ports.documents.closures["GH-5"]["closed_by_slices"] == [SLICE_PR]


CHECKLIST = ["close #12 as duplicate", "retitle #13"]


def _with_checklist(ports, **more):
    ports.documents.save({**ports.documents.load("GH-5"), "tracker_checklist": CHECKLIST, **more})


def _checklist_comments(ports) -> list[str]:
    return [text for _, text in ports.tracker.comments if text.startswith("Tracker work for you: ")]


def test_a_checklist_is_posted_once_over_two_ticks_and_no_run_is_launched(tmp_path):
    ports = _ports({A, B})
    _with_checklist(ports)
    reruns: list[int] = []
    _, lines = _gather(ports, tmp_path, reruns)
    assert _gather(ports, tmp_path, reruns) == ([], [])  # closed: skipped
    assert lines == [
        "agentic: poll: #5 -> closed: every unit landed through slices (#350);"
        " tracker checklist posted"
    ]
    assert len(_checklist_comments(ports)) == 1
    assert "- [ ] close #12 as duplicate" in _checklist_comments(ports)[0]
    assert reruns == []
    closure = ports.documents.closures["GH-5"]
    assert set(closure) == {"closed_at", "closed_by_slices", "tracker_checklist_posted"}
    assert "delivery_outcome" not in closure
    assert set(closure["tracker_checklist_posted"]) == {"at", "hash"}


def test_a_comment_that_fails_closes_nothing_and_the_next_tick_posts_it_once(tmp_path):
    ports = _ports({A, B})
    _with_checklist(ports)
    ports.tracker.comment_failures = 1
    _, lines = _gather(ports, tmp_path)
    assert lines == ["agentic: poll: #5 -> checklist not posted: gh issue failed: scripted"]
    assert "GH-5" not in ports.documents.closures
    _, again = _gather(ports, tmp_path)
    assert again[0].endswith("tracker checklist posted")
    assert len(_checklist_comments(ports)) == 1
    assert "closed_at" in ports.documents.closures["GH-5"]


def test_a_closure_that_fails_after_the_comment_posts_no_second_comment(tmp_path):
    ports = _ports({A, B})
    _with_checklist(ports)
    record = ports.documents.record_closure
    failing = [True]

    def flaky(task_id, values):
        if failing.pop() if failing else False:
            raise OSError("disk full")
        return record(task_id, values)

    ports.documents.record_closure = flaky
    _, lines = _gather(ports, tmp_path)
    assert lines == ["agentic: poll: #5 -> not closed: disk full"]
    assert len(_checklist_comments(ports)) == 1
    _gather(ports, tmp_path)
    assert len(_checklist_comments(ports)) == 1
    assert "closed_at" in ports.documents.closures["GH-5"]


def test_a_recorded_hash_reads_nothing_from_github(tmp_path):
    ports = _ports({A, B})
    posted = tracker_only.post_tracker_checklist(ports, "GH-5", CHECKLIST, None)
    reads: list[str] = []
    read = ports.tracker.issue_comments
    ports.tracker.issue_comments = lambda task_id: reads.append(task_id) or read(task_id)
    _with_checklist(ports, tracker_checklist_posted=posted)
    _, lines = _gather(ports, tmp_path)
    assert reads == []
    assert len(_checklist_comments(ports)) == 1
    assert lines == ["agentic: poll: #5 -> closed: every unit landed through slices (#350)"]


def test_a_checklist_does_not_hide_the_slices_left_from_the_fresh_run(tmp_path):
    ports = _ports({A})
    _with_checklist(ports)
    reruns: list[int] = []
    _gather(ports, tmp_path, reruns)
    assert reruns == [5]
    assert _checklist_comments(ports) == []
