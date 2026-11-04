"""Where `full_checks` runs when the verifier set starts (GH-469): `full_checks_mode` is the one
decision between the suite on this machine (`local`), a CI run a review already pushed for this
very head (`consumable`) and a CI verdict still to wait for (`wait`) -- the node reads the same
decision, so the two cannot drift. A `checkout` handed in is used as is: no second resolution,
no second clean check."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic.core.process.application.full_checks import full_checks, full_checks_mode
from agentic.core.process.application.ports import Checkout, GitError
from agentic.core.process.domain.ci import CiCheck, CiReport
from agentic.core.process.domain.presubmit import CiDispatch
from agentic.core.process.domain.task import INTEGRATION
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryStack, MemoryTracker, MemoryWorkspace, memory_ports

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT, check_timeout_s=100.0, ci_start_grace_s=30.0)
HEAD = "a" * 40
OTHER = "b" * 40
CHECKOUT = Checkout("agentic/GH-1", MemoryWorkspace.ROOT / "GH-1", "base0")


class Workspace(MemoryWorkspace):
    def __init__(self, paths: tuple[str, ...] | None = ()) -> None:
        super().__init__()
        self.paths = paths

    def full_head(self, path):
        return HEAD

    def changed_paths(self, base, head):
        if self.paths is None:
            raise GitError("no diff")
        return self.paths


def state_of(speculated: str | None = HEAD) -> dict:
    state = {
        "id": "GH-1",
        "title": "Mode",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtasks": {
            INTEGRATION: {
                "id": INTEGRATION,
                "title": "integration",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "approved",
                "branch": "agentic/GH-1",
                "base": "b",
            }
        },
        "task_head": HEAD,
    }
    if speculated is not None:
        state["ci_speculation"] = {"head": speculated, "pushed_at": datetime.now(UTC).isoformat()}
    return state


def ports_of(*, ci=("check",), paths: tuple[str, ...] | None = (), dispatch=None, tracker=None):
    stack = MemoryStack(checks={"tests": "pytest"}, delivery_checks=("tests",), ci=ci)
    stack.declared_ci_dispatch = dispatch
    return memory_ports(
        stack=stack,
        commands=MemoryCommands(),
        workspace=Workspace(paths),
        tracker=tracker or MemoryTracker(),
    )


def mode(state, ports) -> str:
    return full_checks_mode(state, CFG, ports, CHECKOUT)


def test_a_product_without_ci_runs_the_suite_here():
    assert mode(state_of(), ports_of(ci=())) == "local"


def test_a_trusted_head_with_the_pushed_run_for_this_commit_consumes_it():
    assert mode(state_of(HEAD), ports_of()) == "consumable"


def test_a_trusted_head_with_nothing_pushed_waits_for_ci():
    assert mode(state_of(None), ports_of()) == "wait"


def test_a_trusted_head_whose_pushed_run_is_another_commit_waits_for_ci():
    assert mode(state_of(OTHER), ports_of()) == "wait"


def test_a_head_that_touches_the_ci_definition_runs_here_when_nothing_is_dispatched():
    assert mode(state_of(HEAD), ports_of(paths=(".github/workflows/ci.yml",))) == "local"


def test_a_head_whose_diff_cannot_be_read_runs_here():
    assert mode(state_of(HEAD), ports_of(paths=None)) == "local"


def test_an_untrusted_head_is_waited_for_on_the_dispatched_workflow_when_one_is_declared():
    dispatch = CiDispatch(workflow="ci.yml", sha_input="sha")
    ports = ports_of(paths=(".github/workflows/ci.yml",), dispatch=dispatch)
    assert mode(state_of(HEAD), ports) == "wait"


def test_the_mode_reads_the_tracker_and_pushes_nothing():
    tracker = MemoryTracker()
    ports = ports_of(tracker=tracker)
    mode(state_of(HEAD), ports)
    assert tracker.branch_reads == []
    assert ports.workspace.ci_ref_calls == []


GREEN = CiReport(HEAD, None, None, None, (CiCheck("check", "COMPLETED", "SUCCESS", "l"),))


class Tracker(MemoryTracker):
    def branch_ci(self, branch):
        self.branch_reads.append(branch)
        return GREEN


async def test_a_checkout_handed_in_is_not_resolved_again_nor_checked_clean():
    ports = ports_of(tracker=Tracker())
    ports.workspace.uncommitted.add(CHECKOUT.path)  # would escalate `dirty` if it were asked
    patch = await full_checks(state_of(None), CFG, ports, checkout=CHECKOUT)
    assert patch["full_checks"]["passed"] is True
    assert ports.workspace.checkouts_made == []
    assert "escalated" not in patch


async def test_without_a_checkout_the_node_resolves_one_and_refuses_a_dirty_tree_as_today():
    ports = ports_of(tracker=Tracker())
    ports.workspace.uncommitted.add(CHECKOUT.path)
    patch = await full_checks(state_of(None), CFG, ports)
    assert ports.workspace.checkouts_made == ["agentic/GH-1"]
    assert patch["escalation_class"] == "setup"
