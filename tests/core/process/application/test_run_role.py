"""Retry semantics and delivery of one role, against a scripted runner."""

from pathlib import Path

import pytest

from agentic.core.process.application.ports import AttemptFailed, RawResult
from agentic.core.process.application.run_role import (
    RoleFailure,
    context_for,
    run_role,
    run_with_retry,
)
from agentic.core.process.domain.roles import PJM, PM, PRODUCT_ANALYST
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, classified, memory_ports

pytestmark = pytest.mark.anyio

OK = classified(cost=0.03)


async def run_pjm(runner, retries=1, check=None):
    return await run_with_retry(
        PJM,
        "task",
        system_prompt="s",
        model="m",
        retries=retries,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        check=check,
    )


async def test_retry_passes_the_error_and_sums_cost():
    runner = ScriptedRunner(pjm=[AttemptFailed("structured output missing", 0.05), OK])
    out = await run_pjm(runner)
    assert out.cost_usd == pytest.approx(0.08)
    assert out.failures == [
        {"role": "PjM", "attempt": 1, "error": "structured output missing", "cost_usd": 0.05}
    ]
    assert runner.calls[0]["prompt"] == "task"
    assert "<PreviousAttemptFailed>\nstructured output missing" in runner.calls[1]["prompt"]
    assert runner.calls[1]["model"] == "m" and runner.calls[1]["cwd"] == Path("/p")


async def test_second_failure_raises_role_failure_with_cost():
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.05), AttemptFailed("b", 0.04)])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner)
    assert info.value.cost_usd == pytest.approx(0.09)
    assert str(info.value) == "PjM failed 2 times: b"


async def test_zero_retries_means_one_attempt():
    with pytest.raises(RoleFailure) as info:
        await run_pjm(ScriptedRunner(pjm=[AttemptFailed("a")]), retries=0)
    assert len(info.value.failures) == 1


@pytest.mark.parametrize(
    "raw,error",
    [
        (RawResult(None, 0.01), "structured output missing"),
        (RawResult({"markdown": "x", "type": "chore"}, 0.01), "structured output invalid"),
        (
            RawResult({"markdown": "  ", "type": "bug", "confidence": "high", "reason": "r"}),
            "empty",
        ),
    ],
)
async def test_invalid_deliveries_are_attempt_failures_with_their_cost(raw, error):
    runner = ScriptedRunner(pjm=[raw, raw])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner)
    assert all(error in f["error"] for f in info.value.failures)
    assert info.value.failures[0]["cost_usd"] == raw.cost_usd


async def test_check_failure_goes_back_to_the_model_like_a_schema_error():
    def check(data):
        if data["confidence"] == "high":
            raise ValueError("be humble")

    runner = ScriptedRunner(pjm=[OK, OK])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner, check=check)
    assert "be humble" in str(info.value)
    assert "be humble" in runner.calls[1]["prompt"] and "be humble" not in runner.calls[0]["prompt"]


async def test_delivery_carries_session_turns_and_model():
    raw = RawResult(OK.structured_output, 0.02, turns=2, session_id="sess-123")
    out = await run_pjm(ScriptedRunner(pjm=[raw]))
    assert (out.session_id, out.turns, out.model) == ("sess-123", 2, "m")
    assert out.text == "Clear." and "markdown" not in out.data
    assert out.data["type"] == "feature"


def test_context_for_reads_declared_sections_in_document_order():
    state = {
        "title": "Export",
        "description": "Users want CSV.\n\n## Why\n\nAudits.",
        "sections": {"PjM": "Clear.", "Product Analyst": "### Behaviour\n\nv1", "PM": "ret"},
    }
    text = context_for(state, PM, "## Product knowledge\n\nk")
    assert text.startswith("## Product knowledge\n\nk\n\n## Original request\n\nExport\n\n")
    assert "### Why" in text and "\n## Why" not in text  # the request cannot open sections
    assert "## PjM" not in text  # PM does not declare it
    assert text.index("## Product Analyst") < text.index("## PM\n\nret")
    assert (
        context_for(state, PJM)
        == "## Original request\n\nExport\n\nUsers want CSV.\n\n### Why\n\nAudits."
    )


async def test_run_role_builds_the_patch_and_the_history(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(
        product_analyst=[
            RawResult(
                {"markdown": "v2", "flags": {"touches_ui": True, "touches_copy": False}},
                0.1,
                3,
                "s2",
            )
        ]
    )
    ports = memory_ports(runner)
    state = {
        "id": "T-1",
        "title": "t",
        "description": "d",
        "sections": {"Product Analyst": "v1", "PM": "returned"},
        "runs": [
            {
                "role": "Product Analyst",
                "version": 1,
                "session_id": "s1",
                "model": "m",
                "cost_usd": 0.1,
                "turns": 1,
            }
        ],
    }
    output, patch = await run_role(state, PRODUCT_ANALYST, cfg, ports)
    assert output is not None and output.text == "v2"
    assert patch["current_role"] == "Product Analyst"
    assert set(patch["prompts"]) == {"product_analyst"}
    assert patch["sections"] == {"Product Analyst": "v2", "Product Analyst (v1)": "v1"}
    assert patch["runs"] == [
        {
            "role": "Product Analyst",
            "version": 2,
            "session_id": "s2",
            "model": "claude-sonnet-5",
            "cost_usd": 0.1,
            "turns": 3,
        }
    ]
    assert patch["cost_usd"] == 0.1 and patch["failures"] == []
    assert ports.documents.saved == []  # saving is the use case's, once per role
    call = runner.calls[0]
    assert call["system_prompt"].startswith("You are the Product Analyst")
    assert "- `touches_ui`: UI" in call["system_prompt"]
    assert call["json_schema"]["properties"]["flags"]["required"] == ["touches_ui", "touches_copy"]
    assert "## PM\n\nreturned" in call["prompt"] and "## Product Analyst\n\nv1" in call["prompt"]


async def test_run_role_turns_a_role_failure_into_an_escalation(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.05), AttemptFailed("b", 0.03)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner)
    )
    assert output is None
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "PjM failed 2 times: b"
    assert [f["error"] for f in patch["failures"]] == ["a", "b"]
    assert patch["cost_usd"] == pytest.approx(0.08)
    assert "sections" not in patch and "runs" not in patch
