"""The evaluation set and record: `agentic.core.measurement.domain.evaluation`."""

import hashlib

import pytest

from agentic.core.measurement.domain import evaluation

VALID = {"issues": [10, 11, 12], "smoke": 11}


def doc(**overrides):
    base = {
        "id": "GH-10",
        "title": "t",
        "description": "d",
        "status": "waiting_on_human",
    }
    base.update(overrides)
    return base


def run(role="Developer", started=None, ended=None, section="Developer"):
    entry = {
        "role": role,
        "section": section,
        "version": 1,
        "session_id": "",
        "model": "m",
        "cost_usd": 1.0,
        "turns": 1,
    }
    if started:
        entry["started_at"] = started
    if ended:
        entry["ended_at"] = ended
    return entry


# --- eval_set_refusal: the accept case, then one rejection per declared rule ---


def test_eval_set_refusal_accepts_a_valid_declaration():
    assert evaluation.eval_set_refusal(VALID, "eval-set.yaml") is None


def test_eval_set_refusal_rejects_a_non_mapping():
    reason = evaluation.eval_set_refusal([1, 2], "eval-set.yaml")
    assert reason is not None
    assert "eval-set.yaml" in reason


def test_eval_set_refusal_rejects_an_unknown_key():
    bad = {**VALID, "threshold": 0.8}
    reason = evaluation.eval_set_refusal(bad, "eval-set.yaml")
    assert reason is not None
    assert "threshold" in reason


def test_eval_set_refusal_rejects_an_empty_issue_list():
    reason = evaluation.eval_set_refusal({"issues": [], "smoke": 1}, "eval-set.yaml")
    assert reason is not None
    assert "issues" in reason


def test_eval_set_refusal_rejects_a_non_integer_issue_number():
    bad = {"issues": [10, "11"], "smoke": 10}
    reason = evaluation.eval_set_refusal(bad, "eval-set.yaml")
    assert reason is not None
    assert "'11'" in reason


def test_eval_set_refusal_rejects_a_duplicated_issue_number():
    bad = {"issues": [10, 11, 10], "smoke": 10}
    reason = evaluation.eval_set_refusal(bad, "eval-set.yaml")
    assert reason is not None
    assert "10" in reason


def test_eval_set_refusal_rejects_a_smoke_task_absent_from_issues():
    bad = {"issues": [10, 11, 12], "smoke": 99}
    reason = evaluation.eval_set_refusal(bad, "eval-set.yaml")
    assert reason is not None
    assert "99" in reason


# --- parse_eval_set: the value, and the raise that mirrors the refusal ---


def test_parse_eval_set_returns_the_declared_issues_and_smoke_task():
    parsed = evaluation.parse_eval_set(VALID, "eval-set.yaml")
    assert parsed == evaluation.EvalSet(issues=(10, 11, 12), smoke=11)


def test_parse_eval_set_raises_with_the_refusal_message():
    bad = {"issues": [10, 11], "smoke": 99}
    with pytest.raises(ValueError, match="99"):
        evaluation.parse_eval_set(bad, "eval-set.yaml")


# --- baseline_id ---


def test_baseline_id_changes_with_the_declaration_text():
    a = evaluation.baseline_id("1.2.3", "issues: [1]\nsmoke: 1\n", {})
    b = evaluation.baseline_id("1.2.3", "issues: [1, 2]\nsmoke: 1\n", {})
    assert a != b
    assert a.startswith("1.2.3+")


def test_baseline_id_is_stable_for_the_same_inputs():
    first = evaluation.baseline_id("1.2.3", "issues: [1]\nsmoke: 1\n", {})
    second = evaluation.baseline_id("1.2.3", "issues: [1]\nsmoke: 1\n", {})
    assert first == second


def test_baseline_id_with_an_empty_experiment_matches_todays_two_argument_value():
    # `baseline_id` mixed only `system_version` and the declaration text before this
    # task (issue #28); an empty snapshot must reproduce that exact value byte for
    # byte, so a default-path baseline never moves.
    text = "issues: [1]\nsmoke: 1\n"
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    assert evaluation.baseline_id("1.2.3", text, {}) == f"1.2.3+{digest}"


def test_baseline_id_mixes_in_a_non_empty_experiment_snapshot():
    text = "issues: [1]\nsmoke: 1\n"
    empty = evaluation.baseline_id("1.2.3", text, {})
    light = evaluation.baseline_id("1.2.3", text, {"review_mode": "light"})
    assert empty != light


# --- score ---


def test_score_is_true_for_a_clean_merge():
    document = doc(closed_at="2026-09-01T00:00:00.000Z", delivery_outcome="merged")
    result = evaluation.score(document, issue=10)
    assert result == evaluation.Score(True, None)


def test_score_is_false_for_a_merge_with_human_changes():
    document = doc(closed_at="2026-09-01T00:00:00.000Z", delivery_outcome="merged_with_changes")
    result = evaluation.score(document, issue=10)
    assert result.value is False
    assert result.pending_reason is None


def test_score_is_false_for_a_closed_unmerged_pull_request():
    document = doc(closed_at="2026-09-01T00:00:00.000Z", delivery_outcome="closed")
    result = evaluation.score(document, issue=10)
    assert result.value is False


def test_score_is_none_with_a_pending_reason_when_not_yet_closed():
    document = doc()
    result = evaluation.score(document, issue=42)
    assert result.value is None
    assert result.pending_reason is not None
    assert "agentic close --issue 42" in result.pending_reason


# --- role_seconds ---


def test_role_seconds_sums_only_runs_with_both_timestamps():
    document = doc(
        runs=[
            run(started="2026-09-01T00:00:00.000", ended="2026-09-01T00:01:00.000"),
            run(started="2026-09-01T00:00:00.000", ended="2026-09-01T00:00:30.000"),
            run(),  # no timestamps at all
        ]
    )
    total, without_timing = evaluation.role_seconds(document)
    assert total == pytest.approx(90.0)
    assert without_timing == 1


def test_role_seconds_is_zero_with_no_runs():
    assert evaluation.role_seconds(doc()) == (0.0, 0)


# --- evaluation_record ---


def test_evaluation_record_reads_every_figure_from_the_document():
    document = doc(
        id="GH-10",
        status="waiting_on_human",
        closed_at="2026-09-01T00:00:00.000Z",
        delivery_outcome="merged",
        cost_usd=12.5,
        prompts={"developer": "abc123"},
        system_version="1.2.3",
        changes_requested={"Tech Lead->Developer": 2},
        failures=[
            {"role": "Developer", "attempt": 1, "error": "e", "cost_usd": 0.1, "session_id": ""}
        ],
        runs=[run(started="2026-09-01T00:00:00.000", ended="2026-09-01T00:00:10.000")],
        escalation_class=None,
    )
    record = evaluation.evaluation_record("gym-saas", 10, document)
    assert record["product"] == "gym-saas"
    assert record["issue"] == 10
    assert record["task"] == "GH-10"
    assert record["status"] == "waiting_on_human"
    assert record["delivery_outcome"] == "merged"
    assert record["score"] is True
    assert record["score_pending_reason"] is None
    assert record["cost_usd"] == 12.5
    # The single run costs $1.0 of the document's $12.5 total: the rest is residual,
    # rework by `metrics.rework_cost_usd`'s own definition.
    assert record["rework_usd"] == pytest.approx(11.5)
    assert record["role_seconds"] == pytest.approx(10.0)
    assert record["runs_without_timing"] == 0
    assert record["prompts"] == {"developer": "abc123"}
    assert record["prompts_fingerprint"]
    assert record["system_version"] == "1.2.3"
    assert record["changes_requested"] == 2
    assert record["failures"] == 1
    assert record["note"] is None
    assert "escalation_class" not in record


def test_evaluation_record_carries_escalation_class_only_when_present():
    document = doc(escalation_class="failure")
    record = evaluation.evaluation_record("gym-saas", 10, document)
    assert record["escalation_class"] == "failure"


def test_evaluation_record_carries_the_experiment_snapshot_only_when_present():
    snapshot = {
        "review_mode": "light",
        "skip_ephemeral": "False",
        "reviewer_model": "claude-sonnet-5",
    }
    with_experiment = evaluation.evaluation_record("gym-saas", 10, doc(experiment=snapshot))
    assert with_experiment["experiment"] == snapshot
    without_experiment = evaluation.evaluation_record("gym-saas", 10, doc())
    assert "experiment" not in without_experiment


def test_evaluation_record_score_pending_when_the_task_is_not_yet_closed():
    document = doc()
    record = evaluation.evaluation_record("gym-saas", 10, document)
    assert record["score"] is None
    assert record["score_pending_reason"] is not None
    assert record["system_version"] is None
    assert record["delivery_outcome"] is None
