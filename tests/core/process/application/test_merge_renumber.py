"""GH-229, ADR-0048: `agentic merge` renumbers the ADR a branch added when upstream master
took its number first. Real git throughout -- a bare upstream, the product clone and the
task branches under `tmp_path` -- with the `Tracker` faked as in `test_merge_for_real`: a
fake workspace cannot show that two landed ADRs never share a number, or that a reference
still points at a file that exists. The `Workspace` is the real `GitWorkspace`; the
no-ADR case wraps it to count the calls it makes."""

from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path

import anyio
import pytest

from agentic import cli
from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.adapters.shell.commands import ShellCommands
from agentic.core.process.application.merge import merge_task
from agentic.core.process.domain import adr_numbers
from agentic.core.process.domain.task import PullRequest, PullRequestCommit
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_merge_for_real import MergingTracker, git
from tests.fakes import memory_ports

SLUG_A = "alpha-decision"
SLUG_B = "beta-decision"
SLUG_C = "gamma-decision"


def url(task: str) -> str:
    return f"https://github.com/o/r/pull/{task.split('-')[1]}"


class Repo:
    """A bare upstream with `docs/adr/0047-base.md`, the product clone, and a way to cut
    task branches from the same base, land one of them, and run the verb on another."""

    def __init__(self, tmp_path: Path):
        self.origin = tmp_path / "origin.git"
        self.product = tmp_path / "product"
        git("init", "-q", "--bare", "-b", "master", str(self.origin), cwd=tmp_path)
        git("clone", "-q", str(self.origin), str(self.product), cwd=tmp_path)
        (self.product / ".agentic").mkdir()
        (self.product / ".agentic/stack.yaml").write_text("product: demo\nchecks:\n  tests: 'true'\n")
        (self.product / "docs/adr").mkdir(parents=True)
        (self.product / "docs/adr/0047-base.md").write_text("# ADR-0047: base\n")
        git("add", "-A", cwd=self.product)
        git("commit", "-q", "-m", "v0", cwd=self.product)
        git("push", "-q", "-u", "origin", "HEAD:master", cwd=self.product)
        git("branch", "-q", "--set-upstream-to=origin/master", cwd=self.product)
        self.cfg = Configuration(product_dir=self.product, run_registry_dir=tmp_path / "registry")
        self.tracker = MergingTracker()
        self.ports = dataclasses.replace(
            memory_ports(),
            workspace=GitWorkspace(self.product, self.cfg.git_identity),
            stack=GitStack(self.product),
            commands=ShellCommands(),
            documents=FilesystemTaskDocuments(self.product),
            tracker=self.tracker,
        )
        self.heads: dict[str, str] = {}

    def branch(self, task: str, files: dict[str, str]) -> str:
        """The task branch cut from upstream master as it is now, one commit adding
        `files`, pushed to origin the way `deliver` does."""
        checkout = self.ports.workspace.task_checkout(task)
        for relative, text in files.items():
            (checkout.path / relative).parent.mkdir(parents=True, exist_ok=True)
            (checkout.path / relative).write_text(text)
        git("add", "-A", cwd=checkout.path)
        git("commit", "-q", "-m", f"work of {task}", cwd=checkout.path)
        head = git("rev-parse", "HEAD", cwd=checkout.path)
        git("push", "-q", "origin", f"agentic/{task}", cwd=checkout.path)
        self.ports.workspace.discard(checkout.path)
        self.heads[task] = head
        return head

    def land(self, task: str) -> str:
        """What the merge of the pull request does to upstream: master becomes the head."""
        git("push", "-q", "origin", f"{self.heads[task]}:refs/heads/master", cwd=self.product)
        return self.heads[task]

    def deliver(self, task: str, **more: object) -> None:
        head = self.heads[task]
        self.tracker.prs[url(task)] = PullRequest(
            state="OPEN",
            created_at="2026-10-05T00:00:00Z",
            merged_at=None,
            commits=(PullRequestCommit((self.cfg.git_author_email,), "work", 1, 0, 1),),
            head_oid=head,
        )
        self.ports.documents.save(
            {
                "id": task,
                "title": "t",
                "description": "d",
                "status": "ready_for_review",
                "pull_request": url(task),
                "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": head}},
                **more,
            }
        )

    def merge(self, task: str) -> None:
        anyio.run(cli._merge, task, int(task.split("-")[1]), self.cfg, self.ports)

    def verb(self, task: str):
        lines: list[str] = []
        result = merge_task(
            task,
            int(task.split("-")[1]),
            self.cfg,
            self.ports,
            self.ports.workspace.upstream(),
            frozenset(),
            lines.append,
        )
        return result, lines

    def doc(self, task: str) -> dict:
        return self.ports.documents.load(task) or {}

    def remote(self, task: str) -> str:
        return git("rev-parse", f"refs/heads/agentic/{task}", cwd=self.origin)

    def landed(self, task: str) -> dict[str, str]:
        """The tree master would hold once `task`'s pull request lands on today's master:
        the task branch merged into a scratch clone of upstream."""
        scratch = self.product.parent / f"scratch-{task}"
        git("clone", "-q", str(self.origin), str(scratch), cwd=self.product.parent)
        git("merge", "-q", "--no-edit", f"origin/agentic/{task}", cwd=scratch)
        return {
            str(p.relative_to(scratch)): p.read_text()
            for p in scratch.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(scratch).parts
        }

    def tree(self, revision: str) -> dict[str, str]:
        names = git("ls-tree", "-r", "--name-only", revision, cwd=self.origin).splitlines()
        return {
            n: subprocess.run(
                ["git", "show", f"{revision}:{n}"],
                cwd=self.origin,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            for n in names
        }


def adr(number: int, slug: str, extra: str = "") -> str:
    return f"# ADR-{number:04d}: {slug}\n\nAbout {slug}.\n{extra}"


def branch_files(number: int, slug: str, who: str) -> dict[str, str]:
    cite = f"See ADR-{number:04d} ({number:04d}-{slug}.md) for why.\n"
    return {
        f"docs/adr/{number:04d}-{slug}.md": adr(number, slug),
        f"docs/notes-{who}.md": f"# Notes {who}\n\n{cite}Untouched line mentioning nothing.\n",
        f"tests/fixtures/{who}.md": f"fixture cites ADR-{number:04d} on purpose\n",
    }


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def resolve(tree: dict[str, str]) -> list[str]:
    """The `ADR-NNNN` and `NNNN-<slug>.md` references outside `tests/` that name no file
    in `docs/adr`, as `path: reference`."""
    numbers = {adr_numbers.adr_number(p) for p in tree if adr_numbers.adr_number(p)}
    files = {Path(p).name for p in tree if adr_numbers.adr_number(p)}
    dangling = []
    for path, text in tree.items():
        if "tests/" in path:
            continue
        for number in re.findall(r"ADR-(\d{4})", text):
            if int(number) not in numbers:
                dangling.append(f"{path}: ADR-{number}")
        for name in re.findall(r"\b(\d{4}-[a-z-]+\.md)", text):
            if name not in files:
                dangling.append(f"{path}: {name}")
    return dangling


def two_branches_one_landed(repo: Repo) -> None:
    repo.branch("GH-1", branch_files(48, SLUG_A, "a"))
    repo.branch("GH-2", branch_files(48, SLUG_B, "b"))
    repo.land("GH-1")
    repo.deliver("GH-2")


def test_two_tasks_adding_the_same_next_adr_end_with_unique_numbers_and_live_references(repo):
    two_branches_one_landed(repo)
    old_head = repo.heads["GH-2"]

    result, lines = repo.verb("GH-2")

    assert (result.kind, result.reason) == ("stopped", "renumbered")
    new_head = repo.remote("GH-2")
    assert new_head != old_head
    tree = repo.landed("GH-2")
    assert sorted(p for p in tree if p.startswith("docs/adr/")) == [
        "docs/adr/0047-base.md",
        f"docs/adr/0048-{SLUG_A}.md",
        f"docs/adr/0049-{SLUG_B}.md",
    ]
    assert tree[f"docs/adr/0049-{SLUG_B}.md"] == adr(49, SLUG_B)
    assert f"See ADR-0049 (0049-{SLUG_B}.md) for why." in tree["docs/notes-b.md"]
    assert "Untouched line mentioning nothing." in tree["docs/notes-b.md"]
    # The landed ADR and what it cites are what master holds, byte for byte.
    landed = repo.tree(repo.heads["GH-1"])
    assert all(tree[p] == landed[p] for p in landed if p.endswith(("alpha-decision.md", "notes-a.md")))
    assert tree["tests/fixtures/b.md"] == "fixture cites ADR-0048 on purpose\n"
    assert tree["tests/fixtures/a.md"] == landed["tests/fixtures/a.md"]
    assert resolve(tree) == []
    assert repo.doc("GH-2")["adr_renumber"] == [
        {
            "from_head": old_head,
            "to_head": new_head,
            "map": {f"docs/adr/0048-{SLUG_B}.md": f"docs/adr/0049-{SLUG_B}.md"},
            "unrewritten": ["tests/fixtures/b.md:1"],
        }
    ]
    assert repo.tracker.comments == [] and repo.tracker.merge_calls == []
    assert any("renumber" in line for line in lines)
    assert lines and all(line.startswith("agentic: merge: GH-2") for line in lines[:1])
    assert not (repo.product / ".agentic/worktrees/merge-GH-2").exists()
