"""The close-time capability pass of PM's final validation: on `approved`, the capability
files PM returned are validated, written into the task checkout, committed there and
recorded as `close_docs`; a task whose PM returns none writes nothing."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application import final_validation as node
from agentic.core.process.domain import document, roles, schemas
from agentic.core.process.domain.task import TaskState, apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    MemoryWorkspace,
    ScriptedRunner,
    design_findings,
    memory_ports,
)
from tests.fakes import final_validation as final_verdict

pytestmark = pytest.mark.anyio

REVIEWED = "a" * 40
JOBS = "## Jobs\n\nCuando entreno, quiero ver mi semana."


def capability(slug: str = "dashboard", content: str = JOBS) -> dict[str, str]:
    return {"capability": slug, "content": content}


def state() -> TaskState:
    return {
        "id": "GH-12",
        "title": "Dashboard",
        "description": "How is my gym doing.",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
        "subtasks": {
            "s0": {
                "id": "s0",
                "title": "s0",
                "brief": "b",
                "seams": ["s"],
                "depends_on": [],
                "status": "approved",
                "reviewed_head": REVIEWED,
            }
        },
    }


class NoChangeWorkspace(MemoryWorkspace):
    """A checkout where the files PM returned are what it already held: no commit."""

    def commit_paths(self, path: Path, relatives: Sequence[str], message: str) -> str | None:
        super().commit_paths(path, relatives, message)
        return None


def runner_with(*finals: Any) -> ScriptedRunner:
    return ScriptedRunner(
        ux_lead_final=[design_findings("approved") for _ in finals],
        ui_lead_final=[design_findings("approved") for _ in finals],
        pm_final=list(finals),
    )


async def run(
    start: TaskState, runner: ScriptedRunner, tmp_path: Path, workspace: MemoryWorkspace
) -> tuple[TaskState, Any]:
    ports = memory_ports(runner, workspace=workspace)
    patch = await node.final_validation(start, Configuration(product_dir=tmp_path), ports)
    return patch, ports


async def test_approved_with_definitions_writes_commits_and_records_close_docs(tmp_path):
    workspace = MemoryWorkspace()
    runner = runner_with(final_verdict("approved", definitions=[capability()]))

    patch, ports = await run(state(), runner, tmp_path, workspace)

    assert ports.knowledge.definitions == {"dashboard": JOBS}
    assert len(workspace.path_commits) == 1
    path, relatives, message = workspace.path_commits[0]
    assert relatives == ("docs/product/dashboard.md",)
    assert message == "GH-12: capability files at close"
    assert patch["close_docs"] == {
        "parent": REVIEWED,
        "head": "c2",
        "paths": ["docs/product/dashboard.md"],
    }
    assert "task_head" not in patch


async def test_the_pm_is_told_the_diff_to_read(tmp_path):
    runner = runner_with(final_verdict("approved"))

    await run(state(), runner, tmp_path, MemoryWorkspace())

    assert f"..{REVIEWED}`" in runner.prompts("pm_final")[0]


async def test_approved_with_no_definitions_writes_nothing(tmp_path):
    workspace = MemoryWorkspace()
    before = {**state(), "close_docs": {"parent": "p", "head": "h", "paths": ["docs/product/x.md"]}}

    patch, ports = await run(before, runner_with(final_verdict("approved")), tmp_path, workspace)

    assert ports.knowledge.definitions == {}
    assert workspace.path_commits == []
    assert patch["close_docs"] is None  # the earlier run's record is cleared, not kept


@pytest.mark.parametrize(
    "bad",
    [capability("Not A Slug"), capability("dashboard", "no jobs here")],
    ids=["bad-slug", "no-jobs"],
)
async def test_an_invalid_definition_is_rejected_and_nothing_is_written(bad, tmp_path):
    workspace = MemoryWorkspace()
    runner = runner_with(*[final_verdict("approved", definitions=[bad])] * 2)

    patch, ports = await run(state(), runner, tmp_path, workspace)

    assert ports.knowledge.definitions == {}
    assert workspace.path_commits == []
    assert patch.get("escalated")
    assert patch.get("close_docs") is None


async def test_a_returned_verdict_with_definitions_is_rejected(tmp_path):
    workspace = MemoryWorkspace()
    returned = final_verdict("returned", blocking=["x"], definitions=[capability()])

    patch, ports = await run(state(), runner_with(returned, returned), tmp_path, workspace)

    assert ports.knowledge.definitions == {}
    assert workspace.path_commits == []
    assert patch.get("escalated")


async def test_a_returned_verdict_without_definitions_clears_close_docs(tmp_path):
    before = {**state(), "close_docs": {"parent": "p", "head": "h", "paths": ["docs/product/x.md"]}}
    returned = final_verdict("returned", blocking=["x"])

    patch, ports = await run(before, runner_with(returned), tmp_path, MemoryWorkspace())

    assert patch["close_docs"] is None
    assert ports.knowledge.definitions == {}


async def test_a_second_approval_with_changes_replaces_the_record(tmp_path):
    first = {"parent": "p", "head": "h1", "paths": ["docs/product/old.md"]}

    patch, _ = await run(
        {**state(), "close_docs": first},
        runner_with(final_verdict("approved", definitions=[capability("other")])),
        tmp_path,
        MemoryWorkspace(),
    )

    assert patch["close_docs"] == {
        "parent": REVIEWED,
        "head": "c2",
        "paths": ["docs/product/other.md"],
    }


async def test_a_second_approval_with_identical_content_records_nothing(tmp_path):
    first = {"parent": "p", "head": "h1", "paths": ["docs/product/dashboard.md"]}
    workspace = NoChangeWorkspace()

    patch, _ = await run(
        {**state(), "close_docs": first},
        runner_with(final_verdict("approved", definitions=[capability()])),
        tmp_path,
        workspace,
    )

    assert len(workspace.path_commits) == 1  # it tried to commit, and git had nothing to add
    assert patch["close_docs"] is None


async def test_an_approval_after_a_return_records_the_new_commit(tmp_path):
    earlier = {"parent": "p", "head": "h1", "paths": ["docs/product/old.md"]}
    returned = final_verdict("returned", blocking=["x"])
    start = {**state(), "close_docs": earlier}

    first, _ = await run(start, runner_with(returned), tmp_path, MemoryWorkspace())
    assert first["close_docs"] is None

    second, ports = await run(
        apply(start, first),
        runner_with(final_verdict("approved", definitions=[capability()])),
        tmp_path,
        MemoryWorkspace(),
    )

    assert ports.knowledge.definitions == {"dashboard": JOBS}
    assert second["close_docs"] == {
        "parent": REVIEWED,
        "head": "c2",
        "paths": ["docs/product/dashboard.md"],
    }


def test_relative_paths_are_taken_against_the_checkout():
    root = Path("/checkouts/GH-12")

    found = node._relative_paths([root / "docs/product/a.md", root / "docs/product/b.md"], root)

    assert found == ["docs/product/a.md", "docs/product/b.md"]


def test_close_docs_round_trips_through_the_document():
    docs = {"parent": REVIEWED, "head": "b" * 40, "paths": ["docs/product/dashboard.md"]}
    full = {**state(), "close_docs": docs}

    text = document.render(full)

    assert document.parse(text)["close_docs"] == docs


def test_a_document_without_close_docs_is_byte_identical_to_today():
    plain = state()

    assert "close_docs" not in document.render(plain)
    assert document.render(apply(plain, {"close_docs": None})) == document.render(plain)


def test_the_final_validation_schema_carries_the_definitions():
    ok = schemas.validate(
        schemas.FinalValidation,
        {
            "markdown": "m",
            "verdict": "approved",
            "reason": "r",
            "blocking": [],
            "definitions": [capability()],
        },
        {},
    )

    assert ok["definitions"] == [capability()]
    assert "glossary" not in schemas.FinalValidation.model_fields


def test_pm_final_reads_knowledge_and_the_whole_checkout_and_writes_nothing():
    assert roles.PM_FINAL.allowed == roles.READ_KNOWLEDGE_RULES + roles.READ_CODE_RULES
    assert roles.PM_FINAL.tools == roles.READ_CODE
    assert not roles.PM_FINAL.executes and not any(
        rule.startswith(("Edit", "Write")) for rule in roles.PM_FINAL.allowed
    )
