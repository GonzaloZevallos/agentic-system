"""The use case that lands the playbook pull request by itself (owner's rule of 2026-11-16).

Seam: `agentic.core.process.application.playbook_land.land_playbook`, over fake tracker,
workspace and an in-memory LandRecord."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
import yaml

from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.adapters.github.tracker import pull_request_number
from agentic.core.process.application.playbook_land import land_playbook
from agentic.core.process.application.ports import ListedPullRequest, TrackerTurnRefused
from agentic.core.process.application.reflect_run import BRANCH
from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import playbook_candidates as pc
from agentic.core.process.domain import playbook_land as pl
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable
from agentic.core.process.domain.roles import DEVELOPER
from tests.fakes import (
    FakeLandRecord,
    MemoryStack,
    MemoryTracker,
    MemoryWorkspace,
    memory_ports,
)

PRODUCT = "demo"
HEAD = "a" * 40
MOVED = "c" * 40
MASTER = "9" * 40
NAMES = ("check",)
NOW = "2026-11-18T10:00:00.000+00:00"
PATH = pc.playbook_file("developer")
LESSON = "Run the declared checks before delivering."
PLAYBOOK = yaml.safe_dump(
    {
        "items": [
            {
                "id": "developer-1",
                "lesson": LESSON,
                "category": "spec-behaviour",
                "since": "empty",
                "seen_in": ["GH-1"],
                "added_at": "2026-10-05",
            }
        ]
    }
)


def check(conclusion: str | None = "SUCCESS", name: str = "check") -> CiCheck:
    status = "COMPLETED" if conclusion else "IN_PROGRESS"
    return CiCheck(name, status, conclusion, "https://github.com/o/r/actions/runs/1")


def report(*checks: CiCheck, head: str = HEAD, completed: bool | None = True) -> CiReport:
    return CiReport(head, "master", "b" * 40, None, checks, completed)


def listed(
    branch: str = BRANCH, state: str = "OPEN", cross_repository: bool | None = False
) -> ListedPullRequest:
    url = "https://github.com/o/r/pull/7"
    return ListedPullRequest(7, url, state, "", "", branch, cross_repository)


class Unfetchable(MemoryWorkspace):
    def fetch_commit(self, sha: str) -> bool:
        return False


class World:
    """A product whose playbook pull request is open, green and covered; a test breaks one fact."""

    def __init__(
        self,
        files: dict[str, str | None] | None = None,
        tracker: MemoryTracker | None = None,
        workspace: MemoryWorkspace | None = None,
    ) -> None:
        self.tracker = tracker or MemoryTracker()
        self.workspace = workspace or MemoryWorkspace()
        self.record = FakeLandRecord()
        self.stack = MemoryStack()
        self.tracker.listed = [listed()]
        self.tracker.ci_reports = [report(check())]
        self.workspace.contains_answer = "yes"
        files = {PATH: PLAYBOOK} if files is None else files
        self.workspace.touched = tuple(files)
        for path, text in files.items():
            if text is not None:
                self.workspace.files[(HEAD, path)] = text
        self.workspace.master_trees[HEAD] = tuple(
            p for p, text in files.items() if text is not None
        )

    def land(
        self, names: tuple[str, ...] = NAMES, master: str | None = MASTER
    ) -> tuple[pl.Verdict | Literal["none"], str]:
        ports = memory_ports(tracker=self.tracker, workspace=self.workspace, stack=self.stack)
        return land_playbook(ports, self.record, PRODUCT, names, NOW, master)

    @property
    def saved(self) -> pl.LandAttempt:
        return self.record.saved[-1][1]


def never_acted(world: World) -> None:
    assert world.tracker.merge_calls == []
    assert world.tracker.closed_pull_requests == []


def test_a_green_pull_request_that_touches_only_playbooks_merges_the_head_the_ci_judged():
    world = World()

    verdict, line = world.land()

    assert verdict == pl.Merge(HEAD)
    assert world.tracker.merge_calls == [("https://github.com/o/r/pull/7", HEAD)]
    assert world.tracker.merge_methods == ["merge"]
    assert HEAD in line
    assert world.saved == pl.LandAttempt(HEAD, NOW, "merge", None, 0, MASTER)


def test_after_that_merge_the_new_lesson_reaches_the_role_prompt_with_no_human_merge(
    tmp_path: Path,
):
    product = tmp_path / "product"
    workspace = MemoryWorkspace()

    class Merging(MemoryTracker):
        def merge(
            self, reference: str, head_sha: str, *, method: Literal["merge"] | None = None
        ) -> str:
            pull_request_number(reference)  # the adapter's own strictness, as every double has it
            # The merge brings the head's playbook file to the product checkout, as master does.
            target = product / PATH
            target.parent.mkdir(parents=True)
            target.write_text(workspace.files[(head_sha, PATH)], encoding="utf-8")
            return super().merge(reference, head_sha, method=method)

    world = World(tracker=Merging(), workspace=workspace)
    before = pb.section(FilesystemPrompts(product).playbook(DEVELOPER))

    verdict, _ = world.land()

    after = pb.section(FilesystemPrompts(product).playbook(DEVELOPER))
    assert verdict == pl.Merge(HEAD)
    assert LESSON not in before
    assert LESSON in after


@pytest.mark.parametrize(
    "files",
    [
        {PATH: PLAYBOOK, ".agentic/stack.yaml": "ci: []\n"},
        {".agentic/playbooks-x/a": "x"},
        {".agentic/playbooks/../stack.yaml": "x"},
        {"agentic/cli.py": "x"},
        {"/etc/passwd": "x"},
        {PATH: PLAYBOOK, "docs/renamed-from.md": None},  # a rename lists its old path too
        {pc.CANDIDATES_FILE: None},  # deleting anything but a role playbook
    ],
)
def test_a_path_outside_the_playbooks_never_merges(files):
    world = World(files)

    verdict, _ = world.land()

    assert verdict == pl.Refuse("path_outside")
    never_acted(world)
    assert world.saved.cause == "path_outside"


def test_a_role_playbook_that_does_not_parse_never_merges():
    world = World({PATH: "items: not-a-list"})

    verdict, _ = world.land()

    assert verdict == pl.Refuse("playbook_invalid")
    never_acted(world)


def test_a_role_playbook_can_be_deleted():
    world = World({PATH: None})

    verdict, _ = world.land()

    assert verdict == pl.Merge(HEAD)


def test_a_role_playbook_that_exists_but_cannot_be_read_is_paths_unreadable_not_deleted():
    world = World()
    del world.workspace.files[(HEAD, PATH)]  # `git show` fails, yet the head's tree holds it

    verdict, _ = world.land()

    assert verdict == pl.Refuse("paths_unreadable")
    never_acted(world)


@pytest.mark.parametrize("cross_repository", [True, None])
def test_a_pull_request_from_a_fork_or_unknown_origin_never_merges_and_reads_nothing_more(
    cross_repository,
):
    world = World()
    world.tracker.listed = [listed(cross_repository=cross_repository)]

    verdict, _ = world.land()

    assert verdict == pl.Refuse("foreign_head")
    never_acted(world)
    assert world.tracker.ci_reads == []
    assert world.workspace.touched_reads == []


def test_a_red_ci_neither_merges_nor_closes_in_this_part_but_is_recorded():
    world = World()
    world.tracker.ci_reports = [report(check("FAILURE"))]

    verdict, line = world.land()

    assert verdict == pl.Refuse("ci_red")
    assert "ci_red" in line
    never_acted(world)
    assert (world.saved.verdict, world.saved.cause, world.saved.streak) == ("refuse", "ci_red", 1)


def test_a_repository_without_merge_commits_merges_nothing_and_records_it():
    world = World()
    world.tracker.merge_error = "merge commits are not allowed"
    world.tracker.merge_failure = "no_merge_method"

    verdict, _ = world.land()

    assert verdict == pl.Refuse("no_merge_commit")
    assert world.tracker.merge_methods == ["merge"]
    assert (world.saved.verdict, world.saved.cause) == ("refuse", "no_merge_commit")


def test_another_failed_merge_call_is_recorded_as_failed_with_its_class():
    world = World()
    world.tracker.merge_error = "gh pr merge failed"
    world.tracker.merge_failure = "gh_rejected"

    verdict, line = world.land()

    assert verdict == pl.Failed("gh_rejected")
    assert "gh_rejected" in line
    assert (world.saved.verdict, world.saved.cause) == ("failed", "gh_rejected")


def test_a_refused_write_turn_is_failed_turn_refused_never_a_merge_call_failure():
    world = World()

    def refused(reference, head_sha, *, method=None):
        raise TrackerTurnRefused("gh pr merge write turn refused: over the 900 s bound")

    world.tracker.merge = refused

    verdict, line = world.land()

    assert verdict == pl.Failed("turn_refused")
    assert "turn_refused" in line and "gh_unreadable" not in line
    assert (world.saved.verdict, world.saved.cause) == ("failed", "turn_refused")


def test_a_head_that_moves_between_the_two_ci_reads_waits():
    world = World()
    world.tracker.ci_reports = [report(check()), report(check(), head=MOVED)]

    verdict, _ = world.land(names=("check", "lint"))

    assert verdict == pl.Wait("ci_pending")
    never_acted(world)
    assert len(world.tracker.ci_reads) == 2


def test_a_name_that_never_reports_on_two_reads_of_one_head_is_refused():
    world = World()
    world.tracker.ci_reports = [report(check()), report(check())]

    verdict, _ = world.land(names=("check", "lint"))

    assert verdict == pl.Refuse("ci_unreported")
    never_acted(world)


def test_ci_is_read_once_when_every_name_reported():
    world = World()

    world.land()

    assert len(world.tracker.ci_reads) == 1


def test_an_unreadable_ci_waits_and_reads_no_git():
    world = World()
    world.tracker.ci_reports = [CiUnreadable("gh_unreadable")]

    verdict, _ = world.land()

    assert verdict == pl.Wait("ci_unreadable")
    assert world.workspace.touched_reads == []
    never_acted(world)


def test_a_master_that_could_not_be_read_waits_before_any_read():
    world = World()

    verdict, _ = world.land(master=None)

    assert verdict == pl.Wait("master_unreadable")
    assert world.tracker.ci_reads == []
    never_acted(world)


def test_a_git_failure_reading_the_paths_is_paths_unreadable_never_an_empty_list():
    world = World()
    world.workspace.touched_error = "git diff failed"

    verdict, _ = world.land()

    assert verdict == pl.Refuse("paths_unreadable")
    never_acted(world)


def test_a_head_that_cannot_be_fetched_is_paths_unreadable():
    world = World(workspace=Unfetchable())

    verdict, _ = world.land()

    assert verdict == pl.Refuse("paths_unreadable")
    never_acted(world)


def test_the_diff_is_read_against_the_master_the_caller_gave_at_the_ci_head():
    world = World()

    world.land()

    assert world.workspace.touched_reads == [(MASTER, HEAD)]


def test_a_head_behind_master_waits_unless_master_gained_only_inert_paths():
    world = World()
    world.workspace.contains_answer = "no"
    world.workspace.gains[("base", MASTER)] = ("inert/a.md",)
    world.workspace.merge_bases[(HEAD, MASTER)] = "base"

    stale, _ = world.land()
    world.stack.declared_merge_inert = ("inert/**",)
    inert, _ = world.land()

    assert stale == pl.Wait("stale_base")
    assert inert == pl.Merge(HEAD)


def test_a_gain_that_cannot_be_read_is_not_covered():
    world = World()
    world.workspace.contains_answer = "no"
    world.stack.declared_merge_inert = ("inert/**",)

    verdict, _ = world.land()

    assert verdict == pl.Wait("stale_base")


def test_no_open_playbook_pull_request_returns_none_and_writes_nothing():
    world = World()
    world.tracker.listed = [listed(state="MERGED"), listed(branch="agentic/GH-1")]

    verdict, _ = world.land()

    assert verdict == "none"
    assert world.record.saved == []
    assert world.tracker.ci_reads == []
    never_acted(world)


def test_the_only_things_it_changes_are_the_merge_and_the_record():
    world = World()

    world.land()

    assert world.workspace.pushes == []
    assert world.workspace.commits == []
    assert world.workspace.pushed == []
    assert world.tracker.events == ["merge"]


def test_the_saved_attempt_follows_next_attempt_across_runs():
    world = World()
    world.tracker.ci_reports = [report(check("FAILURE"))]

    world.land()
    world.land()
    red = world.saved
    world.tracker.ci_reports = [report(check(None))]
    world.land()
    waiting = world.saved
    world.tracker.ci_reports = [report(check("FAILURE"))]
    world.land()
    again = world.saved

    assert (red.verdict, red.cause, red.streak) == ("refuse", "ci_red", 2)
    # A wait while a refusal stands keeps that refusal and its streak (next_attempt).
    assert (waiting.verdict, waiting.cause, waiting.streak) == ("refuse", "ci_red", 2)
    assert (again.verdict, again.cause, again.streak) == ("refuse", "ci_red", 3)
    assert pl.land_halted(again)
    assert [attempt.head for _, attempt in world.record.saved] == [HEAD] * 4
