"""GH-364, subtask `sync-turn-git`: the system's own `merge` and `worktree add` wait on a
git lock and retry when it is still held, so a Developer committing in a sibling worktree
of the same `.git` never fails a turn of the system. Nothing is written to the repository's
config, and an error that is not a lock error is not retried."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.git import workspace as workspace_module
from agentic.core.process.application.ports import GitError, MergeConflict, MergeFailed
from agentic.shared.configuration import Configuration

IDENTITY = Configuration(product_dir=Path("/tmp/p")).git_identity
LOCK_ERROR = "fatal: Unable to create '/p/.git/refs/heads/x.lock': File exists."
REF_LOCK_ERROR = "error: cannot lock ref 'refs/heads/x': is at abc but expected def"


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# The test's own wait for its own commit inside the race below. Literals of this file: the
# system's lock constants are not read here, so the system's calls stay the only ones under test.
COMMIT_LOCK_PATTERN = re.compile(r"Unable to create '.*\.lock': File exists|cannot lock ref")
COMMIT_LOCK_OPTIONS = (
    "-c",
    "core.filesRefLockTimeout=5000",
    "-c",
    "core.packedRefsTimeout=5000",
)
COMMIT_ATTEMPTS = 5


def run_commit(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def pause(seconds: float) -> None:
    time.sleep(seconds)


def commit_waiting(*args: str, cwd: Path) -> str:
    """The test's own `git commit` while sibling committers hold the same locks: waits
    on a lock error and raises at once on any other, with git's stderr in the message."""
    argv = [
        "git",
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        *COMMIT_LOCK_OPTIONS,
        "commit",
        *args,
    ]
    for attempt in range(COMMIT_ATTEMPTS):
        result = run_commit(argv, cwd)
        if result.returncode == 0:
            return result.stdout.strip()
        failure = f"{' '.join(argv)} failed ({result.returncode}): {result.stderr}"
        if not COMMIT_LOCK_PATTERN.search(result.stderr) or attempt == COMMIT_ATTEMPTS - 1:
            raise RuntimeError(failure)
        pause(0.05 * 2**attempt)
    raise AssertionError("unreachable")


def completed(code: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout="", stderr=stderr)


class Scripted:
    """Stands in for `GitWorkspace._run`: answers the next scripted result for the
    command under test and delegates nothing, recording every argument list."""

    def __init__(self, *results: subprocess.CompletedProcess[str]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return self.results.pop(0) if self.results else completed(0)


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(workspace_module.time, "sleep", waited.append)
    return waited


def workspace(tmp_path: Path) -> GitWorkspace:
    return GitWorkspace(tmp_path, IDENTITY)


@pytest.mark.parametrize("error", [LOCK_ERROR, REF_LOCK_ERROR])
def test_a_merge_that_meets_a_held_lock_is_retried_until_it_goes_through(
    tmp_path, monkeypatch, sleeps, error
):
    run = Scripted(completed(128, error), completed(128, error), completed(0))
    monkeypatch.setattr(GitWorkspace, "_run", lambda self, *a, cwd=None: run(*a, cwd=cwd))

    assert workspace(tmp_path).merge(tmp_path, "agentic/GH-1.api") is None

    merges = [call for call in run.calls if "merge" in call]
    assert len(merges) == 3
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1]  # growing backoff


def test_the_wait_travels_as_c_options_of_the_call_and_nothing_else(tmp_path, monkeypatch, sleeps):
    run = Scripted()
    monkeypatch.setattr(GitWorkspace, "_run", lambda self, *a, cwd=None: run(*a, cwd=cwd))

    workspace(tmp_path).merge(tmp_path, "agentic/GH-1.api")

    (call,) = run.calls
    assert call[:4] == (
        "-c",
        f"core.filesRefLockTimeout={workspace_module.LOCK_TIMEOUT_MS}",
        "-c",
        f"core.packedRefsTimeout={workspace_module.LOCK_TIMEOUT_MS}",
    )
    assert "config" not in call


def test_a_merge_that_keeps_meeting_the_lock_gives_up_after_the_bound(
    tmp_path, monkeypatch, sleeps
):
    run = Scripted(*[completed(128, LOCK_ERROR)] * 50)
    monkeypatch.setattr(GitWorkspace, "_run", lambda self, *a, cwd=None: run(*a, cwd=cwd))

    result = workspace(tmp_path).merge(tmp_path, "agentic/GH-1.api")

    assert isinstance(result, MergeFailed) and "Unable to create" in result.reason
    merges = [call for call in run.calls if "merge" in call and "--abort" not in call]
    assert len(merges) == workspace_module.LOCK_RETRIES + 1


def test_a_conflict_is_not_a_lock_error_and_is_not_retried(tmp_path, monkeypatch, sleeps):
    run = Scripted(
        completed(1, "CONFLICT (content): Merge conflict in shared.txt"),
        subprocess.CompletedProcess([], 0, stdout="UU shared.txt\n", stderr=""),
    )
    monkeypatch.setattr(GitWorkspace, "_run", lambda self, *a, cwd=None: run(*a, cwd=cwd))

    result = workspace(tmp_path).merge(tmp_path, "agentic/GH-1.api")

    assert isinstance(result, MergeConflict) and "shared.txt" in result.reason
    assert result.paths == ("shared.txt",)
    assert len([call for call in run.calls if "--no-edit" in call]) == 1
    assert sleeps == []


def test_a_worktree_add_that_meets_a_held_lock_is_retried_and_other_errors_are_not(
    tmp_path, monkeypatch, sleeps
):
    ws = workspace(tmp_path)
    monkeypatch.setattr(ws, "exclude_system_files", lambda: None)
    monkeypatch.setattr(ws, "_branch_exists", lambda branch: True)
    run = Scripted(
        completed(128, LOCK_ERROR),
        completed(0),  # worktree add, second try
        completed(0, ""),  # rev-parse of the base
    )
    monkeypatch.setattr(GitWorkspace, "_run", lambda self, *a, cwd=None: run(*a, cwd=cwd))

    ws.subtask_checkout("GH-1", "api")

    adds = [call for call in run.calls if "worktree" in call and "add" in call]
    assert len(adds) >= 2
    assert all(call[1].startswith("core.filesRefLockTimeout=") for call in adds)

    elsewhere = Scripted(completed(128, "fatal: 'x' is already checked out at '/y'"))
    monkeypatch.setattr(GitWorkspace, "_run", lambda self, *a, cwd=None: elsewhere(*a, cwd=cwd))
    sleeps.clear()
    with pytest.raises(GitError, match="already checked out"):
        ws.detached_checkout("scratch", "abc")
    assert sleeps == []
    assert len(elsewhere.calls) == 1


COMMITTER = textwrap.dedent(
    """
    import subprocess, sys
    worktree = sys.argv[1]
    for i in range({n}):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                        "--allow-empty", "-q", "-m", str(i)], cwd=worktree)
        subprocess.run(["git", "pack-refs", "--all"], cwd=worktree)
    """
)


def test_the_systems_git_calls_hold_up_against_a_developer_committing_in_a_sibling(tmp_path):
    """A real `.git`: a separate process commits (and packs refs) in one worktree while the
    turn's workspace creates worktrees and merges into the task branch, N times. The
    adapter does the waiting; this test retries nothing."""
    repo = tmp_path / "product"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    (repo / "README.md").write_text("v0\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "v0", cwd=repo)
    ws = GitWorkspace(repo, IDENTITY)
    task = ws.task_checkout("GH-9")
    busy = ws.subtask_checkout("GH-9", "busy")
    rounds = 15
    commits_before = int(git("rev-list", "--count", busy.branch, cwd=repo))

    # Several processes, so that the locks `commit` and `pack-refs` take overlap the ones
    # the system's calls need often enough for a call without the wait to fail.
    committers = [
        subprocess.Popen([sys.executable, "-c", COMMITTER.format(n=rounds * 8), str(busy.path)])
        for _ in range(4)
    ]
    try:
        for i in range(rounds):
            made = ws.subtask_checkout("GH-9", f"s{i}")
            (made.path / f"f{i}.txt").write_text(f"{i}\n")
            git("add", "-A", cwd=made.path)
            commit_waiting("-q", "-m", f"s{i}", cwd=made.path)
            assert ws.merge(task.path, made.branch) is None
    finally:
        for committer in committers:
            try:
                committer.wait(timeout=120)
            except subprocess.TimeoutExpired:
                committer.kill()
                committer.wait()

    # The committers did create pressure: their exit codes are not the point (they fail
    # against each other), the commits they landed on the busy branch are.
    assert int(git("rev-list", "--count", busy.branch, cwd=repo)) > commits_before
    assert all((task.path / f"f{i}.txt").is_file() for i in range(rounds))
    config = (repo / ".git" / "config").read_text()
    assert "RefLockTimeout" not in config and "packedRefsTimeout" not in config


@pytest.mark.parametrize(
    ("results", "calls", "outcome"),
    [
        ([completed(128, LOCK_ERROR), completed(128, REF_LOCK_ERROR), completed(0)], 3, "returns"),
        ([completed(1, "fatal: nothing to commit")] * 5, 1, "raises"),
        ([completed(128, LOCK_ERROR)] * 5, COMMIT_ATTEMPTS, "raises"),
    ],
    ids=["lock-twice-then-ok", "non-lock-error", "lock-every-time"],
)
def test_the_commit_helper_waits_on_lock_errors_only_and_gives_up_after_five(
    tmp_path, monkeypatch, results, calls, outcome
):
    script = list(results)
    seen: list[list[str]] = []
    waited: list[float] = []

    def fake_run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        result = script.pop(0)
        result.stdout = "committed" if result.returncode == 0 else ""
        return result

    monkeypatch.setattr(sys.modules[__name__], "run_commit", fake_run)
    monkeypatch.setattr(sys.modules[__name__], "pause", waited.append)

    if outcome == "returns":
        assert commit_waiting("-m", "x", cwd=tmp_path) == "committed"
        assert len(waited) == 2 and waited[0] < waited[1]
    else:
        with pytest.raises(RuntimeError) as raised:
            commit_waiting("-m", "x", cwd=tmp_path)
        assert results[0].stderr in str(raised.value)
    assert len(seen) == calls
    assert all(argv[-3:] == ["commit", "-m", "x"] for argv in seen)
