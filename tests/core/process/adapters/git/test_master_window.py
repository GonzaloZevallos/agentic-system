"""`GitMasterWindow` (GH-694) on a real temporary repository used as the remote, with the CI
reader and the stack's names faked: the history it reads, the ancestry it decides and what it
does when git or an injected callable fails."""

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.adapters.git import master_window
from agentic.core.process.adapters.git.master_window import GitMasterWindow
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.core.process.domain.stage0_rotation import Unreadable, WindowEntry, prunable

Calls = list[tuple[list[str], dict[str, Any]]]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repo: Path, label: str) -> str:
    (repo / "f.txt").write_text(label)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", label, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A repo with twelve commits on master, oldest first in `history`; clones read it."""
    repo = tmp_path / "remote"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    return repo


@pytest.fixture
def history(remote: Path) -> list[str]:
    return [commit(remote, f"c{i}") for i in range(12)]


def green_report(sha: str) -> CiReport:
    run = CiCheck("tests", "COMPLETED", "SUCCESS", "https://github.com/o/r/actions/runs/1/job/1")
    return CiReport(sha, None, None, None, (run,), True)


class FakeReader:
    def __init__(self, behaviour: Callable[[str], CiReport | CiUnreadable] = green_report) -> None:
        self.behaviour = behaviour
        self.read_oids: list[str] = []

    def read(self, oid: str) -> CiReport | CiUnreadable:
        self.read_oids.append(oid)
        return self.behaviour(oid)


def names(_sha: str) -> tuple[str, ...]:
    return ("tests",)


def window(
    tmp_path: Path,
    remote: Path,
    reader: FakeReader | None = None,
    stack_ci: Callable[[str], tuple[str, ...]] = names,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    branch: str = "master",
) -> GitMasterWindow:
    return GitMasterWindow(
        tmp_path / "root", str(remote), reader or FakeReader(), stack_ci, branch=branch, run=run
    )


def recording(calls: Calls, failing: Callable[[list[str]], Exception | None] = lambda _: None):
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        error = failing(argv)
        if error is not None:
            raise error
        return subprocess.run(argv, **kwargs)

    return run


def entries(found: list[WindowEntry] | Unreadable) -> list[WindowEntry]:
    assert not isinstance(found, Unreadable)
    return found


def test_the_window_is_the_ten_newest_first_parent_commits_newest_first(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    found = entries(window(tmp_path, remote).entries(None))
    assert [entry.sha for entry in found] == list(reversed(history))[:10]


def test_a_short_history_gives_every_commit(tmp_path: Path, remote: Path) -> None:
    only = [commit(remote, "a"), commit(remote, "b")]
    found = entries(window(tmp_path, remote).entries(None))
    assert [entry.sha for entry in found] == list(reversed(only))


def test_descent_is_strict_and_the_current_sha_does_not_descend_from_itself(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    current = history[8]
    found = entries(window(tmp_path, remote).entries(current))
    by_sha = {entry.sha: entry.descends for entry in found}
    assert by_sha[history[11]] is True and by_sha[history[9]] is True
    assert by_sha[current] is False
    assert by_sha[history[7]] is False


def test_no_current_stage_means_every_commit_descends(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    found = entries(window(tmp_path, remote).entries(None))
    assert {entry.descends for entry in found} == {True}


def test_a_commit_on_a_diverged_branch_does_not_descend(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    git("checkout", "-q", "-b", "side", history[5], cwd=remote)
    side = commit(remote, "side")
    git("checkout", "-q", "master", cwd=remote)
    found = entries(window(tmp_path, remote).entries(side))
    assert {entry.descends for entry in found} == {False}


def test_a_current_that_git_cannot_resolve_is_unknown_ancestry_everywhere(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    found = entries(window(tmp_path, remote).entries("f" * 40))
    assert {entry.descends for entry in found} == {None}


def test_a_merge_base_that_fails_on_one_commit_is_none_only_there(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    bad = history[10]

    def failing(argv: list[str]) -> Exception | None:
        broken = "merge-base" in argv and argv[-1] == bad
        return subprocess.TimeoutExpired(argv, 1) if broken else None

    found = entries(window(tmp_path, remote, run=recording([], failing)).entries(history[3]))
    by_sha = {entry.sha: entry.descends for entry in found}
    assert by_sha[bad] is None
    assert by_sha[history[11]] is True and by_sha[history[9]] is True


def test_a_missing_branch_is_unreadable(tmp_path: Path, remote: Path, history: list[str]) -> None:
    assert window(tmp_path, remote, branch="nope").entries(None) == Unreadable()


def test_an_empty_remote_is_unreadable(tmp_path: Path, remote: Path) -> None:
    assert window(tmp_path, remote).entries(None) == Unreadable()


def test_a_clone_that_fails_is_unreadable_and_leaves_no_mirror(tmp_path: Path) -> None:
    failed = window(tmp_path, tmp_path / "not-a-repo")
    assert failed.entries(None) == Unreadable()
    assert not (tmp_path / "root" / ".mirror").exists()
    assert not (tmp_path / "root" / ".mirror.clone").exists()


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired("git", 1), OSError("no git")],
    ids=["timeout", "oserror"],
)
@pytest.mark.parametrize("verb", ["clone", "fetch", "rev-list"])
def test_a_git_call_of_the_window_that_fails_or_times_out_is_unreadable(
    tmp_path: Path, remote: Path, history: list[str], verb: str, error: Exception
) -> None:
    first = window(tmp_path, remote)
    assert not isinstance(first.entries(None), Unreadable)  # the mirror exists from here on
    calls: Calls = []

    def failing(argv: list[str]) -> Exception | None:
        return error if verb in argv else None

    broken = window(tmp_path, remote, run=recording(calls, failing))
    if verb == "clone":
        (tmp_path / "root" / ".mirror").rename(tmp_path / "gone")
    assert broken.entries(None) == Unreadable()


def test_a_leftover_mirror_clone_is_replaced(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    leftover = tmp_path / "root" / ".mirror.clone"
    leftover.mkdir(parents=True)
    (leftover / "junk").write_text("half a clone")
    found = entries(window(tmp_path, remote).entries(None))
    assert found[0].sha == history[-1]
    assert not leftover.exists()
    assert (tmp_path / "root" / ".mirror" / "HEAD").exists()


def test_the_second_read_fetches_and_does_not_clone_again(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    calls: Calls = []
    reader = window(tmp_path, remote, run=recording(calls))
    entries(reader.entries(None))
    newest = commit(remote, "new")
    second = entries(reader.entries(None))
    assert second[0].sha == newest
    verbs = [argv[1] for argv, _ in calls]
    assert verbs.count("clone") == 1 and verbs.count("fetch") == 1


def test_every_git_subprocess_runs_under_a_timeout_from_a_module_constant(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    calls: Calls = []
    reader = window(tmp_path, remote, run=recording(calls))
    entries(reader.entries(history[3]))
    entries(reader.entries(history[3]))
    timeouts = {argv[1]: kwargs.get("timeout") for argv, kwargs in calls}
    assert timeouts["clone"] == master_window.MIRROR_FETCH_TIMEOUT_S
    assert timeouts["fetch"] == master_window.MIRROR_FETCH_TIMEOUT_S
    for verb in ("rev-list", "rev-parse", "merge-base"):
        assert timeouts[verb] == master_window.GIT_READ_TIMEOUT_S


def test_a_working_reader_gives_each_commit_its_state(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    reader = FakeReader()
    found = entries(window(tmp_path, remote, reader).entries(None))
    assert {entry.ci for entry in found} == {"green"}
    assert reader.read_oids == [entry.sha for entry in found]


def test_a_reader_that_cannot_read_one_commit_makes_it_unknown_only_there(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    bad = history[9]
    reader = FakeReader(
        lambda oid: CiUnreadable("gh_unavailable") if oid == bad else green_report(oid)
    )
    found = entries(window(tmp_path, remote, reader).entries(None))
    assert {entry.sha: entry.ci for entry in found}[bad] == "unknown"
    assert [entry.ci for entry in found].count("green") == 9


def test_a_reader_that_raises_on_one_commit_makes_it_unknown_only_there(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    bad = history[9]

    def behaviour(oid: str) -> CiReport | CiUnreadable:
        if oid == bad:
            raise RuntimeError("boom")
        return green_report(oid)

    found = entries(window(tmp_path, remote, FakeReader(behaviour)).entries(None))
    assert {entry.sha: entry.ci for entry in found}[bad] == "unknown"
    assert [entry.ci for entry in found].count("green") == 9


def test_a_stack_that_cannot_be_read_at_one_commit_is_unknown_only_there(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    bad = history[8]

    def stack_ci(sha: str) -> tuple[str, ...]:
        if sha == bad:
            raise ValueError("no stack here")
        return ("tests",)

    found = entries(window(tmp_path, remote, stack_ci=stack_ci).entries(None))
    assert {entry.sha: entry.ci for entry in found}[bad] == "unknown"
    assert [entry.ci for entry in found].count("green") == 9


def test_a_commit_whose_stack_declares_no_ci_is_unknown(
    tmp_path: Path, remote: Path, history: list[str]
) -> None:
    found = entries(window(tmp_path, remote, stack_ci=lambda _sha: ()).entries(None))
    assert {entry.ci for entry in found} == {"unknown"}


def test_the_mirror_names_are_never_a_stage_to_prune() -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    stages = [(".mirror", old), (".mirror.clone", old), ("a" * 40, old), ("b" * 40, old)]
    pruned = prunable(stages, current=None, own=None, users=frozenset(), keep=0)
    assert pruned == frozenset({"a" * 40, "b" * 40})
