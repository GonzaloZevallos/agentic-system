"""Retry semantics and delivery of one role, against a scripted runner."""

from pathlib import Path

import pytest

from agentic.core.process.application.ports import AttemptFailed, RawResult
from agentic.core.process.application.run_role import (
    Output,
    RoleFailure,
    TaskCapReached,
    context_for,
    run_record,
    run_role,
    run_with_retry,
    task_cap,
    with_context,
)
from agentic.core.process.domain import rules
from agentic.core.process.domain.document import HUMAN
from agentic.core.process.domain.roles import DEVELOPER, PJM, PM, PRODUCT_ANALYST, ROLES
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, classified, memory_ports

pytestmark = pytest.mark.anyio

OK = classified(cost=0.03)


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
        check=check,
        spent_usd=spent_usd,
        task_cap_usd=task_cap_usd,
        resume=resume,
        cold_prompt=cold_prompt,
    )


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
        }
    ]
    # No session survived the failure: the retry starts over, the error appended.
    assert runner.calls[0]["prompt"] == "task"
    assert runner.calls[1]["prompt"].startswith("task\n\n<PreviousAttemptFailed>\n")
    assert runner.calls[1]["resume"] is None
    assert runner.calls[1]["model"] == "m" and runner.calls[1]["cwd"] == Path("/p")


async def test_a_failed_attempts_denials_are_recorded_on_its_failure():
    cut = AttemptFailed("run failed (max_turns)", 0.05, denials=3)
    runner = ScriptedRunner(pjm=[cut, OK])
    out = await run_pjm(runner)
    assert out.failures[0]["denials"] == 3


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
    runner = ScriptedRunner(
        product_analyst=[
            RawResult(
                {"markdown": "v2", "flags": {"touches_ui": True, "touches_copy": False}},
                0.1,
                3,
                "s2",
                # A sub-agent spent part of this: 0.02 of the 0.1 total is not the
                # role's own model.
                cost_by_model={"claude-sonnet-5": 0.08, "claude-haiku-5": 0.02},
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
        "model": "claude-sonnet-5",
        "cost_usd": 0.1,
        "cost_by_model": {"claude-sonnet-5": 0.08, "claude-haiku-5": 0.02},
        "turns": 3,
        "tokens": {"input": 100, "output": 50, "cache_read": 10, "cache_write": 5},
        "calls": 2,
        "tool_calls": {"Read": 1},
        "subagent_cost_usd": pytest.approx(0.02),
        "resumed_from": "",
        "cold_fallback": False,
        "denials": 0,
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


async def test_run_role_prepends_the_denial_note_when_the_delivery_had_any(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[RawResult(OK.structured_output, 0.03, denials=2)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner)
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
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner)
    )
    assert output is not None
    assert patch["sections"]["PjM"] == f"{rules.denial_note(PJM, 6)}\n\n{output.text}"


async def test_run_role_adds_no_note_when_the_run_had_no_denials(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    ports = memory_ports(ScriptedRunner(pjm=[OK]))
    output, patch = await run_role({"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, ports)
    assert output is not None
    assert patch["sections"]["PjM"] == output.text


async def test_run_role_turns_a_role_failure_into_an_escalation(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    runner = ScriptedRunner(pjm=[AttemptFailed("a", 0.05), AttemptFailed("b", 0.03)])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d"}, PJM, cfg, memory_ports(runner)
    )
    assert output is None
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "PjM failed 2 times: b"
    assert patch["escalation_class"] == "failure"
    assert [f["error"] for f in patch["failures"]] == ["a", "b"]
    assert patch["cost_usd"] == pytest.approx(0.08)
    assert "sections" not in patch and "runs" not in patch


async def test_run_role_escalates_when_the_task_cap_is_reached(tmp_path):
    cfg = Configuration(product_dir=tmp_path, task_budget_usd=50.0)
    runner = ScriptedRunner(pjm=[OK])
    output, patch = await run_role(
        {"id": "T-1", "title": "t", "description": "d", "cost_usd": 50.0},
        PJM,
        cfg,
        memory_ports(runner),
    )
    assert output is None and runner.calls == []
    assert patch["status"] == "waiting_on_human"
    assert patch["escalated"] == "PjM stopped by the task cap: 50.00 of 50.00 USD spent"
    assert patch["escalation_class"] == "cap"
    assert patch["failures"] == [] and patch["cost_usd"] == 0.0
    assert "sections" not in patch and "runs" not in patch


def test_the_task_cap_in_force_grows_with_the_plan_and_the_human_overrides_it(tmp_path):
    # ADR-0004, fourth addendum: the base until there is a plan, then a share per
    # subtask with the integration counted, never over the ceiling.
    cfg = Configuration(product_dir=tmp_path)
    state = {"id": "T-1", "title": "t", "description": "d"}
    assert task_cap(state, cfg) == 30.0
    two = {**state, "subtasks": {"api": {}, "domain": {}}}
    assert task_cap(two, cfg) == 30.0 + 15.0 * 3
    many = {**state, "subtasks": {f"s{n}": {} for n in range(20)}}
    assert task_cap(many, cfg) == 250.0
    assert task_cap(many, Configuration(product_dir=tmp_path, task_budget_usd=400.0)) == 400.0
    # The human raises the cap, never lowers it: a budget set before the plan does not
    # stop the task again once the plan makes the formula larger.
    early = Configuration(product_dir=tmp_path, task_budget_usd=45.0)
    assert task_cap(state, early) == 45.0 and task_cap(two, early) == 75.0
