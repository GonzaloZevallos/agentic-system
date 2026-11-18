"""The system writes, validates, commits and records the draft Spec PM returns (GH-594 part 5a),
through `validate_prd`: fake `SpecValidator`, fake `Workspace`, frozen clock, a real directory
for the checkout."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application.checkouts import task_checkout_in_thread
from agentic.core.process.application.ports import RawResult, Upstream
from agentic.core.process.application.spec_draft import deliver
from agentic.core.process.application.validate_prd import validate_prd
from agentic.core.process.domain.spec_delivery import (
    body_sha256,
    draft_text,
    render_approved_front_matter,
)
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.fakes import (
    REPORTS,
    CommittingWorkspace,
    FakeSpecValidator,
    MemoryWorkspace,
    ScriptedRunner,
    memory_ports,
    verdict,
)

pytestmark = pytest.mark.anyio

BODY = "# Export report\n\n## Goal\n\nUsers want CSV.\n"
STATE = {
    "id": "T-1",
    "title": "Export report",
    "description": "Users want CSV.",
    "type": "feature",
    "path": "full",
    "sections": {"Product Analyst": "v1"},
}
NOW = "2026-11-17T10:00:00.000+00:00"
DRAFT = "docs/specs/2026-11-17-export-report.md"
CONTRACT = {
    "path": DRAFT,
    "slice": "x",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "criteria": [],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}
DEFINITION = [{"capability": "reports", "content": REPORTS}]
TAKEN_AS = "docs/specs/2026-11-17-export-report-t-1.md"


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """The task's checkout: a real directory, so what the system writes can be read back."""
    monkeypatch.setattr(MemoryWorkspace, "ROOT", tmp_path)
    (tmp_path / "T-1").mkdir()
    return tmp_path / "T-1"


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path, owner_login="owner")


def returned(kind: str = "approved", draft: str | None = BODY) -> RawResult:
    base = verdict(kind, definitions=DEFINITION if kind == "approved" else [])
    data = dict(base.structured_output)
    if draft is not None:
        data["spec_draft"] = draft
    return RawResult(data, base.cost_usd)


def ports_for(*results: RawResult, validator=None, workspace=None, now: str = NOW):
    return memory_ports(
        ScriptedRunner(pm=list(results)),
        spec_validator=validator,
        workspace=workspace if workspace is not None else CommittingWorkspace(),
        now=lambda: now,
    )


async def test_an_approved_valid_draft_is_written_committed_and_recorded(root, cfg):
    validator = FakeSpecValidator("spec_unapproved")
    ports = ports_for(returned(), validator=validator)
    patch = await validate_prd(STATE, cfg, ports)
    text = draft_text(BODY)
    assert (root / DRAFT).read_text(encoding="utf-8") == text
    assert text.startswith("---\nstatus: draft\n---\n")
    assert patch["status"] == "definition_ready"
    assert patch["spec_draft"] == {
        "outcome": "written",
        "path": DRAFT,
        "sha256": body_sha256(text),
        "commit": f"{2:040x}",
    }
    assert "spec" not in patch  # a draft is not a spec delivery
    assert "spec" not in apply(STATE, patch)
    assert validator.calls == [(text, DRAFT, "base0", None, "owner")]
    assert ports.workspace.commits == [
        (root, "T-1: product definition"),
        (root, "T-1: draft Spec"),
    ]
    assert ports.documents.last["spec_draft"] == patch["spec_draft"]


@pytest.mark.parametrize("draft", [None, "", "  \n"], ids=["missing", "empty", "blank"])
async def test_a_missing_or_empty_draft_is_recorded_absent(root, cfg, draft):
    ports = ports_for(returned(draft=draft), validator=FakeSpecValidator("spec_unapproved"))
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "absent"}
    assert patch["status"] == "definition_ready"
    assert not (root / "docs").exists()


async def test_without_a_validator_port_nothing_is_written(root, cfg):
    ports = ports_for(returned(), validator=None)
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "unvalidated"}
    assert not (root / "docs").exists()
    assert ports.workspace.commits == [(root, "T-1: product definition")]


@pytest.mark.parametrize("cause", ["spec_invalid", "slice_unknown", "slice_missing"])
async def test_a_draft_the_validator_refuses_is_invalid_and_never_on_disk(root, cfg, cause):
    ports = ports_for(returned(), validator=FakeSpecValidator(cause))
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert patch["status"] == "definition_ready"
    assert not (root / "docs").exists()
    assert "changes_requested" not in patch and "change_history" not in patch


async def test_a_validator_that_says_approved_is_not_a_draft(root, cfg):
    ports = ports_for(returned(), validator=FakeSpecValidator(CONTRACT))
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert not (root / "docs").exists()


@pytest.mark.parametrize(
    "smuggled",
    [
        render_approved_front_matter("owner", "2026-11-17T10:00:00Z", "d" * 64) + BODY,
        "---\nstatus: approved\n---\n" + BODY,
        "﻿---\nstatus: approved\n---\n" + BODY,
        "---\r\nstatus: approved\r\n---\r\n" + BODY,
    ],
    ids=["rendered", "plain", "bom", "crlf"],
)
async def test_a_body_that_carries_front_matter_is_invalid_even_if_the_validator_would_pass(
    root, cfg, smuggled
):
    validator = FakeSpecValidator("spec_unapproved")
    ports = ports_for(returned(draft=smuggled), validator=validator)
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert validator.calls == []  # not even asked
    assert not (root / "docs").exists()  # never on disk as approved


async def test_a_body_that_cannot_be_encoded_is_invalid(root, cfg):
    ports = ports_for(returned(draft="bad \ud800"), validator=FakeSpecValidator("spec_unapproved"))
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert not (root / "docs").exists()


async def test_a_returned_verdict_writes_and_records_nothing(root, cfg):
    validator = FakeSpecValidator("spec_unapproved")
    ports = ports_for(returned("returned"), validator=validator)
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["status"] == "returned"
    assert "spec_draft" not in patch
    assert validator.calls == [] and not (root / "docs").exists()


@pytest.mark.parametrize(
    "state",
    [
        {**STATE, "type": "platform_enhancement"},
        {**STATE, "path": "light"},
        {**STATE, "evaluation": {"cohort": "x"}},
        {**STATE, "spec": CONTRACT},
    ],
    ids=["platform", "light", "evaluation", "spec delivery"],
)
async def test_when_no_draft_is_due_nothing_is_validated_written_or_recorded(root, cfg, state):
    validator = FakeSpecValidator("spec_unapproved")
    ports = ports_for(returned(), validator=validator)
    patch = await validate_prd(state, cfg, ports)
    assert patch["status"] == "definition_ready"
    assert "spec_draft" not in patch and validator.calls == []
    assert not (root / "docs").exists()
    assert ports.workspace.commits == [(root, "T-1: product definition")]


async def test_an_invalid_draft_on_the_second_attempt_still_reaches_definition_ready(root, cfg):
    schema_failure = RawResult({"markdown": "x", "verdict": "approved"}, 0.1)  # no `reason`
    ports = ports_for(schema_failure, returned(), validator=FakeSpecValidator("spec_invalid"))
    patch = await validate_prd(STATE, cfg, ports)
    assert len(ports.runner.calls) == 2
    assert patch["status"] == "definition_ready"
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert "changes_requested" not in patch and "change_history" not in patch
    assert "escalated" not in patch


async def test_a_task_that_re_enters_after_commit_without_record_keeps_one_file(root, cfg):
    await validate_prd(
        STATE, cfg, ports_for(returned(), validator=FakeSpecValidator("spec_unapproved"))
    )
    # The run stopped after the commit and before the record, and the second run is another
    # day: a fresh name would be a second file.
    second = ports_for(
        returned(draft=BODY + "\nMore.\n"),
        validator=FakeSpecValidator("spec_unapproved"),
        now="2026-11-18T09:00:00.000+00:00",
    )
    patch = await validate_prd(STATE, cfg, second)
    assert sorted(p.name for p in (root / "docs/specs").iterdir()) == [Path(DRAFT).name]
    assert patch["spec_draft"]["outcome"] == "written" and patch["spec_draft"]["path"] == DRAFT
    assert (root / DRAFT).read_text(encoding="utf-8") == draft_text(BODY + "\nMore.\n")


async def test_a_task_that_re_enters_after_write_without_commit_keeps_one_file(root, cfg):
    (root / "docs/specs").mkdir(parents=True)
    (root / DRAFT).write_text(draft_text("old\n"), encoding="utf-8")  # untracked, no record
    ports = ports_for(
        returned(),
        validator=FakeSpecValidator("spec_unapproved"),
        now="2026-11-20T09:00:00.000+00:00",
    )
    patch = await validate_prd(STATE, cfg, ports)
    assert sorted(p.name for p in (root / "docs/specs").iterdir()) == [Path(DRAFT).name]
    assert patch["spec_draft"]["path"] == DRAFT
    assert ports.workspace.commits[-1] == (root, "T-1: draft Spec")


async def test_the_recorded_path_wins_on_re_entry(root, cfg):
    recorded = "docs/specs/2026-11-01-older-name.md"
    state = {
        **STATE,
        "spec_draft": {"outcome": "written", "path": recorded, "sha256": "e" * 64},
    }
    ports = ports_for(returned(), validator=FakeSpecValidator("spec_unapproved"))
    patch = await validate_prd(state, cfg, ports)
    assert patch["spec_draft"]["path"] == recorded
    assert (root / recorded).is_file() and not (root / DRAFT).exists()


async def test_a_recorded_path_that_is_not_a_spec_path_is_not_followed(root, cfg):
    state = {
        **STATE,
        "spec_draft": {"outcome": "written", "path": "../../outside.md", "sha256": "e" * 64},
    }
    ports = ports_for(returned(), validator=FakeSpecValidator("spec_unapproved"))
    patch = await validate_prd(state, cfg, ports)
    assert patch["spec_draft"]["path"] == DRAFT
    assert not (root.parent.parent / "outside.md").exists()


async def test_a_name_taken_in_the_upstream_gets_the_task_id(root, cfg):
    workspace = CommittingWorkspace()
    workspace.upstream_answer = Upstream("origin/master", "f" * 40, None)
    workspace.files[("f" * 40, DRAFT)] = "---\nstatus: approved\n---\nsomeone else's\n"
    ports = ports_for(
        returned(), validator=FakeSpecValidator("spec_unapproved"), workspace=workspace
    )
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"]["path"] == TAKEN_AS
    assert not (root / DRAFT).exists()


async def test_a_name_taken_in_the_checkout_gets_the_task_id(root, cfg):
    # Someone else's file, already in the base: not this task's own draft.
    (root / "docs/specs").mkdir(parents=True)
    (root / DRAFT).write_text("---\nstatus: approved\n---\nothers\n", encoding="utf-8")
    workspace = CommittingWorkspace()
    workspace.files[("base0", DRAFT)] = "---\nstatus: approved\n---\nothers\n"
    ports = ports_for(
        returned(), validator=FakeSpecValidator("spec_unapproved"), workspace=workspace
    )
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"]["path"] == TAKEN_AS
    assert (root / DRAFT).read_text(encoding="utf-8").endswith("others\n")


async def test_a_draft_committed_twice_records_the_second_commit_and_hash(root, cfg):
    ports = ports_for(validator=FakeSpecValidator("spec_unapproved"))
    task = await task_checkout_in_thread(STATE, cfg, ports)
    first = deliver(STATE, {"spec_draft": BODY}, task, cfg, ports)
    again = BODY + "\nMore.\n"
    second = deliver(
        {**STATE, "spec_draft": first}, {"spec_draft": again}, task, cfg, ports
    )
    assert first["outcome"] == second["outcome"] == "written"
    assert first["path"] == second["path"] == DRAFT
    assert first["commit"] == f"{1:040x}" and second["commit"] == f"{2:040x}"
    assert second["sha256"] == body_sha256(draft_text(again)) != first["sha256"]
    # Each commit still holds the text it recorded.
    workspace = ports.workspace
    assert workspace.show(second["commit"], DRAFT) == draft_text(again)
    assert workspace.show(first["commit"], DRAFT) == draft_text(BODY)


async def test_a_commit_that_added_nothing_records_the_head_that_holds_the_text(root, cfg):
    sha = "9" * 40
    workspace = CommittingWorkspace(pinned_head=sha)
    workspace.files[(sha, DRAFT)] = draft_text(BODY)
    ports = ports_for(returned(), validator=FakeSpecValidator("spec_unapproved"), workspace=workspace)
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {
        "outcome": "written",
        "path": DRAFT,
        "sha256": body_sha256(draft_text(BODY)),
        "commit": sha,
    }


@pytest.mark.parametrize("held", [None, "---\nstatus: draft\n---\nsomething else\n"])
async def test_a_head_that_does_not_hold_the_written_text_is_not_recorded(root, cfg, held):
    sha = "9" * 40
    workspace = CommittingWorkspace(pinned_head=sha)
    if held is not None:
        workspace.files[(sha, DRAFT)] = held
    ports = ports_for(returned(), validator=FakeSpecValidator("spec_unapproved"), workspace=workspace)
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert patch["status"] == "definition_ready"


async def test_a_symlink_at_the_target_is_invalid(root, cfg):
    (root / "docs/specs").mkdir(parents=True)
    (root / DRAFT).symlink_to(root / "elsewhere.md")
    ports = ports_for(returned(), validator=FakeSpecValidator("spec_unapproved"))
    patch = await validate_prd(STATE, cfg, ports)
    assert patch["spec_draft"] == {"outcome": "invalid"}
    assert not (root / "elsewhere.md").exists()
