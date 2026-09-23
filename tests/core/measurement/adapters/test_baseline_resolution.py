"""The sha-to-baseline resolver: `telemetry.resolve_baseline`, beside `system_baseline`
(Tech Lead plan, subtask `scorecard`). A real, throwaway git repo under `tmp_path` for
every case that reads git at all, so the resolver is exercised against the exact
`git describe --match` idiom `system_baseline` already uses, never a real checkout."""

import subprocess

import pytest

from agentic.core.measurement.adapters.otel import telemetry


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _sha(repo) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("1")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "first")
    return root


@pytest.fixture(autouse=True)
def clear_cache():
    telemetry._BASELINE_OF_SHA.clear()
    yield
    telemetry._BASELINE_OF_SHA.clear()


# --- bucket: a `bl-*` tag reachable from the sha ---


def test_resolve_baseline_returns_the_reachable_tag(repo):
    sha = _sha(repo)
    _git(repo, "tag", "bl-2026-09-23", sha)
    assert telemetry.resolve_baseline(sha, repo) == "bl-2026-09-23"


# --- bucket: pre-baseline, sha resolves but no bl-* tag reaches it ---


def test_resolve_baseline_is_pre_baseline_with_no_reachable_tag(repo):
    sha = _sha(repo)
    assert telemetry.resolve_baseline(sha, repo) == "pre-baseline"


def test_resolve_baseline_ignores_a_tag_that_does_not_match_the_glob(repo):
    sha = _sha(repo)
    _git(repo, "tag", "v0.1", sha)  # not `bl-*`: the same glob `system_baseline` uses
    assert telemetry.resolve_baseline(sha, repo) == "pre-baseline"


# --- bucket: unknown -- absent, empty, unresolvable or ambiguous sha ---


def test_resolve_baseline_is_unknown_with_no_system_commit(repo):
    assert telemetry.resolve_baseline(None, repo) == "unknown"
    assert telemetry.resolve_baseline("", repo) == "unknown"


def test_resolve_baseline_is_unknown_for_a_sha_this_repo_never_saw():
    # The rejecting input for this bucket: a sha that simply does not exist here --
    # `git rev-parse --verify` refuses it, never raises past the resolver.
    assert telemetry.resolve_baseline("0" * 40, telemetry.REPO) == "unknown"


def test_resolve_baseline_is_unknown_when_rev_parse_times_out(repo, monkeypatch):
    sha = _sha(repo)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 5.0))

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert telemetry.resolve_baseline(sha, repo) == "unknown"


def test_resolve_baseline_is_unknown_on_an_os_error(repo, monkeypatch):
    sha = _sha(repo)

    def fake_run(*args, **kwargs):
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert telemetry.resolve_baseline(sha, repo) == "unknown"


# --- every git call carries the caller's own timeout ---


def test_resolve_baseline_passes_its_timeout_to_every_git_call(repo, monkeypatch):
    sha = _sha(repo)
    seen = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    telemetry.resolve_baseline(sha, repo, timeout=1.5)
    assert seen and all(t == 1.5 for t in seen)


# --- memoised per (repo, sha): one pair of git calls for many documents on one sha ---


def test_resolve_baseline_is_memoised_per_repo_and_sha(repo, monkeypatch):
    sha = _sha(repo)
    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(args)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    telemetry.resolve_baseline(sha, repo)
    first_call_count = len(calls)
    telemetry.resolve_baseline(sha, repo)
    assert len(calls) == first_call_count  # second call answered from cache, no new git call
