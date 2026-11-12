"""GH-603, docs `validate` mode: the merges that bring branches together settle a conflict
confined to `docs.paths` from one source instead of handing it to a role. Two seams: the
`integrate` application function merging approved subtasks, and the `develop` dependency-merge
path. Commit mode, and every input the settlement must refuse, behave as before."""

from __future__ import annotations

import pytest

from agentic.core.process.application.develop import develop
from agentic.core.process.application.integrate import integrate_turn
from agentic.core.process.application.ports import MergeConflict
from agentic.core.process.domain.docs import Docs
from agentic.core.process.domain.task import INTEGRATION, DependencyState
from agentic.shared.configuration import Configuration
from tests.fakes import MERGE_BASE, MemoryStack, ScriptedRunner, delivered, memory_ports

pytestmark = pytest.mark.anyio

OWNED = "docs/api/a.md"
VALIDATE = Docs("make docs", ("docs/api/**",), mode="validate")
COMMIT = Docs("make docs", ("docs/api/**",))
TASK = {"id": "GH-12", "title": "Dashboard", "description": "d", "type": "feature"}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def approved(sid: str) -> dict:
    return {
        "id": sid,
        "title": sid,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "approved",
        "branch": f"agentic/GH-12.{sid}",
        "base": "base1",
    }


def _ports(docs: Docs | None, *paths: str, master: str | None = "tip", runner=None):
    ports = memory_ports(runner or ScriptedRunner(), stack=MemoryStack(docs=docs))
    workspace = ports.workspace
    workspace.conflicts.add("agentic/GH-12.api")
    workspace.conflict_paths["agentic/GH-12.api"] = paths
    if master is not None:
        workspace.remote_tips["master"] = master
    workspace.ancestries[(MERGE_BASE, MERGE_BASE)] = "same"
    return ports


def _state() -> dict:
    return {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}


# ---- integrate ---------------------------------------------------------------------------


def test_a_docs_only_conflict_merges_settled_from_one_source_with_no_integrator(cfg):
    ports = _ports(VALIDATE, OWNED)
    patch = integrate_turn(_state(), cfg, ports)
    [(_, branch, paths, source)] = ports.workspace.merge_settling_calls
    assert (branch, paths, source) == ("agentic/GH-12.api", (OWNED,), MERGE_BASE)
    assert patch["subtasks"][INTEGRATION]["status"] == "delivered"
    assert not patch["subtasks"][INTEGRATION].get("conflicts")


def test_a_mixed_conflict_is_handed_to_the_integrator_untouched(cfg):
    ports = _ports(VALIDATE, OWNED, "app.py")
    patch = integrate_turn(_state(), cfg, ports)
    assert ports.workspace.merge_settling_calls == []
    assert patch["subtasks"][INTEGRATION]["conflicts"] == ["agentic/GH-12.api"]


@pytest.mark.parametrize("docs", [COMMIT, None])
def test_commit_mode_and_no_block_never_retry_a_conflict(cfg, docs):
    ports = _ports(docs, OWNED)
    patch = integrate_turn(_state(), cfg, ports)
    assert ports.workspace.merge_settling_calls == []
    assert ports.workspace.remote_tip_calls == []
    assert patch["subtasks"][INTEGRATION]["conflicts"] == ["agentic/GH-12.api"]


def test_an_unreadable_master_or_source_leaves_the_conflict_to_the_integrator(cfg):
    for scenario in ("no tip", "tip error", "diverged"):
        ports = _ports(VALIDATE, OWNED, master=None if scenario == "no tip" else "tip")
        if scenario == "tip error":
            ports.workspace.remote_tip_error = "offline"
        if scenario == "diverged":
            ports.workspace.ancestries.clear()
        patch = integrate_turn(_state(), cfg, ports)
        assert ports.workspace.merge_settling_calls == []
        assert patch["subtasks"][INTEGRATION]["conflicts"] == ["agentic/GH-12.api"], scenario


def test_a_settling_that_the_adapter_refuses_stays_a_conflict(cfg):
    ports = _ports(VALIDATE, OWNED)
    ports.workspace.settling_refusals["agentic/GH-12.api"] = MergeConflict(("app.py",), "late")
    patch = integrate_turn(_state(), cfg, ports)
    assert len(ports.workspace.merge_settling_calls) == 1
    assert patch["subtasks"][INTEGRATION]["conflicts"] == ["agentic/GH-12.api"]


# ---- develop: the dependency merge --------------------------------------------------------


def _dependent():
    return {
        **TASK,
        "subtasks": {
            "domain": {**approved("domain"), "branch": "agentic/GH-12.api"},
            "api": {**approved("api"), "status": "pending", "depends_on": ["domain"]},
        },
        "subtask_id": "api",
    }


async def _develop(cfg, docs, *paths):
    runner = ScriptedRunner(developer=[delivered()])
    ports = _ports(docs, *paths, runner=runner)
    api = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(api.path)
    ports.workspace.dependency_states["agentic/GH-12.api"] = [
        DependencyState(False, False, ()),
        DependencyState(True, False, ()),
    ]
    patch = await develop(_dependent(), cfg, ports)
    return ports, api, runner, patch


async def test_a_docs_only_dependency_conflict_is_settled_and_no_developer_is_told(cfg):
    ports, api, runner, patch = await _develop(cfg, VALIDATE, OWNED)
    assert ports.workspace.settle_owned_calls == [(api.path, (OWNED,), MERGE_BASE)]
    assert [path for path, _ in ports.workspace.commits] == [api.path]  # the merge is concluded
    assert "does not apply clean" not in (runner.calls[0].get("prompt") or "")
    assert "conflicted_paths" not in patch["subtasks"]["api"]


async def test_a_mixed_dependency_conflict_hands_over_only_the_other_paths(cfg):
    ports, api, runner, patch = await _develop(cfg, VALIDATE, OWNED, "pyproject.toml")
    assert ports.workspace.settle_owned_calls == [(api.path, (OWNED,), MERGE_BASE)]
    assert ports.workspace.commits == []  # the Developer still has a conflict to resolve
    prompt = runner.calls[0]["prompt"]
    assert "`pyproject.toml`" in prompt and f"`{OWNED}`" not in prompt


async def test_commit_mode_leaves_a_dependency_conflict_whole_to_the_developer(cfg):
    ports, api, runner, patch = await _develop(cfg, COMMIT, OWNED, "pyproject.toml")
    assert ports.workspace.settle_owned_calls == []
    prompt = runner.calls[0]["prompt"]
    assert f"`{OWNED}`" in prompt and "`pyproject.toml`" in prompt


async def test_a_failing_settlement_hands_the_whole_conflict_over_as_before(cfg):
    runner = ScriptedRunner(developer=[delivered()])
    ports = _ports(VALIDATE, OWNED, runner=runner)
    ports.workspace.settle_owned_error = "boom"
    api = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(api.path)
    ports.workspace.dependency_states["agentic/GH-12.api"] = [
        DependencyState(False, False, ()),
        DependencyState(True, False, ()),
    ]
    await develop(_dependent(), cfg, ports)
    assert f"`{OWNED}`" in runner.calls[0]["prompt"]
