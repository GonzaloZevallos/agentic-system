"""The reserved `docs` check, run as the shell command `stack.docs_command` builds, in a real
git repository with a stub `generate`: a stale generated file fails it, a current one passes,
and the owned paths are left as HEAD has them on every exit."""

import os
import subprocess
from pathlib import Path

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.domain.docs import Docs

OWNED = "docs/architecture/generated"
GENERATOR = """\
mkdir -p docs/architecture/generated
case "$MODE" in
  pass) printf 'one\\n' > docs/architecture/generated/a.mmd ;;
  modify) printf 'changed\\n' > docs/architecture/generated/a.mmd ;;
  untracked) printf 'one\\n' > docs/architecture/generated/a.mmd
             printf 'new\\n' > "docs/architecture/generated/new file.mmd" ;;
  nested) mkdir -p docs/architecture/generated/deep
          printf 'new\\n' > docs/architecture/generated/deep/b.mmd ;;
  delete) rm docs/architecture/generated/a.mmd ;;
  staged) printf 'new\\n' > docs/architecture/generated/s.mmd
          git add docs/architecture/generated/s.mmd ;;
  fail) printf 'half\\n' > docs/architecture/generated/a.mmd; exit 3 ;;
  nothing) ;;
esac
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "master")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/gen.sh").write_text(GENERATOR)
    (tmp_path / "docs/architecture/generated").mkdir(parents=True)
    (tmp_path / "docs/architecture/generated/a.mmd").write_text("one\n")
    (tmp_path / "docs/architecture/written.md").write_text("by hand\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "v0")
    return tmp_path


def command(paths: tuple[str, ...] = (f"{OWNED}/**",)) -> str:
    return stack.docs_command(Docs("sh scripts/gen.sh", paths))


def run_check(repo: Path, mode: str, text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", text or command()],
        cwd=repo,
        env={**os.environ, "MODE": mode},
        capture_output=True,
        text=True,
        check=False,
    )


def assert_as_head_has_it(repo: Path) -> None:
    assert git(repo, "status", "--porcelain", "--untracked-files=all", "--", OWNED) == ""
    assert (repo / OWNED / "a.mmd").read_text() == "one\n"


def test_a_current_generated_doc_passes_and_leaves_the_paths_as_head_has_them(repo):
    result = run_check(repo, "pass")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "docs stale" not in result.stdout
    assert_as_head_has_it(repo)


def test_a_modified_tracked_file_fails_names_the_file_and_is_restored(repo):
    result = run_check(repo, "modify")
    assert result.returncode == 1
    assert "docs stale: commit these" in result.stdout
    assert f"M {OWNED}/a.mmd" in result.stdout
    assert_as_head_has_it(repo)


def test_a_new_untracked_file_is_stale_and_is_removed(repo):
    result = run_check(repo, "untracked")
    assert result.returncode == 1
    assert "docs stale: commit these" in result.stdout
    assert "new file.mmd" in result.stdout
    assert not (repo / OWNED / "new file.mmd").exists()
    assert_as_head_has_it(repo)


def test_a_new_file_in_a_new_directory_is_stale_and_the_directory_is_removed(repo):
    result = run_check(repo, "nested")
    assert result.returncode == 1
    assert f"{OWNED}/deep/b.mmd" in result.stdout
    assert not (repo / OWNED / "deep").exists()
    assert_as_head_has_it(repo)


def test_a_deleted_tracked_file_is_stale_and_comes_back(repo):
    result = run_check(repo, "delete")
    assert result.returncode == 1
    assert f"D {OWNED}/a.mmd" in result.stdout
    assert_as_head_has_it(repo)


def test_a_file_generate_staged_is_stale_and_leaves_neither_index_nor_tree(repo):
    result = run_check(repo, "staged")
    assert result.returncode == 1
    assert f"{OWNED}/s.mmd" in result.stdout
    assert not (repo / OWNED / "s.mmd").exists()
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert_as_head_has_it(repo)


def test_a_generate_that_exits_non_zero_fails_even_with_nothing_changed_and_restores(repo):
    result = run_check(repo, "fail")
    assert result.returncode == 1
    assert "docs generate exited 3" in result.stdout
    assert "docs stale: commit these" in result.stdout  # it also left a half-written file
    assert_as_head_has_it(repo)


def test_a_generate_that_exits_non_zero_and_changes_nothing_still_fails(repo):
    text = stack.docs_command(Docs("exit 4", (f"{OWNED}/**",)))
    result = run_check(repo, "nothing", text)
    assert result.returncode == 1
    assert "docs generate exited 4" in result.stdout
    assert "docs stale" not in result.stdout
    assert_as_head_has_it(repo)


def test_owned_paths_that_match_no_tracked_file_yet_neither_fail_the_restore_nor_the_check(repo):
    git(repo, "rm", "-rq", "--", OWNED)
    git(repo, "commit", "-q", "-m", "no generated docs yet")
    assert run_check(repo, "nothing").returncode == 0
    added = run_check(repo, "untracked")
    assert added.returncode == 1
    assert "new file.mmd" in added.stdout
    assert not (repo / OWNED / "new file.mmd").exists()
    assert git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_only_the_owned_paths_are_read_and_restored(repo):
    (repo / "docs/architecture/written.md").write_text("edited by hand\n")
    (repo / "notes.txt").write_text("untracked, not owned\n")
    result = run_check(repo, "pass")
    assert result.returncode == 0
    assert (repo / "docs/architecture/written.md").read_text() == "edited by hand\n"
    assert (repo / "notes.txt").exists()


def test_every_owned_path_is_checked_and_a_path_with_a_space_or_a_quote_is_quoted(repo):
    odd = repo / "odd dir"
    odd.mkdir()
    (odd / "it's.mmd").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "odd")
    text = command((f"{OWNED}/**", "odd dir/**"))
    (odd / "it's.mmd").write_text("edited\n")  # dirty before: generate does not touch it
    result = run_check(repo, "pass", text)
    assert result.returncode == 1
    assert "it's.mmd" in result.stdout
    assert (odd / "it's.mmd").read_text() == "x\n"


def test_a_directory_path_without_a_glob_owns_everything_under_it(repo):
    result = run_check(repo, "untracked", command((f"{OWNED}/",)))
    assert result.returncode == 1
    assert "new file.mmd" in result.stdout
    assert not (repo / OWNED / "new file.mmd").exists()
