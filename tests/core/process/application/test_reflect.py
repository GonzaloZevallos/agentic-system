"""The reflector's use case (ADR-0043): its input is built in code from a closed task's
`return_history` and review sections, its deltas fold through `playbook.apply`, and a
failure is a result, never an exception into a task.

Seam: `reflect_task` over `ScriptedRunner`/`memory_ports`, plus `preflight` rendering
`reflector.md` and the roster."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from agentic.core.process.application import preflight as preflight_module
from agentic.core.process.application import reflect
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.preflight import PLACEHOLDERS, preflight
from agentic.core.process.application.reflect import Reflected, reflect_task
from agentic.core.process.application.run_role import load_prompt
from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import rules, schemas
from agentic.core.process.domain.document import HUMAN
from agentic.core.process.domain.roles import REFLECTOR, ROLES, prompt_hash
from agentic.core.process.domain.task import Carried
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
    closed: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": "A task",
        "type": task_type,
        "status": "ready_for_review",
        **(
            {"closed_at": "2026-10-04T00:00:00+00:00", "delivery_outcome": "merged"}
            if closed
            else {}
        ),
        **extra,
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
    assert "PRD gap" in prompt  # the table attributes the PRD return to product_analyst
    assert "no-such-category" not in prompt
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
    history = [{"pair": "PM→Tech Lead", "category": "spec-behaviour", "reason": "x"}]
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
    # `since` is the hash of the section that contains the item: the key a task that ran
    # with it carries in `playbooks`, which is what the window matches.
    assert item.since == prompt_hash(pb.section(result.playbooks["developer"]))
    assert item.added_at == NOW


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
    result = await run(cfg, runner, document(closed=False))
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


def _validated(delta: dict[str, Any]) -> dict[str, Any]:
    raw = {"markdown": "m", "deltas": [delta]}
    return schemas.validate(schemas.Reflection, raw, {})["deltas"][0]


def test_a_delta_of_today_reads_as_it_did_and_a_new_one_carries_its_rating():
    old = _validated(add())
    assert (old["confidence"], old["scope"], old["decision"]) == ("high", "product", 0)
    new = _validated(
        {
            **add(category="human-decision"),
            "confidence": "low",
            "scope": "system",
            "source": "human_decision",
            "decision": 2,
        }
    )
    assert (new["confidence"], new["scope"], new["source"], new["decision"]) == (
        "low",
        "system",
        "human_decision",
        2,
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"source": "human_decision"},
        {"source": "human_decision", "decision": 0},
        {"confidence": "certain"},
        {"scope": "everywhere"},
        {"source": "rumour"},
    ],
)
def test_a_delta_is_refused_when_a_human_decision_names_no_decision_or_a_field_is_out_of_range(
    extra,
):
    with pytest.raises(ValueError):
        _validated({**add(), **extra})


def test_the_reflector_prompt_renders_with_the_placeholders_the_system_already_has(cfg):
    prompt = load_prompt(REFLECTOR, cfg, memory_ports(), {}, **PLACEHOLDERS)
    assert prompt.text.strip() and "{{" not in prompt.text
    assert prompt.playbook == ""  # an empty playbook leaves the prompt as `render` made it


def test_the_reflector_prompt_teaches_the_new_fields_the_schema_validates(cfg):
    text = load_prompt(REFLECTOR, cfg, memory_ports(), {}, **PLACEHOLDERS).text
    for section in ("## Returns", "## Findings", "## Design remarks", "## Human decisions"):
        assert section in text
    for field in ("`confidence`", "`scope`", "`source`", "`decision`", "omitted"):
        assert field in text
    assert "when in doubt" in text.lower() and "`system`" in text


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


# ---- the four sources (ADR-0056) -----------------------------------------------------------

DECISIONS = (
    "### Decision 1\n\nEscalated: quotes\n\nSpanish quotes in docs are a follow-up.\n\n"
    "### Decision 2\n\nEscalated: naming\n\nName the flag with a verb."
)


def finding(claim: str, **over: Any) -> dict[str, Any]:
    return {
        "subtask": "parser",
        "version": 1,
        "location": "a.py:1",
        "claim": claim,
        "severity": "blocking",
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
        **over,
    }


def test_a_dismissed_or_outside_finding_neither_appears_nor_backs_anything():
    doc = document(
        history=[],
        findings=[
            finding("OPEN-CLAIM"),
            finding("DISMISSED-CLAIM", status="dismissed", location="b.py:2"),
            finding("OUTSIDE-CLAIM", scope="outside", location="c.py:3"),
            finding("PLAN-CLAIM", scope="plan", category="plan-calibration", location="d.py:4"),
        ],
    )
    found = reflect.facts(doc)
    assert sorted(f.text.rsplit("\n", 1)[-1] for f in found) == ["OPEN-CLAIM", "PLAN-CLAIM"]
    assert set(reflect.backings(doc)) == {
        pb.Backing("finding", "developer", "spec-behaviour"),
        pb.Backing("finding", "tech_lead", "plan-calibration"),
    }
    text = reflect.reflection_input("GH-10", found, {}, [])
    assert "OPEN-CLAIM" in text and "DISMISSED-CLAIM" not in text and "OUTSIDE-CLAIM" not in text


def test_the_pm_final_goes_to_the_developer_and_a_lead_remark_to_its_designer():
    doc = document(
        history=[],
        findings=[finding("PM-CLAIM", subtask="integration", location="PM: final validation")],
        design_items=[
            {"role_slug": "ux_lead_final", "kind": "blocking", "step": "s1", "text": "UX-TEXT"},
            {"role_slug": "ui_lead", "kind": "observation", "step": "s2", "text": "UI-TEXT"},
            {"role_slug": "pm_consolidator", "kind": "observation", "step": "s", "text": "NOPE"},
        ],
        change_history=None,
    )
    assert set(reflect.backings(doc)) == {
        pb.Backing("finding", "developer_integrator", "spec-behaviour"),
        pb.Backing("design", "ux_designer", "design"),
        pb.Backing("design", "ui_designer", "design"),
    }


def test_a_merged_task_with_human_decisions_has_human_decision_backings_above_the_read_ones():
    doc = document(history=[], prompts={"developer": "h1", "pm": "h2"}, sections={HUMAN: DECISIONS})
    assert set(reflect.backings(doc)) == {
        pb.Backing("human_decision", "developer", "human-decision", "1"),
        pb.Backing("human_decision", "developer", "human-decision", "2"),
    }
    later = reflect.backings(doc, decisions=1)
    assert [b.ref for b in later] == ["2"]


def test_a_carried_human_section_keeps_its_decision_numbering():
    carried = Carried(1, 0.0, "", DECISIONS, ())
    entered = rules.entering(
        {"id": "GH-9", "title": "t", "description": "d"}, cap=False, carried=carried
    )
    assert set(reflect.decision_blocks(entered)) == {1, 2}
    # The next decision is numbered after the carried ones, and the document reads it so.
    more = rules.human_section(cast(Any, entered), None, "Use a table.")
    assert set(reflect.decision_blocks({"sections": {HUMAN: more}})) == {1, 2, 3}


def test_the_input_caps_each_source_by_severity_then_recency_and_says_what_it_omitted():
    claims = [finding(f"C{n}", location=f"f.py:{n}", severity="observation") for n in range(45)]
    claims.append(finding("THE-BLOCKING-ONE", location="old.py:1"))
    claims.append(finding("X" * 7000, location="big.py:1", severity="important"))
    doc = document(history=[], findings=[claims[-2], claims[-1], *claims[:-2]])
    text = reflect.reflection_input("GH-10", reflect.facts(doc), {}, [])
    assert "THE-BLOCKING-ONE" in text
    assert "[7 entries omitted]" in text
    assert "X" * 6001 not in text and "[the text is cut here]" in text
    assert text.count("### Finding ") == reflect.REFLECT_MAX_ENTRIES


async def test_a_decision_lesson_is_checked_against_the_decisions_the_code_read(cfg):
    doc = document(history=[], prompts={"developer": "h"}, sections={HUMAN: DECISIONS})
    lesson = "Treat Spanish quotes in docs as a follow-up, not a blocker."
    runner = ScriptedRunner(reflector=[reflection(add(lesson, category="human-decision"))])
    result = await run(cfg, runner, doc)
    assert "Human decision 1" in runner.calls[0]["prompt"]
    # The backing exists (the code built it), but the delta cites no `Decision N` yet: the
    # curator refuses it, it never counts as backed by prose.
    assert result.applied == () and "does not cite Decision" in result.refused[0].reason


async def test_the_decision_gate_reads_only_decisions_and_a_not_yet_ended_task_backs_nothing(cfg):
    doc = document(
        closed=False,
        status="discarded",
        history=[{"pair": "Tech Lead→Developer:p", "category": "standard-other", "reason": "R-X"}],
        prompts={"developer": "h"},
        sections={HUMAN: DECISIONS},
    )
    runner = ScriptedRunner(reflector=[reflection()])
    await reflect_task(cfg, memory_ports(runner), doc, {}, [], now=NOW, gate="decision")
    prompt = runner.calls[0]["prompt"]
    assert "R-X" not in prompt and "Decision 1" in prompt
    only_review = ScriptedRunner(reflector=[reflection(add())])
    result = await reflect_task(cfg, memory_ports(only_review), doc, {}, [], now=NOW)
    assert "not closed" in result.refused[0].reason  # the review gate wants a kept delivery
