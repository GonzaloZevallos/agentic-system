"""GH-364, subtask `sync-turn-git`: the system's turn between two moments of the technical
stage is a plain synchronous function, so the scheduling loop can run it in a worker thread
one turn at a time. `integrate` is its async wrapper and behaves exactly as before."""

from __future__ import annotations

import hashlib
import inspect
import subprocess
from dataclasses import replace

import pytest

from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.application.integrate import integrate, integrate_turn
from agentic.core.process.application.ports import SpecUnreadable
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import INTEGRATION, apply
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports, real_commands

pytestmark = pytest.mark.anyio

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


def test_the_turn_is_synchronous_and_merges_what_was_approved(cfg):
    assert not inspect.iscoroutinefunction(integrate_turn)
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}
    ports = memory_ports(ScriptedRunner())

    patch = integrate_turn(state, cfg, ports)

    assert [branch for _, branch in ports.workspace.merges] == [
        "agentic/GH-12.api",
        "agentic/GH-12.domain",
    ]
    assert patch["subtasks"][INTEGRATION]["status"] == "delivered"


async def test_integrate_answers_what_the_turn_answers(cfg):
    state = {**TASK, "subtasks": {"api": approved("api"), "domain": approved("domain")}}

    from_turn = integrate_turn(state, cfg, memory_ports(ScriptedRunner()))
    from_node = await integrate(state, cfg, memory_ports(ScriptedRunner()))

    assert from_node == from_turn


async def test_a_turn_with_the_task_escalated_touches_no_workspace(cfg):
    state = {**TASK, "escalated": "stop", "subtasks": {"api": approved("api")}}
    ports = memory_ports(ScriptedRunner())

    patch = integrate_turn(state, cfg, ports)

    assert patch["status"] == "waiting_on_human"
    assert ports.workspace.merges == []
    assert await integrate(state, cfg, ports) == patch


# --- a resumed turn over a task whose landing pass was cut (GH-450), through real git ------------


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _landed(product, names):
    """A product repo with one branch per unit, each adding its own file on top of master."""
    (product / ".agentic").mkdir()
    (product / ".agentic/stack.yaml").write_text("checks:\n  tests: pytest\n")
    _git("init", "-q", "-b", "master", ".", cwd=product)
    _git("add", "-A", cwd=product)
    _git("commit", "-q", "-m", "v0", cwd=product)
    base = _git("rev-parse", "HEAD", cwd=product)
    units = {}
    for name in names:
        _git("checkout", "-q", "-b", f"agentic/GH-12.{name}", base, cwd=product)
        (product / f"{name}.txt").write_text(name)
        _git("add", "-A", cwd=product)
        _git("commit", "-q", "-m", name, cwd=product)
        units[name] = {**approved(name), "base": base, "reviewed_head": "a" * 40}
    _git("checkout", "-q", "master", cwd=product)
    return units


@pytest.mark.parametrize("names", [("api",), ("api", "domain")])
def test_a_resumed_turn_on_an_all_approved_task_with_an_open_fix_changes_nothing(tmp_path, names):
    units = _landed(tmp_path, names)
    cfg = Configuration(product_dir=tmp_path)
    ports = replace(
        memory_ports(ScriptedRunner()),
        workspace=GitWorkspace(tmp_path, cfg.git_identity),
        stack=GitStack(tmp_path),
        commands=real_commands(),
    )
    state = {**TASK, "subtasks": units}
    first = integrate_turn(state, cfg, ports)  # the wave that merged them, before the kill
    head = _git("rev-parse", "agentic/GH-12", cwd=tmp_path)
    assert all((tmp_path / ".agentic/worktrees/GH-12" / f"{n}.txt").exists() for n in names)

    open_fix = {
        "head": head,
        "step": "fix",
        "refusal": "ci_failed",
        "at": "2026-10-30T10:19:54+00:00",
        "cost_base": 18.8231,
    }
    merged = apply(state, first)
    if INTEGRATION in merged["subtasks"]:  # reviewed and approved before the kill
        merged = apply(merged, {"subtasks": {INTEGRATION: {"status": "approved"}}})
    killed = apply(merged, {"pull_request": "https://example.test/pr/1", "landing": [open_fix]})
    assert rules.landing_pass(killed) == "unfinished" and rules.resume_target(killed) == "integrate"

    patch = integrate_turn(killed, cfg, ports)

    assert _git("rev-parse", "agentic/GH-12", cwd=tmp_path) == head  # nothing new on the branch
    assert "landing" not in patch  # the open entry is neither closed nor rewritten
    assert apply(killed, patch)["landing"] == [open_fix]


def asked(sid: str, kind: str, text: str) -> dict:
    return {
        **approved(sid),
        "promote_request": {"kind": kind, "from_role": f"Developer: {sid}", "text": text},
    }


LIGHT = {"path": "light", "initial_path": "light", "path_label": "none"}


def test_the_turn_applies_the_selected_request_once_and_clears_every_request(cfg):
    """GH-591: a risk_area request beats a role one; both are cleared in the same patch."""
    state = {
        **TASK,
        **LIGHT,
        "subtasks": {
            "api": asked("api", "role", "bigger than it looked"),
            "domain": asked("domain", "risk_area", "data-migration"),
        },
    }
    patch = integrate_turn(state, cfg, memory_ports(ScriptedRunner()))

    assert patch["path"] == "full"
    assert (patch["promotion"]["kind"], patch["promotion"]["text"]) == (
        "risk_area",
        "data-migration",
    )
    assert all("promote_request" not in s for s in patch["subtasks"].values())
    assert apply(state, patch)["path"] == "full"


def test_under_the_owner_label_the_requests_are_cleared_and_the_notice_written(cfg):
    state = {
        **TASK,
        **LIGHT,
        "path_label": "light",
        "sections": {"Developer: api": "done"},
        "subtasks": {"api": asked("api", "role", "bigger")},
    }
    patch = integrate_turn(state, cfg, memory_ports(ScriptedRunner()))

    assert patch.get("path") is None and "promotion" not in patch
    assert "promote_request" not in patch["subtasks"]["api"]
    assert "path:light" in patch["sections"]["Developer: api"]


def test_a_turn_with_nothing_asked_changes_no_path(cfg):
    state = {**TASK, **LIGHT, "subtasks": {"api": approved("api")}}
    patch = integrate_turn(state, cfg, memory_ports(ScriptedRunner()))
    assert "path" not in patch and "promotion" not in patch


# --- the cut of the task branch under a spec delivery (GH-594, part 4b-3) ---------------

SPEC_PATH = "docs/specs/a.md"
SPEC_BODY = "## Goal\n\nText.\n"
SPEC_TEXT = "---\nstatus: approved\n---\n" + SPEC_BODY
RECORDED = "a" * 40
BASE = "b" * 40


class _SpecReader:
    def __init__(self, texts):
        self.texts = texts

    def read(self, commit, path):
        if commit not in self.texts:
            raise SpecUnreadable("missing")
        return self.texts[commit]


def _spec_state():
    spec = {
        "path": SPEC_PATH,
        "slice": "a",
        "commit": RECORDED,
        "sha256": hashlib.sha256(SPEC_BODY.encode()).hexdigest(),
        "criteria": ["c"],
        "screens": [],
        "risks": [],
        "touches_ui": False,
        "touches_copy": False,
    }
    return {**TASK, "spec": spec, "subtasks": {"api": {**approved("api"), "status": "pending"}}}


def _spec_ports(texts):
    ports = memory_ports(ScriptedRunner(), spec_reader=_SpecReader(texts))
    ports.workspace.existing_branches[rules.task_branch("GH-12")] = ("x", BASE)
    return ports


def test_a_cut_after_the_spec_moved_refuses_before_any_developer(cfg):
    moved = SPEC_TEXT.replace("Text.", "Other.")
    ports = _spec_ports({RECORDED: SPEC_TEXT, BASE: moved})

    patch = integrate_turn(_spec_state(), cfg, ports)

    assert patch["spec_refusal"] == {"cause": "spec_changed", "detail": [SPEC_PATH]}
    assert patch["status"] == "waiting_on_human"
    assert "spec" not in patch
    assert ports.workspace.merges == []
    assert ports.workspace.checkouts_made == [rules.task_branch("GH-12")]  # only the cut itself


def test_a_cut_over_the_same_spec_re_records_the_commit(cfg):
    ports = _spec_ports({RECORDED: SPEC_TEXT, BASE: SPEC_TEXT})

    patch = integrate_turn(_spec_state(), cfg, ports)

    assert patch["spec"]["commit"] == BASE
    assert "spec_refusal" not in patch


def test_without_a_spec_the_cut_reads_nothing(cfg):
    class Raising:
        def read(self, commit, path):
            raise AssertionError("a read was made")

    state = {k: v for k, v in _spec_state().items() if k != "spec"}
    ports = memory_ports(ScriptedRunner(), spec_reader=Raising())

    patch = integrate_turn(state, cfg, ports)

    assert "spec" not in patch and "spec_refusal" not in patch
