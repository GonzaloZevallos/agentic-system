"""`GhFormatFixReads` and `GhRepoSlug` over a fake tracker and a fake `gh` (GH-1067)."""

import json
import subprocess
from pathlib import Path

import pytest

from agentic.core.process.adapters.github.format_fix_reads import GhFormatFixReads, GhRepoSlug
from agentic.core.process.application.ports import IssueState, TrackerCallFailed

SLUG = "Owner/Repo"
STATE = IssueState(
    state="CLOSED",
    state_reason="COMPLETED",
    blocked_by=(),
    closing_merge_commit="a" * 40,
    labels=(),
)


class FakeTracker:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[int] = []
        self.error = error

    def issue_state(self, number: int) -> IssueState:
        self.calls.append(number)
        if self.error is not None:
            raise self.error
        return STATE


def _reads(tracker=None, slug=SLUG, ancestry=None):
    tracker = tracker if tracker is not None else FakeTracker()
    return tracker, GhFormatFixReads(
        tracker,
        lambda: slug,
        ancestry if ancestry is not None else (lambda sha: None),
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Owner/Repo/issues/7",
        "https://github.com/Owner/Repo/issues/7/",
        "https://github.com/owner/repo/issues/7",
        "https://github.com/OWNER/REPO/issues/7",
    ],
)
def test_the_system_repos_issue_is_read_through_the_tracker(url):
    tracker, reads = _reads()

    assert reads.closing(url) == STATE
    assert tracker.calls == [7]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Other/Repo/issues/7",
        "https://github.com/Owner/Other/issues/7",
        "https://gitlab.com/Owner/Repo/issues/7",
        "http://github.com/Owner/Repo/issues/7",
        "https://github.com/Owner/Repo/pull/7",
        "https://github.com/Owner/Repo/issues/seven",
        "https://github.com/Owner/Repo/issues/",
        "https://github.com/Owner/Repo/issues/7?x=1",
        "https://github.com/Owner/Repo/issues/7#issuecomment-1",
        "https://github.com/Owner/Repo/issues/7/comments",
        "https://github.com/Owner/Repo/issues/-7",
        "https://github.com/Owner/Repo/issues/07x",
        "https://github.com.evil.example/Owner/Repo/issues/7",
        "7",
        "",
    ],
)
def test_any_other_url_is_none_and_makes_no_tracker_call(url):
    tracker, reads = _reads()

    assert reads.closing(url) is None
    assert tracker.calls == []


def test_an_unreadable_slug_is_none_and_makes_no_tracker_call():
    tracker, reads = _reads(slug=None)

    assert reads.closing("https://github.com/Owner/Repo/issues/7") is None
    assert tracker.calls == []


@pytest.mark.parametrize("error", [TrackerCallFailed("down"), ValueError("gh"), KeyError("state")])
def test_a_failed_read_is_none(error):
    tracker, reads = _reads(FakeTracker(error))

    assert reads.closing("https://github.com/Owner/Repo/issues/7") is None
    assert tracker.calls == [7]


def test_contains_is_the_ancestry_answer():
    seen: list[str] = []

    def ancestry(sha: str) -> bool | None:
        seen.append(sha)
        return {"a" * 40: True, "b" * 40: False}.get(sha)

    _, reads = _reads(ancestry=ancestry)

    assert [reads.contains("a" * 40), reads.contains("b" * 40), reads.contains("c" * 40)] == [
        True,
        False,
        None,
    ]
    assert seen == ["a" * 40, "b" * 40, "c" * 40]


class FakeGh:
    def __init__(self, *outcomes: str | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[list[str], Path]] = []

    def __call__(self, argv, *, cwd, **_):
        self.calls.append((list(argv), cwd))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(argv, 0, outcome, "")


def _name(value: str) -> str:
    return json.dumps({"nameWithOwner": value})


def test_the_slug_is_read_in_the_given_directory_and_a_success_is_remembered(tmp_path):
    gh = FakeGh(_name("Owner/Repo"))
    slug = GhRepoSlug(tmp_path, run=gh)

    assert (slug(), slug()) == ("Owner/Repo", "Owner/Repo")
    assert gh.calls == [(["gh", "repo", "view", "--json", "nameWithOwner"], tmp_path)]


def test_a_failed_read_is_not_remembered_and_the_next_call_retries(tmp_path):
    failure = subprocess.CalledProcessError(1, "gh", stderr="boom")
    gh = FakeGh(failure, "not json", _name("no-slash"), _name("Owner/Repo"))
    slug = GhRepoSlug(tmp_path, run=gh)

    assert [slug(), slug(), slug()] == [None, None, None]
    assert slug() == "Owner/Repo"
    assert len(gh.calls) == 4


def test_a_gh_that_is_not_installed_or_stalls_gives_none(tmp_path):
    slug = GhRepoSlug(tmp_path, run=FakeGh(FileNotFoundError("gh")))
    assert slug() is None
    slug = GhRepoSlug(tmp_path, run=FakeGh(subprocess.TimeoutExpired("gh", 60)))
    assert slug() is None
