"""`restore_task` (GH-406, ADR-0011 and ADR-0048 addenda), over the fakes. The shape is apostrophe
GH-374's: an archived, approved, delivered task whose archived run left a delivered `diagnose` at
the head and open `PM: final validation` plan rows, its pull request closed and conflicting with
master. The fixture is built from the issue's facts (the sandbox does not read the apostrophe
checkout) and loads through `document.parse`, the way the archived text does.

Every landing outcome claimed here is the rule's answer over the restored document's facts
(`rules.auto_merge_decision`, then `landing_plan.step_for`), never a refusal handed in."""

from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import pytest

from agentic.core.process.application import landing_plan, restore
from agentic.core.process.application.merge import settlement_facts
from agentic.core.process.application.ports import GitError
from agentic.core.process.domain import ci, document, landing, plan_settlement, rules
from agentic.core.process.domain.roles import PAIR_PM_TECH_LEAD, PM_FINAL
from agentic.core.process.domain.task import PullRequest, TaskState
from tests.fakes import MemoryDocuments, MemoryTracker, MemoryWorkspace, memory_ports

PM = PM_FINAL.name
TASK = "GH-374"
PREFIX = "archive/run1-93e1891"
HEAD = "e9c47772" + "0" * 32
SUB_HEAD = "5ab" + "1" * 37
MASTER = "b" * 40
NEW_MASTER = "c" * 40
URL = "https://github.com/o/apostrophe/pull/443"
PRODUCT = Path("/memory/product")
DOC = f".agentic/tasks/{TASK}.md"
BRANCH = f"agentic/{TASK}"
SUB_BRANCH = f"agentic/{TASK}.api"
INTEGRATION_BRANCH = f"agentic/{TASK}.integration"
DIAGNOSE = {
    "head": HEAD,
    "step": "diagnose",
    "refusal": "merge_failed",
    "delivered": True,
    "verdict": "unlandable",
    "at": "2026-10-20T00:00:00+00:00",
}
NOW = "2026-10-28T12:00:00+00:00"


def _row(claim: str, version: int) -> dict:
    return {
        "subtask": "integration",
        "version": version,
        "location": PM,
        "claim": claim,
        "severity": "blocking",
        "scope": "plan",
        "category": "implementation",
        "status": "open",
    }


def archived_state(**more) -> dict:
    """The archived approved task: ready for review, a pull request, two integration-owned
    plan rows `PM: final validation` raised before the fix, and a `diagnose` at the head."""
    history = [{"pair": PAIR_PM_TECH_LEAD.key, "category": "implementation", "reason": "x"}] * 2
    base: dict = {
        "id": TASK,
        "title": "Task",
        "description": "Do it.",
        "type": "feature",
        "flags": {},
        "status": "ready_for_review",
        "pull_request": URL,
        "subtasks": {
            "integration": {"id": "integration", "status": "approved", "reviewed_head": HEAD},
            "api": {"id": "api", "status": "approved", "reviewed_head": SUB_HEAD},
        },
        "runs": [{"role": "PM", "section": PM, "version": v, "cost_usd": 0.1} for v in (1, 2, 3)],
        "change_history": history,
        "findings": [_row("a", 1), _row("b", 2)],
        "landing": [DIAGNOSE],
        "cost_usd": 4.5,
    }
    return {**base, **more}


def pull_request(
    state: Literal["OPEN", "MERGED", "CLOSED"] = "CLOSED", head: str = HEAD
) -> PullRequest:
    return PullRequest(
        state=state,
        created_at="2026-10-19T00:00:00Z",
        merged_at=None,
        commits=(),
        head_oid=head,
        mergeable="CONFLICTING",
        base_oid=MASTER,
    )


class CutTracker(MemoryTracker):
    """A tracker whose reopen can find the pull request merged meanwhile."""

    def __init__(self) -> None:
        super().__init__()
        self.merges_on_reopen = False

    def reopen_pull_request(self, number: int) -> None:
        if self.merges_on_reopen:
            self.prs[URL] = pull_request("MERGED")
        super().reopen_pull_request(number)  # raises when the platform refuses
        self.prs[URL] = replace(self.prs[URL], state="OPEN")


class CutWorkspace(MemoryWorkspace):
    """A workspace whose `create_branch` for one branch is killed once, mid-restore."""

    def __init__(self) -> None:
        super().__init__()
        self.kill_once: str | None = None

    def create_branch(self, name: str, sha: str) -> None:
        if name == self.kill_once:
            self.kill_once = None
            raise GitError("killed")
        super().create_branch(name, sha)

    def push(self, branch: str, *, lease: str | None = None) -> None:
        super().push(branch, lease=lease)
        self.remote_tips[branch] = self.existing_branches[branch][0]


class CutDocuments(MemoryDocuments):
    """Documents whose next `save` raises once, as a crash between two steps would."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False
        self.arm_after: int | None = None  # saves that pass before the next one raises

    def save(self, state: TaskState):
        if self.arm_after is not None:
            if self.arm_after == 0:
                self.arm_after = None
                raise OSError("disk full")
            self.arm_after -= 1
        if self.armed:
            self.armed = False
            raise OSError("disk full")
        return super().save(state)


class World:
    """The fakes of one restore, typed: a test reads what each port recorded."""

    def __init__(self, archived: dict | None = None, *, live: dict | None = None, pr=None) -> None:
        self.workspace = CutWorkspace()
        self.tracker = CutTracker()
        self.documents = CutDocuments()
        self.ports = memory_ports(
            documents=self.documents, workspace=self.workspace, tracker=self.tracker
        )
        text = document.render(
            archived if archived is not None else archived_state(), system_commit="93e1891"
        )
        self.workspace.files[(f"{PREFIX}/{BRANCH}", DOC)] = text
        self.workspace.existing_branches[f"{PREFIX}/{BRANCH}"] = ("tip-task", "b0")
        self.workspace.existing_branches[f"{PREFIX}/{SUB_BRANCH}"] = ("tip-api", "b0")
        self.workspace.existing_branches[f"{PREFIX}/{INTEGRATION_BRANCH}"] = ("tip-int", "b0")
        self.workspace.ancestors.update({HEAD, SUB_HEAD})
        self.workspace.upstream_answer = replace(self.workspace.upstream_answer, sha=NEW_MASTER)
        self.tracker.prs[URL] = pr if pr is not None else pull_request()
        if live is not None:
            self.documents.save(cast(TaskState, live))

    def run(self, prefix: str = PREFIX) -> restore.RestoreResult:
        return restore.restore_task(TASK, prefix, self.ports, PRODUCT, clock=lambda: NOW)

    def document(self) -> dict:
        loaded = self.documents.load(TASK)
        assert loaded is not None
        return loaded

    def untouched(self) -> bool:
        return not (
            self.workspace.created
            or self.workspace.renamed
            or self.workspace.pushed
            or self.tracker.reopened_pull_requests
            or any("restorations" in saved for saved in self.documents.saved)
        )

    def outcome(self, **ci_facts):
        """The refusal and the landing step the rules give the restored task's facts: the
        pull request reopened and conflicting with a master that moved on, CI as `ci_facts`."""
        state = restore.state_from(self.document())
        base = settlement_facts(TASK, state, pull_request("OPEN"), 443, NEW_MASTER)
        facts = replace(
            base,
            in_eval_set=False,
            touched_paths=("agentic/x.py",),
            conflicts=True,
            github_conflicting=True,
            ci_required=("test",),
            **ci_facts,
        )
        decision = rules.auto_merge_decision(facts)
        assert decision.reason is not None
        return decision.reason, landing_plan.step_for(state, decision.reason, facts)


GREEN_ON_THE_OLD_HEAD = {"ci_head": HEAD, "ci_verdict": ci.CiVerdict("green", (), ())}
UNREADABLE_CI = {"ci_head": None, "ci_verdict": None}


@pytest.mark.parametrize("ci_facts", [GREEN_ON_THE_OLD_HEAD, UNREADABLE_CI], ids=["green", "none"])
def test_a_restored_gh374_shape_settles_its_rows_and_is_synced_not_left_with_nothing_to_do(
    ci_facts,
):
    world = World(live=archived_state())
    plain = restore.state_from(archived_state())
    stuck = landing_plan.step_for(
        plain, "merge_conflict", settlement_facts(TASK, plain, pull_request("OPEN"), 443, MASTER)
    )
    assert stuck.kind == "none"  # the state this task fixes: a diagnose at the head ends it

    result = world.run()

    assert result.refusal is None
    assert result.steps == ("create_refs", "write_document", "push", "reopen", "settle")
    assert result.settlement == "settled"
    assert result.settled_rows == 2
    assert world.tracker.reopened_pull_requests == [443]
    reason, step = world.outcome(**ci_facts)
    assert reason == "merge_conflict"  # past the findings gate; the CI is the merge's to ask
    assert step.kind == "sync_master"


def test_the_restored_branches_stand_at_the_recorded_heads_and_the_archive_stays():
    world = World()

    world.run()

    workspace = world.workspace
    assert set(workspace.created) == {
        (BRANCH, HEAD),
        (INTEGRATION_BRANCH, HEAD),
        (SUB_BRANCH, SUB_HEAD),
    }
    assert workspace.pushed == [BRANCH]
    assert workspace.renamed == []
    assert f"{PREFIX}/{BRANCH}" in workspace.existing_branches
    assert f"{PREFIX}/{SUB_BRANCH}" in workspace.existing_branches


def test_the_document_gains_a_restoration_and_loses_no_entry_row_or_finding():
    world = World(live=archived_state())
    before = archived_state()

    world.run()

    after = world.document()
    assert after["restorations"] == [
        {"archive": PREFIX, "head": HEAD, "upstream": NEW_MASTER, "at": NOW, "landing_cut": 1}
    ]
    assert after["landing"] == before["landing"]
    assert after["findings"] == before["findings"]
    assert after["cost_usd"] == before["cost_usd"]
    assert [row["claim"] for row in after["plan_settled"]] == ["a", "b"]
    assert plan_settlement.pending(restore.state_from(after)) == []
    assert landing.live_entries(after) == []


def test_a_live_copy_with_landing_entries_the_archive_lacks_keeps_them_all_before_the_cut():
    extra = {"head": HEAD, "step": "sync_master", "at": "2026-10-27T00:00:00+00:00"}
    world = World(live=archived_state(landing=[DIAGNOSE, extra]))

    world.run()

    after = world.document()
    assert after["landing"] == [DIAGNOSE, extra]
    assert after["restorations"][0]["landing_cut"] == 2


def test_a_task_with_no_pm_final_approval_is_restored_but_not_settled():
    world = World(archived_state(runs=[]))

    result = world.run()

    assert result.refusal is None
    assert result.settlement == "no_pm_approval"
    assert "plan_settled" not in world.document()
    assert world.tracker.reopened_pull_requests == [443]


def test_a_displaced_live_run_is_archived_and_its_restarts_and_cost_carried():
    displaced = archived_state(
        pull_request=None,
        status="in_progress",
        cost_usd=3.0,
        landing_restarts=1,
        landing_cost_carried=2.0,
    )
    world = World(live=displaced)
    world.workspace.existing_branches[BRANCH] = ("other-head", "b0")

    result = world.run()

    assert result.steps[:2] == ("archive_live", "create_refs")
    assert world.workspace.renamed == [(BRANCH, f"archive/run2-unknown/{BRANCH}")]
    assert world.workspace.existing_branches[BRANCH][0] == HEAD
    after = world.document()
    assert after["landing_restarts"] == 1
    assert after["landing_cost_carried"] == 5.0
    assert after["cost_usd"] == 4.5


def test_the_archived_restarts_win_when_they_are_deeper_than_the_displaced_ones():
    world = World(archived_state(landing_restarts=2), live=archived_state(landing_restarts=1))

    world.run()

    assert world.document()["landing_restarts"] == 2


# --- refusals: each leaves every verb that changes something untouched ----------------------


def test_no_ref_under_the_prefix_refuses_no_archive():
    world = World()
    assert world.run(prefix="archive/run9-ffffff").refusal == "no_archive"
    assert world.untouched()


def test_an_archive_with_no_readable_document_refuses_no_document():
    world = World()
    world.workspace.files.clear()
    assert world.run().refusal == "no_document"
    world.workspace.files[(f"{PREFIX}/{BRANCH}", DOC)] = "not a document"
    assert world.run().refusal == "no_document"
    assert world.untouched()


def test_a_task_that_never_reached_review_refuses_not_delivered():
    world = World(archived_state(status="in_progress"))
    assert world.run().refusal == "not_delivered"
    assert world.untouched()


def test_an_already_merged_pull_request_refuses_pr_merged():
    world = World(pr=pull_request("MERGED"))
    assert world.run().refusal == "pr_merged"
    assert world.untouched()


def test_a_pull_request_on_another_head_refuses_head_disagrees():
    world = World(pr=pull_request(head="d" * 40))
    assert world.run().refusal == "head_disagrees"
    assert world.untouched()


def test_a_head_the_archive_does_not_hold_refuses_head_not_reachable():
    world = World()
    world.workspace.ancestors.discard(SUB_HEAD)
    assert world.run().refusal == "head_not_reachable"
    assert world.untouched()


def test_a_remote_branch_at_another_commit_refuses_remote_diverged():
    world = World()
    world.workspace.remote_tips[BRANCH] = "f" * 40
    assert world.run().refusal == "remote_diverged"
    assert world.untouched()


def test_a_restoration_from_another_archive_refuses_already_restored():
    world = World()
    other = "archive/run2-8e8d229"
    world.workspace.existing_branches[f"{other}/{BRANCH}"] = ("tip", "b0")
    world.workspace.files[(f"{other}/{BRANCH}", DOC)] = world.workspace.files[
        (f"{PREFIX}/{BRANCH}", DOC)
    ]
    assert world.run().refusal is None

    result = world.run(prefix=other)

    assert result.refusal == "already_restored"
    assert len(world.document()["restorations"]) == 1


def test_a_retry_whose_pull_request_moved_off_the_recorded_head_refuses_pr_head_moved():
    world = World()
    assert world.run().refusal is None
    world.tracker.prs[URL] = pull_request("OPEN", head="d" * 40)
    assert world.run().refusal == "pr_head_moved"


def test_a_remote_that_refuses_the_push_is_push_refused_and_a_retry_finishes():
    world = World()
    world.workspace.push_failures = 1

    first = world.run()

    assert first.refusal == "push_refused"
    assert first.detail is not None
    assert first.steps == ("create_refs", "write_document")
    assert world.tracker.reopened_pull_requests == []
    second = world.run()
    assert second.refusal is None
    assert second.steps == ("push", "reopen", "settle")


def test_a_platform_that_refuses_the_reopen_is_pr_base_gone_and_never_retried():
    world = World()
    world.tracker.reopen_error = "base branch was deleted"

    result = world.run()

    assert result.refusal == "pr_base_gone"
    assert result.detail == "base branch was deleted"
    assert world.tracker.events.count("reopen_pull_request") == 1


def test_a_platform_that_refuses_the_reopen_of_a_merged_pull_request_is_pr_merged():
    world = World()
    world.tracker.reopen_error = "already merged"
    world.tracker.merges_on_reopen = True

    assert world.run().refusal == "pr_merged"


# --- a retry after a cut at every step ------------------------------------------------------


def test_a_cut_at_create_refs_is_finished_by_a_retry_that_archives_and_renames_nothing():
    world = World()
    world.workspace.kill_once = SUB_BRANCH
    with pytest.raises(GitError):
        world.run()

    second = world.run()

    assert second.refusal is None
    assert world.workspace.renamed == []
    assert world.workspace.existing_branches[BRANCH][0] == HEAD
    assert world.workspace.existing_branches[SUB_BRANCH][0] == SUB_HEAD


def test_a_platform_failure_not_about_the_base_is_raised_not_reported_as_pr_base_gone():
    world = World()
    world.tracker.reopen_error = "gh pr reopen failed: HTTP 502"

    with pytest.raises(ValueError, match="502"):
        world.run()

    world.tracker.reopen_error = None
    assert world.run().steps == ("reopen", "settle")


def test_a_cut_after_archive_live_is_retried_without_archiving_the_displaced_run_twice():
    displaced = archived_state(
        pull_request=None,
        status="in_progress",
        cost_usd=3.0,
        landing_restarts=1,
        landing_cost_carried=2.0,
    )
    world = World(live=displaced)
    world.workspace.existing_branches[BRANCH] = ("other-head", "b0")
    world.workspace.kill_once = SUB_BRANCH  # after the archive, mid create_refs
    with pytest.raises(GitError):
        world.run()
    assert world.workspace.renamed == [(BRANCH, f"archive/run2-unknown/{BRANCH}")]

    second = world.run()

    assert second.refusal is None
    assert "archive_live" not in second.steps
    assert world.workspace.renamed == [(BRANCH, f"archive/run2-unknown/{BRANCH}")]
    assert not [name for name in world.workspace.existing_branches if "run3" in name]
    assert world.workspace.existing_branches[BRANCH][0] == HEAD
    assert world.workspace.existing_branches[SUB_BRANCH][0] == SUB_HEAD
    after = world.document()
    assert after["landing_restarts"] == 1
    assert after["landing_cost_carried"] == 5.0
    assert len(after["restorations"]) == 1


def test_a_cut_at_settle_is_retried_and_the_rows_are_written_once():
    world = World(live=archived_state())
    first = world.run()
    assert first.settlement == "settled"
    reference = [row["claim"] for row in world.document()["plan_settled"]]
    assert reference == ["a", "b"]

    cut = World(live=archived_state())
    cut.documents.arm_after = 1  # the write_document save passes, the settle save raises
    with pytest.raises(OSError, match="disk full"):
        cut.run()
    assert "plan_settled" not in cut.document()

    second = cut.run()

    assert second.steps == ("settle",)
    assert [row["claim"] for row in cut.document()["plan_settled"]] == ["a", "b"]
    assert cut.run().steps == ()
    assert [row["claim"] for row in cut.document()["plan_settled"]] == ["a", "b"]
    assert cut.workspace.renamed == []


def test_a_cut_at_write_document_is_finished_by_a_retry():
    world = World(live=archived_state())
    world.documents.armed = True
    with pytest.raises(OSError, match="disk full"):
        world.run()

    second = world.run()

    assert second.steps == ("write_document", "push", "reopen", "settle")
    assert world.workspace.renamed == []


def test_a_cut_after_every_step_ends_in_the_state_an_uncut_restore_leaves():
    def snapshot(world: World) -> tuple:
        doc = world.document()
        return (
            sorted(world.workspace.existing_branches.items()),
            doc["restorations"],
            doc["landing"],
            [row["claim"] for row in doc.get("plan_settled", [])],
            world.tracker.reopened_pull_requests,
            world.workspace.pushed,
        )

    reference = World(live=archived_state())
    reference.run()

    cut = World(live=archived_state())
    cut.workspace.push_failures = 1
    assert cut.run().refusal == "push_refused"
    cut.tracker.reopen_error = "flaky"
    with pytest.raises(ValueError, match="flaky"):
        cut.run()
    cut.tracker.reopen_error = None
    assert cut.run().refusal is None
    assert cut.run().steps == ()  # nothing left to do, and nothing done twice

    assert snapshot(cut) == snapshot(reference)
    assert cut.workspace.renamed == []
