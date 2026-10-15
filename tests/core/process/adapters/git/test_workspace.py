"""The git adapter against a real temporary repository with a bare remote."""

import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.knowledge.adapters.git.stack import GitStack
from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git import workspace as workspace_module
from agentic.core.process.adapters.git.workspace import GitError
from agentic.core.process.domain import rules
from agentic.shared.configuration import Configuration

# The identity as the composition root hands it in.
IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def product(tmp_path):
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "README.md").write_text("v0\n")
    git("add", "-A", cwd=repo)
    # The product's first commit needs an identity; the adapter's commits must not.
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v0", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    return repo


def test_task_checkout_is_a_worktree_on_its_own_branch_and_idempotent(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    assert task.branch == "agentic/GH-12"
    assert task.path == product / ".agentic/worktrees/GH-12"
    assert (task.path / "README.md").read_text() == "v0\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=task.path) == "agentic/GH-12"
    assert task.base == git("rev-parse", "--short", "HEAD", cwd=product)
    # Only the call that made the worktree says so: the product's setup runs once.
    assert task.created
    again = ws.task_checkout("GH-12")
    assert not again.created and replace(again, created=True) == task
    # The product's own checkout is untouched, and the worktrees never show up in it.
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=product) == "master"
    assert git("status", "--porcelain", cwd=product) == ""


def test_subtask_branches_off_the_task_branch_as_it_is_now(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    (task.path / "CONTEXT.md").write_text("glossary\n")
    sha = ws.commit_all(task.path, "GH-12: glossary")
    assert sha == ws.head(task.path) and sha != task.base
    # Signed by the system, on a repo with no `user.*` configured at all, and the
    # identity beats whatever `user.*` the product's config carries.
    git("config", "user.name", "human", cwd=product)
    git("config", "user.email", "human@example.com", cwd=product)
    (task.path / "ADR.md").write_text("adr\n")
    sha = ws.commit_all(task.path, "GH-12: adr")
    author = git("log", "-1", "--format=%an <%ae>", cwd=task.path)
    assert author == "agentic-system <agentic-system@users.noreply.github.com>"
    assert git("log", "-1", "--format=%cn <%ce>", cwd=task.path) == author
    assert ws.commit_all(task.path, "nothing") is None

    sub = ws.subtask_checkout("GH-12", "api")
    assert sub.branch == "agentic/GH-12.api"
    assert sub.path == product / ".agentic/worktrees/GH-12.api"
    assert (sub.path / "CONTEXT.md").read_text() == "glossary\n"  # from the task branch
    assert sub.base == sha
    # A later commit on the subtask moves its head; the base stays what the review diffs from.
    (sub.path / "api.py").write_text("x\n")
    ws.commit_all(sub.path, "api")
    assert ws.subtask_checkout("GH-12", "api").base == sha
    assert ws.head(sub.path) != sha

    # A subtask that depends on `api` gets its code by merging the branch; twice is a no-op.
    dep = ws.subtask_checkout("GH-12", "ui")
    ws.merge(dep.path, sub.branch)
    ws.merge(dep.path, sub.branch)
    assert (dep.path / "api.py").read_text() == "x\n"


def test_the_identity_is_the_environment_the_configuration_builds(product):
    identity = {
        "GIT_AUTHOR_NAME": "bot",
        "GIT_AUTHOR_EMAIL": "b@x",
        "GIT_COMMITTER_NAME": "bot",
        "GIT_COMMITTER_EMAIL": "b@x",
    }
    ws = GitWorkspace(product, identity)
    task = ws.task_checkout("GH-12")
    (task.path / "x").write_text("x\n")
    ws.commit_all(task.path, "x")
    assert git("log", "-1", "--format=%an <%ae>", cwd=task.path) == "bot <b@x>"


def test_push_and_cleanup_keep_the_branches(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    ws.subtask_checkout("GH-12", "api")
    ws.subtask_checkout("GH-13", "other")
    ws.push(task.branch)
    assert git("branch", "-r", cwd=product) == "origin/agentic/GH-12"

    ws.cleanup("GH-12")
    root = product / ".agentic/worktrees"
    assert sorted(p.name for p in root.iterdir()) == ["GH-13", "GH-13.other"]
    branches = git("branch", "--format=%(refname:short)", cwd=product).splitlines()
    assert {"agentic/GH-12", "agentic/GH-12.api", "agentic/GH-13"} <= set(branches)
    ws.cleanup("GH-99")  # nothing to do, no error


def test_a_checkout_is_clean_only_without_changes_tracked_or_not(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    assert ws.is_clean(task.path)
    (task.path / "new.py").write_text("x\n")  # untracked counts: a Developer's new file
    assert not ws.is_clean(task.path)
    ws.commit_all(task.path, "GH-12: new")
    assert ws.is_clean(task.path)
    (task.path / "README.md").write_text("v1\n")
    assert not ws.is_clean(task.path)


def test_what_a_resume_checks_against_git(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    api = ws.subtask_checkout("GH-12", "api")
    assert ws.branch_head("agentic/GH-12") == git("rev-parse", "--short", "HEAD", cwd=task.path)
    assert ws.branch_head("agentic/GH-99") is None
    assert ws.base_of(api.branch) == api.base and ws.base_of("master") is None
    assert ws.worktrees("GH-12") == [task.path, api.path]
    # A worktree removed by hand is forgotten by `prune`, and asking again recreates it.
    subprocess.run(["rm", "-rf", str(api.path)], check=True)
    ws.prune()
    assert ws.worktrees("GH-12") == [task.path]
    assert ws.subtask_checkout("GH-12", "api") == api and api.path.is_dir()


def test_the_checkpointer_file_stays_out_of_the_products_status(product, tmp_path):
    ws = GitWorkspace(product, IDENTITY)
    ws.exclude_system_files()
    ws.exclude_system_files()  # once each
    exclude = (product / ".git/info/exclude").read_text()
    assert ".agentic/logs/" in exclude  # every invocation's log, never the product's own gitignore
    (product / ".agentic").mkdir()
    (product / ".agentic/state.sqlite").write_text("x")
    (product / ".agentic/state.sqlite-journal").write_text("x")
    (product / ".agentic/state").mkdir()
    (product / ".agentic/state/GH-1.sqlite").write_text("x")
    assert git("status", "--porcelain", cwd=product) == ""
    exclude = (product / ".git/info/exclude").read_text().splitlines()
    assert exclude.count(".agentic/state.sqlite*") == 1
    assert exclude.count(".agentic/state/") == 1
    (tmp_path / "not-a-repo").mkdir()
    GitWorkspace(tmp_path / "not-a-repo", IDENTITY).exclude_system_files()  # nothing to do


def test_a_merge_that_applies_returns_nothing_and_one_that_conflicts_says_why(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    api = ws.subtask_checkout("GH-12", "api")
    domain = ws.subtask_checkout("GH-12", "domain")

    # `api` touches a file of its own: the task branch takes it without a word.
    (api.path / "api.py").write_text("get\n")
    ws.commit_all(api.path, "api")
    assert ws.merge(task.path, api.branch) is None
    assert (task.path / "api.py").read_text() == "get\n"
    assert ws.merge(task.path, api.branch) is None  # again is a no-op

    # `domain` edits the same line of the same file, from the base the task branch
    # no longer has: a conflict is a fact about the code, not a crash.
    (domain.path / "api.py").write_text("post\n")
    ws.commit_all(domain.path, "domain")
    reason = ws.merge(task.path, domain.branch)
    assert reason is not None and "api.py" in reason
    # Undone, so the checkout is one a role can work in and nothing half-merged ships.
    assert ws.is_clean(task.path)
    assert (task.path / "api.py").read_text() == "get\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=task.path) == "agentic/GH-12"


# -- the repository ------------------------------------------------------------------


def test_a_worktree_answers_the_repository_it_was_made_from(product):
    """Two directories, one repository: comparing paths would call them different, and
    running the system over a worktree of its own repo is the same mistake as running it
    over the repo (`preflight`; ADR-0017)."""
    ws = GitWorkspace(product, IDENTITY)
    worktree = ws.task_checkout("GH-44").path
    assert worktree != product
    assert ws.repository_at(worktree) == ws.repository_at(product) == (product / ".git").resolve()


def test_a_relative_path_is_read_from_here_not_from_the_product(product, monkeypatch):
    monkeypatch.chdir(product.parent)
    ws = GitWorkspace(product, IDENTITY)
    assert ws.repository_at(Path(product.name)) == (product / ".git").resolve()


def test_outside_a_repository_there_is_none(product, tmp_path):
    ws = GitWorkspace(product, IDENTITY)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ws.repository_at(plain) is None
    assert ws.repository_at(tmp_path / "not-there") is None


def test_without_git_there_is_no_repository_and_no_crash(product, monkeypatch):
    """`default_ports` asks this on every path now, so a machine without git, or a
    `product_dir` that is not there, has to answer "no repository" and not explode
    inside the composition root."""
    ws = GitWorkspace(product, IDENTITY)
    monkeypatch.setattr(ws, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
    assert ws.repository_at(product) is None


def test_a_product_directory_that_is_not_there_is_no_repository(tmp_path):
    ws = GitWorkspace(tmp_path / "gone", IDENTITY)
    assert ws.repository_at(tmp_path / "also-gone") is None


# -- dependency merges (issue: gym #16, adjacent block deletions) -------------------


def test_two_branches_deleting_adjacent_blocks_of_one_file_conflict_and_the_developer_settles_it(
    product,
):
    """The behavioural case from the request: two branches each delete their own block
    of the same file, the blocks adjacent, so git cannot fold two deletions next to
    each other on its own. `merge_keeping_conflicts` leaves the worktree conflicted
    instead of aborting, names exactly the file, and a scripted resolution (the same
    hunk a human deleted by hand in gym #16) is what `dependency_state` then reports
    clean, with the dependency contained, nothing in progress, and no marker left."""
    ws = GitWorkspace(product, IDENTITY)
    base = ws.task_checkout("GH-16")
    lines = [f'[[tool.mypy.overrides]]\nmodule = "pkg.mod{n}"\n\n' for n in range(3)]
    (base.path / "pyproject.toml").write_text("".join(lines))
    ws.commit_all(base.path, "base: three override blocks")

    # Both subtasks branch off the same base: each deletes its own block, the two
    # adjacent, exactly gym #16's own shape.
    first = ws.subtask_checkout("GH-16", "column-expression-spelling")
    (first.path / "pyproject.toml").write_text("".join([lines[0], lines[2]]))  # drops block 1
    ws.commit_all(first.path, "drop mod1's override")

    second = ws.subtask_checkout("GH-16", "routers-and-models")
    (second.path / "pyproject.toml").write_text("".join([lines[0], lines[1]]))  # drops block 2
    ws.commit_all(second.path, "drop mod2's override")

    outcome = ws.merge_keeping_conflicts(second.path, first.branch)
    assert outcome.reason is not None and outcome.conflicted_paths == ("pyproject.toml",)
    # Left conflicted, not aborted: the file still carries the markers for a role to
    # resolve, unlike `Workspace.merge`, which would have undone it by now.
    assert rules.has_conflict_markers((second.path / "pyproject.toml").read_text())
    assert not ws.is_clean(second.path)

    mid = ws.dependency_state(second.path, first.branch, ["pyproject.toml"])
    assert not mid.contained and mid.merge_in_progress and mid.marked_paths == ("pyproject.toml",)

    # The human's own resolution in gym #16: the union of both deletions, nothing else.
    (second.path / "pyproject.toml").write_text(lines[0])
    git("add", "pyproject.toml", cwd=second.path)
    # A plain git call, not the adapter's own commit: needs its own identity, the same
    # way the fixture's first commit already does.
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--no-edit", "-q", cwd=second.path)

    settled = ws.dependency_state(second.path, first.branch, ["pyproject.toml"])
    assert settled.contained and not settled.merge_in_progress and settled.marked_paths == ()


def test_a_marker_left_after_staging_and_committing_still_reads_as_conflicted(product):
    """Its twin, where the Developer cannot settle it: `git add` and a commit clear
    git's own conflict bookkeeping, but the file's content still carries the marker —
    the request's own closing criterion, "staging or committing a marker-laden file
    does not count as resolution" — so `dependency_state` still reports it, and the
    verdict over it (`rules.dependency_delivered`) still reads `unmergeable`."""
    ws = GitWorkspace(product, IDENTITY)
    base = ws.task_checkout("GH-17")
    (base.path / "x.py").write_text("a = 1\n")
    ws.commit_all(base.path, "base")

    left = ws.subtask_checkout("GH-17", "left")
    (left.path / "x.py").write_text("a = 2\n")
    ws.commit_all(left.path, "left changes x")

    right = ws.subtask_checkout("GH-17", "right")
    (right.path / "x.py").write_text("a = 3\n")
    ws.commit_all(right.path, "right changes x too")

    outcome = ws.merge_keeping_conflicts(right.path, left.branch)
    assert outcome.conflicted_paths == ("x.py",)

    # Stage and commit the file exactly as git left it, markers and all: this is the
    # shape the closing criterion names, never a real resolution.
    git("add", "x.py", cwd=right.path)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--no-edit", "-q", cwd=right.path)
    assert ws.is_clean(right.path)  # git's own bookkeeping says nothing is unmerged

    state = ws.dependency_state(right.path, left.branch, ["x.py"])
    assert state.contained and not state.merge_in_progress  # git agrees it landed
    assert state.marked_paths == ("x.py",)  # the file's own content still disagrees
    assert not rules.dependency_delivered(state)


def test_a_refused_merge_with_nothing_conflicted_leaves_no_marked_paths(product):
    """An unknown branch: the merge never starts, so there is nothing for a Developer
    to resolve and `dependency_after_attempt` reads `unmergeable` on it."""
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-18")
    outcome = ws.merge_keeping_conflicts(task.path, "agentic/GH-18.ghost")
    assert outcome.reason is not None
    assert outcome.conflicted_paths == ()


def test_an_untracked_file_leaves_the_tree_committed_but_not_clean(product):
    ws = GitWorkspace(product, IDENTITY)
    assert ws.is_clean(product) and ws.is_committed(product)
    (product / "scratch.log").write_text("noise\n")
    assert not ws.is_clean(product)
    assert ws.is_committed(product)  # what `system_version` asks: no `+dirty`
    (product / "README.md").write_text("edited\n")
    assert not ws.is_committed(product)


# -- archiving (ADR-0011, sixth addendum) --------------------------------------------


def test_branches_is_a_dumb_prefix_verb_with_no_task_semantics(product):
    """`branches("agentic/GH-1")` also answers `agentic/GH-12`: the adapter owes no
    task semantics, `rules.branches_of_task` is what narrows it."""
    ws = GitWorkspace(product, IDENTITY)
    ws.task_checkout("GH-1")
    ws.task_checkout("GH-12")
    ws.subtask_checkout("GH-12", "api")
    assert set(ws.branches("agentic/GH-1")) == {
        "agentic/GH-1",
        "agentic/GH-12",
        "agentic/GH-12.api",
    }
    assert ws.branches("agentic/GH-99") == []


def test_renaming_a_task_and_its_subtask_branches_archives_them_with_their_heads_intact(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    (task.path / "x").write_text("x\n")
    task_head = ws.commit_all(task.path, "GH-12: x")
    api = ws.subtask_checkout("GH-12", "api")
    (api.path / "api.py").write_text("x\n")
    api_head = ws.commit_all(api.path, "api")

    # Archiving order (ADR-0011, sixth addendum): the worktrees go before the rename,
    # since git cannot rename a branch a worktree still has checked out.
    ws.cleanup("GH-12")
    ws.rename_branch("agentic/GH-12", "archive/run1-abc/agentic/GH-12")
    ws.rename_branch("agentic/GH-12.api", "archive/run1-abc/agentic/GH-12.api")

    assert ws.branch_head("agentic/GH-12") is None
    assert ws.branch_head("archive/run1-abc/agentic/GH-12") == task_head
    assert ws.branch_head("archive/run1-abc/agentic/GH-12.api") == api_head

    # A fresh run advances the product's HEAD before the next `task_checkout` (the
    # very case `--fresh` archives for): the reborn branch starts there, with its own
    # `agenticBase` -- never the one the archived branch kept.
    (product / "README.md").write_text("v1\n")
    git("add", "-A", cwd=product)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "v1", cwd=product)
    reborn = ws.task_checkout("GH-12")
    assert reborn.branch == "agentic/GH-12"
    assert reborn.base != task.base
    assert reborn.base == git("rev-parse", "--short", "HEAD", cwd=product)


def test_renaming_a_branch_is_idempotent_when_the_destination_already_exists(product):
    """Destination present, source absent: a retried archive's own no-op, since a
    previous, interrupted archive may already have renamed this branch."""
    ws = GitWorkspace(product, IDENTITY)
    ws.task_checkout("GH-12")
    ws.rename_branch("agentic/GH-12", "archive/run1-abc/agentic/GH-12")
    assert ws.branch_head("agentic/GH-12") is None
    ws.rename_branch("agentic/GH-12", "archive/run1-abc/agentic/GH-12")  # no error, no-op
    assert ws.branch_head("archive/run1-abc/agentic/GH-12") is not None


def test_renaming_a_branch_onto_a_live_destination_raises(product):
    ws = GitWorkspace(product, IDENTITY)
    ws.task_checkout("GH-12")
    ws.task_checkout("GH-13")
    with pytest.raises(GitError):
        ws.rename_branch("agentic/GH-12", "agentic/GH-13")


def test_is_ignored_answers_the_products_own_gitignore(product):
    ws = GitWorkspace(product, IDENTITY)
    (product / ".gitignore").write_text("*.secret\n")
    git("add", "-A", cwd=product)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "gitignore", cwd=product)
    assert ws.is_ignored(product, "x.secret") is True
    assert ws.is_ignored(product, "README.md") is False


# -- ancestry (GH-137: a launch predicate never merges, so it only ever asks) --------


def test_is_ancestor_is_true_once_the_commit_sits_on_head(product):
    ws = GitWorkspace(product, IDENTITY)
    sha = git("rev-parse", "HEAD", cwd=product)
    (product / "README.md").write_text("v1\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-aq", "-m", "v1", cwd=product)
    assert ws.is_ancestor(product, sha) is True


def test_is_ancestor_is_false_for_a_commit_on_a_diverging_branch(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-9")
    (task.path / "feature.md").write_text("only on the task branch\n")
    sha = ws.commit_all(task.path, "GH-9: feature")
    # `product` itself never merged the task branch: its own commit is not an ancestor.
    assert ws.is_ancestor(product, sha) is False


def test_is_ancestor_is_false_and_does_not_raise_for_an_object_this_checkout_never_heard_of(
    product,
):
    """The launch predicate's own 'ante la duda, no lanza': an unknown git object -- a
    merge commit `gh` reported that this checkout has not fetched, say -- answers
    False instead of raising `GitError` out of a bare `merge-base --is-ancestor`."""
    ws = GitWorkspace(product, IDENTITY)
    assert ws.is_ancestor(product, "0" * 40) is False


def test_is_ancestor_checks_against_the_given_ref_not_only_head(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-9")
    (task.path / "feature.md").write_text("on the task branch\n")
    sha = ws.commit_all(task.path, "GH-9: feature")
    # Not an ancestor of the product's own HEAD...
    assert ws.is_ancestor(product, sha) is False
    # ...but it is an ancestor of the branch it actually landed on.
    assert ws.is_ancestor(product, sha, ref=task.branch) is True


def test_is_ancestor_raises_git_error_on_anything_but_the_two_documented_exit_codes(
    product, monkeypatch
):
    """`merge-base --is-ancestor` only ever means 0 or 1; the launch predicate must
    never read a real git failure as "launch anyway"."""
    ws = GitWorkspace(product, IDENTITY)
    sha = git("rev-parse", "HEAD", cwd=product)
    real_run = ws._run

    def fake(*args, cwd=None):
        if args[:2] == ("merge-base", "--is-ancestor"):
            return subprocess.CompletedProcess(args, 128, "", "fatal: not a valid object")
        return real_run(*args, cwd=cwd)

    monkeypatch.setattr(ws, "_run", fake)
    with pytest.raises(GitError, match="merge-base"):
        ws.is_ancestor(product, sha)


# -- the fetched upstream (GH-180): a bare origin and a clone that is never pulled ------


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message, cwd=repo)


class Remote:
    """A bare `origin`, the `clone` a run works in, and a second clone that merges on
    origin behind the first one's back, as a PR merged on GitHub does."""

    def __init__(self, tmp_path: Path):
        self.origin = tmp_path / "origin.git"
        git("init", "--bare", "-q", "-b", "master", str(self.origin), cwd=tmp_path)
        self.other = tmp_path / "other"
        git("clone", "-q", str(self.origin), str(self.other), cwd=tmp_path)
        git("checkout", "-q", "-b", "master", cwd=self.other)
        _commit(self.other, "README.md", "v0\n", "v0")
        _commit(self.other, ".agentic/stack.yaml", "checks:\n  tests: base\n", "stack")
        git("push", "-q", "origin", "master", cwd=self.other)
        self.clone = tmp_path / "clone"
        git("clone", "-q", str(self.origin), str(self.clone), cwd=tmp_path)
        self.n = 0

    def merge_on_origin(self, files: dict[str, str] | None = None) -> str:
        self.n += 1
        git("checkout", "-q", "-b", f"pr{self.n}", cwd=self.other)
        for name, text in (files or {f"pr{self.n}.txt": "x\n"}).items():
            _commit(self.other, name, text, f"pr {self.n}")
        git("checkout", "-q", "master", cwd=self.other)
        git(
            "-c", "user.name=t", "-c", "user.email=t@t",
            "merge", "-q", "--no-ff", "-m", f"Merge pull request #{self.n}", f"pr{self.n}",
            cwd=self.other,
        )  # fmt: skip
        git("push", "-q", "origin", "master", cwd=self.other)
        return git("rev-parse", "HEAD", cwd=self.other)

    def snapshot(self) -> dict[str, object]:
        """HEAD, the local branch, the index and the worktree's files, all of them."""
        relative = [p.relative_to(self.clone) for p in sorted(self.clone.rglob("*")) if p.is_file()]
        files = {
            str(r): (self.clone / r).read_bytes()
            for r in relative
            if r.parts[0] != ".git" and r.parts[:2] != (".agentic", "worktrees")
        }
        return {
            "head": git("rev-parse", "HEAD", cwd=self.clone),
            "master": git("rev-parse", "refs/heads/master", cwd=self.clone),
            "index": git("ls-files", "-s", cwd=self.clone),
            "status": git("status", "--porcelain", cwd=self.clone),
            "files": files,
        }

    def tracking(self) -> str:
        return git("rev-parse", "refs/remotes/origin/master", cwd=self.clone)


@pytest.fixture
def remote(tmp_path):
    return Remote(tmp_path)


def test_a_clone_one_merge_behind_origin_branches_from_the_merge_and_is_left_untouched(remote):
    merge = remote.merge_on_origin()
    before = remote.snapshot()
    task = GitWorkspace(remote.clone, IDENTITY).task_checkout("GH-45")
    assert git("rev-parse", "agentic/GH-45", cwd=remote.clone) == merge
    assert (
        task.base
        == merge[:7]
        == git("config", "--get", "branch.agentic/GH-45.agenticBase", cwd=remote.clone)
    )
    assert (task.path / "pr1.txt").exists()
    assert remote.snapshot() == before
    assert not (remote.clone / "pr1.txt").exists()


def test_a_branch_with_commits_the_upstream_lacks_starts_from_the_upstream_and_names_them(remote):
    merge = remote.merge_on_origin()
    _commit(remote.clone, "local.txt", "l\n", "local only")
    local = git("rev-parse", "--short", "HEAD", cwd=remote.clone)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert (found.ref, found.sha) == ("refs/remotes/origin/master", merge)
    assert found.note is not None and local in found.note


def test_the_left_out_commits_are_capped_at_ten_and_the_rest_counted(remote):
    for i in range(12):
        _commit(remote.clone, f"l{i}.txt", "l\n", f"local {i}")
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.note is not None and "and 2 more" in found.note


def test_with_no_remote_the_base_is_head_as_before(remote, capsys):
    git("remote", "remove", "origin", cwd=remote.clone)
    head = git("rev-parse", "HEAD", cwd=remote.clone)
    ws = GitWorkspace(remote.clone, IDENTITY)
    found = ws.upstream()
    assert (found.ref, found.sha) == ("HEAD", head) and found.note
    ws.task_checkout("GH-45")
    assert git("rev-parse", "agentic/GH-45", cwd=remote.clone) == head
    assert "starting from HEAD" in capsys.readouterr().err


def test_a_detached_head_starts_from_head_with_a_note(remote):
    git("checkout", "-q", "--detach", cwd=remote.clone)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.sha == git("rev-parse", "HEAD", cwd=remote.clone)
    assert found.note and "detached" in found.note


def test_a_branch_with_no_upstream_starts_from_head_with_a_note(remote):
    remote.merge_on_origin()
    git("checkout", "-q", "-b", "feature", cwd=remote.clone)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.sha == git("rev-parse", "HEAD", cwd=remote.clone)
    assert found.note and "no upstream" in found.note


def test_an_upstream_that_is_a_local_branch_starts_from_head_with_a_note(remote):
    git("checkout", "-q", "-b", "feature", "--track", "master", cwd=remote.clone)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.sha == git("rev-parse", "HEAD", cwd=remote.clone)
    assert found.note and "local branch" in found.note


def test_an_existing_task_branch_keeps_its_base_and_never_fetches(remote):
    ws = GitWorkspace(remote.clone, IDENTITY)
    first = ws.task_checkout("GH-45")
    remote.merge_on_origin()
    stale = remote.tracking()
    again = ws.task_checkout("GH-45")
    assert again.base == first.base
    assert remote.tracking() == stale  # no fetch ran
    assert git("rev-parse", "agentic/GH-45", cwd=remote.clone) == stale


def test_a_subtask_checkout_never_fetches(remote):
    ws = GitWorkspace(remote.clone, IDENTITY)
    task = ws.task_checkout("GH-45")
    remote.merge_on_origin()
    stale = remote.tracking()
    sub = ws.subtask_checkout("GH-45", "api")
    assert remote.tracking() == stale
    assert sub.base == task.base


def _break_the_remote(remote: Remote, tmp_path: Path) -> None:
    git("remote", "set-url", "origin", str(tmp_path / "nowhere.git"), cwd=remote.clone)


def test_a_failed_fetch_with_head_ahead_of_the_last_fetched_ref_starts_from_head(remote, tmp_path):
    _commit(remote.clone, "local.txt", "l\n", "local only")
    _break_the_remote(remote, tmp_path)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert (found.ref, found.sha) == ("HEAD", git("rev-parse", "HEAD", cwd=remote.clone))
    assert found.note and "fetch failed" in found.note


def test_a_failed_fetch_with_head_behind_the_last_fetched_ref_uses_that_ref(remote, tmp_path):
    merge = remote.merge_on_origin()
    git("fetch", "-q", cwd=remote.clone)
    _break_the_remote(remote, tmp_path)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.sha == merge
    assert found.note and "fetch failed" in found.note


def test_a_failed_fetch_with_a_diverged_head_uses_the_ref_and_names_the_left_out(remote, tmp_path):
    merge = remote.merge_on_origin()
    git("fetch", "-q", cwd=remote.clone)
    _commit(remote.clone, "local.txt", "l\n", "local only")
    local = git("rev-parse", "--short", "HEAD", cwd=remote.clone)
    _break_the_remote(remote, tmp_path)
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.sha == merge
    assert found.note and "fetch failed" in found.note and local in found.note


def test_a_fetch_that_hangs_is_killed_with_its_whole_group_and_falls_back(
    remote, tmp_path, monkeypatch
):
    pidfile = tmp_path / "ssh.pid"
    script = tmp_path / "ssh.sh"
    script.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 30\n")
    script.chmod(0o755)
    git("remote", "set-url", "origin", "ssh://fake/x", cwd=remote.clone)
    git("config", "core.sshCommand", str(script), cwd=remote.clone)
    monkeypatch.setattr(workspace_module, "FETCH_TIMEOUT_S", 0.5)
    started = time.monotonic()
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert time.monotonic() - started < 5
    assert found.sha == git("rev-parse", "HEAD", cwd=remote.clone)
    assert found.note and "timed out after 0.5 s" in found.note
    pid = int(pidfile.read_text())
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_an_unborn_head_with_an_origin_answers_a_note_and_no_sha(tmp_path):
    repo = tmp_path / "unborn"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("remote", "add", "origin", str(tmp_path / "nowhere.git"), cwd=repo)
    found = GitWorkspace(repo, IDENTITY).upstream()
    assert found.sha is None and found.note


def test_without_git_on_the_path_upstream_answers_a_note_and_does_not_raise(remote, monkeypatch):
    monkeypatch.setenv("PATH", "")
    found = GitWorkspace(remote.clone, IDENTITY).upstream()
    assert found.sha is None and found.note


def test_a_merge_only_origin_has_is_an_ancestor_of_the_upstream_and_not_of_head(remote):
    merge = remote.merge_on_origin()
    ws = GitWorkspace(remote.clone, IDENTITY)
    found = ws.upstream()
    assert ws.is_ancestor(remote.clone, merge, ref=found.sha or "HEAD")
    assert not ws.is_ancestor(remote.clone, merge)


def test_with_no_remote_the_upstream_judges_exactly_as_head_does(remote):
    git("remote", "remove", "origin", cwd=remote.clone)
    ws = GitWorkspace(remote.clone, IDENTITY)
    head = git("rev-parse", "HEAD", cwd=remote.clone)
    ref = ws.upstream().sha or "HEAD"
    assert ws.is_ancestor(remote.clone, head, ref=ref) and ws.is_ancestor(remote.clone, head)


def test_a_local_only_stack_edit_never_reaches_the_new_task_base(remote):
    merge = remote.merge_on_origin({".agentic/stack.yaml": "checks:\n  tests: origin\n"})
    _commit(remote.clone, ".agentic/stack.yaml", "checks:\n  tests: local\n", "local stack")
    local = git("rev-parse", "HEAD", cwd=remote.clone)
    ws = GitWorkspace(remote.clone, IDENTITY)
    ws.task_checkout("GH-45")
    base = ws.base_of("agentic/GH-45")
    assert base == merge[:7]
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", local, "agentic/GH-45"], cwd=remote.clone
    )
    assert probe.returncode == 1
    assert GitStack(remote.clone).checks(base or "") == {"tests": "origin"}
    assert local[:7] in (ws.upstream().note or "")


def test_full_head_is_a_whole_object_id_and_head_abbreviates_it(product):
    ws = GitWorkspace(product, IDENTITY)
    full = ws.full_head(product)
    assert rules.full_commit_id(full)
    assert full == git("rev-parse", "HEAD", cwd=product)
    assert full.startswith(ws.head(product)) and ws.head(product) != full


def test_head_stays_abbreviated_and_is_not_a_full_id(product):
    ws = GitWorkspace(product, IDENTITY)
    assert not rules.full_commit_id(ws.head(product))


def test_commit_paths_commits_only_the_named_paths(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    document = ".agentic/tasks/GH-12.md"
    (task.path / ".agentic/tasks").mkdir(parents=True)
    (task.path / document).write_text("the task document\n")
    (task.path / "stray.txt").write_text("untracked\n")
    (task.path / "README.md").write_text("modified\n")

    sha = ws.commit_paths(task.path, [document], "GH-12: task document")

    assert sha == ws.head(task.path)
    assert git("show", "--name-only", "--format=", "HEAD", cwd=task.path) == document
    assert git("log", "-1", "--format=%s", cwd=task.path) == "GH-12: task document"
    # What was not named stays where it was: modified, and untracked.
    status = git("status", "--porcelain", cwd=task.path).splitlines()
    assert sorted(line.strip() for line in status) == ["?? stray.txt", "M README.md"]


def test_commit_paths_has_nothing_to_commit_when_the_path_is_already_committed(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    document = ".agentic/tasks/GH-12.md"
    (task.path / ".agentic/tasks").mkdir(parents=True)
    (task.path / document).write_text("the task document\n")
    assert ws.commit_paths(task.path, [document], "first") is not None
    head = ws.head(task.path)

    assert ws.commit_paths(task.path, [document], "again") is None
    assert ws.head(task.path) == head


def test_commit_paths_raises_a_git_error_for_a_path_git_cannot_add(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    with pytest.raises(GitError, match="git add"):
        ws.commit_paths(task.path, [".agentic/tasks/not-there.md"], "x")


# -- push(lease=) and remote_tip against a real bare origin ------------------------------


def _remote(product: Path) -> Path:
    return Path(git("remote", "get-url", "origin", cwd=product))


def _commit_on(ws: GitWorkspace, path: Path, name: str) -> str:
    (path / name).write_text(f"{name}\n")
    sha = ws.commit_all(path, name)
    assert sha is not None
    return git("rev-parse", "HEAD", cwd=path)


def _third_clone_pushes(product: Path, branch: str, tmp_path: Path) -> str:
    """Somebody else's clone commits on top of what `origin` holds for `branch`."""
    other = tmp_path / "other"
    git("clone", "-q", "--branch", branch, str(_remote(product)), str(other), cwd=tmp_path)
    (other / "theirs").write_text("theirs\n")
    git("add", "-A", cwd=other)
    git("-c", "user.name=o", "-c", "user.email=o@o", "commit", "-qm", "theirs", cwd=other)
    git("push", "-q", "origin", branch, cwd=other)
    return git("rev-parse", "HEAD", cwd=other)


def test_remote_tip_of_a_branch_origin_does_not_have_is_none(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    assert ws.remote_tip(task.path, task.branch) is None


def test_remote_tip_is_the_full_sha_with_its_object_local_and_the_ref_private(product, tmp_path):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    ws.push(task.branch)
    theirs = _third_clone_pushes(product, task.branch, tmp_path)
    tip = ws.remote_tip(task.path, task.branch)
    assert tip == theirs and len(tip) == 40
    # The object is here now, and the ref that holds it is not a branch.
    assert git("cat-file", "-t", tip, cwd=task.path) == "commit"
    heads = git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=product).splitlines()
    assert not any("remote-tips" in name for name in heads)
    assert git("rev-parse", f"refs/agentic/remote-tips/{task.branch}", cwd=product) == tip


def test_remote_tip_against_an_unreachable_origin_raises_and_never_reads_absent(product, tmp_path):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    git("remote", "set-url", "origin", str(tmp_path / "nowhere.git"), cwd=product)
    with pytest.raises(GitError, match="ls-remote"):
        ws.remote_tip(task.path, task.branch)


def test_remote_tip_raises_when_the_remote_moved_between_the_read_and_the_fetch(
    product, tmp_path, monkeypatch
):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    ws.push(task.branch)
    seen = {}
    real = ws._git

    def moving(*args, cwd=None):
        out = real(*args, cwd=cwd)
        if args[0] == "ls-remote":
            seen["observed"] = out.split()[0]
            seen["moved"] = _third_clone_pushes(product, task.branch, tmp_path)
        return out

    monkeypatch.setattr(ws, "_git", moving)
    with pytest.raises(GitError, match="moved between ls-remote"):
        ws.remote_tip(task.path, task.branch)
    assert seen["observed"] != seen["moved"]


def test_a_plain_push_fast_forwards_and_a_diverged_one_is_refused(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    first = _commit_on(ws, task.path, "a")
    ws.push(task.branch)
    second = _commit_on(ws, task.path, "b")
    ws.push(task.branch)
    assert git("rev-parse", task.branch, cwd=_remote(product)) == second
    git("reset", "-q", "--hard", first, cwd=task.path)
    _commit_on(ws, task.path, "c")
    with pytest.raises(GitError, match="rejected"):
        ws.push(task.branch)
    assert git("rev-parse", task.branch, cwd=_remote(product)) == second


def test_a_lease_push_replaces_a_diverged_remote_that_is_still_the_observed_one(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    _commit_on(ws, task.path, "a")
    ws.push(task.branch)
    old = ws.remote_tip(task.path, task.branch)
    assert old is not None
    git("reset", "-q", "--hard", "HEAD~1", cwd=task.path)
    new = _commit_on(ws, task.path, "other")
    ws.push(task.branch, lease=old)
    assert git("rev-parse", task.branch, cwd=_remote(product)) == new


def test_a_lease_push_is_rejected_when_a_third_clone_pushed_after_the_observation(
    product, tmp_path
):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-12")
    _commit_on(ws, task.path, "a")
    ws.push(task.branch)
    observed = ws.remote_tip(task.path, task.branch)
    assert observed is not None
    theirs = _third_clone_pushes(product, task.branch, tmp_path)
    git("reset", "-q", "--hard", "HEAD~1", cwd=task.path)
    _commit_on(ws, task.path, "mine")
    with pytest.raises(GitError, match="stale info|rejected"):
        ws.push(task.branch, lease=observed)
    # Git's own refusal text is kept, and what the third clone pushed is still there.
    assert git("rev-parse", task.branch, cwd=_remote(product)) == theirs


# -- GH-216: whether a head has master's commit among its ancestors ------------------------


def _commit_file(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


def test_contains_is_yes_when_the_head_has_masters_commit_behind_it(product):
    master = git("rev-parse", "HEAD", cwd=product)
    head = _commit_file(product, "on-top")
    assert GitWorkspace(product, IDENTITY).contains(head, master) == "yes"
    assert GitWorkspace(product, IDENTITY).contains(master, master) == "yes"


def test_contains_is_no_when_master_moved_past_the_head(product):
    base = git("rev-parse", "HEAD", cwd=product)
    git("checkout", "-q", "-b", "task", cwd=product)
    head = _commit_file(product, "task-work")
    git("checkout", "-q", "master", cwd=product)
    master = _commit_file(product, "master-moved")
    assert base != master
    assert GitWorkspace(product, IDENTITY).contains(head, master) == "no"


def test_contains_is_unknown_for_a_sha_neither_here_nor_fetchable_never_yes(product):
    head = git("rev-parse", "HEAD", cwd=product)
    ws = GitWorkspace(product, IDENTITY)
    assert ws.contains(head, "0" * 40) == "unknown"
    assert ws.contains("0" * 40, head) == "unknown"


@pytest.mark.parametrize("failure", ["exit-128", "git-error", "no-git"])
def test_contains_is_unknown_for_any_git_failure_never_raised(product, monkeypatch, failure):
    ws = GitWorkspace(product, IDENTITY)
    sha = git("rev-parse", "HEAD", cwd=product)
    real_run = ws._run

    def fake(*args, cwd=None):
        if args[:2] == ("merge-base", "--is-ancestor"):
            if failure == "exit-128":
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            raise GitError("boom") if failure == "git-error" else FileNotFoundError("git")
        return real_run(*args, cwd=cwd)

    monkeypatch.setattr(ws, "_run", fake)
    assert ws.contains(sha, sha) == "unknown"


# -- GH-255: whether a head conflicts with master, read without touching a worktree --------


def _two_branches(product: Path, same_line: bool) -> tuple[str, str]:
    """`(head, master)`: two commits off one base, editing README.md's one line differently
    (a conflict) or two different files (a clean merge)."""
    git("checkout", "-q", "-b", "task", cwd=product)
    if same_line:
        (product / "README.md").write_text("task\n")
        git("add", "-A", cwd=product)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "t", cwd=product)
        head = git("rev-parse", "HEAD", cwd=product)
    else:
        head = _commit_file(product, "task-work")
    git("checkout", "-q", "master", cwd=product)
    if same_line:
        (product / "README.md").write_text("master\n")
        git("add", "-A", cwd=product)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "m", cwd=product)
        master = git("rev-parse", "HEAD", cwd=product)
    else:
        master = _commit_file(product, "master-work")
    return head, master


def test_merge_conflicts_is_false_for_two_branches_that_merge_clean(product):
    head, master = _two_branches(product, same_line=False)
    assert GitWorkspace(product, IDENTITY).merge_conflicts(head, master) is False


def test_merge_conflicts_is_true_for_two_branches_editing_the_same_line(product):
    head, master = _two_branches(product, same_line=True)
    before = git("status", "--porcelain", cwd=product)
    assert GitWorkspace(product, IDENTITY).merge_conflicts(head, master) is True
    # The probe touched neither the worktree nor HEAD.
    assert git("status", "--porcelain", cwd=product) == before
    assert git("rev-parse", "HEAD", cwd=product) == master


def test_merge_conflicts_is_none_for_a_sha_neither_here_nor_fetchable(product):
    head = git("rev-parse", "HEAD", cwd=product)
    ws = GitWorkspace(product, IDENTITY)
    assert ws.merge_conflicts(head, "0" * 40) is None
    assert ws.merge_conflicts("0" * 40, head) is None


@pytest.mark.parametrize("failure", ["timeout", "git-error", "no-git", "exit-129"])
def test_merge_conflicts_is_none_for_any_failure_never_raised(product, monkeypatch, failure):
    head, master = _two_branches(product, same_line=True)
    ws = GitWorkspace(product, IDENTITY)

    def fake(*args):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        if failure == "git-error":
            raise GitError("boom")
        if failure == "no-git":
            raise FileNotFoundError("git")
        return subprocess.CompletedProcess(args, 129, "", "usage")

    monkeypatch.setattr(ws, "_run_timed", fake)
    assert ws.merge_conflicts(head, master) is None


def test_every_git_call_merge_conflicts_makes_runs_under_a_timeout(product, monkeypatch):
    head, master = _two_branches(product, same_line=False)
    calls: list[tuple[list[str], float | None]] = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append((cmd, kwargs.get("timeout")))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(workspace_module.subprocess, "run", spy)
    ws = GitWorkspace(product, IDENTITY)
    # The shas are here (existence checks and merge-tree), then one is not (a fetch too).
    assert ws.merge_conflicts(head, master) is False
    assert ws.merge_conflicts(head, "0" * 40) is None
    verbs = {cmd[1] for cmd, _ in calls}
    assert {"cat-file", "merge-tree", "fetch"} <= verbs
    assert all(timeout is not None for _, timeout in calls), calls
    merge_tree = [timeout for cmd, timeout in calls if cmd[1] == "merge-tree"]
    assert merge_tree == [workspace_module.MERGE_TREE_TIMEOUT_S]


# -- merge_upstream_into (GH-239) ----------------------------------------------------


def _task_with_a_moved_master(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-239")
    (task.path / "mine.py").write_text("mine = 1\n")
    ws.commit_all(task.path, "task work")
    (product / "theirs.py").write_text("theirs = 1\n")
    git("add", "-A", cwd=product)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "master moves",
        cwd=product,
    )
    return ws, task, git("rev-parse", "HEAD", cwd=product)


def test_merge_upstream_into_a_disjoint_master_is_clean_and_reports_the_new_head(product):
    ws, task, master = _task_with_a_moved_master(product)
    merged = ws.merge_upstream_into(task.path, master)
    assert merged.clean and merged.conflicted == ()
    assert merged.head == git("rev-parse", "HEAD", cwd=task.path)
    assert (task.path / "theirs.py").exists()


def test_merge_upstream_into_names_the_conflicted_paths_and_leaves_them_for_a_role(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-240")
    (task.path / "x.py").write_text("a = 2\n")
    ws.commit_all(task.path, "task edits x")
    (product / "x.py").write_text("a = 3\n")
    git("add", "-A", cwd=product)
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "master edits x",
        cwd=product,
    )
    merged = ws.merge_upstream_into(task.path, git("rev-parse", "HEAD", cwd=product))
    assert not merged.clean and merged.conflicted == ("x.py",)
    assert not ws.is_clean(task.path)


def test_merge_upstream_into_a_refusal_that_is_not_a_conflict_raises_git_error(product):
    ws = GitWorkspace(product, IDENTITY)
    task = ws.task_checkout("GH-241")
    with pytest.raises(GitError):
        ws.merge_upstream_into(task.path, "0" * 40)
