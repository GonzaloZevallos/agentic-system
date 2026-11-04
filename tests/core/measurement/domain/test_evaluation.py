"""The evaluation set and record: `agentic.core.measurement.domain.evaluation`."""

import hashlib
from pathlib import Path

import pytest

from agentic.core.measurement.domain import evaluation

SHA_A = "a" * 40
SHA_B = "0123456789abcdef" * 2 + "01234567"


def entry(issue, base=SHA_A, title="a title", body="a body"):
    return {"issue": issue, "base": base, "title": title, "body": body}


VALID = {"entries": [entry(10), entry(11), entry(12, SHA_B)], "smoke": 11}


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


def test_eval_set_refusal_rejects_the_old_issues_form():
    reason = evaluation.eval_set_refusal({"issues": [10, 11], "smoke": 10}, "eval-set.yaml")
    assert reason is not None
    assert "issues" in reason


def test_eval_set_refusal_rejects_an_empty_entry_list():
    reason = evaluation.eval_set_refusal({"entries": [], "smoke": 1}, "eval-set.yaml")
    assert reason is not None
    assert "entries" in reason


@pytest.mark.parametrize(
    ("bad", "named"),
    [
        (entry("11"), "'11'"),
        (entry(True), "True"),
        (entry(0), "0"),
        (entry(-3), "-3"),
        (entry(11, "A" * 40), "base"),
        (entry(11, "a" * 39), "base"),
        (entry(11, "main"), "'main'"),
        (entry(11, 5), "base"),
        (entry(11, title=""), "title"),
        (entry(11, title="  "), "title"),
        (entry(11, body=""), "body"),
        (entry(11, body=None), "body"),
        ({"issue": 11, "base": SHA_A, "title": "t"}, "body"),
        ({**entry(11), "extra": 1}, "extra"),
        ("11", "entry mapping"),
    ],
)
def test_eval_set_refusal_rejects_each_invalid_entry(bad, named):
    reason = evaluation.eval_set_refusal(
        {"entries": [entry(10), bad], "smoke": 10}, "eval-set.yaml"
    )
    assert reason is not None
    assert named in reason
    assert "eval-set.yaml" in reason


def test_eval_set_refusal_rejects_a_duplicated_issue_and_base():
    bad = {"entries": [entry(10), entry(11), entry(10)], "smoke": 10}
    reason = evaluation.eval_set_refusal(bad, "eval-set.yaml")
    assert reason is not None
    assert "10" in reason


def test_eval_set_refusal_accepts_one_issue_at_two_bases():
    both = {"entries": [entry(10, SHA_A), entry(10, SHA_B)], "smoke": 10}
    assert evaluation.eval_set_refusal(both, "eval-set.yaml") is None


def test_eval_set_refusal_rejects_a_smoke_task_absent_from_the_entries():
    bad = {"entries": [entry(10), entry(11)], "smoke": 99}
    reason = evaluation.eval_set_refusal(bad, "eval-set.yaml")
    assert reason is not None
    assert "99" in reason


# --- parse_eval_set: the value, and the raise that mirrors the refusal ---


def test_parse_eval_set_returns_the_entries_and_smoke_task():
    parsed = evaluation.parse_eval_set(VALID, "eval-set.yaml")
    assert parsed.smoke == 11
    assert parsed.entries[2] == evaluation.EvalEntry(
        issue=12, base=SHA_B, title="a title", body="a body"
    )


def test_issues_are_the_distinct_issue_numbers_sorted():
    declared = {"entries": [entry(12), entry(10, SHA_A), entry(10, SHA_B)], "smoke": 12}
    assert evaluation.parse_eval_set(declared, "eval-set.yaml").issues == (10, 12)


def test_parse_eval_set_raises_with_the_refusal_message():
    bad = {"entries": [entry(10), entry(11)], "smoke": 99}
    with pytest.raises(ValueError, match="99"):
        evaluation.parse_eval_set(bad, "eval-set.yaml")


# --- is_evaluation, is_evaluation_root, wiring_refusal: what an evaluation run is ---


def test_a_document_with_an_evaluation_key_is_an_evaluation():
    assert evaluation.is_evaluation(doc(evaluation={"issue": 1, "base": SHA_A, "ci": "local"}))
    assert not evaluation.is_evaluation(doc())


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/p/.agentic/eval/EVAL-1-aaaaaaaa-default-1", True),
        ("/p/.agentic/eval/EVAL-1-aaaaaaaa-default-1/", True),
        ("/p/.agentic/eval/docs", False),
        ("/p/.agentic/eval", False),
        ("/p/.agentic/eval/EVAL-1-aaaaaaaa-default-1/sub", False),
        ("/p/.agentic/tasks/EVAL-1-aaaaaaaa-default-1", False),
        ("/p/eval/EVAL-1-aaaaaaaa-default-1", False),
        ("/p", False),
    ],
)
def test_is_evaluation_root_is_the_shape_of_the_path(path, expected):
    assert evaluation.is_evaluation_root(Path(path)) is expected


def test_is_evaluation_root_resolves_a_symlink_before_it_looks(tmp_path):
    root = tmp_path / "product" / ".agentic" / "eval" / "EVAL-1-aaaaaaaa-default-1"
    root.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(root)
    assert evaluation.is_evaluation_root(link)
    plain = tmp_path / "plain"
    plain.mkdir()
    disguised = tmp_path / "EVAL-1-aaaaaaaa-default-1"
    disguised.symlink_to(plain)
    assert not evaluation.is_evaluation_root(disguised)


EVAL_ID = "EVAL-7-aaaaaaaa-default-1"
ROOT = Path("/p/.agentic/eval/EVAL-7-aaaaaaaa-default-1")


def test_wiring_refusal_lets_agreeing_root_and_id_through():
    assert evaluation.wiring_refusal(ROOT, EVAL_ID) is None
    assert evaluation.wiring_refusal(Path("/p"), "GH-7") is None


def test_wiring_refusal_refuses_an_eval_id_outside_an_evaluation_root():
    reason = evaluation.wiring_refusal(Path("/p"), EVAL_ID)
    assert reason is not None
    assert EVAL_ID in reason


def test_wiring_refusal_refuses_a_real_id_inside_an_evaluation_root():
    reason = evaluation.wiring_refusal(ROOT, "GH-7")
    assert reason is not None
    assert "GH-7" in reason


# --- eval_task_id and its parser ---


def test_eval_task_id_is_issue_short_sha_arm_and_ordinal():
    assert evaluation.eval_task_id(7, SHA_B, "default", 2) == "EVAL-7-01234567-default-2"


@pytest.mark.parametrize("arm", ["default", "no-ephemeral", "a-1", "x" * 24])
def test_eval_task_id_round_trips_through_its_parser(arm):
    task_id = evaluation.eval_task_id(7, SHA_B, arm, 12)
    assert evaluation.parse_eval_task_id(task_id) == evaluation.EvalTaskId(7, "01234567", arm, 12)


@pytest.mark.parametrize(
    "task_id",
    ["GH-7", "EVAL-7", "EVAL-x-01234567-default-1", "EVAL-7-0123456-default-1",
     "EVAL-7-01234567-default-0", "EVAL-7-01234567-default", "EVAL-7-01234567--1",
     "EVAL-7-0123456G-default-1"],
)  # fmt: skip
def test_parse_eval_task_id_refuses_what_eval_task_id_never_makes(task_id):
    assert evaluation.parse_eval_task_id(task_id) is None


# --- evaluation_documents: the one filter every reader of eval/docs goes through ---


def test_evaluation_documents_keeps_evaluations_and_unreadable_ones_only():
    marked = (
        "EVAL-7-aaaaaaaa-default-1",
        doc(id="EVAL-7-aaaaaaaa-default-1", evaluation={"issue": 7}),
    )
    real = ("GH-7", doc(id="GH-7"))
    assert evaluation.evaluation_documents([marked, real, None]) == [marked, None]


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


def test_score_is_unscored_with_the_adr_reason_for_an_auto_merged_delivery():
    document = doc(closed_at="2026-09-01T00:00:00.000Z", delivery_outcome="auto_merged")
    result = evaluation.score(document, issue=10)
    assert result.value is None
    assert result.pending_reason is not None
    assert "ADR-0045" in result.pending_reason
    assert "no human verdict" in result.pending_reason


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


# `fingerprint_divergence` (ADR-0043): which roles' playbook and which roles' template
# make `eval compare`'s fingerprint groups differ.


def test_two_groups_differing_only_in_a_playbook_name_the_playbook_role():
    a = [doc(prompts={"developer": "h1", "pm": "p"}, playbooks={"developer": "pb1"})]
    b = [doc(prompts={"developer": "h2", "pm": "p"}, playbooks={"developer": "pb2"})]
    assert evaluation.fingerprint_divergence([a, b]) == (("developer",), ())


def test_a_playbook_present_in_one_group_and_absent_in_the_other_is_a_playbook_difference():
    a = [doc(prompts={"developer": "h1"}, playbooks={"developer": "pb1"})]
    b = [doc(prompts={"developer": "h2"})]
    assert evaluation.fingerprint_divergence([a, b]) == (("developer",), ())


def test_two_groups_differing_only_in_the_template_name_the_template_role():
    a = [doc(prompts={"developer": "h1", "pm": "p"}, playbooks={"developer": "pb"})]
    b = [doc(prompts={"developer": "h1", "pm": "q"}, playbooks={"developer": "pb"})]
    assert evaluation.fingerprint_divergence([a, b]) == ((), ("pm",))


def test_a_template_difference_with_no_playbooks_at_all_is_still_a_template_difference():
    a = [doc(prompts={"developer": "h1"})]
    b = [doc(prompts={"developer": "h2"})]
    assert evaluation.fingerprint_divergence([a, b]) == ((), ("developer",))


def test_a_role_that_ran_in_only_one_group_is_named_in_neither_list():
    a = [doc(prompts={"developer": "h1", "tech_lead": "t"})]
    b = [doc(prompts={"developer": "h1"})]
    assert evaluation.fingerprint_divergence([a, b]) == ((), ())


def test_identical_groups_diverge_nowhere():
    a = [doc(prompts={"developer": "h1"}, playbooks={"developer": "pb"})]
    assert evaluation.fingerprint_divergence([a, a]) == ((), ())


def test_a_task_closed_by_its_slices_has_no_score_not_a_failing_one():
    document = {"closed_at": "t", "closed_by_slices": ["https://x/pull/3"]}
    assert evaluation.score(document, 5) == evaluation.Score(None, evaluation.SLICES_REASON)
