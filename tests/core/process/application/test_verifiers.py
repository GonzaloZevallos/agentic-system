"""`verifiers.verify` (GH-469): the verifiers of the integrated head run together over one
checkout and come back as one patch. Fake runs stand for the four nodes, a workspace counts the
checkouts it hands out, and the document port shows what was persisted."""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from agentic.core.process.application.ports import Checkout, CommandResult, RunStopped
from agentic.core.process.application.verifiers import verify
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import INTEGRATION, WAITING_ON_HUMAN, apply
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands, MemoryDocuments, MemoryStack, MemoryWorkspace, memory_ports

pytestmark = pytest.mark.anyio

CFG = Configuration(product_dir=MemoryWorkspace.ROOT)
HEAD = "a" * 40
OTHER = "b" * 40
FILES = [{"capability": "checks", "content": "# Checks\n"}]
ORDER = ("full_checks", "copy_review", "final_validation", "qa_performance")


class Workspace(MemoryWorkspace):
    def full_head(self, path):
        return HEAD


def state_of(due: tuple[str, ...] = ORDER, **more: Any) -> Any:
    state: Any = {
        "id": "GH-1",
        "title": "t",
        "description": "d",
        "type": "feature",
        "task_head": HEAD,
        "sections": {},
        "runs": [{"role": "Tech Lead", "section": rules.review_section(INTEGRATION), "version": 1}],
        "subtasks": {
            INTEGRATION: {
                "id": INTEGRATION,
                "title": "i",
                "brief": "b",
                "seams": [],
                "depends_on": [],
                "status": "approved",
                "reviewed_head": HEAD,
                "reason": "",
            },
            "api": {"status": "approved"},
        },
        **more,
    }
    if "copy_review" in due:
        state["flags"] = {"touches_copy": True}
    if "final_validation" in due:
        state["flags"] = {**state.get("flags", {}), "touches_ui": True}
    if "qa_performance" in due:
        state["performance"] = [{"scenario": "load"}]
    if "full_checks" not in due:
        state["full_checks"] = {"head": HEAD, "passed": True}
    return state


def approved(name: str, cost: float = 1.0) -> dict[str, Any]:
    return {"cost_usd": cost, "runs": [{"role": name, "section": name, "version": 1}]}


def returned(state: Any, name: str, reason: str, cost: float = 1.0) -> dict[str, Any]:
    target = state["subtasks"][INTEGRATION]
    return {
        **approved(name, cost),
        "subtasks": {
            INTEGRATION: {**target, "status": "returned", "reason": reason, "returned_by": name}
        },
        "findings": [{"subtask": INTEGRATION, "claim": reason, "location": name}],
    }


def escalated(text: str, cost: float = 1.0) -> dict[str, Any]:
    return {**approved("x", cost), **rules.escalation("returns", text), "status": WAITING_ON_HUMAN}


class Fakes:
    """One callable per verifier: what it returns, what it saw, when it started and ended."""

    def __init__(self, patches: dict[str, Any], *, delays: dict[str, float] | None = None):
        self.patches = patches
        self.delays = delays or {}
        self.calls: list[str] = []
        self.finished: list[str] = []
        self.seen: dict[str, tuple[Any, Checkout]] = {}

    @property
    def run(self) -> dict[str, Any]:
        return {name: self._one(name) for name in self.patches}

    def _one(self, name: str):
        async def run(state, checkout):
            self.calls.append(name)
            self.seen[name] = (state, checkout)
            await anyio.sleep(self.delays.get(name, 0.0))
            outcome = self.patches[name]
            self.finished.append(name)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return run


def ports_of(*, ci: tuple[str, ...] = (), workspace: MemoryWorkspace | None = None):
    stack = MemoryStack(checks={"tests": "pytest"}, delivery_checks=("tests",), ci=ci)
    return memory_ports(stack=stack, commands=MemoryCommands(), workspace=workspace or Workspace())


def approving(names=ORDER) -> dict[str, Any]:
    return {name: approved(name) for name in names}


async def test_every_due_verifier_runs_on_the_same_checkout_and_the_deltas_fold_in_order():
    state, ports = state_of(), ports_of()
    fakes = Fakes(approving())
    patch = await verify(state, CFG, ports, fakes.run)
    assert sorted(fakes.calls) == sorted(ORDER)
    assert [r["role"] for r in patch["runs"]] == list(ORDER)
    assert patch["cost_usd"] == 4.0
    assert "returned_by" not in patch.get("subtasks", {}).get(INTEGRATION, {})
    assert ports.documents.last == apply(state, patch)


async def test_only_the_due_verifiers_run():
    due = ("copy_review", "qa_performance")
    fakes = Fakes(approving(due))
    patch = await verify(state_of(due), CFG, ports_of(), fakes.run)
    assert sorted(fakes.calls) == sorted(due)
    assert patch["cost_usd"] == 2.0


async def test_one_return_is_the_return_of_that_verifier_alone():
    state = state_of()
    patches = {**approving(), "qa_performance": returned(state, "qa_performance", "too slow")}
    patch = await verify(state, CFG, ports_of(), Fakes(patches).run)
    after = apply(state, patch)
    unit = after["subtasks"][INTEGRATION]
    assert unit["status"] == "returned" and unit["reason"] == "too slow"
    assert rules.returned_by_all(unit) == ("qa_performance",)
    assert patch["cost_usd"] == 4.0


async def test_two_returns_are_one_return_with_both_reasons_and_both_findings():
    state = state_of()
    patches = {
        **approving(),
        "copy_review": returned(state, "final_validation", "wording"),
        "qa_performance": returned(state, "qa_performance", "slow"),
    }
    patch = await verify(state, CFG, ports_of(), Fakes(patches).run)
    unit = apply(state, patch)["subtasks"][INTEGRATION]
    assert rules.returned_by_all(unit) == ("final_validation", "qa_performance")
    assert "wording" in unit["reason"] and "slow" in unit["reason"]
    assert [f["claim"] for f in patch["findings"]] == ["wording", "slow"]


async def test_a_return_and_a_limit_escalation_are_one_escalation_that_says_both():
    state = state_of()
    patches = {
        **approving(),
        "copy_review": returned(state, "final_validation", "wording"),
        "qa_performance": escalated("pair limit reached"),
    }
    patch = await verify(state, CFG, ports_of(), Fakes(patches).run)
    after = apply(state, patch)
    assert after["status"] == WAITING_ON_HUMAN and after["escalated"]
    assert "pair limit reached" in after["escalated"] and "wording" in after["escalated"]
    assert after["subtasks"][INTEGRATION]["status"] == "returned"


async def test_a_sibling_that_escalates_keeps_the_deltas_of_the_others():
    state = state_of()
    patches = {**approving(), "final_validation": escalated("stuck")}
    patch = await verify(state, CFG, ports_of(), Fakes(patches).run)
    assert patch["cost_usd"] == 4.0
    assert {r["role"] for r in patch["runs"]} >= {"copy_review", "qa_performance", "full_checks"}
    assert apply(state, patch)["status"] == WAITING_ON_HUMAN


async def test_a_sibling_that_raises_persists_the_others_and_the_error_propagates():
    state, ports = state_of(), ports_of()
    boom = RuntimeError("runner died")
    fakes = Fakes({**approving(), "copy_review": boom}, delays={"full_checks": 0.01})
    with pytest.raises(RuntimeError, match="runner died"):
        await verify(state, CFG, ports, fakes.run)
    saved = ports.documents.last
    assert saved["cost_usd"] == 3.0
    assert {r["role"] for r in saved["runs"][1:]} == {
        "full_checks",
        "final_validation",
        "qa_performance",
    }
    assert sorted(fakes.finished) == sorted(ORDER)  # none was cancelled


async def test_the_first_error_in_verifier_order_is_the_one_raised():
    patches = {
        **approving(),
        "qa_performance": KeyError("late"),
        "copy_review": ValueError("early"),
    }
    with pytest.raises(ValueError, match="early"):
        await verify(state_of(), CFG, ports_of(), Fakes(patches).run)


async def test_fakes_finishing_in_reverse_order_give_the_same_patch():
    state = state_of()
    patches = {
        **approving(),
        "copy_review": returned(state, "final_validation", "wording"),
        "qa_performance": returned(state, "qa_performance", "slow"),
    }
    forward = Fakes(
        patches, delays={"copy_review": 0.0, "final_validation": 0.01, "qa_performance": 0.02}
    )
    backward = Fakes(
        patches, delays={"copy_review": 0.02, "final_validation": 0.01, "qa_performance": 0.0}
    )
    first = await verify(state, CFG, ports_of(), forward.run)
    second = await verify(state, CFG, ports_of(), backward.run)
    assert forward.finished != backward.finished
    assert first == second


async def test_the_task_checkout_is_resolved_once_for_the_whole_set():
    ports = ports_of()
    fakes = Fakes(approving())
    await verify(state_of(), CFG, ports, fakes.run)
    assert ports.workspace.checkouts_made == ["agentic/GH-1"]
    assert len({id(seen[1]) for seen in fakes.seen.values()}) == 1
    assert ports.workspace.restored == []


async def test_a_dirty_tree_returns_the_suite_dirty_patch_and_starts_nothing():
    workspace = Workspace()
    workspace.uncommitted.add(MemoryWorkspace.ROOT / "GH-1")
    fakes = Fakes(approving())
    patch = await verify(state_of(), CFG, ports_of(workspace=workspace), fakes.run)
    assert fakes.calls == [] and patch["escalation_class"] == "setup"


async def test_a_setup_that_fails_on_a_new_checkout_escalates_and_starts_nothing():
    commands = MemoryCommands(**{"make env": [CommandResult(1, "no env")]})
    stack = MemoryStack(
        checks={"tests": "pytest"}, delivery_checks=("tests",), setup={"env": "make env"}
    )
    ports = memory_ports(stack=stack, commands=commands, workspace=Workspace())
    fakes = Fakes(approving())
    patch = await verify(state_of(), CFG, ports, fakes.run)
    assert fakes.calls == [] and patch["escalation_class"] == "setup"
    assert [command for _, command in commands.ran] == ["make env"]


async def test_a_stop_during_setup_discards_the_checkout_and_is_not_a_setup_failure():
    class Stopped(MemoryCommands):
        def run(self, cwd, command, timeout_s, *, env=None):
            raise RunStopped

    workspace = Workspace()
    stack = MemoryStack(
        checks={"tests": "pytest"}, delivery_checks=("tests",), setup={"env": "make env"}
    )
    ports = memory_ports(stack=stack, commands=Stopped(), workspace=workspace)
    fakes = Fakes(approving())
    with pytest.raises(RunStopped):
        await verify(state_of(), CFG, ports, fakes.run)
    assert fakes.calls == []
    assert workspace.discarded == [MemoryWorkspace.ROOT / "GH-1"]


# --- full_checks first or with the others -----------------------------------------------------


def speculated() -> dict[str, Any]:
    return {"ci_speculation": {"head": HEAD, "pushed_at": "2026-11-04T10:00:00+00:00"}}


async def test_a_red_suite_returns_before_any_role_starts():
    state = state_of(**speculated())
    red = returned(state, "full_checks", "check failed")
    fakes = Fakes({**approving(), "full_checks": red})
    patch = await verify(state, CFG, ports_of(ci=("check",)), fakes.run)
    assert fakes.calls == ["full_checks"]
    assert apply(state, patch)["subtasks"][INTEGRATION]["returned_by"] == "full_checks"


async def test_a_suite_that_runs_here_goes_first_and_alone_and_the_roles_see_its_result():
    state = state_of()
    suite = {**approved("full_checks"), "sections": {"Full checks": "Passed."}}
    fakes = Fakes({**approving(), "full_checks": suite})
    await verify(state, CFG, ports_of(), fakes.run)
    assert fakes.calls[0] == "full_checks"
    assert fakes.finished[0] == "full_checks"
    assert fakes.seen["copy_review"][0]["sections"]["Full checks"] == "Passed."


async def test_a_suite_that_waits_on_ci_starts_with_the_roles_before_any_of_them_ends():
    started: list[str] = []
    everyone = anyio.Event()
    fakes = Fakes(approving())
    inner = fakes.run

    def gated(name):
        async def run(state, checkout):
            started.append(name)
            if len(started) == len(ORDER):
                everyone.set()
            with anyio.fail_after(2):  # a suite run first and alone would wait here for ever
                await everyone.wait()
            return await inner[name](state, checkout)

        return run

    patch = await verify(state_of(), CFG, ports_of(ci=("check",)), {n: gated(n) for n in ORDER})
    assert patch["cost_usd"] == 4.0
    assert sorted(started) == sorted(ORDER)


async def test_a_stale_returned_with_does_not_outlive_a_lone_return():
    state = state_of()
    unit = state["subtasks"][INTEGRATION]
    unit |= {
        "status": "approved",
        "returned_by": "copy_review",
        "returned_with": ["qa_performance"],
    }
    patches = {**approving(), "qa_performance": returned(state, "qa_performance", "slow")}
    patch = await verify(state, CFG, ports_of(), Fakes(patches).run)
    assert rules.returned_by_all(apply(state, patch)["subtasks"][INTEGRATION]) == (
        "qa_performance",
    )


# --- the capability files final_validation approved with ---------------------------------------


def owing(parent: str = HEAD) -> dict[str, Any]:
    return {"pending_close_docs": {"parent": parent, "definitions": FILES}}


async def test_the_files_owed_on_the_reviewed_head_are_committed_when_nothing_returned():
    state, ports = state_of(), ports_of()
    patches = {**approving(), "final_validation": {**approved("final_validation"), **owing()}}
    patch = await verify(state, CFG, ports, Fakes(patches).run)
    assert len(ports.workspace.path_commits) == 1
    assert patch["pending_close_docs"] is None
    assert patch["close_docs"]["parent"] == HEAD


async def test_files_owed_on_another_head_are_dropped_without_a_commit():
    state, ports = state_of(("copy_review",), **owing(OTHER)), ports_of()
    patch = await verify(state, CFG, ports, Fakes(approving(("copy_review",))).run)
    assert ports.workspace.path_commits == []
    assert patch["pending_close_docs"] is None and "close_docs" not in patch


async def test_files_owed_with_nothing_else_due_are_committed_by_the_set_alone():
    state, ports = state_of((), **owing()), ports_of()
    fakes = Fakes({})
    patch = await verify(state, CFG, ports, fakes.run)
    assert fakes.calls == [] and len(ports.workspace.path_commits) == 1
    assert patch["pending_close_docs"] is None


async def test_nothing_due_and_nothing_owed_is_an_empty_patch_that_resolves_no_checkout():
    ports = ports_of()
    assert await verify(state_of(()), CFG, ports, {}) == {}
    assert ports.workspace.checkouts_made == []


async def test_a_return_clears_the_files_owed_and_commits_nothing():
    state, ports = state_of(), ports_of()
    patches = {
        **approving(),
        "final_validation": {**approved("final_validation"), **owing()},
        "qa_performance": returned(state, "qa_performance", "slow"),
    }
    patch = await verify(state, CFG, ports, Fakes(patches).run)
    assert ports.workspace.path_commits == []
    assert patch["pending_close_docs"] is None


async def test_pm_approving_while_a_sibling_escalates_leaves_the_files_owed_uncommitted():
    state, ports = state_of(), ports_of()
    patches = {
        **approving(),
        "final_validation": {**approved("final_validation"), **owing()},
        "copy_review": escalated("stuck"),
    }
    patch = await verify(state, CFG, ports, Fakes(patches).run)
    assert ports.workspace.path_commits == []
    assert patch["pending_close_docs"] == owing()["pending_close_docs"]


async def test_a_task_document_saved_by_the_set_is_the_state_with_the_patch_folded_in():
    state, ports = state_of(), ports_of()
    patch = await verify(state, CFG, ports, Fakes(approving()).run)
    assert isinstance(ports.documents, MemoryDocuments)
    assert ports.documents.last == apply(state, patch)
