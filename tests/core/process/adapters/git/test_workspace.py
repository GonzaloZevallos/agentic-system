"""The git adapter against a real temporary repository with a bare remote."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
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
    (product / ".agentic").mkdir()
    (product / ".agentic/state.sqlite").write_text("x")
    (product / ".agentic/state.sqlite-journal").write_text("x")
    assert git("status", "--porcelain", cwd=product) == ""
    exclude = (product / ".git/info/exclude").read_text().splitlines()
    assert exclude.count(".agentic/state.sqlite*") == 1
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
