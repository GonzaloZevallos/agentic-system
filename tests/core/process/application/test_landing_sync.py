"""GH-239, subtask `landing-sync`: `sync_master`, `re_review` and the `reopen` they share, the
`rules.resume_target` clause that lets a task with a pull request run again, and `deliver`
re-delivering under the pull request it already has.

The behaviour tests drive `poll.run_once` and `landing.land` (and `merge.merge_task` for the
final merge) on the in-memory ports: a round decides, spawns `agentic land` and
`agentic merge` in-process through a launcher fake, and the Developer / review that
`agentic resume` would run is simulated by writing the approved document the review would."""

from __future__ import annotations

import anyio
import pytest

from agentic.core.measurement.domain import metrics
from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.application import landing, landing_sync, merge
from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.ports import GitError, Upstream, UpstreamMerge
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain import playbook, rules
from agentic.core.process.domain.task import TaskState
from tests.core.process.application.test_poll import ESCALATED_AT, _cfg, _round
from tests.core.process.application.test_poll_merge import (
    HEAD,
    MASTER,
    URL,
    _HeldLocks,
    _ports,
)
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

NEW = "d" * 40
FOREIGN = "c" * 40
LIVE = frozenset({"sync_master", "re_review"})
MASTER_CLEAN = UpstreamMerge(clean=True, conflicted=(), head=NEW)


def run(coroutine_function, *args):
    return anyio.run(coroutine_function, *args)


class _World(FakeLauncher):
    """Runs `agentic land` and `agentic merge` in-process the way their verbs would, and
    records the `agentic resume` the landing asks for instead of running it."""

    def __init__(self, ports, tmp_path):
        super().__init__()
        self.ports, self.tmp_path = ports, tmp_path
        self.resumed: list[int] = []
        self.merged: list[merge.MergeResult] = []
        self.landed: list[landing.LandResult] = []

    def resume(self, number: int) -> int:
        self.resumed.append(number)
        return 777

    def spawn(self, argv):
        pid = super().spawn(argv)
        task_id, upstream = f"GH-{argv[3]}", Upstream("origin/master", MASTER, None)
        if argv[1] == "land":
            self.landed.append(
                landing.land(
                    task_id,
                    _cfg(self.tmp_path),
                    self.ports,
                    _HeldLocks(),
                    upstream,
                    frozenset(),
                    lambda line: None,
                    clock=lambda: "2026-10-14T00:00:00+00:00",
                    live=LIVE,
                    resume=self.resume,
                )
            )
        elif argv[1] == "merge":
            self.merged.append(
                merge.merge_task(
                    task_id,
                    int(argv[3]),
                    _cfg(self.tmp_path),
                    self.ports,
                    upstream,
                    frozenset(),
                    lambda line: None,
                )
            )
        return pid

    def tick(self) -> list[str]:
        return _round(
            _cfg(self.tmp_path),
            self.ports,
            MemoryRunRegistry(),
            self,
            ESCALATED_AT,
            live_steps=LIVE,
        )


def _diffs(ports, *, merged: tuple[str, ...], own: tuple[str, ...]) -> None:
    """What `changed_paths` answers: the paths the merge of master brought in (between the
    reviewed head and the synced one), the branch's own (from its base to the reviewed
    head), and their union for the whole pull request."""

    def changed(base: str, head: str) -> tuple[str, ...]:
        if base == HEAD and head == NEW:
            return merged
        if head == HEAD:
            return own
        return (*own, *merged)

    ports.workspace.changed_paths = changed


def _document(ports, number: int = 5) -> TaskState:
    return ports.documents.load(f"GH-{number}")


def _unit(ports, number: int = 5) -> dict:
    return _document(ports, number)["subtasks"]["only"]


def _reviewed_again(ports, head: str = NEW, number: int = 5) -> None:
    """What the technical stage leaves after the resume the landing asked for: the unit
    approved over the head the pull request now has."""
    document = _document(ports, number)
    unit = {**document["subtasks"]["only"], "status": "approved", "reviewed_head": head}
    ports.documents.save({**document, "subtasks": {"only": unit}})
    ports.tracker.head_oid = head


def _redeliver(ports, tmp_path, number: int = 5) -> TaskState:
    state = {"title": "t", "description": "d", **_document(ports, number)}
    return run(deliver, state, _cfg(tmp_path), ports)


def _behind(tmp_path, **document):
    """Task 5, approved at `HEAD` with the checks out of attempts on it: master moved and
    `attempts_exhausted` asks for a `sync_master`."""
    ports = _ports(5, checks=False, merge_attempts={"head": HEAD, "count": 3}, **document)
    ports.workspace.upstream_merges[MASTER] = MASTER_CLEAN
    return ports, _World(ports, tmp_path)


# ---- the five behaviours -----------------------------------------------------------------


def test_a_branch_behind_master_clean_and_disjoint_carries_the_verdict_and_merges(tmp_path):
    ports, world = _behind(tmp_path)
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))

    world.tick()

    assert [r.step for r in world.landed] == ["sync_master"]
    unit = _unit(ports)
    assert unit["reviewed_head"] == NEW
    assert unit["reviewed_head_carried_from"] == HEAD
    assert ports.workspace.pushed  # the synced branch is on the remote
    assert world.resumed == []  # nothing reopened: the verdict carried
    [entry] = _document(ports)["landing"]
    assert (entry["head"], entry["step"]) == (HEAD, "sync_master")
    assert entry["resolved_at"]

    ports.tracker.head_oid = NEW  # what GitHub reports once the push is there
    world.tick()

    assert [m.kind for m in world.merged] == ["merged"]
    assert ports.tracker.merge_calls == [(URL.format(5), NEW)]


def test_a_clean_but_overlapping_sync_is_re_reviewed_re_delivered_and_merged(tmp_path):
    ports, world = _behind(tmp_path)
    _diffs(ports, merged=("feature.py",), own=("feature.py",))

    world.tick()

    assert world.resumed == [5]
    document = _document(ports)
    assert document["status"] == "developing"
    assert _unit(ports)["status"] == "delivered"  # a review alone, no Developer pass
    assert "reviewed_head_carried_from" not in _unit(ports)
    assert rules.resume_target(document) == "integrate"
    assert document["pull_request"] == URL.format(5)

    # The resumed review approves the merged head and the delivery goes under the same PR.
    _reviewed_again(ports)
    pushes = len(ports.workspace.pushed)
    patch = _redeliver(ports, tmp_path)

    assert patch["pull_request"] == URL.format(5)
    assert ports.tracker.pull_requests == []  # no second pull request
    assert len(ports.workspace.pushed) == pushes + 1
    [entry] = _document(ports)["landing"]
    assert entry["resolved_at"] and "cost_usd" in entry

    world.tick()

    assert [m.kind for m in world.merged] == ["merged"]
    assert ports.tracker.merge_calls == [(URL.format(5), NEW)]


def test_a_conflicting_sync_gives_the_conflict_to_a_developer_pass_then_merges(tmp_path):
    ports = _ports(
        5,
        checks=False,
        merge_checks={"head": HEAD, "master": MASTER, "passed": False, "failed_step": "conflict"},
    )
    ports.workspace.upstream_merges[MASTER] = UpstreamMerge(
        clean=False, conflicted=("shared.py",), head=HEAD
    )
    world = _World(ports, tmp_path)

    world.tick()

    assert [r.step for r in world.landed] == ["sync_master"]
    unit = _unit(ports)
    assert unit["status"] == "returned"
    assert "shared.py" in unit["reason"]  # the conflict is the Developer's input
    assert unit["change_category"] == "integration-crosscut"
    assert world.resumed == [5]
    assert rules.dispatched_subtasks(_document(ports)) == ["only"]
    assert not _document(ports).get("returns")  # the pair's counter is untouched
    # While the pass runs the poller leaves the task alone.
    spawned = len(world.spawned)
    world.tick()
    assert len(world.spawned) == spawned

    _reviewed_again(ports)
    _redeliver(ports, tmp_path)
    world.tick()

    assert [m.kind for m in world.merged] == ["merged"]


def test_a_head_pushed_by_somebody_else_is_re_reviewed_and_never_carried(tmp_path):
    ports = _ports(5)
    ports.tracker.head_oid = FOREIGN
    world = _World(ports, tmp_path)

    world.tick()

    assert [r.step for r in world.landed] == ["re_review"]
    unit = _unit(ports)
    assert unit["status"] == "delivered"
    assert unit["reviewed_head"] == HEAD  # the verdict stays on the head it was given on
    assert "reviewed_head_carried_from" not in unit
    assert ports.workspace.fetched_commits[0] == FOREIGN  # the foreign head is brought in
    assert world.resumed == [5]
    assert world.merged == []


def test_a_guarded_path_pull_request_merges_and_the_merge_records_the_guarded_paths(tmp_path):
    ports, world = _behind(tmp_path)
    guarded = (".agentic/stack.yaml", "feature.py")
    _diffs(ports, merged=("upstream.py",), own=guarded)

    world.tick()  # sync: carried
    ports.tracker.head_oid = NEW
    world.tick()  # merge

    assert [m.kind for m in world.merged] == ["merged"]
    assert _document(ports)["auto_merge_guarded"] == [".agentic/stack.yaml"]


# ---- deliver ------------------------------------------------------------------------------


def _task(**more) -> TaskState:
    return {
        "id": "GH-5",
        "title": "t",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
        **more,
    }


def test_the_body_of_a_delivery_leads_with_the_guarded_changes(tmp_path):
    ports = memory_ports()
    ports.workspace.diff_paths = (".agentic/stack.yaml", "CODING_STANDARDS.md", "src/a.py")
    run(deliver, _task(), _cfg(tmp_path), ports)
    body = ports.tracker.pull_requests[0][2]
    assert body.startswith("## Guarded changes")
    assert "- `.agentic/stack.yaml`" in body and "- `CODING_STANDARDS.md`" in body
    assert "src/a.py" not in body


def test_a_delivery_without_guarded_paths_has_the_body_it_always_had(tmp_path):
    ports = memory_ports()
    ports.workspace.diff_paths = ("src/a.py", ".agentic/tasks/GH-5.md")  # the task's own record
    run(deliver, _task(), _cfg(tmp_path), ports)
    body = ports.tracker.pull_requests[0][2]
    assert body == "## PM\n\n### Consolidated definition\n\nSix numbers."


def test_a_delivery_that_cannot_read_the_diff_opens_the_pull_request_without_the_preface(
    tmp_path,
):
    ports = memory_ports()

    def unreadable(base, head):
        raise GitError("no such revision")

    ports.workspace.changed_paths = unreadable
    run(deliver, _task(), _cfg(tmp_path), ports)
    assert "Guarded" not in ports.tracker.pull_requests[0][2]


def test_a_task_with_a_pull_request_is_re_delivered_by_a_push_alone(tmp_path):
    ports = memory_ports()
    entry = {"head": HEAD, "step": "sync_master", "refusal": "x", "at": "t", "cost_base": 1.0}
    state = _task(pull_request=URL.format(5), landing=[entry], cost_usd=3.5)
    patch = run(deliver, state, _cfg(tmp_path), ports)
    assert patch["pull_request"] == URL.format(5)
    assert ports.tracker.pull_requests == []
    assert ports.workspace.pushed
    [closed] = ports.documents.load("GH-5")["landing"]
    assert closed["resolved_at"]
    assert closed["cost_usd"] == pytest.approx(2.5)


# ---- resume_target ------------------------------------------------------------------------


def _with_pr(status: str, *, landing_entries=None) -> TaskState:
    state: TaskState = {
        "id": "GH-5",
        "status": "ready_for_review",
        "pull_request": URL.format(5),
        "subtasks": {"only": {"id": "only", "status": status}},
    }
    return {**state, "landing": landing_entries} if landing_entries is not None else state


OPEN = [{"head": HEAD, "step": "re_review", "refusal": "not_approved", "at": "t"}]


def test_a_reopened_task_with_a_pull_request_resumes_into_the_fan_in():
    assert rules.resume_target(_with_pr("returned", landing_entries=OPEN)) == "integrate"
    assert rules.resume_target(_with_pr("delivered", landing_entries=OPEN)) == "integrate"


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(_with_pr("delivered"), id="delivered_no_landing"),
        pytest.param(_with_pr("approved"), id="approved_no_landing"),
        pytest.param(
            _with_pr("returned", landing_entries=[{**OPEN[0], "resolved_at": "t2"}]),
            id="landing_resolved",
        ),
        pytest.param(_with_pr("approved", landing_entries=OPEN), id="nothing_reopened"),
    ],
)
def test_a_task_with_a_pull_request_and_no_open_landing_still_has_nowhere_to_go(state):
    assert rules.resume_target(state) is None


# ---- reopen -------------------------------------------------------------------------------


def _planned() -> TaskState:
    return {
        "id": "GH-5",
        "title": "t",
        "status": "ready_for_review",
        "pull_request": URL.format(5),
        "changes_requested": {"Tech Lead→Developer:a": 1},
        "change_history": [
            {"pair": "Tech Lead→Developer:a", "category": "bug", "reason": "earlier"}
        ],
        "subtasks": {
            "a": {"id": "a", "status": "approved", "reviewed_head": HEAD},
            "integration": {
                "id": "integration",
                "status": "approved",
                "reviewed_head": HEAD,
                "depends_on": ["a"],
            },
        },
    }


def test_reopen_returns_units_for_a_developer_pass_and_sends_the_integration_back():
    state = _planned()
    reopened = landing_sync.reopen(
        state, ["a"], "conflict in x.py", "integration-crosscut", "returned"
    )

    assert reopened["status"] == "developing"
    assert reopened["subtasks"]["a"]["status"] == "returned"
    assert reopened["subtasks"]["a"]["reason"] == "conflict in x.py"
    assert reopened["subtasks"]["integration"]["status"] == "pending"
    assert reopened["pull_request"] == state["pull_request"]
    assert rules.dispatched_subtasks(reopened) == ["a"]


def test_reopen_for_a_review_alone_marks_the_unit_delivered_without_a_reason():
    reopened = landing_sync.reopen(
        _planned(), ["integration"], "review it", "integration-crosscut", "delivered"
    )
    assert reopened["subtasks"]["integration"]["status"] == "delivered"
    assert "reason" not in reopened["subtasks"]["integration"]
    assert reopened["subtasks"]["a"]["status"] == "approved"


def test_a_landing_reopen_is_in_the_history_by_landing_and_never_a_return():
    state = _planned()
    reopened = landing_sync.reopen(state, ["a"], "why", "integration-crosscut", "returned")

    assert reopened["changes_requested"] == state["changes_requested"]  # `returns` unchanged
    assert reopened["change_history"][:1] == state["change_history"]
    [entry] = reopened["change_history"][1:]
    assert entry["by"] == "landing" and entry["category"] == "integration-crosscut"
    # What reads the history does not take it for a return somebody made.
    assert metrics.changes_by_category(reopened) == {"bug": 1}
    assert metrics.change_categories({"change_history": [entry]}) == []
    assert metrics.invocation_returns_by_category({"change_history": [entry]}) == {}
    assert playbook.accountable(entry, "feature") is None


def test_the_same_entry_without_the_landing_mark_is_still_a_return_somebody_made():
    entry = {"pair": "Tech Lead→Developer:a", "category": "integration-crosscut", "reason": "r"}
    assert metrics.changes_by_category({"change_history": [entry]}) == {"integration-crosscut": 1}
    assert playbook.accountable(entry, "feature") == "tech_lead"


# ---- spawn_and_mark (GH-242) ---------------------------------------------------------------


class _Resumer:
    def __init__(self, pid: int | None) -> None:
        self.pid, self.numbers = pid, []

    def __call__(self, number: int) -> int | None:
        self.numbers.append(number)
        return self.pid


class _Spawning:
    """The part of a landing context `spawn_and_mark` reads."""

    def __init__(self, ports, resume, log=lambda line: None) -> None:
        self.task_id, self.ports, self.resume, self.log = "GH-5", ports, resume, log


def _on_disk(tmp_path):
    """Task 5 saved the way `_reopened` saves it, on the real filesystem adapter: a block
    `landing` the writer re-rendered, with the entry's cost base."""
    ports = memory_ports(documents=FilesystemTaskDocuments(tmp_path))
    entry = {"head": HEAD, "step": "re_review", "refusal": "head_not_reviewed", "at": "t"}
    marked = landing_rules.with_cost_base([entry], HEAD, "re_review", 3.5)
    ports.documents.save({"id": "GH-5", "title": "t", "description": "d", "landing": marked})
    return ports


def test_a_spawn_that_answers_a_pid_is_counted_on_the_last_entry_and_keeps_the_cost_base(
    tmp_path,
):
    ports = _on_disk(tmp_path)
    resumer = _Resumer(4242)

    pid = landing_sync.spawn_and_mark(
        _Spawning(ports, resumer), lambda: "2026-10-14T12:00:00+00:00"
    )

    assert (pid, resumer.numbers) == (4242, [5])
    [entry] = ports.documents.load("GH-5")["landing"]
    assert entry["resume_spawns"] == 1
    assert entry["resume_spawned_at"] == "2026-10-14T12:00:00+00:00"
    assert entry["cost_base"] == 3.5

    landing_sync.spawn_and_mark(_Spawning(ports, resumer), lambda: "2026-10-14T12:10:00+00:00")
    [entry] = ports.documents.load("GH-5")["landing"]
    assert entry["resume_spawns"] == 2
    assert entry["resume_spawned_at"] == "2026-10-14T12:10:00+00:00"


@pytest.mark.parametrize("resume", [None, _Resumer(None)], ids=["no_resumer", "no_pid"])
def test_a_spawn_that_answers_no_pid_writes_no_counter(tmp_path, resume):
    ports = _on_disk(tmp_path)
    before = ports.documents.load("GH-5")

    assert landing_sync.spawn_and_mark(_Spawning(ports, resume)) is None

    assert ports.documents.load("GH-5") == before


def test_a_reopened_landing_is_resumed_through_spawn_and_mark(tmp_path):
    ports = _ports(5)
    ports.tracker.head_oid = FOREIGN
    world = _World(ports, tmp_path)

    world.tick()

    assert world.resumed == [5]
    entry = _document(ports)["landing"][-1]
    assert entry["resume_spawns"] == 1 and entry["resume_spawned_at"]
    assert "cost_base" in entry


def test_a_reopened_landing_whose_resume_answers_no_pid_counts_nothing(tmp_path):
    ports = _ports(5)
    ports.tracker.head_oid = FOREIGN
    world = _World(ports, tmp_path)
    world.resume = lambda number: None

    world.tick()

    entry = _document(ports)["landing"][-1]
    assert "resume_spawns" not in entry and "resume_spawned_at" not in entry
    assert "cost_base" in entry
