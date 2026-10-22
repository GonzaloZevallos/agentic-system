"""Retry semantics and delivery of one role, against a scripted runner."""

from pathlib import Path
from typing import get_args

import anyio
import pytest
import yaml

from agentic.core.process.application.ports import AttemptFailed, RawResult
from agentic.core.process.application.preflight import PLACEHOLDERS
from agentic.core.process.application.run_role import (
    Output,
    RoleFailure,
    TaskCapReached,
    context_for,
    load_prompt,
    run_record,
    run_role,
    run_with_retry,
    with_context,
)
from agentic.core.process.application.schema_guard import RoleSchemaGuard
from agentic.core.process.domain import rules, turn_notice
from agentic.core.process.domain.document import HUMAN
from agentic.core.process.domain.playbook import Item, Playbook, parse
from agentic.core.process.domain.reaping import ReapedProcess
from agentic.core.process.domain.roles import (
    DEVELOPER,
    PJM,
    PM,
    PRODUCT_ANALYST,
    ROLES,
    TECH_LEAD_EPHEMERAL,
    TECH_LEAD_INTERVIEWEE,
    describe_flags,
    prompt_hash,
    render,
)
from agentic.core.process.domain.task import FailureReason, apply, delta
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, classified, delivered, memory_ports, questions, tech_answers

pytestmark = pytest.mark.anyio

OK = classified(cost=0.03)

LESSON = {
    "id": "developer-1",
    "lesson": "Run the checks first.",
    "category": "spec-behaviour",
    "since": "empty",
    "seen_in": ["GH-1"],
    "added_at": "2026-10-05",
}


async def run_pjm(
    runner,
    retries=1,
    check=None,
    spent_usd=0.0,
    task_cap_usd=None,
    resume=None,
    cold_prompt=None,
):
    return await run_with_retry(
        PJM,
        "task",
        system_prompt="s",
        model="m",
        retries=retries,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
        check=check,
        spent_usd=spent_usd,
        task_cap_usd=task_cap_usd,
        resume=resume,
        cold_prompt=cold_prompt,
    )


async def run_developer(runner, retries=1):
    return await run_with_retry(
        DEVELOPER,
        "task",
        system_prompt="s",
        model="m",
        retries=retries,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
    )


async def test_run_with_retry_builds_a_schema_guard_for_a_role_that_executes():
    # DEVELOPER executes (domain/roles.py); the guard is wired only for those roles
    # (brief's own condition), visible here rather than in `hooks_for`/`options_for`,
    # which take whatever they are given.
    runner = ScriptedRunner(developer=[delivered()])
    await run_developer(runner)
    guard = runner.calls[0]["guard"]
    assert isinstance(guard, RoleSchemaGuard)
    assert guard.schema is DEVELOPER.schema


async def test_run_with_retry_wires_no_guard_for_a_role_that_does_not_execute():
    runner = ScriptedRunner(pjm=[OK])
    await run_pjm(runner)
    assert runner.calls[0]["guard"] is None


async def test_the_same_guard_instance_and_cap_survive_the_retry():
    # "built in run_with_retry, outside the attempt loop": the one-denial cap has to
    # carry over from the first attempt to the retry, not reset with a fresh instance.
    schema_failure = AttemptFailed("structured output invalid: x", 0.05, reason="schema")
    runner = ScriptedRunner(developer=[schema_failure, delivered()])
    await run_developer(runner)
    first, second = runner.calls[0]["guard"], runner.calls[1]["guard"]
    assert first is second


async def test_retry_passes_the_error_and_sums_cost():
    runner = ScriptedRunner(pjm=[AttemptFailed("structured output missing", 0.05), OK])
    out = await run_pjm(runner)
    assert out.cost_usd == pytest.approx(0.08)
    assert out.failures == [
        {
            "role": "PjM",
            "attempt": 1,
            "error": "structured output missing",
            "cost_usd": 0.05,
            "session_id": "",
            "reason": "other",
            "slug": "pjm",
            "section": "PjM",
            "cold_fallback": False,
            "denials": 0,
            "turn_notice": False,
            "turn_notice_firings": 0,
            "deliveries": 0,
            "presubmit_denials": 0,
            "main_thread_turns": 0,
        }
    ]
    # No session survived the failure: the retry starts over, the error appended.
    assert runner.calls[0]["prompt"] == "task"
    assert runner.calls[1]["prompt"].startswith("task\n\n<PreviousAttemptFailed>\n")
    assert runner.calls[1]["resume"] is None
    assert runner.calls[1]["model"] == "m" and runner.calls[1]["cwd"] == Path("/p")


async def test_a_cancelled_attempt_starts_no_second_attempt_and_writes_no_failure():
    # Subtask `signals`, assertion only (`run_role.py` itself is untouched): a
    # cancellation is not `AttemptFailed`, so `run_with_retry`'s own `except
    # AttemptFailed: continue` never sees it and no `Failure` is appended -- the
    # cancellation just propagates out, the way `RoleFailure`/`TaskCapReached` never
    # do. Verified against a runner that actually hangs mid-attempt, not assumed.
    class HangingRunner:
        def __init__(self):
            self.calls = 0
            self.entered = anyio.Event()

        async def run_once(self, role, prompt, **kwargs):
            self.calls += 1
            self.entered.set()
            await anyio.sleep_forever()

    runner = HangingRunner()
    finished = False

    async def go():
        nonlocal finished
        await run_pjm(runner, retries=2)
        finished = True  # never reached: the attempt is cancelled first

    async with anyio.create_task_group() as tg:
        tg.start_soon(go)
        await runner.entered.wait()
        tg.cancel_scope.cancel()

    assert runner.calls == 1  # no retry started after the cancellation
    assert finished is False  # `run_with_retry` never returned an `Output`


async def test_a_failed_attempts_denials_are_recorded_on_its_failure():
    cut = AttemptFailed("run failed (max_turns)", 0.05, denials=3)
    runner = ScriptedRunner(pjm=[cut, OK])
    out = await run_pjm(runner)
    assert out.failures[0]["denials"] == 3


async def test_a_failed_attempts_turn_notice_is_recorded_on_its_failure():
    cut = AttemptFailed("run failed (max_turns)", 0.05, turn_notice=True)
    runner = ScriptedRunner(pjm=[cut, OK])
    out = await run_pjm(runner)
    assert out.failures[0]["turn_notice"] is True


async def test_retry_continues_the_session_a_failed_attempt_left():
    # The cap or the turns cut the attempt with its session alive: the retry resumes
    # it with the error as the next message, so the model keeps what it read and did.
    cut = AttemptFailed("run failed (error_max_budget_usd)", 3.05, session_id="sess-1")
    runner = ScriptedRunner(pjm=[cut, OK])
    out = await run_pjm(runner)
    assert out.failures[0]["session_id"] == "sess-1"
    retry = runner.calls[1]
    assert retry["resume"] == "sess-1"
    assert retry["prompt"].startswith("<PreviousAttemptFailed>\nrun failed (error_max_budget_usd)")
    assert "task" not in retry["prompt"]  # the session already has it


async def test_a_stale_caller_session_falls_back_cold_once():
    # `resume` is the caller's, but the harness never opened it: the retry must not try
    # the same dead id again (that would just repeat the failure); it drops it and runs
    # cold with the caller's own `cold_prompt` instead (ADR-0004, sixth addendum).
    stale = AttemptFailed("session not found", 0.02, session_id="")
    cold_delivery = RawResult(OK.structured_output, 0.03, session_id="cold-1")
    runner = ScriptedRunner(pjm=[stale, cold_delivery])
    out = await run_pjm(runner, resume="dead-id", cold_prompt="COLD CONTEXT")
    first, retry = runner.calls
    assert first["resume"] == "dead-id" and first["prompt"] == "task"
    assert retry["resume"] is None and retry["prompt"] == "COLD CONTEXT"
    # The decision is recorded once, read off the attempt that actually delivered.
    assert out.cold_fallback is True
    assert out.resumed_from == ""  # the cold attempt resumed nothing
    assert out.session_id == "cold-1"  # the new session the cold attempt opened
    assert out.failures[0]["cold_fallback"] is False  # the stale attempt is not the fallback


async def test_a_resumed_attempt_that_delivers_records_what_it_resumed():
    # No failure at all: `resumed_from` still names the session the delivery continued,
    # the same fact `RoleRun` persists, taken from the call and not re-derived from it.
    out = await run_pjm(ScriptedRunner(pjm=[OK]), resume="sess-live")
    assert out.resumed_from == "sess-live" and out.cold_fallback is False


async def test_no_cold_prompt_means_no_fallback_and_todays_retry_stands():
    # A caller with no `cold_prompt` (an interview's turn) keeps today's behaviour: the
    # stale id is tried again, exactly like any other attempt that left no session.
    stale = AttemptFailed("session not found", 0.02, session_id="")
    runner = ScriptedRunner(pjm=[stale, OK])
    out = await run_pjm(runner, resume="dead-id")
    retry = runner.calls[1]
    assert retry["resume"] == "dead-id"  # tried again, not dropped
    assert out.cold_fallback is False


async def test_the_cold_fallback_is_spent_at_most_once_per_run():
    # The cold attempt is the run's last (ROLE_RETRIES's default of one retry): if it
    # fails too there is no third attempt to worry about, but the record must still show
    # exactly one fallback, on the attempt that actually dropped the caller's session.
    stale = AttemptFailed("session not found", 0.02, session_id="")
    runner = ScriptedRunner(pjm=[stale, stale])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner, resume="dead-id", cold_prompt="COLD CONTEXT")
    calls = runner.calls
    assert calls[1]["resume"] is None and calls[1]["prompt"] == "COLD CONTEXT"  # the one fallback
    assert [f["cold_fallback"] for f in info.value.failures] == [False, True]


async def test_a_further_failure_of_the_cold_attempt_continues_it_not_the_dropped_id():
    # With a second retry available, a cold attempt that fails but does open its own
    # session is a run that already went cold: it continues from there, never back to
    # the id this run already gave up on.
    stale = AttemptFailed("session not found", 0.02, session_id="")
    cut_cold = AttemptFailed("cut", 0.01, session_id="cold-sess")
    cold_delivery = RawResult(OK.structured_output, 0.02, session_id="cold-sess")
    runner = ScriptedRunner(pjm=[stale, cut_cold, cold_delivery])
    out = await run_pjm(runner, retries=2, resume="dead-id", cold_prompt="COLD CONTEXT")
    third = runner.calls[2]
    assert third["resume"] == "cold-sess"  # the cold session, not "dead-id"
    assert "dead-id" not in third["prompt"]
    assert out.resumed_from == "cold-sess" and out.cold_fallback is False


async def test_an_attempt_failure_with_a_session_never_triggers_the_fallback():
    # The caller's session did open (the harness left a `session_id`): this is an
    # ordinary failure, not a stale session, so the normal retry continues it.
    cut = AttemptFailed("cut", 0.02, session_id="live-1")
    runner = ScriptedRunner(pjm=[cut, OK])
    out = await run_pjm(runner, resume="live-1", cold_prompt="COLD CONTEXT")
    assert runner.calls[1]["resume"] == "live-1"
    assert out.cold_fallback is False


async def test_an_invalid_delivery_also_keeps_its_session_for_the_retry():
    invalid = RawResult({"markdown": "x", "type": "chore"}, 0.01, session_id="sess-2")
    runner = ScriptedRunner(pjm=[invalid, OK])
    out = await run_pjm(runner)
    assert out.failures[0]["session_id"] == "sess-2"
    assert runner.calls[1]["resume"] == "sess-2"


async def test_second_failure_raises_role_failure_with_cost():
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.05), AttemptFailed("b", 0.04)])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner)
    assert info.value.cost_usd == pytest.approx(0.09)
    assert str(info.value) == "PjM failed 2 times: b"


async def test_role_failure_carries_the_last_attempts_motive_verbatim():
    # `AttemptFailed`'s own choke point (`rules.motive`) already guarantees the motive
    # names a cause; `RoleFailure` must not summarise or reshape it on top.
    motive = "API Error: You've hit your session limit · resets 11:20pm (subtype=sdk)"
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.01), AttemptFailed(motive, 0.02)])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner)
    assert str(info.value) == f"PjM failed 2 times: {motive}"
    assert info.value.failures[-1]["error"] == motive


async def test_role_failure_names_the_runs_total_denials_when_there_were_any():
    # The run's total: every recorded failure's denials, never a per-attempt figure
    # (glossary: Negación) — attempt 1 can burn every denial fighting the permission
    # layer and attempt 2 still fail on something else.
    a = AttemptFailed("a", 0.01, denials=5)
    b = AttemptFailed("b", 0.02, denials=3)
    runner = ScriptedRunner(pjm=[a, b])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner)
    assert str(info.value) == f"PjM failed 2 times: b {rules.denial_note(PJM, 8)}"


async def test_role_failure_says_nothing_about_denials_when_there_were_none():
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.05), AttemptFailed("b", 0.04)])
    with pytest.raises(RoleFailure) as info:
        await run_pjm(runner)
    assert str(info.value) == "PjM failed 2 times: b"


async def test_a_max_turns_cut_with_a_session_gets_the_turn_cut_message():
    # A cut with the work fine and a session alive (ADR-0004, seventh addendum): the
    # retry must read `rules.turn_cut_retry_message`, never the "fix what failed"
    # order, and still resume the session the cut attempt left.
    cut = AttemptFailed("run failed (max_turns)", 5.0, session_id="sess-cut", reason="max_turns")
    runner = ScriptedRunner(developer=[cut, delivered()])
    out = await run_with_retry(
        DEVELOPER,
        "task",
        system_prompt="s",
        model="m",
        retries=1,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
    )
    retry = runner.calls[1]
    assert retry["resume"] == "sess-cut"
    assert retry["prompt"] == rules.turn_cut_retry_message(DEVELOPER)
    assert "PreviousAttemptFailed" not in retry["prompt"]
    assert "Fix exactly what failed" not in retry["prompt"]
    assert out.cost_usd == pytest.approx(5.5)


async def test_a_max_turns_cut_with_no_session_appends_the_message_to_the_prompt():
    # The harness broke before opening a session: the message still lands the same
    # way today's retry appends any error, on the caller's own prompt and session.
    cut = AttemptFailed("run failed (max_turns)", 0.5, session_id="", reason="max_turns")
    runner = ScriptedRunner(developer=[cut, delivered()])
    out = await run_with_retry(
        DEVELOPER,
        "task",
        system_prompt="s",
        model="m",
        retries=1,
        vocabulary={},
        cwd=Path("/p"),
        runner=runner,
        owner_task="GH-1",
        resume="caller-session",
    )
    retry = runner.calls[1]
    assert retry["resume"] == "caller-session"
    assert retry["prompt"] == f"task\n\n{rules.turn_cut_retry_message(DEVELOPER)}"
    assert out.cost_usd == pytest.approx(1.0)


@pytest.mark.parametrize("reason", [r for r in get_args(FailureReason) if r != "max_turns"])
async def test_every_other_failure_reason_keeps_todays_retry_message_byte_for_byte(reason):
    cut = AttemptFailed("something broke", 0.05, session_id="sess-1", reason=reason)
    runner = ScriptedRunner(pjm=[cut, OK])
    out = await run_pjm(runner)
    retry = runner.calls[1]
    assert retry["resume"] == "sess-1"
    assert retry["prompt"] == (
        "<PreviousAttemptFailed>\nsomething broke\n</PreviousAttemptFailed>\n\n"
        "Retry. Fix exactly what failed."
    )
    assert out.cost_usd == pytest.approx(0.08)


async def test_the_task_cap_stops_a_role_before_its_first_attempt():
    # The task already spent the cap: nothing else runs, and it is not a role failure.
    runner = ScriptedRunner(pjm=[OK])
    with pytest.raises(TaskCapReached) as info:
        await run_pjm(runner, spent_usd=100.0, task_cap_usd=100.0)
    assert runner.calls == []
    assert info.value.failures == [] and info.value.cost_usd == 0.0
    assert str(info.value) == "PjM stopped by the task cap: 100.00 of 100.00 USD spent"


async def test_the_task_cap_counts_a_failed_attempt_before_the_retry():
    # The failed attempt is paid and pushes the run over the cap: the retry, which
    # could cost another attempt cap, does not happen. The failure is kept.
    runner = ScriptedRunner(pjm=[AttemptFailed("cut", 1.5, session_id="s"), OK])
    with pytest.raises(TaskCapReached) as info:
        await run_pjm(runner, spent_usd=9.0, task_cap_usd=10.0)
    assert len(runner.calls) == 1
    assert [f["error"] for f in info.value.failures] == ["cut"]
    assert info.value.cost_usd == pytest.approx(1.5)
    assert "10.50 of 10.00 USD spent" in str(info.value)


async def test_under_the_task_cap_the_role_runs_as_usual():
    out = await run_pjm(ScriptedRunner(pjm=[OK]), spent_usd=99.0, task_cap_usd=100.0)
    assert out.cost_usd == pytest.approx(0.03)


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


def test_every_role_reads_the_human_decisions_last_without_declaring_them():
    # The human's decisions hold for the whole task, so no role can miss them, and the
    # roles that enter later need not remember to declare them.
    state = {
        "title": "Export",
        "description": "Users want CSV.",
        "sections": {"Human": "### Decision 1\n\nUSD.", "Developer: api": "done"},
    }
    for role in ROLES:
        assert isinstance(role.reads, tuple) and HUMAN not in role.reads, role.slug
    text = context_for(state, DEVELOPER, extra_sections=("Developer: api",))
    assert text.endswith("## Developer: api\n\ndone\n\n## Human\n\n### Decision 1\n\nUSD.")
    assert context_for(state, PJM).endswith("## Human\n\n### Decision 1\n\nUSD.")
    assert "## Human" not in context_for({**state, "sections": {}}, PJM)


async def test_run_role_builds_the_patch_and_the_history(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    actor_model = Configuration.model_fields["actor_model"].default
    runner = ScriptedRunner(
        product_analyst=[
            RawResult(
                {"markdown": "v2", "flags": {"touches_ui": True, "touches_copy": False}},
                0.1,
                3,
                "s2",
                # A sub-agent spent part of this: 0.02 of the 0.1 total is not the
                # role's own model.
                cost_by_model={actor_model: 0.08, "claude-haiku-5": 0.02},
                usage={"input": 100, "output": 50, "cache_read": 10, "cache_write": 5},
                calls=2,
                tool_calls={"Read": 1},
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
    (run,) = patch["runs"]
    started_at, ended_at, queue_wait_s = (
        run.pop("started_at"),
        run.pop("ended_at"),
        run.pop("queue_wait_s"),
    )
    assert run == {
        "role": "Product Analyst",
        "slug": "product_analyst",
        "section": "Product Analyst",
        "version": 2,
        "session_id": "s2",
        "model": actor_model,
        "cost_usd": 0.1,
        "cost_by_model": {actor_model: 0.08, "claude-haiku-5": 0.02},
        "turns": 3,
        "max_turns": PRODUCT_ANALYST.max_turns,
        "tokens": {"input": 100, "output": 50, "cache_read": 10, "cache_write": 5},
        "calls": 2,
        "tool_calls": {"Read": 1},
        "subagent_cost_usd": pytest.approx(0.02),
        "resumed_from": "",
        "cold_fallback": False,
        "failed_cost_usd": 0.0,
        "denials": 0,
        "turn_notice": False,
        "turn_notice_firings": 0,
        "deliveries": 0,
        "presubmit_denials": 0,
        "main_thread_turns": 0,
    }
    # Timed inside the concurrency slot: started no later than ended, both ISO-8601 UTC.
    assert started_at <= ended_at
    assert queue_wait_s >= 0.0
    assert patch["cost_usd"] == 0.1 and patch["failures"] == []
    assert ports.documents.saved == []  # saving is the use case's, once per role
    call = runner.calls[0]
    assert call["system_prompt"].startswith("You are the Product Analyst")
    assert "- `touches_ui`: UI" in call["system_prompt"]
    assert call["json_schema"]["properties"]["flags"]["required"] == ["touches_ui", "touches_copy"]
    assert "## PM\n\nreturned" in call["prompt"] and "## Product Analyst\n\nv1" in call["prompt"]


def test_with_context_prepends_the_full_reading_before_the_given_text():
    # The shape every cold-start caller already needs (`interview`'s first round, a
    # consultant with no session, and now a returned Developer's cold fallback):
    # standards, the declared sections plus `extra_sections`, then whatever the
    # caller's own text adds on top.
    state = {
        "title": "Export",
        "description": "Users want CSV.",
        "sections": {"Developer: api": "done"},
    }
    ports = memory_ports()
    text = with_context(
        state, DEVELOPER, ports, None, "## Delta\n\nnew", extra_sections=("Developer: api",)
    )
    assert text.startswith("## Engineering standards\n\nHex.\n\n## Original request")
    assert text.endswith("## Developer: api\n\ndone\n\n## Delta\n\nnew")


def test_run_record_carries_tokens_calls_and_tool_calls_from_the_output():
    output = Output(
        text="ok",
        data={},
        cost_usd=0.5,
        turns=4,
        session_id="s",
        model="claude-sonnet-5",
        usage={"input": 10, "output": 20, "cache_read": 30, "cache_write": 40},
        calls=5,
        tool_calls={"Bash": 3, "Read": 2},
        subagent_cost_usd=0.07,
    )
    run = run_record({}, PJM, "PjM", output)
    assert run["tokens"] == {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}
    assert run["calls"] == 5
    assert run["tool_calls"] == {"Bash": 3, "Read": 2}
    assert run["subagent_cost_usd"] == 0.07
    assert run["denials"] == 0


def test_run_record_carries_the_delivering_attempts_denials():
    output = Output(text="ok", data={}, denials=2)
    assert run_record({}, PJM, "PjM", output)["denials"] == 2


def test_run_record_carries_the_delivering_attempts_turn_notice():
    output = Output(text="ok", data={}, turn_notice=True)
    assert run_record({}, PJM, "PjM", output)["turn_notice"] is True


def test_run_record_carries_the_delivering_attempts_deliveries():
    output = Output(text="ok", data={}, deliveries=3)
    assert run_record({}, PJM, "PjM", output)["deliveries"] == 3


def test_run_record_carries_the_delivering_attempts_presubmit_denials():
    output = Output(text="ok", data={}, presubmit_denials=1)
    assert run_record({}, PJM, "PjM", output)["presubmit_denials"] == 1


async def test_run_role_appends_the_turn_notice_note_when_the_delivery_had_one(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    delivery = delivered().structured_output
    runner = ScriptedRunner(
        developer=[RawResult(delivery, 0.03, turn_notice=True, turn_notice_firings=1)]
    )
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"},
        DEVELOPER,
        cfg,
        memory_ports(runner),
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )
    assert output is not None
    note = turn_notice.notice_note(cut=False, firings=1)
    assert patch["sections"]["Developer"] == f"{note}\n\n{output.text}"


async def test_run_role_folds_a_failed_attempts_turn_notice_into_the_delivering_runs_note(
    tmp_path,
):
    # The run's total, the same fold `denials` gets: attempt 1 can enter the window and
    # get cut, attempt 2 can still deliver clean, and the run stays legible either way,
    # marked as a run that was cut (`cut=True`) even though the delivering attempt's
    # own flag is `False`.
    cfg = Configuration(product_dir=tmp_path)
    cut = AttemptFailed(
        "run failed (max_turns)", 0.01, reason="max_turns", turn_notice=True, turn_notice_firings=2
    )
    runner = ScriptedRunner(
        developer=[cut, RawResult(delivered().structured_output, 0.03, turn_notice=False)]
    )
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"},
        DEVELOPER,
        cfg,
        memory_ports(runner),
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )
    assert output is not None
    note = turn_notice.notice_note(cut=True, firings=2)
    assert patch["sections"]["Developer"] == f"{note}\n\n{output.text}"


async def test_run_role_adds_no_turn_notice_note_when_the_run_had_none(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(developer=[RawResult(delivered().structured_output, 0.03)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"},
        DEVELOPER,
        cfg,
        memory_ports(runner),
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )
    assert output is not None
    assert patch["sections"]["Developer"] == output.text


async def test_run_role_orders_the_denial_note_before_the_turn_notice_note(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(
        developer=[
            RawResult(
                delivered().structured_output,
                0.03,
                denials=1,
                turn_notice=True,
                turn_notice_firings=1,
            )
        ]
    )
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"},
        DEVELOPER,
        cfg,
        memory_ports(runner),
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )
    assert output is not None
    note = turn_notice.notice_note(cut=False, firings=1)
    expected = f"{rules.denial_note(DEVELOPER, 1)}\n\n{note}\n\n{output.text}"
    assert patch["sections"]["Developer"] == expected


async def test_run_role_prepends_the_denial_note_when_the_delivery_had_any(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[RawResult(OK.structured_output, 0.03, denials=2)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner), product="x"
    )
    assert output is not None
    note = rules.denial_note(PJM, 2)
    assert patch["sections"]["PjM"] == f"{note}\n\n{output.text}"


async def test_run_role_folds_a_failed_attempts_denials_into_the_delivering_runs_note(tmp_path):
    # The run's total: the failed attempt's denials plus the delivering one's, not the
    # delivering attempt's alone — attempt 1 can burn every denial and attempt 2 deliver
    # clean, which must not read as a run with none.
    cfg = Configuration(product_dir=tmp_path)
    cut = AttemptFailed("run failed (max_turns)", 0.01, denials=6)
    runner = ScriptedRunner(pjm=[cut, RawResult(OK.structured_output, 0.03, denials=0)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner), product="x"
    )
    assert output is not None
    assert patch["sections"]["PjM"] == f"{rules.denial_note(PJM, 6)}\n\n{output.text}"


async def test_run_role_adds_no_note_when_the_run_had_no_denials(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    ports = memory_ports(ScriptedRunner(pjm=[OK]))
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, ports, product="x"
    )
    assert output is not None
    assert patch["sections"]["PjM"] == output.text


async def test_run_role_adds_no_deliveries_note_with_a_single_emission(tmp_path):
    # Byte-for-byte today's section: the line only earns its place past one.
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[RawResult(OK.structured_output, 0.03, deliveries=1)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner), product="x"
    )
    assert output is not None
    assert patch["sections"]["PjM"] == output.text


async def test_run_role_adds_the_deliveries_note_when_there_were_several(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[RawResult(OK.structured_output, 0.03, deliveries=3)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner), product="x"
    )
    assert output is not None
    note = rules.deliveries_note(3)
    assert patch["sections"]["PjM"] == f"{note}\n\n{output.text}"


async def test_run_role_folds_a_failed_attempts_deliveries_into_the_delivering_runs_note(
    tmp_path,
):
    # The run's total, the same fold `denials` gets: attempt 1 can spend several
    # emissions on an invalid shape (issue #32) and attempt 2 still deliver on its
    # first try, and the run must not read as though it had only one.
    cfg = Configuration(product_dir=tmp_path)
    cut = AttemptFailed("structured output invalid", 0.01, reason="schema", deliveries=2)
    runner = ScriptedRunner(pjm=[cut, RawResult(OK.structured_output, 0.03, deliveries=1)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner), product="x"
    )
    assert output is not None
    assert patch["sections"]["PjM"] == f"{rules.deliveries_note(3)}\n\n{output.text}"


async def test_run_role_orders_the_deliveries_note_after_denial_and_turn_notice(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(
        developer=[
            RawResult(
                delivered().structured_output,
                0.03,
                denials=1,
                turn_notice=True,
                turn_notice_firings=1,
                deliveries=3,
            )
        ]
    )
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"},
        DEVELOPER,
        cfg,
        memory_ports(runner),
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )
    assert output is not None
    note = turn_notice.notice_note(cut=False, firings=1)
    expected = (
        f"{rules.denial_note(DEVELOPER, 1)}\n\n{note}\n\n"
        f"{rules.deliveries_note(3)}\n\n{output.text}"
    )
    assert patch["sections"]["Developer"] == expected


def test_run_record_stamps_the_roles_own_turn_cap():
    """`max_turns` comes from the `Role` that ran, not the output: the cap in force for
    this run, so a reader can compute turns spent against it without a second lookup
    into `roles.py` (and without it drifting if the role's cap changes later)."""
    output = Output(text="ok", data={}, cost_usd=0.1, turns=1, session_id="s", model="m")
    run = run_record({}, PJM, "PjM", output)
    assert run["max_turns"] == PJM.max_turns == 2


def test_run_record_carries_the_failed_attempts_cost_already_inside_cost_usd():
    output = Output(
        text="ok",
        data={},
        cost_usd=0.08,
        failures=[
            {
                "role": "PjM",
                "attempt": 1,
                "error": "e",
                "cost_usd": 0.05,
                "session_id": "",
                "reason": "other",
                "slug": "pjm",
                "section": "PjM",
                "cold_fallback": False,
            }
        ],
    )
    run = run_record({}, PJM, "PjM", output)
    assert run["failed_cost_usd"] == pytest.approx(0.05)


def test_run_record_failed_cost_usd_is_zero_without_a_failed_attempt():
    output = Output(text="ok", data={}, cost_usd=0.03)
    run = run_record({}, PJM, "PjM", output)
    assert run["failed_cost_usd"] == 0.0


async def test_run_role_turns_a_role_failure_into_an_escalation(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.05), AttemptFailed("b", 0.03)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner), product="x"
    )
    assert output is None
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "PjM failed 2 times: b"
    assert patch["escalation_class"] == "failure"
    assert [f["error"] for f in patch["failures"]] == ["a", "b"]
    assert patch["cost_usd"] == pytest.approx(0.08)
    assert "sections" not in patch and "runs" not in patch


async def test_run_role_escalates_when_the_task_cap_is_reached(tmp_path):
    cfg = Configuration(product_dir=tmp_path, task_max_budget_usd=50.0)
    runner = ScriptedRunner(pjm=[OK])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d", "cost_usd": 50.0},
        PJM,
        cfg,
        memory_ports(runner),
        product="x",
    )
    assert output is None and runner.calls == []
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "PjM stopped by the task cap: 50.00 of 50.00 USD spent"
    assert patch["escalation_class"] == "cap"
    assert patch["failures"] == [] and patch["cost_usd"] == 0.0
    assert "sections" not in patch and "runs" not in patch


@pytest.mark.parametrize("subtask_count", [1, 12])
async def test_the_task_cap_is_the_same_flat_ceiling_for_any_plan_size(tmp_path, subtask_count):
    # ADR-0004, 2026-10-05 addendum: 250 for a 1-subtask plan and a 12-subtask plan alike.
    subtasks = {f"s{n}": {} for n in range(subtask_count)}

    async def run(cost_usd, cfg):
        state = {"id": "T-1", "title": "t", "description": "d", "subtasks": subtasks}
        runner = ScriptedRunner(pjm=[OK])
        output, patch = await run_role(
            {**state, "cost_usd": cost_usd}, PJM, cfg, memory_ports(runner), product="x"
        )
        return output, patch, runner

    default = Configuration(product_dir=tmp_path, task_max_budget_usd=250.0)
    output, _, runner = await run(249.0, default)
    assert output is not None and len(runner.calls) == 1
    output, patch, runner = await run(250.0, default)
    assert output is None and runner.calls == []
    assert patch["escalation_class"] == "cap"
    assert patch["escalated"] == "PjM stopped by the task cap: 250.00 of 250.00 USD spent"
    # The human raises it, and the plan size changes nothing.
    raised = Configuration(product_dir=tmp_path, task_max_budget_usd=250.0, task_budget_usd=400.0)
    output, _, runner = await run(250.0, raised)
    assert output is not None and len(runner.calls) == 1


async def test_the_ephemeral_tech_lead_runs_on_the_actor_model_the_interviewee_does_not(
    tmp_path,
):
    # ADR-0014, addendum 2026-10-05: the efímero (interviewer) resolves to `actor_model`
    # through `role.runs_on`, while Tech Lead interviewee (who plans) stays on
    # `verifier_model`, unmoved -- the rejected case that fails if `run_role.py` still
    # passes `role.kind` for the efímero.
    cfg = Configuration(
        product_dir=tmp_path, actor_model="actor-sentinel", verifier_model="verifier-sentinel"
    )
    state = {"id": "T-1", "title": "t", "description": "d"}
    runner = ScriptedRunner(
        tech_lead_ephemeral=[questions(done=True)],
        tech_lead_interviewee=[tech_answers()],
    )
    ports = memory_ports(runner)
    await run_role(state, TECH_LEAD_EPHEMERAL, cfg, ports, round=1, max_rounds=12)
    await run_role(state, TECH_LEAD_INTERVIEWEE, cfg, ports)
    ephemeral_call = next(c for c in runner.calls if c["slug"] == "tech_lead_ephemeral")
    interviewee_call = next(c for c in runner.calls if c["slug"] == "tech_lead_interviewee")
    assert ephemeral_call["model"] == cfg.actor_model
    assert interviewee_call["model"] == cfg.verifier_model
    assert interviewee_call["model"] != cfg.actor_model


def lessons(*texts: str) -> Playbook:
    return Playbook(
        slug="developer",
        items=tuple(
            Item(
                id=f"developer-{n}",
                lesson=text,
                category="spec-behaviour",
                since="empty",
                seen_in=("GH-1",),
                added_at="2026-10-05",
            )
            for n, text in enumerate(texts, 1)
        ),
    )


CFG = Configuration(product_dir=Path("/repos/product"))


def load(ports, role=DEVELOPER):
    return load_prompt(role, CFG, ports, ports.flags.load(), **PLACEHOLDERS)


def test_an_empty_playbook_leaves_the_prompt_and_its_hash_as_render_makes_them():
    ports = memory_ports()
    raw, source = ports.prompts.raw(DEVELOPER)
    expected = render(
        raw,
        source,
        max_changes=CFG.max_changes_requested_per_pair,
        max_turns=DEVELOPER.max_turns,
        flags=describe_flags(ports.flags.load()),
        **PLACEHOLDERS,
    )
    assert load(ports) == expected
    assert load(ports).playbook == ""


def test_a_playbook_adds_its_section_after_the_text_and_moves_the_hash():
    ports = memory_ports()
    empty = load(ports)
    ports.prompts.playbooks["developer"] = lessons("Run the checks first.", "Cite the file.")
    loaded = load(ports)
    section = (
        "### Playbook\n- Run the checks first. (spec-behaviour)\n- Cite the file. (spec-behaviour)"
    )
    assert loaded.text == empty.text + "\n\n" + section
    raw, _ = ports.prompts.raw(DEVELOPER)
    assert loaded.hash == prompt_hash(raw + "\n" + section)
    assert loaded.hash != empty.hash
    assert loaded.playbook == prompt_hash(section)
    assert loaded.source == empty.source


def test_a_lesson_with_a_placeholder_cannot_even_be_parsed():
    with pytest.raises(ValueError, match="placeholder"):
        parse(
            yaml.safe_dump({"items": [{**LESSON, "lesson": "Use {{date}} always."}]}),
            "developer",
        )


def test_the_section_is_never_filled_like_the_template():
    """Built past `parse` on purpose, so the section's own text is under test: a
    `{{date}}` in it stays `{{date}}`, not today's date, and no placeholder is looked up."""
    ports = memory_ports()
    ports.prompts.playbooks["developer"] = lessons("Keep {{date}} and {{nope}} as they are.")
    loaded = load(ports)
    assert "- Keep {{date}} and {{nope}} as they are. (spec-behaviour)" in loaded.text


async def _run_developer(state, ports, tmp_path):
    return await run_role(
        state,
        DEVELOPER,
        Configuration(product_dir=tmp_path),
        ports,
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )


def _ports_for_runs(count: int = 1):
    runner = ScriptedRunner(
        developer=[RawResult(delivered().structured_output, 0.03) for _ in range(count)]
    )
    return memory_ports(runner)


def _playbook_hash(ports) -> str:
    return load(ports).playbook


async def test_the_first_run_with_a_playbook_records_its_hash_under_playbooks(tmp_path):
    ports = _ports_for_runs()
    ports.prompts.playbooks["developer"] = lessons("Run the checks first.")
    _, patch = await _run_developer(
        {"id": "T-1", "title": "t", "description": "d"}, ports, tmp_path
    )
    assert patch["playbooks"] == {"developer": _playbook_hash(ports)}
    assert "playbooks_changed" not in patch


async def test_a_task_with_no_playbook_gets_neither_key(tmp_path):
    ports = _ports_for_runs()
    _, patch = await _run_developer(
        {"id": "T-1", "title": "t", "description": "d"}, ports, tmp_path
    )
    assert "playbooks" not in patch
    assert "playbooks_changed" not in patch


async def test_the_first_playbook_hash_is_never_overwritten_across_runs(tmp_path):
    ports = _ports_for_runs(3)
    ports.prompts.playbooks["developer"] = lessons("Run the checks first.")
    state = {"id": "T-1", "title": "t", "description": "d"}
    first_hash = _playbook_hash(ports)
    _, first = await _run_developer(state, ports, tmp_path)
    state = apply(state, first)
    # The same playbook again: nothing new to record.
    _, same = await _run_developer(state, ports, tmp_path)
    assert "playbooks" not in same and "playbooks_changed" not in same
    # A different one: the first stays, the later goes beside it.
    ports.prompts.playbooks["developer"] = lessons("Run the checks first.", "Cite the file.")
    later_hash = _playbook_hash(ports)
    _, later = await _run_developer(state, ports, tmp_path)
    assert "playbooks" not in later
    assert later["playbooks_changed"] == {"developer": later_hash}
    folded = apply(state, later)
    assert folded["playbooks"] == {"developer": first_hash}
    assert folded["playbooks_changed"] == {"developer": later_hash}


async def test_a_playbook_that_appears_after_an_empty_first_run_is_recorded_as_changed(tmp_path):
    ports = _ports_for_runs(2)
    state = {"id": "T-1", "title": "t", "description": "d"}
    _, first = await _run_developer(state, ports, tmp_path)
    state = apply(state, first)
    ports.prompts.playbooks["developer"] = lessons("Run the checks first.")
    _, later = await _run_developer(state, ports, tmp_path)
    recorded = {"developer": _playbook_hash(ports)}
    assert later["playbooks"] == recorded
    assert later["playbooks_changed"] == recorded


def test_the_playbook_keys_fold_and_subtract_through_the_reducers():
    before = {"id": "T-1", "title": "t", "description": "d", "playbooks": {"developer": "a"}}
    patch = {"playbooks": {"developer": "b", "pm": "c"}, "playbooks_changed": {"developer": "b"}}
    after = apply(before, patch)
    assert after["playbooks"] == {"developer": "a", "pm": "c"}
    assert after["playbooks_changed"] == {"developer": "b"}
    assert delta(before, after) == {
        "playbooks": {"pm": "c"},
        "playbooks_changed": {"developer": "b"},
    }


# --- GH-282: what the reaper stopped travels with the attempt --------------------------

LEAK: list[ReapedProcess] = [{"pid": 41, "command": "sample_app.py", "outcome": "kill"}]


async def test_run_role_marks_the_attempt_with_the_parent_task_and_records_what_was_reaped(
    tmp_path,
):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(developer=[RawResult(delivered().structured_output, 0.03, reaped=LEAK)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d", "subtask_id": "api"},
        DEVELOPER,
        cfg,
        memory_ports(runner),
        subtask="api",
        invariants="",
        branches="",
        checks="",
    )
    assert output is not None
    assert runner.calls[0]["owner_task"] == "T-1"  # the parent, never the subtask
    assert patch["runs"][0]["reaped"] == LEAK
    assert patch["sections"]["Developer"] == (f"{rules.reaped_note(LEAK)}\n\n{output.text}")
    assert "Reaped 1 background process(es): 1 kill" in rules.reaped_note(LEAK)


async def test_a_failed_attempts_reaped_processes_are_recorded_on_its_failure():
    runner = ScriptedRunner(pjm=[AttemptFailed("boom", 0.05, reaped=LEAK), OK])
    out = await run_pjm(runner)
    assert out.failures[0]["reaped"] == LEAK
    assert "reaped" not in (await run_pjm(ScriptedRunner(pjm=[OK]))).failures


def test_a_run_with_nothing_reaped_writes_no_reaped_key():
    output = Output(text="ok", data={})
    assert "reaped" not in run_record({}, PJM, "PjM", output)


# --- GH-292: the index is written where the role runs, before it runs -------------------

CHECKOUT = Path("/memory/worktrees/GH-1")


def _reader_ports(**kwargs):
    from tests.fakes import MemoryKnowledge

    knowledge = MemoryKnowledge(**kwargs)
    knowledge.index = {".agentic/index/product.md": "- docs/product/x.md\n"}
    return memory_ports(knowledge=knowledge), knowledge


async def _run(role, ports, runner, **kwargs):
    state = {"id": "GH-1", "title": "t", "description": "d", "sections": {}}
    cfg = Configuration(product_dir=Path("/product"), role_retries=0)
    return await run_role(state, role, cfg, ports, **PLACEHOLDERS, **kwargs)


async def test_a_role_that_reads_by_index_gets_it_written_into_the_checkout_it_runs_in():
    ports, knowledge = _reader_ports()
    runner = ScriptedRunner(pm=[delivered()])
    ports = memory_ports(knowledge=knowledge, runner=runner)
    await _run(PM, ports, runner, cwd=CHECKOUT)
    assert ports.workspace.indexes == {CHECKOUT: knowledge.index}
    assert runner.calls[0]["cwd"] == CHECKOUT
    # One value, never two: the knowledge was read from the very checkout the role ran in.
    assert knowledge.read_from == CHECKOUT


async def test_a_resumed_role_that_receives_only_a_delta_still_gets_a_current_index():
    ports, knowledge = _reader_ports()
    runner = ScriptedRunner(pm=[delivered()])
    ports = memory_ports(knowledge=knowledge, runner=runner)
    await _run(PM, ports, runner, cwd=CHECKOUT, resume="s1", prompt="## Delta\n\nnew")
    assert ports.workspace.indexes == {CHECKOUT: knowledge.index}
    assert runner.calls[0]["prompt"] == "## Delta\n\nnew"


async def test_a_role_that_reads_nothing_gets_no_index_written():
    ports, _ = _reader_ports()
    runner = ScriptedRunner(pjm=[OK])
    ports = memory_ports(runner=runner)
    await _run(PJM, ports, runner, cwd=CHECKOUT)
    assert ports.workspace.indexes == {}


async def test_the_default_checkout_is_the_product_dir_for_the_read_and_the_write():
    ports, knowledge = _reader_ports()
    runner = ScriptedRunner(pm=[delivered()])
    ports = memory_ports(knowledge=knowledge, runner=runner)
    await _run(PM, ports, runner)
    assert set(ports.workspace.indexes) == {Path("/product")} == {knowledge.read_from}


async def test_the_developer_gets_the_standards_by_index_and_a_verifier_gets_them_whole():
    from agentic.core.process.domain.roles import TECH_LEAD

    ports, knowledge = _reader_ports()
    runner = ScriptedRunner(developer=[delivered()], tech_lead=[delivered()])
    ports = memory_ports(knowledge=knowledge, runner=runner)
    await _run(DEVELOPER, ports, runner, cwd=CHECKOUT)
    assert knowledge.standards_whole is False and knowledge.standards_from == CHECKOUT
    await _run(TECH_LEAD, ports, runner, cwd=CHECKOUT)
    assert knowledge.standards_whole is True and knowledge.standards_from == CHECKOUT


@pytest.mark.parametrize(
    "role",
    [r for r in ROLES if r.reads_knowledge or r.reads_standards],
    ids=lambda role: role.slug,
)
async def test_every_reader_gets_an_index_from_the_checkout_it_runs_in(role, tmp_path):
    # The knowledge adapter is built over ANOTHER directory with other content: the index
    # can only come from the `cwd` the role runs in. And one real entry of that index
    # exists under the checkout (or is one of the files the system writes there).
    from agentic.core.knowledge.adapters.filesystem.knowledge import FilesystemKnowledge

    other, checkout = tmp_path / "other", tmp_path / "checkout"
    for root, name in ((other, "elsewhere"), (checkout, "here")):
        (root / "docs/product").mkdir(parents=True)
        (root / "docs/product" / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
        (root / "CODING_STANDARDS.md").write_text("# S\n\n## 1. Hex\n\nx\n", encoding="utf-8")
    runner = ScriptedRunner(**{role.slug: [delivered()]})
    ports = memory_ports(knowledge=FilesystemKnowledge(other), runner=runner)
    state = {"id": "GH-1", "title": "t", "description": "d", "sections": {}}
    cfg = Configuration(product_dir=other, role_retries=0)
    await run_role(state, role, cfg, ports, cwd=checkout, **PLACEHOLDERS)
    assert runner.calls[0]["cwd"] == checkout
    files = ports.workspace.indexes[checkout]
    listed = " ".join(files.values())
    assert "docs/product/here.md" in listed and "elsewhere" not in listed
    assert (checkout / "docs/product/here.md").exists()
    assert not (checkout / "docs/product/elsewhere.md").exists()
