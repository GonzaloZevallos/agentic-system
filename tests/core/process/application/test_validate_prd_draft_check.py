"""GH-594 part 5c-1: the draft pre-check `validate_prd` composes into `run_role`'s check under
`rules.spec_fallback_due`: a refused body goes back to PM once and never reaches `deliver`."""

from __future__ import annotations

import pytest

from agentic.core.process.application import validate_prd as module
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.validate_prd import validate_prd
from agentic.core.process.domain import spec_approval
from agentic.core.process.domain.spec_delivery import DRAFT_PREVIEW_PATH, body_sha256, draft_text
from agentic.shared.configuration import Configuration
from tests.fakes import (
    REPORTS,
    CommittingWorkspace,
    FakeDraftSpecValidator,
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
CONTRACT = {
    "path": DRAFT_PREVIEW_PATH,
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


@pytest.fixture
def root(tmp_path, monkeypatch):
    from tests.fakes import MemoryWorkspace

    monkeypatch.setattr(MemoryWorkspace, "ROOT", tmp_path)
    (tmp_path / "T-1").mkdir()
    return tmp_path / "T-1"


@pytest.fixture
def delivered(monkeypatch):
    """The bodies `spec_draft.deliver` was called with."""
    bodies: list[object] = []
    real = module.spec_draft.deliver

    def spy(state, data, *args):
        bodies.append(data.get("spec_draft"))
        return real(state, data, *args)

    monkeypatch.setattr(module.spec_draft, "deliver", spy)
    return bodies


def returned(draft="x") -> RawResult:
    base = verdict("approved", definitions=DEFINITION)
    data = dict(base.structured_output)
    if draft is not None:
        data["spec_draft"] = draft
    return RawResult(data, base.cost_usd)


def ports_for(*results, validator, now=NOW):
    return memory_ports(
        ScriptedRunner(pm=list(results)),
        spec_validator=validator,
        workspace=CommittingWorkspace(),
        now=lambda: now,
    )


def cfg_for(tmp_path, login="owner"):
    return Configuration(product_dir=tmp_path, owner_login=login)


@pytest.mark.parametrize(
    ("bad", "cause"),
    [
        ("", "draft_empty"),
        (None, "draft_empty"),
        ("---\nstatus: approved\n---\n" + BODY, "draft_front_matter"),
        ("bad \ud800", "draft_unhashable"),
    ],
)
async def test_a_body_refused_by_the_pre_check_is_retried_and_never_delivered(
    root, tmp_path, delivered, bad, cause
):
    ports = ports_for(returned(bad), returned(BODY), validator=FakeDraftSpecValidator())
    patch = await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert delivered == [BODY]
    assert f"draft Spec refused: {cause}:" in ports.runner.prompts("pm")[1]
    assert patch["spec_draft"]["outcome"] == "written"


async def test_a_draft_the_validator_refuses_is_retried_with_a_fixed_sentence(
    root, tmp_path, delivered
):
    validator = FakeDraftSpecValidator(draft="spec_invalid")
    ports = ports_for(returned(BODY), returned(BODY), returned(BODY), validator=validator)
    patch = await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert delivered == []
    assert len(ports.runner.calls) == 3  # PM was retried twice, then the task failed
    assert "draft Spec refused: draft_invalid:" in ports.runner.prompts("pm")[1]
    assert "spec_draft" not in patch and patch.get("status") != "definition_ready"


@pytest.mark.parametrize("login", ["a b", ""], ids=["malformed", "empty"])
async def test_without_a_usable_login_the_preview_is_skipped_and_never_approves(
    root, tmp_path, delivered, monkeypatch, login
):
    def boom(*args):
        raise AssertionError("approve must not run")

    monkeypatch.setattr(spec_approval, "approve", boom)
    validator = FakeDraftSpecValidator()
    ports = ports_for(returned(BODY), validator=validator)
    patch = await validate_prd(STATE, cfg_for(tmp_path, login), ports)
    assert patch["spec_draft"]["outcome"] == "written"
    assert [c[0].startswith("---\nstatus: draft") for c in validator.calls[:1]] == [True]
    assert all(c[1] != DRAFT_PREVIEW_PATH or "status: draft" in c[0] for c in validator.calls[:1])
    assert validator.calls[0][4] is None  # the login is not passed on, whatever it holds


async def test_a_valid_login_previews_the_approved_draft(root, tmp_path, delivered):
    validator = FakeDraftSpecValidator()
    ports = ports_for(returned(BODY), validator=validator)
    await validate_prd(STATE, cfg_for(tmp_path), ports)
    check_calls = validator.calls[:2]
    assert "status: draft" in check_calls[0][0]
    assert check_calls[1][0].startswith("---\nstatus: approved\napproved_by: owner\n")
    assert "approved_at: 2026-11-17T10:00:00.000Z\n" in check_calls[1][0]
    assert {c[1] for c in check_calls} == {DRAFT_PREVIEW_PATH}
    assert all(c[3] is None for c in check_calls)


async def test_a_first_multi_slice_draft_is_refused_with_the_fold_or_split_instruction(
    root, tmp_path, delivered
):
    validator = FakeDraftSpecValidator(preview="slice_missing")
    ports = ports_for(returned(BODY), returned(BODY), validator=validator)
    await validate_prd(STATE, cfg_for(tmp_path), ports)
    retry = ports.runner.prompts("pm")[1]
    assert "draft Spec refused: preview_slice_missing:" in retry
    assert "fold the work into one slice" in retry


async def test_a_second_multi_slice_draft_ends_in_a_split_proposal_not_needs_human(
    root, tmp_path, delivered
):
    validator = FakeDraftSpecValidator(preview="slice_missing")
    ports = ports_for(returned(BODY), returned(BODY), validator=validator)
    patch = await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert len(ports.runner.calls) == 2
    assert patch["status"] == "definition_ready"
    assert patch["spec_draft"] == {
        "outcome": "split_proposed",
        "sha256": body_sha256(draft_text(BODY)),
    }
    assert "escalation" not in patch and patch["status"] != "needs-human"
    assert not (root / "docs").exists()  # nothing written
    assert ports.workspace.commits == [(root, "T-1: product definition")]


async def test_a_multi_slice_draft_after_another_refusal_is_a_split_proposal(
    root, tmp_path, delivered
):
    validator = FakeDraftSpecValidator(preview="slice_missing")
    ports = ports_for(returned(""), returned(BODY), validator=validator)
    patch = await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert "draft Spec refused: draft_empty:" in ports.runner.prompts("pm")[1]
    assert patch["spec_draft"]["outcome"] == "split_proposed"
    assert patch["status"] == "definition_ready"


async def test_a_multi_slice_draft_without_a_valid_login_is_written_without_the_sentence(
    root, tmp_path, delivered
):
    validator = FakeDraftSpecValidator(preview="slice_missing")  # would refuse, if asked
    ports = ports_for(returned(BODY), validator=validator)
    patch = await validate_prd(STATE, cfg_for(tmp_path, "a b"), ports)
    assert len(ports.runner.calls) == 1
    assert patch["spec_draft"]["outcome"] == "written" and patch["status"] == "definition_ready"
    assert "fold the work into one slice" not in ports.runner.prompts("pm")[0]


async def test_when_no_draft_is_due_the_prompt_does_not_ask_for_one_slice(
    root, tmp_path, delivered
):
    ports = ports_for(returned(BODY), validator=FakeDraftSpecValidator())
    await validate_prd({**STATE, "path": "light"}, cfg_for(tmp_path), ports)
    assert "exactly one slice" not in ports.runner.prompts("pm")[0]


async def test_a_clock_that_does_not_read_as_an_instant_skips_the_preview(
    root, tmp_path, delivered
):
    validator = FakeDraftSpecValidator(preview="slice_missing")  # would refuse, if asked
    ports = ports_for(returned(BODY), validator=validator, now="2026-11-17T10:00:00")
    patch = await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert patch["spec_draft"]["outcome"] == "written"
    assert delivered == [BODY]


async def test_without_a_validator_nothing_is_checked(root, tmp_path, delivered):
    ports = ports_for(returned(""), validator=None)
    patch = await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert patch["spec_draft"] == {"outcome": "absent"}
    assert len(ports.runner.calls) == 1


async def test_outside_the_predicate_the_check_is_the_approval_check_itself(
    root, tmp_path, monkeypatch
):
    seen: list[object] = []

    async def fake_run_role(*args, **kwargs):
        seen.append(kwargs["check"])
        return None, {}

    monkeypatch.setattr(module, "run_role", fake_run_role)
    ports = ports_for(validator=FakeDraftSpecValidator())
    sentinel = lambda data: None  # noqa: E731
    approval_checks = {"feature": sentinel}
    monkeypatch.setattr(
        type(ports.knowledge), "approval_check", lambda self, kind: approval_checks[kind]
    )
    await validate_prd({**STATE, "path": "light"}, cfg_for(tmp_path), ports)
    assert seen == [sentinel] and seen[0] is sentinel
    await validate_prd(STATE, cfg_for(tmp_path), ports)
    assert seen[1] is not sentinel and callable(seen[1])
