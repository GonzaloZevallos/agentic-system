"""The reflector's use case (ADR-0043): its input is built in code from a closed task's
`return_history` and review sections, its deltas fold through `playbook.apply`, and a
failure is a result, never an exception into a task.

Seam: `reflect_task` over `ScriptedRunner`/`memory_ports`, plus `preflight` rendering
`reflector.md` and the roster."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application import preflight as preflight_module
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.preflight import PLACEHOLDERS, preflight
from agentic.core.process.application.reflect import Reflected, reflect_task
from agentic.core.process.application.run_role import load_prompt
from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import schemas
from agentic.core.process.domain.roles import REFLECTOR, ROLES
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryPrompts, ScriptedRunner, memory_ports

pytestmark = pytest.mark.anyio

NOW = "2026-10-05"
LESSON = "Run every declared check before delivering, not only the single test file."


def document(
    task_id: str = "GH-10",
    history: list[dict[str, str]] | None = None,
    sections: dict[str, str] | None = None,
    task_type: str = "feature",
    status: str = "closed",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": "A task",
        "type": task_type,
        "status": status,
        "description": "the request",
        "change_history": history
        if history is not None
        else [
            {
                "pair": "Tech Lead→Developer:parser",
                "category": "standard-other",
                "reason": "left a check unrun",
            }
        ],
        "sections": sections
        if sections is not None
        else {
            "Tech Lead: review parser (v1)": "### Verdict\n\nthe parser review one",
            "Tech Lead: review parser": "### Verdict\n\nthe parser review two",
        },
    }


def reflection(*deltas: dict[str, Any], cost: float = 0.4) -> RawResult:
    return RawResult({"markdown": "### Reflection\n\nDone.", "deltas": list(deltas)}, cost, 2, "r1")


def add(lesson: str = LESSON, slug: str = "developer", category: str = "standard-other") -> dict:
    return {
        "op": "add",
        "slug": slug,
        "category": category,
        "lesson": lesson,
        "item_id": "",
        "tasks": ["GH-10"],
    }


@pytest.fixture
def cfg(tmp_path: Path) -> Configuration:
    return Configuration(product_dir=tmp_path)


async def run(
    cfg: Configuration,
    runner: ScriptedRunner,
    doc: Mapping[str, Any] | None = None,
    playbooks: Mapping[str, pb.Playbook] | None = None,
    rejected: list[pb.Rejected] | None = None,
) -> Reflected:
    return await reflect_task(
        cfg,
        memory_ports(runner),
        doc if doc is not None else document(),
        playbooks or {},
        rejected or [],
        now=NOW,
    )


async def test_the_input_holds_only_attributable_returns_and_their_review_sections(cfg):
    history = [
        {"pair": "Tech Lead→Developer:parser", "category": "standard-other", "reason": "unrun"},
        {"pair": "PM→Product Analyst", "category": "spec-behaviour", "reason": "PRD gap"},
        {"pair": "Tech Lead→Developer:other", "category": "no-such-category", "reason": "x"},
    ]
    sections = {
        "Tech Lead: review parser (v1)": "PARSER-REVIEW-ONE",
        "Tech Lead: review parser": "PARSER-REVIEW-TWO",
        "Tech Lead: review other": "OTHER-REVIEW-SHOULD-NOT-APPEAR",
    }
    runner = ScriptedRunner(reflector=[reflection()])
    result = await run(cfg, runner, document(history=history, sections=sections))
    [call] = runner.calls
    prompt = call["prompt"]
    assert "standard-other" in prompt and "developer" in prompt
    assert "PARSER-REVIEW-ONE" in prompt
    assert "PARSER-REVIEW-TWO" not in prompt  # the first return maps to the first delivery
    assert "PRD gap" not in prompt and "spec-behaviour" not in prompt
    assert "OTHER-REVIEW-SHOULD-NOT-APPEAR" not in prompt
    assert not result.failed


async def test_the_input_carries_the_accountable_roles_playbook_with_ids_and_its_rejections(cfg):
    item = pb.Item("developer-1", "Keep diffs small.", "standard-other", "empty", ("GH-1",), NOW)
    rejected = [pb.Rejected("developer", "standard-other", "never mock the store")]
    runner = ScriptedRunner(reflector=[reflection()])
    await run(
        cfg,
        runner,
        playbooks={"developer": pb.Playbook("developer", (item,))},
        rejected=rejected,
    )
    prompt = runner.calls[0]["prompt"]
    assert "developer-1" in prompt and "Keep diffs small." in prompt
    assert "never mock the store" in prompt


async def test_no_attributable_return_means_no_model_call(cfg):
    runner = ScriptedRunner()  # a call would raise: nothing is scripted
    history = [{"pair": "PM→Product Analyst", "category": "spec-behaviour", "reason": "x"}]
    result = await run(cfg, runner, document(history=history))
    assert runner.calls == []
    assert result.applied == () and result.refused == () and result.cost_usd == 0.0
    assert result.playbooks == {} and not result.failed


async def test_a_task_with_no_returns_is_empty_without_a_call(cfg):
    runner = ScriptedRunner()
    result = await run(cfg, runner, document(history=[]))
    assert runner.calls == [] and not result.failed and result.applied == ()


async def test_an_applicable_delta_returns_the_new_playbook_and_the_cost(cfg):
    runner = ScriptedRunner(reflector=[reflection(add(), cost=0.37)])
    result = await run(cfg, runner)
    assert [d.op for d in result.applied] == ["add"]
    assert result.cost_usd == pytest.approx(0.37)
    [item] = result.playbooks["developer"].items
    assert item.lesson == LESSON and item.seen_in == ("GH-10",)
    assert item.since == "empty" and item.added_at == NOW


async def test_the_runner_gets_the_actor_model_and_the_reflect_cap(tmp_path):
    cfg = Configuration(product_dir=tmp_path, reflect_max_budget_usd=0.6)
    runner = ScriptedRunner(reflector=[reflection()])
    await run(cfg, runner)
    [call] = runner.calls
    assert call["max_budget_usd"] == 0.6
    assert call["model"] == cfg.actor_model
    assert call["role"] is REFLECTOR


def test_the_reflect_cap_defaults_to_one_dollar(cfg):
    assert cfg.reflect_max_budget_usd == 1.0


async def test_a_refused_delta_is_reported_with_its_reason_and_changes_nothing(cfg):
    runner = ScriptedRunner(
        reflector=[
            reflection(
                add("See https://example.com for the rule."),
                add(category="plan-calibration"),
                add(slug="no_such_role"),
            )
        ]
    )
    result = await run(cfg, runner)
    assert result.applied == ()
    reasons = [r.reason for r in result.refused]
    assert len(reasons) == 3
    assert "url" in reasons[0]
    assert "no cited task has a return_history entry" in reasons[1]
    assert "no_such_role" in reasons[2]
    assert result.playbooks == {}


async def test_a_lesson_rejected_before_is_refused(cfg):
    rejected = [pb.Rejected("developer", "standard-other", pb.normalise(LESSON))]
    runner = ScriptedRunner(reflector=[reflection(add())])
    result = await run(cfg, runner, rejected=rejected)
    assert result.applied == () and "rejected" in result.refused[0].reason


async def test_a_task_that_is_not_closed_backs_no_lesson(cfg):
    runner = ScriptedRunner(reflector=[reflection(add())])
    result = await run(cfg, runner, document(status="open"))
    assert result.applied == () and "not closed" in result.refused[0].reason


async def test_a_role_failure_is_a_failed_result_not_an_exception(cfg):
    broken = RawResult(None, 0.2, 1, "r")
    runner = ScriptedRunner(reflector=[broken, broken])
    result = await run(cfg, runner)
    assert result.failed and result.error
    assert result.applied == () and result.playbooks == {}
    assert result.cost_usd == pytest.approx(0.4)  # the paid attempts count


async def test_an_exception_from_the_runner_is_a_failed_result(cfg):
    runner = ScriptedRunner(reflector=[RuntimeError("harness down"), RuntimeError("harness down")])
    result = await run(cfg, runner)
    assert result.failed and "harness down" in result.error


async def test_a_malformed_delta_is_a_schema_failure_not_a_crash(cfg):
    bad = RawResult({"markdown": "x", "deltas": [{"op": "rewrite"}]}, 0.1, 1, "r")
    runner = ScriptedRunner(reflector=[bad, bad])
    result = await run(cfg, runner)
    assert result.failed


async def test_nothing_is_written_and_the_closed_document_is_not_touched(cfg):
    ports = memory_ports(ScriptedRunner(reflector=[reflection(add())]))
    doc = document()
    before = repr(doc)
    result = await reflect_task(cfg, ports, doc, {}, [], now=NOW)
    assert result.task_id == "GH-10"
    assert ports.documents.saved == []  # no task document, no checkpoint written
    assert repr(doc) == before
    assert ports.tracker.opened_issues == []


def test_reflector_is_a_role_that_cannot_write_and_reads_nothing_of_the_task():
    assert REFLECTOR in ROLES
    assert (REFLECTOR.slug, REFLECTOR.kind) == ("reflector", "actor")
    assert REFLECTOR.reads == () and not REFLECTOR.executes
    assert set(REFLECTOR.tools) <= {"Read"}
    assert REFLECTOR.max_turns == 6 and REFLECTOR.effort == "medium"
    assert REFLECTOR.schema is schemas.Reflection


def test_the_reflection_schema_is_valid_and_rejects_an_unknown_op():
    out = schemas.json_schema(schemas.Reflection, {})
    assert "deltas" in out["properties"]
    ok = {"markdown": "m", "deltas": [add()]}
    assert schemas.validate(schemas.Reflection, ok, {})["deltas"][0]["op"] == "add"
    with pytest.raises(ValueError):
        schemas.validate(schemas.Reflection, {"markdown": "m", "deltas": [{"op": "rewrite"}]}, {})


def test_the_reflector_prompt_renders_with_the_placeholders_the_system_already_has(cfg):
    prompt = load_prompt(REFLECTOR, cfg, memory_ports(), {}, **PLACEHOLDERS)
    assert prompt.text.strip() and "{{" not in prompt.text
    assert prompt.playbook == ""  # an empty playbook leaves the prompt as `render` made it


def test_preflight_renders_the_reflector_prompt_and_refuses_a_stray_placeholder(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(preflight_module, "CHECKOUT", tmp_path / "stage0")
    product = tmp_path / "product"
    ports = memory_ports()
    preflight(Configuration(product_dir=product), ports)  # the shipped prompt renders
    stray = replace(ports, prompts=MemoryPrompts(reflector="Today is {{date}}. Stray: {{nope}}"))
    with pytest.raises(ValueError, match="nope"):
        preflight(Configuration(product_dir=product), stray)
