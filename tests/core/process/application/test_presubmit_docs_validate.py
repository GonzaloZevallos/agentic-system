"""`CheckoutPresubmit` in docs `validate` mode (GH-603): the delivery gate restores what the
branch changed under `docs.paths` to master's side before the checks, and never aborts."""

from pathlib import Path

from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.docs import Docs
from tests.fakes import FakeCheckScheduler, MemoryCommands, MemoryStack, MemoryWorkspace

PATH = Path("/memory/worktrees/GH-603.wiring")


def gate(workspace: MemoryWorkspace, docs: Docs | None) -> CheckoutPresubmit:
    workspace.moved.add(PATH)
    ports = type(
        "Ports",
        (),
        {
            "workspace": workspace,
            "commands": MemoryCommands(),
            "check_scheduler": FakeCheckScheduler(),
            "stack": MemoryStack(),
        },
    )()
    return CheckoutPresubmit(
        ports, PATH, "base", {"tests": "pytest"}, 30.0, priority=CheckPriority.SUBTASK, docs=docs
    )


VALIDATE = Docs(generate="make docs", paths=("docs/api/**",), mode="validate")


def test_validate_restores_the_owned_drift_from_the_merge_base_and_records_it():
    workspace = MemoryWorkspace()
    workspace.remote_tips["master"] = "tip"
    workspace.owned_drifts[("head1", "tip")] = ("docs/api/a.md",)
    presubmit = gate(workspace, VALIDATE)
    assert presubmit() is None
    assert workspace.owned_drift_asks == [("head1", "tip", (":(glob)docs/api/**",))]
    [(_, _source, paths, message)] = workspace.restore_owned_calls
    assert paths == ("docs/api/a.md",) and "docs/api/a.md" in message
    assert "docs/api/a.md" in (presubmit.markdown() or "")
    assert presubmit.head == "head2"  # the head after the restore is the one recorded


def test_validate_with_nothing_drifted_restores_nothing():
    workspace = MemoryWorkspace()
    workspace.remote_tips["master"] = "tip"
    assert gate(workspace, VALIDATE)() is None
    assert workspace.restore_owned_calls == []


def test_a_failing_restore_never_aborts_the_delivery_and_leaves_one_line():
    workspace = MemoryWorkspace()
    workspace.remote_tips["master"] = "tip"
    workspace.owned_drifts[("head1", "tip")] = ("docs/api/a.md",)
    workspace.restore_owned_error = "boom"
    presubmit = gate(workspace, VALIDATE)
    assert presubmit() is None
    assert "not restored" in (presubmit.markdown() or "") and "boom" in (presubmit.markdown() or "")


def test_an_unreadable_master_restores_nothing_and_says_so():
    workspace = MemoryWorkspace()
    workspace.remote_tip_error = "offline"
    presubmit = gate(workspace, VALIDATE)
    assert presubmit() is None
    assert workspace.restore_owned_calls == [] and workspace.owned_drift_asks == []
    assert "not restored" in (presubmit.markdown() or "")


def test_commit_mode_and_no_block_reach_no_new_operation():
    for docs in (Docs(generate="make docs", paths=("docs/api/**",)), None):
        workspace = MemoryWorkspace()
        assert gate(workspace, docs)() is None
        assert workspace.remote_tip_calls == [] and workspace.owned_drift_asks == []
        assert workspace.restore_owned_calls == []
