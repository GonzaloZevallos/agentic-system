"""The definitions of the Medición glossary, as functions over the state."""

import pytest

from agentic.core.measurement.domain import metrics


def run(role, version, cost):
    return {
        "role": role,
        "version": version,
        "session_id": "",
        "model": "m",
        "cost_usd": cost,
        "turns": 1,
    }


def test_run_rework_usd_is_the_whole_cost_of_a_returned_run():
    assert metrics.run_rework_usd(run("Product Analyst", 2, 0.1)) == pytest.approx(0.1)


def test_run_rework_usd_is_only_the_failed_attempts_cost_of_a_first_delivery():
    first = run("PM", 1, 0.2)
    first["failed_cost_usd"] = 0.05
    assert metrics.run_rework_usd(first) == pytest.approx(0.05)


def test_run_rework_usd_reads_zero_for_a_run_recorded_before_the_field_existed():
    assert metrics.run_rework_usd(run("PjM", 1, 0.01)) == 0.0


def test_rework_is_repeated_deliveries_plus_failed_attempts_already_inside_cost_usd():
    first = run("PM", 1, 0.25)
    first["failed_cost_usd"] = 0.05  # already counted once inside its own cost_usd
    state = {
        "runs": [run("PjM", 1, 0.01), first, run("Product Analyst", 2, 0.1)],
        "cost_usd": 0.36,
    }
    # 0.05 (PM's own failed attempt) + 0.1 (the returned Product Analyst run), no
    # unaccounted money left over: 0.01 + 0.25 + 0.1 == 0.36.
    assert metrics.rework_cost_usd(state) == pytest.approx(0.15)


def test_rework_cost_usd_counts_unaccounted_money_once():
    # An escalation's own residual: `cost_usd` above what any run in the patch carries.
    state = {"runs": [run("PjM", 1, 0.01)], "cost_usd": 0.09}
    assert metrics.rework_cost_usd(state) == pytest.approx(0.08)


def test_a_clean_task_has_no_rework():
    assert metrics.rework_cost_usd({"runs": [run("PjM", 1, 0.01)], "cost_usd": 0.01}) == 0
    assert metrics.rework_cost_usd({}) == 0


def test_changes_requested_total_sums_every_pair():
    counts = {"changes_requested": {"PM→Product Analyst": 2, "QA→Dev": 1}}
    assert metrics.changes_requested_total(counts) == 3
    assert metrics.changes_requested_total({}) == 0


def test_returns_by_category_counts_the_history():
    history = [
        {"pair": "Tech Lead→Developer:a", "category": "standard-typing-silenced", "reason": "r"},
        {"pair": "Tech Lead→Developer:b", "category": "standard-typing-silenced", "reason": "r"},
        {"pair": "PM→Product Analyst", "category": "definition", "reason": "r"},
    ]
    state = {"changes_requested": {"x": 3}, "change_history": history}
    assert metrics.changes_by_category(state) == {"standard-typing-silenced": 2, "definition": 1}
    # A task with no returns still leaves its sample; one recorded before the history
    # existed counts what the history lacks as `unknown`.
    assert metrics.changes_by_category({}) == {"none": 0}
    assert metrics.changes_by_category({"changes_requested": {"x": 2}}) == {"unknown": 2}
    assert metrics.change_categories({"change_history": history}) == [
        "standard-typing-silenced",
        "standard-typing-silenced",
        "definition",
    ]
    assert metrics.change_categories({}) == []


def test_prompts_fingerprint_ignores_order_and_is_short():
    a = metrics.prompts_fingerprint({"prompts": {"pjm": "aaa", "pm": "bbb"}})
    b = metrics.prompts_fingerprint({"prompts": {"pm": "bbb", "pjm": "aaa"}})
    assert a == b and len(a) == 8
    assert metrics.prompts_fingerprint({"prompts": {"pjm": "ccc", "pm": "bbb"}}) != a
    assert metrics.prompts_fingerprint({}) == ""


def dev_run(section, version, cost, slug="developer"):
    return {
        "role": "Developer",
        "slug": slug,
        "section": section,
        "version": version,
        "session_id": "",
        "model": "m",
        "cost_usd": cost,
        "turns": 1,
    }


def test_role_slug_falls_back_to_unknown_for_a_run_recorded_before_the_field_existed():
    assert metrics.role_slug({"role": "PjM", "slug": "pjm"}) == "pjm"
    assert metrics.role_slug({"role": "PjM"}) == "unknown"


def test_target_run_is_the_last_run_the_patch_wrote_a_section_for():
    patch = {
        "runs": [dev_run("Developer: api", 1, 0.1), dev_run("Developer: api", 2, 0.2)],
        "sections": {"Developer: api": "text"},
    }
    assert metrics.target_run(patch) == patch["runs"][-1]


def test_target_run_is_none_without_a_matching_run():
    assert metrics.target_run({}) is None
    assert metrics.target_run({"runs": [dev_run("Developer: api", 1, 0.1)]}) is None


def test_role_run_outcome_is_delivered_for_every_run_but_the_target():
    runs = [dev_run("Developer: api", 1, 0.1), dev_run("Developer: api", 2, 0.2)]
    patch = {"runs": runs, "sections": {"Developer: api": "text"}, "status": "returned"}
    assert metrics.role_run_outcome(patch, runs[0]) == "delivered"
    assert metrics.role_run_outcome(patch, runs[1]) == "returned"


def test_role_run_outcome_escalated_and_delivered():
    run = dev_run("Developer: api", 1, 0.1)
    escalated = {"runs": [run], "sections": {"Developer: api": "t"}, "escalated": "e"}
    assert metrics.role_run_outcome(escalated, run) == "escalated"
    delivered = {"runs": [run], "sections": {"Developer: api": "t"}}
    assert metrics.role_run_outcome(delivered, run) == "delivered"


def test_residual_outcome_is_failed_without_a_target_run_else_escalated():
    assert metrics.residual_outcome({"escalated": "e"}) == "failed"
    run = dev_run("Developer: api", 1, 0.1)
    with_target = {"runs": [run], "sections": {"Developer: api": "t"}, "escalated": "e"}
    assert metrics.residual_outcome(with_target) == "escalated"


def test_residual_cost_usd_is_what_no_run_record_carries():
    assert metrics.residual_cost_usd({"cost_usd": 0.08}) == pytest.approx(0.08)
    runs = [dev_run("Developer: api", 1, 0.1)]
    assert metrics.residual_cost_usd({"cost_usd": 0.1, "runs": runs}) == 0.0
    # Never negative, whatever rounding noise a floating sum leaves.
    assert metrics.residual_cost_usd({"cost_usd": 0.0999, "runs": runs}) == 0.0
    assert metrics.residual_cost_usd({}) == 0.0


def test_has_residual_sample_covers_the_empty_patch():
    assert metrics.has_residual_sample({}) is True  # nothing to represent this patch
    runs = [dev_run("Developer: api", 1, 0.1)]
    assert metrics.has_residual_sample({"cost_usd": 0.1, "runs": runs}) is False
    assert metrics.has_residual_sample({"cost_usd": 0.15, "runs": runs}) is True


def test_has_residual_sample_gates_on_the_epsilon_not_the_rounding_floor():
    runs = [dev_run("Developer: api", 1, 0.1)]
    # Floating-point noise, well below the epsilon: no sample.
    assert metrics.has_residual_sample({"cost_usd": 0.1 + 1e-12, "runs": runs}) is False
    # Above the epsilon but below the old floor, which no longer gates emission (many
    # small patches like this one could otherwise hide money nothing else accounts for).
    assert metrics.has_residual_sample({"cost_usd": 0.1005, "runs": runs}) is True


def test_residual_rework_is_unconditionally_true():
    # Every residual is money that produced no delivery, by the glossary's own
    # definition, whether or not this patch happens to carry a failed attempt.
    assert metrics.residual_rework({}) is True
    assert metrics.residual_rework({"failures": [{"cost_usd": 0.01}]}) is True


def test_residual_phase_is_the_delivering_runs_phase():
    patch = {
        "runs": [dev_run("Developer: api", 1, 0.1)],
        "sections": {"Developer: api": "t"},
    }
    assert metrics.residual_phase(patch) == "development"


def test_residual_phase_falls_back_to_the_first_failures_section():
    patch = {"failures": [{"role": "PM", "section": "PM", "cost_usd": 0.01}]}
    assert metrics.residual_phase(patch) == "definition"


def test_residual_phase_is_unknown_without_a_target_run_or_a_failure_section():
    assert metrics.residual_phase({}) == "unknown"
    assert metrics.residual_phase({"failures": [{"role": "PM", "cost_usd": 0.01}]}) == "unknown"


def test_failures_by_reason_counts_and_sums_cost():
    failures = [
        {"cost_usd": 0.01, "reason": "schema"},
        {"cost_usd": 0.02, "reason": "schema"},
        {"cost_usd": 0.03, "reason": "sdk"},
        {"cost_usd": 0.04},  # recorded before `reason` existed
    ]
    assert metrics.failures_by_reason(failures) == {"schema": 2, "sdk": 1, "other": 1}
    assert metrics.failure_cost_by_reason(failures) == pytest.approx(
        {"schema": 0.03, "sdk": 0.03, "other": 0.04}
    )
    assert metrics.failures_by_reason([]) == {}


def test_run_duration_s_is_ended_minus_started():
    run = {
        "started_at": "2026-09-18T10:00:00.000+00:00",
        "ended_at": "2026-09-18T10:00:02.500+00:00",
    }
    assert metrics.run_duration_s(run) == pytest.approx(2.5)


def test_run_duration_s_is_none_for_a_run_recorded_before_the_fields_existed():
    assert metrics.run_duration_s({"role": "PjM"}) is None
    assert metrics.run_duration_s({"started_at": "2026-09-18T10:00:00.000+00:00"}) is None


def test_interview_questions_by_destination():
    stats = {
        "interview": "solution",
        "rounds": 3,
        "questions_to_pm": 1,
        "questions_to_human": 2,
        "invariants_proposed": 1,
    }
    assert metrics.interview_questions(stats) == {"interviewee": 3, "pm": 1, "human": 2}


def test_invocation_returns_by_category_reads_only_the_deltas_own_history():
    # No `changes_requested` counter to reconcile against: an invocation delta carries
    # exactly the entries it added, so there is no `unknown` bucket to compute.
    patch = {
        "change_history": [
            {"pair": "PM→Product Analyst", "category": "definition", "reason": "r"},
            {"pair": "Tech Lead→Developer:a", "category": "spec-behaviour", "reason": "r"},
            {"pair": "Tech Lead→Developer:a", "category": "spec-behaviour", "reason": "r"},
        ]
    }
    assert metrics.invocation_returns_by_category(patch) == {
        "definition": 1,
        "spec-behaviour": 2,
    }


def test_invocation_returns_by_category_is_empty_without_a_return_this_invocation():
    assert metrics.invocation_returns_by_category({}) == {}
