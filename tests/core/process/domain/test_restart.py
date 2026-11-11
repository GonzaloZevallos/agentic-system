"""GH-593 domain: the return-limit decision, the clean restart, the mechanical causes and
`pause_kind`, at the seams the plan agrees (`rules.*`, `restart.*`, `ESCALATION_PROMPTS`)."""

import shlex
from typing import Any, get_args

import pytest

from agentic.cli import build_parser
from agentic.core.process.domain import restart, rules
from agentic.core.process.domain.roles import PAIR_PM_PA, pair_for
from agentic.core.process.domain.task import EscalationClass, MechanicalCause, apply

PAIR = pair_for("a")
CAUSES: tuple[Any, ...] = get_args(MechanicalCause)
CLASSES: tuple[Any, ...] = get_args(EscalationClass)


def _state(**sub: Any) -> Any:
    return {
        "id": "T",
        "subtasks": {"a": {"id": "a", "brief": "do a", "status": "returned", **sub}},
        "changes_requested": {PAIR.key: 2},
    }


def _finding(subtask: str = "a", version: int = 1, **extra: Any) -> Any:
    return {
        "subtask": subtask,
        "version": version,
        "location": "x.py:1",
        "claim": "c",
        "severity": "blocking",
        "scope": "subtask",
        "category": "spec-behaviour",
        "status": "open",
        **extra,
    }


# --- eligible / on_return_limit ---------------------------------------------------------


def test_a_never_approved_subtask_at_its_limit_restarts():
    assert restart.eligible(_state(), PAIR, "a") is True
    assert rules.on_return_limit(_state(), PAIR, 2, "a") == "restart"


def test_a_pair_with_returns_left_counts():
    state = _state()
    state["changes_requested"][PAIR.key] = 1
    assert rules.on_return_limit(state, PAIR, 2, "a") == "count"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["subtasks"]["a"].update(approved_once=True),
        lambda s: s["subtasks"]["a"].update(status="approved"),
        lambda s: s["subtasks"]["a"].update(reviewed_head="h", returned_by="full_checks"),
        lambda s: s["subtasks"]["a"].update(returned_by="qa_performance"),
        lambda s: s.update(pull_request="https://x/pr/1"),
        lambda s: s.update(restarts={PAIR.key: 1}),
    ],
)
def test_each_condition_that_makes_a_restart_ineligible_escalates(mutate):
    state = _state()
    mutate(state)
    assert restart.eligible(state, PAIR, "a") is False
    assert rules.on_return_limit(state, PAIR, 2, "a") == "escalate"


def test_the_integration_and_other_pairs_never_restart():
    state = _state()
    state["subtasks"]["integration"] = {"id": "integration", "status": "returned"}
    state["changes_requested"][pair_for("integration").key] = 2
    assert restart.eligible(state, pair_for("integration"), "integration") is False
    assert restart.eligible(state, PAIR_PM_PA, "a") is False
    state["changes_requested"][PAIR_PM_PA.key] = 2
    assert rules.on_return_limit(state, PAIR_PM_PA, 2, None) == "escalate"


def test_the_restarted_attempts_second_limit_escalates():
    state = _state()
    after = apply(state, restart.restart_patch(state, "a", PAIR, "brief", "h1"))
    assert rules.on_return_limit(after, PAIR, 2, "a") == "count"
    after["changes_requested"][PAIR.key] = 2
    assert rules.on_return_limit(after, PAIR, 2, "a") == "escalate"


# --- restart_patch / brief ---------------------------------------------------------------


def test_restart_patch_writes_every_key_in_one_patch():
    state = _state(reviewed_head="h0", reason="r", returned_by="review", change_category="x")
    state["findings"] = [_finding(version=2), _finding("b", version=5)]
    state["runs"] = [
        {"role": "Developer", "section": "Developer: a", "version": 1, "session_id": "s1"},
        {"role": "Developer", "section": "Developer: a", "version": 2, "session_id": "s2"},
    ]

    patch = restart.restart_patch(state, "a", PAIR, "the brief", "h1")

    assert patch["restarts"] == {PAIR.key: 1}
    assert patch["changes_requested"] == {PAIR.key: 0}
    assert patch["subtasks"]["a"] == {"id": "a", "brief": "do a", "status": "pending", "attempt": 2}
    assert patch["session_floor"] == {"Developer: a": 2}
    assert patch["restart_settled"] == [
        {"subtask": "a", "version": 3, "outcome": "discarded", "reason": "restarted"}
    ]
    assert patch["restart_pending"] == {"subtask": "a", "archived_head": "h1"}
    assert patch["sections"] == {"Restart a": "the brief"}


def test_restart_closes_the_open_rows_of_the_subtask_and_forgets_its_session():
    state = _state()
    state["findings"] = [_finding(version=2), _finding("b", location="y.py:2", version=2)]
    state["runs"] = [
        {"role": "Developer", "section": "Developer: a", "version": 1, "session_id": "s1"}
    ]
    after = apply(state, restart.restart_patch(state, "a", PAIR, "b", "h"))

    status = {row["subtask"]: row["status"] for row in rules.findings_rows(after)}
    assert status == {"a": "fixed", "b": "open"}
    assert rules.last_session(after, "Developer", "Developer: a") is None
    assert rules.last_session(state, "Developer", "Developer: a") == "s1"


def test_return_sections_after_a_restart_is_only_the_restart_section():
    state = _state()
    assert rules.return_sections(state, "a") == ("Tech Lead: review a",)
    after = apply(state, restart.restart_patch(state, "a", PAIR, "b", "h"))
    assert rules.return_sections(after, "a") == ("Restart a",)
    delivered = apply(after, {"subtasks": {"a": {**after["subtasks"]["a"], "status": "returned"}}})
    assert rules.return_sections(delivered, "a") == ("Tech Lead: review a",)


def test_brief_is_deterministic_and_names_what_repeated():
    first = [_finding(claim="no test")]
    second = [_finding(version=2, claim="still no test")]
    history: Any = [
        {"pair": PAIR.key, "category": "spec-behaviour", "reason": "one"},
        {"pair": PAIR.key, "category": "spec-behaviour", "reason": "two"},
    ]
    text = restart.brief("do a", first, second, history)
    assert text == restart.brief("do a", first, second, history)
    assert "do a" in text and "x.py:1: no test (spec-behaviour)" in text
    assert "still no test" in text and "- one" in text and "- two" in text
    assert "- category spec-behaviour" in text and "- file x.py" in text


def test_approval_writes_approved_once():
    state = _state()
    patch = rules.approval_patch(state, "a", state["subtasks"]["a"], 1, [], "head")
    assert patch["subtasks"]["a"]["approved_once"] is True
    assert patch["subtasks"]["a"]["status"] == "approved"


def test_change_entry_carries_the_attempt_only_above_one():
    assert "attempt" not in rules.change_entry(PAIR, "spec-behaviour", "r")
    assert rules.change_entry(PAIR, "spec-behaviour", "r", 2)["attempt"] == 2
    assert restart.attempt(_state(), PAIR) == 1
    assert restart.attempt({"restarts": {PAIR.key: 1}}, PAIR) == 2


# --- causes, retakes and pause_kind ------------------------------------------------------


def _esc(value: Any, cause: Any = None) -> Any:
    return rules.escalation_entry(value, cause=cause)


def _fail(reason: str) -> Any:
    return {"kind": "failure", "value": reason, "section": "s"}


def test_escalation_writes_the_cause_with_the_class_and_the_log():
    patch = rules.escalation("ci", "no verdict", cause="ci_gh_unavailable")
    assert patch["escalation_cause"] == "ci_gh_unavailable"
    assert "escalation_cause" not in rules.escalation("ci", "no verdict")
    assert rules.logged(patch, at="t")["pause_log"] == [
        {"kind": "escalation", "value": "ci", "at": "t", "cause": "ci_gh_unavailable"}
    ]
    assert "cause" not in rules.escalation_entry("ci")


def test_auto_resumable_accepts_mechanical_causes_and_mixed_epochs():
    only_cause: Any = {"pause_log": [_esc("ci", "ci_run_unfinished")]}
    assert rules.auto_resumable(only_cause) == "ci_run_unfinished"
    mixed: Any = {"pause_log": [_fail("network"), _esc("failure"), _esc("ci", "ci_gh_unreadable")]}
    assert rules.auto_resumable(mixed) == "ci_gh_unreadable"
    external: Any = {"pause_log": [_fail("account_limit"), _esc("failure")]}
    assert rules.auto_resumable(external) == "account_limit"


@pytest.mark.parametrize(
    "log",
    [
        [_esc("ci")],
        [_esc("returns")],
        [_fail("schema"), _esc("failure")],
        [_fail("network"), _esc("returns")],
        [_esc("ci", "ci_run_unfinished"), _esc("questions")],
        [],
    ],
)
def test_a_judgment_epoch_is_never_auto_resumable(log):
    state: Any = {"pause_log": log}
    assert rules.auto_resumable(state) is None
    assert rules.pause_kind(log) == "human"


def test_pause_kind_is_external_for_mechanical_pauses():
    assert rules.pause_kind([_fail("upstream"), _esc("failure")]) == "external"
    assert rules.pause_kind([_esc("system_delivery", "delivery_transient")]) == "external"


def test_schedule_and_limit_for_mechanical_causes_and_unchanged_reasons():
    for cause in CAUSES:
        assert rules.schedule_for(cause) == rules.RETAKE_SCHEDULE
        assert rules.auto_resume_limit({}, cause) == rules.MECHANICAL_RETAKE_MAX
    assert rules.MECHANICAL_RETAKE_MAX == 3
    assert rules.schedule_for("account_limit") == rules.ACCOUNT_LIMIT_SCHEDULE
    assert rules.schedule_for("network") == rules.RETAKE_SCHEDULE
    assert rules.auto_resume_limit({}, "account_limit") == len(rules.ACCOUNT_LIMIT_SCHEDULE)
    assert rules.auto_resume_limit({}, "network") == len(rules.RETAKE_SCHEDULE)


# --- the escalation table ----------------------------------------------------------------


def test_every_class_has_a_row_and_the_recommended_answer_is_an_option():
    for cls in CLASSES:
        question, recommended, options = rules.ESCALATION_PROMPTS[(cls, None)]
        assert question and recommended in options
    for _, recommended, options in rules.ESCALATION_PROMPTS.values():
        assert recommended in options and len(set(options)) == len(options)


def test_every_option_names_a_command_that_exists():
    parser = build_parser()
    for _, _, options in rules.ESCALATION_PROMPTS.values():
        for option in options:
            words = shlex.split(option.format(issue=7, usd=1.5).split(" (")[0])
            if words[0] == "gh":
                assert words[:3] == ["gh", "issue", "close"]
                continue
            assert words[0] == "agentic"
            command = parser.commands[(words[1],)]
            flags = {w for w in words[2:] if w.startswith("--")}
            assert flags <= {s for a in command._actions for s in a.option_strings}, option


def test_a_command_the_cli_does_not_have_is_rejected_by_the_check():
    parser = build_parser()
    assert ("resume",) in parser.commands
    assert ("answer",) not in parser.commands
    assert "--nope" not in {
        s for a in parser.commands[("resume",)]._actions for s in a.option_strings
    }


def test_every_mechanical_cause_has_a_row():
    for cause in CAUSES:
        assert any(key[1] == cause for key in rules.ESCALATION_PROMPTS)
