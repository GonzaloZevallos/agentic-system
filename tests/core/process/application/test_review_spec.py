"""The review's spec pre-check (GH-594 part 4b-3): a head whose spec is not intact never reaches a
reviewer. Fake ports; the reader is a scripted `SpecReader`, the runner a spy."""

from __future__ import annotations

import hashlib
from typing import Any, cast

import pytest

from agentic.core.process.application.ports import SpecUnreadable, Upstream
from agentic.core.process.application.review import review
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, MemoryWorkspace, ScriptedRunner, memory_ports, reviewed

pytestmark = pytest.mark.anyio

PATH = "docs/specs/a.md"
RECORDED = "a" * 40
HEAD = "b" * 40
UPSTREAM = "c" * 40
BODY = "## Goal\n\nText.\n"
TEXT = "---\nstatus: approved\n---\n" + BODY
SHA = hashlib.sha256(BODY.encode()).hexdigest()
SPEC = {
    "path": PATH,
    "slice": "a",
    "commit": RECORDED,
    "sha256": SHA,
    "criteria": ["c"],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}
REFUSALS = ("missing", "oversize", "git", "commit", "path", "not_blob", "encoding")
CHANGED_BODY = TEXT.replace("Text.", "Other.")


class Reader:
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts
        self.calls: list[tuple[str, str]] = []

    def read(self, commit: str, path: str) -> str:
        self.calls.append((commit, path))
        answer = self.texts.get(commit, "missing")
        if answer in REFUSALS:
            raise SpecUnreadable(cast(Any, answer))
        return answer


class Raising:
    def read(self, commit: str, path: str) -> str:
        raise AssertionError("a read was made")


class Workspace(MemoryWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.upstream_answer = Upstream("origin/main", UPSTREAM, None)

    def full_head(self, path):
        return HEAD


def subtask(sid: str, **more: Any) -> dict:
    return {
        "id": sid,
        "title": sid,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": "delivered",
        "branch": f"agentic/GH-9.{sid}",
        "base": "base1",
        **more,
    }


def state_of(*, spec: dict | None = SPEC, subtasks: dict | None = None, **more: Any) -> dict:
    return {
        "id": "GH-9",
        "title": "Spec",
        "description": "d",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nx"},
        "subtask_id": "api",
        "subtasks": subtasks or {"api": subtask("api")},
        **({"spec": spec} if spec else {}),
        **more,
    }


def ports_of(reader, *, stack=None, workspace=None, runner=None):
    runner = runner or ScriptedRunner(tech_lead_review=[reviewed("approved")])
    return memory_ports(
        runner,
        stack=stack or MemoryStack(),
        workspace=workspace or Workspace(),
        spec_reader=reader,
    )


async def run(state, ports):
    return await review(state, Configuration(product_dir=MemoryWorkspace.ROOT), ports)


CHANGES = {
    "changed body": {RECORDED: TEXT, HEAD: CHANGED_BODY},
    "front matter only": {RECORDED: TEXT, HEAD: TEXT.replace("approved", "draft")},
    "deleted": {RECORDED: TEXT},
}

NEVER_WRITTEN = (
    "changes_requested",
    "change_history",
    "restarts",
    "review_scopes",
    "ci_speculation",
    "experiment",
    "subtasks",
)


@pytest.mark.parametrize("texts", CHANGES.values(), ids=CHANGES.keys())
async def test_a_changed_spec_adds_the_row_and_escalates_without_a_reviewer(texts):
    runner = ScriptedRunner()
    ports = ports_of(Reader(texts), runner=runner)

    patch = await run(state_of(changes_requested={"api": 1}), ports)

    assert patch["spec_refusal"] == {"cause": "spec_changed", "detail": [PATH]}
    assert patch["status"] == "waiting_on_human"
    assert patch["escalation_class"] == "classification"
    assert [
        (r["location"], r["category"], r["severity"], r["status"]) for r in patch["findings"]
    ] == [(PATH, "spec-scope", "blocking", "open")]
    assert runner.calls == []
    assert [key for key in NEVER_WRITTEN if key in patch] == []


@pytest.mark.parametrize("reason", ["oversize", "git"])
async def test_an_unreadable_spec_escalates_spec_unreadable(reason):
    runner = ScriptedRunner()
    reader = Reader({RECORDED: TEXT, HEAD: reason})

    patch = await run(state_of(), ports_of(reader, runner=runner))

    assert patch["spec_refusal"] == {"cause": "spec_unreadable", "detail": [PATH, reason]}
    assert patch["findings"][0]["category"] == "spec-scope"
    assert runner.calls == []
    assert [key for key in NEVER_WRITTEN if key in patch] == []


async def test_the_change_that_came_from_upstream_says_so():
    reader = Reader({RECORDED: TEXT, HEAD: CHANGED_BODY, UPSTREAM: CHANGED_BODY})
    patch = await run(state_of(), ports_of(reader))
    assert patch["spec_refusal"] == {"cause": "spec_changed", "detail": [PATH, "upstream"]}


async def test_a_resume_over_an_unreverted_head_escalates_the_same_again():
    runner = ScriptedRunner()
    ports = ports_of(Reader({RECORDED: TEXT, HEAD: CHANGED_BODY}), runner=runner)
    first = await run(state_of(), ports)
    second = await run(state_of(), ports)
    assert first["spec_refusal"] == second["spec_refusal"]
    assert runner.calls == []
    assert [key for key in NEVER_WRITTEN if key in second] == []


async def test_a_revert_lets_the_next_review_run_as_usual():
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    patch = await run(state_of(), ports_of(Reader({RECORDED: TEXT, HEAD: TEXT}), runner=runner))
    assert "spec_refusal" not in patch
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert len(runner.calls) == 1


async def test_a_non_final_subtask_that_edits_the_spec_escalates_at_its_own_review():
    state = state_of(subtasks={"api": subtask("api"), "ui": subtask("ui")})
    runner = ScriptedRunner()
    reader = Reader({RECORDED: TEXT, HEAD: CHANGED_BODY})
    patch = await run(state, ports_of(reader, runner=runner))
    assert patch["spec_refusal"]["cause"] == "spec_changed"
    assert runner.calls == []


async def test_a_sibling_already_approved_keeps_its_head_and_the_task_waits():
    approved = subtask("ui", status="approved", reviewed_head="d" * 40)
    state = state_of(subtasks={"api": subtask("api"), "ui": approved})
    reader = Reader({RECORDED: TEXT, HEAD: CHANGED_BODY})
    patch = await run(state, ports_of(reader))
    assert patch["status"] == "waiting_on_human"
    assert "subtasks" not in patch  # the sibling's reviewed_head is not rewritten


async def test_a_changed_spec_makes_no_speculative_push():
    workspace = Workspace()
    ports = ports_of(
        Reader({RECORDED: TEXT, HEAD: CHANGED_BODY}),
        stack=MemoryStack(ci=("check",), delivery_checks=("tests",)),
        workspace=workspace,
    )
    patch = await run(state_of(), ports)
    assert workspace.ci_ref_calls == []
    assert "ci_speculation" not in patch


async def test_without_a_spec_nothing_is_read_and_the_review_is_unchanged():
    runner = ScriptedRunner(tech_lead_review=[reviewed("approved")])
    patch = await run(state_of(spec=None), ports_of(Raising(), runner=runner))
    assert patch["subtasks"]["api"]["status"] == "approved"
    assert "spec_refusal" not in patch
    assert len(runner.calls) == 1
