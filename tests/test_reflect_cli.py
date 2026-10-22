"""`agentic reflect` end to end over a real git repository (ADR-0043): the cap per run, the
first run that only starts the clock, a candidate PR closed unmerged that is never asked
twice and never re-proposed, and the proof that the default branch and the closed task
documents are left alone. gh and the runner are fakes; git, the files and the lock are real.

Seam: `agentic.cli reflect (agentic reflect verb)`, `_reflect` over real ports."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from agentic import cli
from agentic.core.process.adapters.filesystem.documents import FilesystemTaskDocuments
from agentic.core.process.adapters.filesystem.prompts import FilesystemPrompts
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.application import reflect_run
from agentic.core.process.application.ports import ListedPullRequest, Ports
from agentic.core.process.domain import playbook_candidates as pc
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_reflect import reflection
from tests.fakes import MemoryTracker, ScriptedRunner, memory_ports

pytestmark = pytest.mark.anyio

LESSON = "Run every declared check before delivering, not only the single test file."
CLOSED = "2026-10-0{day}T09:00:00.000+00:00"


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=_ENV
    )
    return result.stdout.strip()


_ENV = {
    "PATH": __import__("os").environ["PATH"],
    "HOME": __import__("os").environ["HOME"],
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def add_delta(task: str) -> dict:
    return {
        "op": "add",
        "slug": "developer",
        "category": "standard-other",
        "lesson": LESSON,
        "item_id": "",
        "tasks": [task],
        "confidence": "high",
    }


def product(tmp_path: Path, tasks: int) -> tuple[Path, Configuration, Ports, MemoryTracker]:
    root = tmp_path / "product"
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", str(remote))
    cfg = Configuration(product_dir=root)
    documents = FilesystemTaskDocuments(root)
    for n in range(1, tasks + 1):
        documents.save(
            {
                "id": f"GH-{n}",
                "title": "A task",
                "description": "the request",
                "type": "feature",
                "status": "ready_for_review",
                "change_history": [
                    {
                        "pair": "Tech Lead→Developer:parser",
                        "category": "standard-other",
                        "reason": "left a check unrun",
                    }
                ],
                "sections": {"Tech Lead: review parser": "### Verdict\n\nthe review"},
            }
        )
        documents.record_closure(
            f"GH-{n}", {"closed_at": CLOSED.format(day=n), "delivery_outcome": "merged"}
        )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "closed tasks")
    tracker = MemoryTracker()
    ports = memory_ports(
        ScriptedRunner(reflector=[reflection(add_delta(f"GH-{n}"), cost=0.3) for n in range(1, 9)]),
        documents=documents,
        prompts=FilesystemPrompts(root),
        workspace=GitWorkspace(root, cfg.git_identity),
        tracker=tracker,
    )
    return root, cfg, ports, tracker


def listed(tracker: MemoryTracker, state: str, branch: str = "agentic/playbook") -> None:
    branch_name, _, body = tracker.pull_requests[-1]
    tracker.listed = [ListedPullRequest(7, tracker.url, state, body, "", branch_name)]


async def test_the_cap_reflects_five_of_eight_and_leaves_the_default_branch_and_documents_alone(
    tmp_path, capsys
):
    root, cfg, ports, tracker = product(tmp_path, 8)
    before = git(root, "rev-parse", "HEAD")
    await cli._reflect(cfg, ports, None, "2026-10-01")
    assert "reflected 5, 3 left for the next run" in capsys.readouterr().out
    assert git(root, "rev-parse", "HEAD") == before
    assert git(root, "status", "--porcelain", "--", ".agentic/tasks") == ""
    ledger = pc.parse_ledger(git(root, "show", f"agentic/playbook:{pc.LEDGER_FILE}"))
    assert list(ledger.tasks) == [f"GH-{n}@review" for n in range(1, 6)]
    [(branch, _, body)] = tracker.pull_requests
    assert branch == "agentic/playbook"
    assert pc.parse_block(body) is not None
    assert "must be read" in body or "Read each one" in body
    assert tracker.labels == [] and tracker.comments == []


async def test_the_first_run_writes_started_at_and_reflects_nothing(tmp_path, capsys):
    root, cfg, ports, tracker = product(tmp_path, 3)
    await cli._reflect(cfg, ports, None, None)
    assert "reflected 0, 0 left for the next run" in capsys.readouterr().out
    ledger = pc.parse_ledger(git(root, "show", f"agentic/playbook:{pc.LEDGER_FILE}"))
    assert ledger.started_at and not ledger.tasks
    assert ports.runner.calls == []
    assert len(tracker.pull_requests) == 1


async def test_a_pr_closed_unmerged_is_never_asked_twice_and_its_lesson_is_refused(
    tmp_path, capsys
):
    root, cfg, ports, tracker = product(tmp_path, 8)
    await cli._reflect(cfg, ports, None, "2026-10-01")
    calls = len(ports.runner.calls)
    assert calls == 5
    listed(tracker, "CLOSED")  # the person closed the candidate without merging
    await cli._reflect(cfg, ports, None, "2026-10-01")
    assert "reflected 3, 0 left for the next run" in capsys.readouterr().out.splitlines()[-2]
    assert len(ports.runner.calls) == calls + 3  # GH-6..8 only
    ledger = pc.parse_ledger(git(root, "show", f"agentic/playbook:{pc.LEDGER_FILE}"))
    assert set(ledger.tasks) == {f"GH-{n}@review" for n in range(1, 9)}
    # The lesson the closed PR proposed is refused for good: no playbook item, one record.
    rejected = pc.parse_rejected(git(root, "show", f"agentic/playbook:{pc.REJECTED_FILE}"))
    assert [r.slug for r in rejected] == ["developer"]
    shown = subprocess.run(
        ["git", "show", f"agentic/playbook:{pc.playbook_file('developer')}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert shown.returncode != 0 or "items: []" in shown.stdout
    assert git(root, "branch", "--list", "archive/playbook-*")
    assert len(tracker.pull_requests) == 2  # a fresh candidate, never an edit of the old
    assert not reflect_run.pending(cfg, ports)


async def test_an_auto_merged_task_is_neither_reflected_nor_counted_as_kept(tmp_path, capsys):
    """ADR-0045: an auto-merged delivery landed without a human verdict, so there is
    nothing a person kept to learn from; `pc.KEPT` holds only `human_kept` outcomes."""
    root, cfg, ports, _tracker = product(tmp_path, 2)
    ports.documents.record_closure(
        "GH-2", {"closed_at": CLOSED.format(day=2), "delivery_outcome": "auto_merged"}
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "GH-2 auto-merged")
    assert "auto_merged" not in pc.KEPT
    assert [d["id"] for d in reflect_run.pending(cfg, ports, since="2026-10-01")] == ["GH-1"]
    await cli._reflect(cfg, ports, None, "2026-10-01")
    assert "reflected 1, 0 left for the next run" in capsys.readouterr().out
    ledger = pc.parse_ledger(git(root, "show", f"agentic/playbook:{pc.LEDGER_FILE}"))
    assert list(ledger.tasks) == ["GH-1@review"]


async def test_a_replaced_candidate_branch_is_pushed_on_a_lease_at_the_tip_remote_tip_read(
    tmp_path,
):
    """The playbook branch is the system's own: the replace asks `remote_tip` and leases
    on that sha (the first push, with no remote branch, is plain)."""
    root, cfg, ports, tracker = product(tmp_path, 8)
    leases: list[str | None] = []
    tips: list[str | None] = []

    class Spy(GitWorkspace):
        def push(self, branch, *, lease=None):
            leases.append(lease)
            super().push(branch, lease=lease)

        def remote_tip(self, path, branch):
            tip = super().remote_tip(path, branch)
            tips.append(tip)
            return tip

    ports = dataclasses.replace(ports, workspace=Spy(root, cfg.git_identity))
    await cli._reflect(cfg, ports, None, "2026-10-01")
    assert leases == [None]
    first = git(root, "rev-parse", "agentic/playbook")
    listed(tracker, "CLOSED")
    await cli._reflect(cfg, ports, None, "2026-10-01")
    assert tips == [first] and leases == [None, first]
