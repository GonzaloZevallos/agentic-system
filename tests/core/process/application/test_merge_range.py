"""GH-547: the merge gate judges trust on what the merge brings in, `merge-base(head, master)
...head`, read once per verb call, and reads `ci_dispatch` at master. A task that merged master
after it started must not be judged on master's own later commits. Every test drives
`merge.merge_task` over the in-memory ports; `_ranges` scripts the diff per range, so the range
the verb reads decides, not a single answer for every range."""

from __future__ import annotations

import pytest

from agentic.core.process.application import landing_plan
from agentic.core.process.domain.ci import CiReport
from agentic.core.process.domain.presubmit import CiDispatch
from tests.core.process.application.test_merge_ci import _ago, _check, _doc, _report, _world
from tests.core.process.application.test_merge_ci import _run as _ci_run
from tests.core.process.application.test_merge_ci_run import REF, _Clock
from tests.core.process.application.test_merge_ci_run import _call as _fresh_call
from tests.core.process.application.test_merge_ci_run import _world as _fresh_world
from tests.core.process.application.test_merge_gain import HEAD as GAIN_HEAD
from tests.core.process.application.test_merge_gain import INERT, NEW, OLD, World, _record
from tests.core.process.application.test_poll_merge import HEAD, MASTER
from tests.fakes import MERGE_BASE

TASK_BASE = "f" * 40
WORKFLOW = ".github/workflows/ci.yml"
STACK = ".agentic/stack.yaml"
DISPATCH = CiDispatch("ci.yml", "sha")


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


def _ranges(workspace, ranges: dict[tuple[str, str], tuple[str, ...]]) -> None:
    """`changed_paths` answers only the ranges scripted; any other range read is a failure."""

    def changed_paths(base: str, head: str) -> tuple[str, ...]:
        workspace.diff_reads.append((base, head))
        return ranges[(base, head)]

    workspace.changed_paths = changed_paths


def _long_task(ports, task: str, branch_head: str, gained: tuple[str, ...], own=("src/a.py",)):
    """A task started at `TASK_BASE`, which merged master after `gained` landed on it: the diff
    from its base holds master's commits, the one from the merge base only its own."""
    ports.workspace.existing_branches[f"agentic/{task}"] = (branch_head, TASK_BASE)
    return {(TASK_BASE, branch_head): (*gained, *own), (MERGE_BASE, branch_head): own}


def test_a_long_task_that_merged_masters_ci_change_is_judged_on_its_own_range(tmp_path):
    ports, clock = _fresh_world(), _Clock()
    _ranges(ports.workspace, _long_task(ports, "GH-5", HEAD, (WORKFLOW,)))
    ports.stack.declared_ci_dispatch_at[MASTER] = DISPATCH

    result = _fresh_call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert ports.workspace.ci_ref_calls == [("publish", REF)]  # trusted: a branch push, no dispatch
    assert ports.workspace.diff_reads == [(MERGE_BASE, HEAD)]
    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]


def test_a_long_task_is_not_refused_for_a_dispatch_its_old_base_did_not_declare(tmp_path):
    """The dispatch is read at master: a stack the task base predates, or the head's own, names
    none, and master's is the one that counts."""
    ports = _world(_report(_check()))
    _ranges(ports.workspace, _long_task(ports, "GH-5", HEAD, (WORKFLOW,)))
    ports.stack.declared_ci_dispatch_at[MASTER] = DISPATCH

    result, _ = _ci_run(ports, tmp_path)

    assert result.reason != "ci_untrusted_no_dispatch"


def test_a_head_that_changes_the_ci_itself_is_still_untrusted_without_masters_dispatch(tmp_path):
    ports = _world(_report(_check()))
    _ranges(ports.workspace, {(MERGE_BASE, HEAD): (WORKFLOW, "src/a.py")})
    ports.workspace.existing_branches["agentic/GH-5"] = (HEAD, TASK_BASE)
    # Declared at the task base and at the head, never at master: neither may decide.
    ports.stack.declared_ci_dispatch_at[TASK_BASE] = DISPATCH
    ports.stack.declared_ci_dispatch_at[HEAD] = DISPATCH

    result, _ = _ci_run(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "ci_untrusted_no_dispatch")
    assert ports.workspace.diff_reads == [(MERGE_BASE, HEAD)]


@pytest.mark.parametrize(
    ("dispatch", "kind", "reason", "publish"),
    [
        (DISPATCH, "stopped", "ci_wait", ("publish_dispatch", "GH-5")),
        (None, "refused", "ci_untrusted_no_dispatch", None),
    ],
    ids=["dispatch_declared", "no_dispatch"],
)
def test_a_short_task_decides_as_it_did_when_the_merge_base_is_its_base(
    tmp_path, dispatch, kind, reason, publish
):
    ports, clock = _fresh_world(), _Clock()
    ports.workspace.existing_branches["agentic/GH-5"] = (HEAD, TASK_BASE)
    ports.workspace.merge_bases[(HEAD, MASTER)] = TASK_BASE
    _ranges(ports.workspace, {(TASK_BASE, HEAD): (WORKFLOW,)})
    if dispatch is not None:
        ports.stack.declared_ci_dispatch_at[MASTER] = dispatch
        ports.stack.declared_ci_dispatch_at[TASK_BASE] = dispatch

    result = _fresh_call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == (kind, reason)
    assert ports.workspace.diff_reads == [(TASK_BASE, HEAD)]
    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]
    assert ports.workspace.ci_ref_calls == ([publish] if publish else [])


def test_an_unreadable_merge_base_stops_the_merge_and_never_falls_back_to_the_task_base(tmp_path):
    ports = _world(_report(_check()))
    ports.workspace.existing_branches["agentic/GH-5"] = (HEAD, TASK_BASE)
    ports.workspace.merge_base_error = "git merge-base failed: no common ancestor"

    result, _ = _ci_run(ports, tmp_path)

    assert result.kind == "stopped"
    assert result.reason is not None
    assert result.reason.startswith("diff_unreadable: git merge-base failed")
    assert ports.workspace.diff_reads == []
    assert ports.tracker.merge_calls == [] and ports.tracker.comments == []
    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]


def _gain_world(touched_at_base: tuple[str, ...], own: tuple[str, ...]) -> World:
    world = World(_record(OLD), inert=INERT)
    world.ports.workspace.merge_bases[(GAIN_HEAD, NEW)] = MERGE_BASE
    _ranges(
        world.ports.workspace,
        {("taskbase1", GAIN_HEAD): touched_at_base, (MERGE_BASE, GAIN_HEAD): own},
    )
    return world


def test_masters_own_stack_change_no_longer_forces_the_suite_on_a_long_task():
    world = _gain_world((STACK, "src/app.py"), ("src/app.py",))

    result = world.merge()

    assert result.kind == "merged"
    assert world.ports.workspace.detached == [] and world.ports.commands.ran == []
    assert world.doc()["auto_merge_inert_from"] == [OLD]
    assert world.ports.workspace.diff_reads == [(MERGE_BASE, GAIN_HEAD)]


def test_a_stack_change_of_the_task_itself_still_forces_the_suite():
    world = _gain_world(("src/app.py",), (STACK, "src/app.py"))

    world.merge()

    assert world.ports.workspace.detached != []
    assert "auto_merge_inert_from" not in world.doc()


def test_the_guarded_list_leaves_out_the_paths_master_brought_in():
    clean = _gain_world((WORKFLOW, STACK, "src/app.py"), ("src/app.py",))
    clean.ports.documents.save({**clean.doc(), "merge_checks": _record(NEW)})
    own = _gain_world(("src/app.py",), (WORKFLOW, "src/app.py"))
    own.ports.documents.save({**own.doc(), "merge_checks": _record(NEW)})

    assert clean.merge().kind == "merged"
    assert own.merge().kind == "merged"

    assert "auto_merge_guarded" not in clean.doc()
    assert own.doc()["auto_merge_guarded"] == [WORKFLOW]


def test_names_declared_at_the_task_base_that_master_renamed_are_refused_as_unknown(tmp_path):
    """The known limit (ADR-0080, GH-547): `ci:` names are read at the task base while the
    dispatched workflow is master's, so a job master renamed leaves the long task refused."""
    report = CiReport(HEAD, "master", MASTER, _ago(300), (_check("build"),), runs_completed=True)
    ports = _world(
        report,
        ci_required=["web"],
        pushed_head=HEAD,
        pushed_at=_ago(120),
    )
    _ranges(ports.workspace, _long_task(ports, "GH-5", HEAD, (WORKFLOW,)))
    ports.stack.declared_ci_dispatch_at[MASTER] = DISPATCH

    result, _ = _ci_run(ports, tmp_path)

    assert (result.kind, result.reason) == ("refused", "ci_unknown_names")
    assert _doc(ports).get("auto_merge_via") is None
