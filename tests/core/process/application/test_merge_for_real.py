"""GH-178: `agentic merge` over a real git repository -- `GitWorkspace` makes the throwaway
worktree and merges the upstream into it, `ShellCommands` runs the checks there,
`GitStack` reads them at the task's base -- with the `Tracker` fake and the real task
document. A fake cannot show that the checks see the head merged with master, or that a
refusal writes nothing the closure could mistake for intent."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import anyio
import pytest

from agentic import cli
from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application import landing_plan
from agentic.core.process.application.merge import merge_task
from agentic.core.process.application.ports import MergeCallFailed, TrackerUnreachable, Upstream
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import PullRequest, PullRequestCommit
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryTracker, memory_ports, real_commands

GREEN = "test -f feature.txt && test -f upstream.txt"
# stderr carries one CSI escape and one tab. No single quote, so `World`'s `{checks!r}`
# stays a YAML single-quoted scalar, which reads literally: the file holds each backslash
# doubled, `sh` undoes that inside the double quotes and `printf` gets `\033` and `\t`.
FAILING = 'printf "\\033[31mboom\\t!\\033[0m\\n" >&2; exit 3'
URL = "https://github.com/o/r/pull/210"


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    """These tests pin the merge verb's refusal evidence, not the landing: no step is live,
    so a refusal stays the single comment it was. A test that wants one patches it itself."""
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class MergingTracker(MemoryTracker):
    """`gh pr merge` for real enough: once merged, the pull request reads MERGED."""

    def merge(self, reference: str, head_sha: str) -> str:
        method = super().merge(reference, head_sha)
        self.prs[reference] = dataclasses.replace(
            self.prs[reference], state="MERGED", merged_at="2026-10-06T00:00:00Z"
        )
        return method


class World:
    def __init__(self, tmp_path: Path, checks: str, witness: str | None = None):
        self.origin = tmp_path / "origin.git"
        self.product = tmp_path / "product"
        git("init", "-q", "--bare", "-b", "master", str(self.origin), cwd=tmp_path)
        git("clone", "-q", str(self.origin), str(self.product), cwd=tmp_path)
        (self.product / ".agentic").mkdir()
        (self.product / ".agentic/stack.yaml").write_text(
            f"product: demo\nchecks:\n  tests: {checks!r}\n"
            + (f"witness: {witness!r}\n" if witness else "")
        )
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
            commands=real_commands(),
            documents=FilesystemTaskDocuments(self.product),
            tracker=self.tracker,
        )
        checkout = self.ports.workspace.task_checkout("GH-209")
        (checkout.path / "feature.txt").write_text("feature\n")
        git("add", "-A", cwd=checkout.path)
        git("commit", "-q", "-m", "feature", cwd=checkout.path)
        self.head = git("rev-parse", "HEAD", cwd=checkout.path)
        self.ports.workspace.discard(checkout.path)
        self.advance_master()
        self.deliver()

    def advance_master(self) -> str:
        (self.product / "upstream.txt").write_text(f"{len(list(self.product.iterdir()))}\n")
        git("add", "upstream.txt", cwd=self.product)
        git("commit", "-q", "-m", "master moves", cwd=self.product)
        git("push", "-q", "origin", "HEAD:master", cwd=self.product)
        return git("rev-parse", "HEAD", cwd=self.product)

    def deliver(self, **more: object) -> None:
        self.tracker.prs[URL] = PullRequest(
            state="OPEN",
            created_at="2026-10-05T00:00:00Z",
            merged_at=None,
            commits=(PullRequestCommit((self.cfg.git_author_email,), "feature", 1, 0, 1),),
            head_oid=self.head,
        )
        self.ports.documents.save(
            {
                "id": "GH-209",
                "title": "t",
                "description": "d",
                "status": "ready_for_review",
                "pull_request": URL,
                "subtasks": {
                    "only": {"id": "only", "status": "approved", "reviewed_head": self.head}
                },
                **more,
            }
        )

    def merge(self) -> None:
        anyio.run(cli._merge, "GH-209", 209, self.cfg, self.ports)

    def doc(self) -> dict:
        return self.ports.documents.load("GH-209") or {}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path, GREEN)


def test_a_green_head_merged_with_master_is_merged_and_closed_as_auto_merged(world):
    world.merge()

    doc = world.doc()
    master = git("rev-parse", "origin/master", cwd=world.product)
    assert doc["merge_checks"]["passed"] is True
    assert doc["merge_checks"]["head"] == world.head and doc["merge_checks"]["master"] == master
    assert world.tracker.merge_calls == [(URL, world.head)]
    assert doc["auto_merge_head"] == world.head and doc["auto_merge_master"] == master
    assert doc["auto_merge_method"] == "squash" and doc["auto_merged_at"]
    assert doc["delivery_outcome"] == "auto_merged"
    assert [text for _, text in world.tracker.comments] == [f"Auto-merged: {world.head} via squash"]
    # The checks ran in the throwaway worktree: gone now, and the product tree untouched.
    assert not (world.product / ".agentic/worktrees/merge-GH-209").exists()
    assert not (world.product / "feature.txt").exists()


def test_the_merge_targets_the_pull_request_never_the_issue_number(world):
    world.merge()

    assert world.tracker.merge_calls == [(URL, world.head)]
    # The head is a sha that may hold "209" by chance: look only at what names the target.
    assert all(target == URL for target, _ in world.tracker.merge_calls)
    assert world.doc()["delivery_outcome"] == "auto_merged"


def test_a_document_with_an_unparsable_pull_request_stops_before_any_intent(world):
    world.deliver(pull_request="not-a-url")

    result = merge_task(
        "GH-209",
        209,
        world.cfg,
        world.ports,
        world.ports.workspace.upstream(),
        frozenset(),
        lambda line: None,
    )

    assert result.kind == "stopped" and (result.reason or "").startswith("tracker_error")
    doc = world.doc()
    assert "auto_merge_head" not in doc and world.tracker.merge_calls == []
    assert world.tracker.comments == []


def test_the_merge_tracker_double_rejects_a_bad_reference_and_records_nothing():
    tracker = MemoryTracker()
    with pytest.raises(ValueError, match="not a pull request URL"):
        tracker.merge("209", "abc")
    with pytest.raises(ValueError, match="not a pull request URL"):
        tracker.pull_request("209")
    assert tracker.merge_calls == [] and tracker.events == []


def test_a_red_head_is_refused_once_and_writes_no_intent(tmp_path):
    world = World(tmp_path, "exit 1")

    world.merge()
    world.merge()

    doc = world.doc()
    assert doc["merge_checks"]["passed"] is False
    assert "auto_merge_head" not in doc and world.tracker.merge_calls == []
    assert [text.splitlines()[0] for _, text in world.tracker.comments] == [
        f"Auto-merge refused: {world.head} checks_failed"
    ]
    assert doc["merge_attempts"] == {"head": world.head, "count": 1}  # the second ran nothing


def test_a_really_failing_check_reaches_the_record_and_the_log_with_its_sanitized_tail(
    tmp_path, capsys
):
    world = World(tmp_path, FAILING)
    assert "'" not in FAILING
    # The declaration reached the commit the task is based on as `World` writes it: the
    # repr of the command, backslashes doubled.
    assert GitStack(world.product).checks("HEAD") == {"tests": repr(FAILING)[1:-1]}

    world.merge()

    doc = world.doc()
    assert doc["merge_checks"]["failed_step"] == "checks"
    # The fixture declares a single check; the tail is a list of lines, tab expanded.
    assert doc["merge_checks"]["checks"] == [
        {"name": "tests", "passed": False, "tail": ["boom    !"]}
    ]
    assert not any("\x1b" in line for line in doc["merge_checks"]["checks"][0]["tail"])
    assert doc["merge_checks"]["passed"] is False
    assert "auto_merge_head" not in doc and world.tracker.merge_calls == []
    out = capsys.readouterr().out.splitlines()
    assert "agentic: merge: GH-209: Failed: tests" in out
    assert "    boom    !" in out


def test_a_leftover_merge_worktree_does_not_break_entry(world):
    leftover = world.product / ".agentic/worktrees/merge-GH-209"
    leftover.mkdir(parents=True)
    (leftover / "stale.txt").write_text("from a crashed run")

    world.merge()

    assert world.tracker.merge_calls == [(URL, world.head)]
    assert not leftover.exists()


def test_master_moving_after_the_checks_refuses_and_writes_nothing(world, monkeypatch):
    real = world.ports.workspace
    calls = []

    class Moving:
        def __getattr__(self, name):
            return getattr(real, name)

        def upstream(self):
            calls.append(1)
            if len(calls) == 2:  # the last look, after the checks
                return Upstream("origin/master", "f" * 40, None)
            return real.upstream()

    world.ports = dataclasses.replace(world.ports, workspace=Moving())
    world.merge()

    doc = world.doc()
    assert world.tracker.merge_calls == [] and "auto_merge_head" not in doc
    assert doc["merge_checks"]["passed"] is True
    assert world.tracker.comments == []
    # The human merges that very head untouched later: the human's verdict, `merged`.
    world.tracker.prs[URL] = dataclasses.replace(
        world.tracker.prs[URL], state="MERGED", merged_at="2026-10-06T01:00:00Z"
    )
    assert cli._close_document("GH-209", world.cfg, world.ports).outcome == "merged"


def test_a_merge_that_fails_with_the_pull_request_open_withdraws_the_intent(world):
    world.tracker.merge_error = "head moved"

    world.merge()

    doc = world.doc()
    assert doc["auto_merge_head"] == ""
    # The method and the merge time are written only after a merge that returned; the
    # withdrawal touches the head alone.
    assert "auto_merge_method" not in doc and "auto_merged_at" not in doc
    assert doc.get("auto_merge_master") and doc.get("auto_merge_at")
    # The human merges that very head untouched later: the human's verdict, `merged`.
    world.tracker.prs[URL] = dataclasses.replace(
        world.tracker.prs[URL], state="MERGED", merged_at="2026-10-06T01:00:00Z"
    )
    result = cli._close_document("GH-209", world.cfg, world.ports)
    assert result.outcome == "merged"


def test_a_merge_that_fails_but_landed_keeps_the_intent_and_closes_as_auto_merged(world):
    def landed(reference: str, head: str) -> str:
        pull_request_number(reference)
        world.tracker.prs[URL] = dataclasses.replace(
            world.tracker.prs[URL], state="MERGED", merged_at="2026-10-06T00:00:00Z"
        )
        raise ValueError("gh timed out after merging")

    world.tracker.merge = landed  # type: ignore[method-assign]

    world.merge()

    doc = world.doc()
    assert doc["auto_merge_head"] == world.head
    assert "auto_merge_method" not in doc and "auto_merged_at" not in doc
    assert doc["delivery_outcome"] == "auto_merged"


def test_an_issue_in_the_eval_set_on_upstream_alone_is_never_merged(world):
    (world.product / ".agentic/eval-set.yaml").write_text("issues: [209, 10]\nsmoke: 209\n")
    git("add", ".agentic/eval-set.yaml", cwd=world.product)
    git("commit", "-q", "-m", "eval set", cwd=world.product)
    git("push", "-q", "origin", "HEAD:master", cwd=world.product)
    # Present upstream only: the product's own checkout has it unmerged-looking too, so
    # drop it locally and keep the pushed commit.
    git("reset", "-q", "--hard", "HEAD~1", cwd=world.product)

    world.merge()

    assert world.tracker.merge_calls == [] and world.tracker.comments == []
    assert "merge_checks" not in world.doc()


def test_the_eval_set_helper_reads_upstream_and_refuses_what_it_cannot_read(world):
    upstream = world.ports.workspace.upstream()
    assert cli._upstream_eval_set(world.ports, upstream) == frozenset()

    (world.product / ".agentic/eval-set.yaml").write_text("issues: [: broken\n")
    git("add", ".agentic/eval-set.yaml", cwd=world.product)
    git("commit", "-q", "-m", "broken", cwd=world.product)
    git("push", "-q", "origin", "HEAD:master", cwd=world.product)
    assert cli._upstream_eval_set(world.ports, world.ports.workspace.upstream()) is None

    assert cli._upstream_eval_set(world.ports, Upstream("HEAD", "a" * 40, "fell back")) is None


def test_a_guarded_path_is_no_longer_refused_it_merges_on_the_local_checks(world):
    """GH-239: `guarded_path` left `MergeRefusal`. The change to the standards merges, and
    the decision was the local checks' (`ci_untrusted` is False for it, but no CI is declared
    here)."""
    checkout = world.ports.workspace.task_checkout("GH-209")
    (checkout.path / "CODING_STANDARDS.md").write_text("weaker\n")
    git("add", "-A", cwd=checkout.path)
    git("commit", "-q", "-m", "standards", cwd=checkout.path)
    head = git("rev-parse", "HEAD", cwd=checkout.path)
    world.ports.workspace.discard(checkout.path)
    world.head = head
    world.deliver()

    world.merge()

    assert world.tracker.merge_calls == [("https://github.com/o/r/pull/210", head)]
    assert all("guarded_path" not in text for _, text in world.tracker.comments)


@pytest.mark.parametrize(
    ("witness", "passed"),
    [("pwd", True), ("echo /somewhere/else", False)],
    ids=["agrees", "disagrees"],
)
def test_a_witness_that_disagrees_makes_the_checks_fail_even_when_they_pass(
    tmp_path, witness, passed
):
    world = World(tmp_path, GREEN, witness=witness)

    world.merge()

    assert world.doc()["merge_checks"]["passed"] is passed
    assert bool(world.tracker.merge_calls) is passed


# GH-196: a merge that keeps failing with the pull request still OPEN.
SECRET = "remote: token ghp_SECRET rejected"


def _failing(world, failure="gh_rejected"):
    world.tracker.merge_error = SECRET
    world.tracker.merge_failure = failure


def _first_lines(world):
    return [text.splitlines()[0] for _, text in world.tracker.comments]


def test_a_merge_that_keeps_failing_stops_at_the_budget_and_comments_once_on_the_last(world):
    _failing(world)
    seen = []
    for _ in range(rules.MERGE_CALLS_MAX):
        world.merge()
        seen.append(len(world.tracker.comments))
    assert seen == [0] * (rules.MERGE_CALLS_MAX - 1) + [1]
    world.merge()
    world.merge()

    assert len(world.tracker.merge_calls) == rules.MERGE_CALLS_MAX
    assert _first_lines(world) == [
        f"Auto-merge refused: {world.head} gh pr merge failed: gh_rejected"
    ]
    body = world.tracker.comments[0][1]
    assert rules.MERGE_FAILURE_ADVICE["gh_rejected"] in body
    # GH-239: the landing diagnoses an exhausted merge, so it is not left to the human.
    assert "Left for the human to merge." not in body
    assert world.doc()["merge_failures"] == {
        "head": world.head,
        "count": rules.MERGE_CALLS_MAX,
        "failure": "gh_rejected",
    }
    assert world.doc()["auto_merge_head"] == ""


def test_a_terminal_failure_costs_one_call_and_then_the_comment(world):
    _failing(world, "no_merge_method")
    world.merge()
    world.merge()

    assert len(world.tracker.merge_calls) == 1
    assert _first_lines(world) == [
        f"Auto-merge refused: {world.head} gh pr merge failed: no_merge_method"
    ]
    assert rules.MERGE_FAILURE_ADVICE["no_merge_method"] in world.tracker.comments[0][1]


def test_a_plain_value_error_counts_as_unreadable_and_stops_at_the_budget(world):
    world.tracker.merge_error = SECRET  # no `merge_failure`: a bare `ValueError`
    for _ in range(rules.MERGE_CALLS_MAX + 2):
        world.merge()

    assert len(world.tracker.merge_calls) == rules.MERGE_CALLS_MAX
    assert _first_lines(world) == [
        f"Auto-merge refused: {world.head} gh pr merge failed: gh_unreadable"
    ]


def test_the_failure_is_recorded_before_the_pull_request_is_read_again(world, monkeypatch):
    _failing(world)
    recorded_at_read = []
    real = world.tracker.pull_request

    def spy(reference):
        pull_request_number(reference)
        if world.tracker.merge_calls:  # a read after the merge call
            recorded_at_read.append(world.doc().get("merge_failures"))
        return real(reference)

    monkeypatch.setattr(world.tracker, "pull_request", spy)
    world.merge()

    assert recorded_at_read == [{"head": world.head, "count": 1, "failure": "gh_rejected"}]


def test_nothing_is_fetched_or_diffed_after_the_merge_call_and_no_stderr_is_recorded(world):
    _failing(world)
    real = world.ports.workspace
    events = world.tracker.events

    class Watching:
        def __getattr__(self, name):
            return getattr(real, name)

        def fetch_commit(self, sha):
            events.append("fetch_commit")
            return real.fetch_commit(sha)

        def changed_paths(self, base, head):
            events.append("changed_paths")
            return real.changed_paths(base, head)

    world.ports = dataclasses.replace(world.ports, workspace=Watching())
    for _ in range(rules.MERGE_CALLS_MAX):
        events.clear()
        world.merge()
        after = events[events.index("merge") + 1 :]
        assert "fetch_commit" not in after and "changed_paths" not in after
        assert "fetch_commit" in events and "changed_paths" in events

    assert "SECRET" not in repr(world.doc())
    assert all("SECRET" not in text for _, text in world.tracker.comments)


def test_a_spent_document_with_no_comment_gets_exactly_one_comment(world):
    world.ports.documents.record_closure(
        "GH-209",
        {
            "merge_failures": {
                "head": world.head,
                "count": rules.MERGE_CALLS_MAX,
                "failure": "gh_rejected",
            }
        },
    )
    world.merge()
    assert _first_lines(world) == [
        f"Auto-merge refused: {world.head} gh pr merge failed: gh_rejected"
    ]
    world.merge()
    assert len(world.tracker.comments) == 1
    assert world.tracker.merge_calls == []


def test_a_spent_document_without_a_valid_class_comments_with_the_unknown_advice(world):
    world.ports.documents.record_closure(
        "GH-209",
        {"merge_failures": {"head": world.head, "count": rules.MERGE_CALLS_MAX, "failure": SECRET}},
    )
    world.merge()
    assert _first_lines(world) == [f"Auto-merge refused: {world.head} gh pr merge failed: unknown"]
    assert rules.MERGE_FAILURE_UNKNOWN_ADVICE in world.tracker.comments[0][1]
    assert SECRET not in world.tracker.comments[0][1]


def test_a_new_head_resets_the_failure_count_and_the_merge_goes_ahead(world):
    world.ports.documents.record_closure(
        "GH-209",
        {
            "merge_failures": {
                "head": "c" * 40,
                "count": rules.MERGE_CALLS_MAX,
                "failure": "gh_rejected",
            }
        },
    )
    world.merge()
    assert world.tracker.merge_calls == [(URL, world.head)]
    assert world.doc()["delivery_outcome"] == "auto_merged"


def test_a_merge_that_timed_out_but_landed_keeps_the_intent_and_closes_as_auto_merged(world):
    # GH-266: the adapter maps a `pr merge` stall to `MergeCallFailed("gh_unreadable")`.
    def landed(reference: str, head: str) -> str:
        pull_request_number(reference)
        world.tracker.prs[URL] = dataclasses.replace(
            world.tracker.prs[URL], state="MERGED", merged_at="2026-10-06T00:00:00Z"
        )
        raise MergeCallFailed("gh_unreadable", "gh pr merge timed out after 60 s")

    world.tracker.merge = landed  # type: ignore[method-assign]

    world.merge()

    doc = world.doc()
    assert doc["auto_merge_head"] == world.head
    assert doc["delivery_outcome"] == "auto_merged"


# GH-290: gh cannot reach GitHub.


class _Unreachable(MergingTracker):
    """`Tracker.merge` as `GhTracker.merge` raises it: unwrapped from the `repo view` read,
    or wrapped as `gh_unreadable` from the `pr merge` write."""

    def __init__(self, step: str):
        super().__init__()
        self.step = step

    def merge(self, reference: str, head_sha: str) -> str:
        pull_request_number(reference)
        self.merge_calls.append((reference, head_sha))
        error = TrackerUnreachable("gh repo view could not reach GitHub (error connecting to)")
        if self.step == "repo view":
            raise error
        raise MergeCallFailed("gh_unreadable", str(error)) from error


def test_an_unreachable_repo_view_withdraws_the_intent_and_records_no_failure(world):
    world.tracker = _Unreachable("repo view")
    world.ports = dataclasses.replace(world.ports, tracker=world.tracker)
    world.deliver()
    lines: list[str] = []

    result = merge_task(
        "GH-209",
        209,
        world.cfg,
        world.ports,
        world.ports.workspace.upstream(),
        frozenset(),
        lines.append,
    )

    assert (result.kind, result.reason) == ("stopped", "unreachable")
    doc = world.doc()
    assert doc["auto_merge_head"] == ""
    assert "merge_failures" not in doc and "auto_merged_at" not in doc
    assert world.tracker.merge_calls == [(URL, world.head)]  # called once, not retried
    assert world.tracker.comments == []
    assert lines[-1].startswith("agentic: merge: GH-209: gh could not reach GitHub (")
    assert lines[-1].endswith("): nothing merged, next round retries")
    assert "merge_failed" not in "".join(lines)


def test_an_unreachable_pr_merge_is_a_counted_gh_unreadable_and_keeps_the_ambiguity(world):
    world.tracker = _Unreachable("pr merge")
    world.ports = dataclasses.replace(world.ports, tracker=world.tracker)
    world.deliver()

    world.merge()

    doc = world.doc()
    assert doc["merge_failures"] == {"head": world.head, "count": 1, "failure": "gh_unreadable"}
    assert doc["auto_merge_head"] == ""  # the pull request still reads OPEN: not merged
    assert len(world.tracker.merge_calls) == 1


def test_an_auto_merge_records_the_files_the_pull_request_changed(world):
    world.tracker.changed_files_count = 7

    world.merge()

    assert world.doc()["actual_files"] == 7
    assert set(world.tracker.changed_files_asked) == {URL}


def test_an_auto_merge_whose_file_count_is_unreadable_still_merges_without_actual_files(world):
    world.tracker.changed_files_error = "gh printed garbage"

    world.merge()

    doc = world.doc()
    assert doc["delivery_outcome"] == "auto_merged"
    assert "actual_files" not in doc
