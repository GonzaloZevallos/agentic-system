"""The landing steps that fix a failure outside the code (GH-610): `fix_pr_body` writes the
product's `Breaking-API:` line into the pull request body and reopens it for a fresh run, and
`escalate_api_break` asks the owner for the reason the task never recorded. Fake ports, the
real landing."""

from __future__ import annotations

import dataclasses

from agentic.core.process.application import landing_plan, poll
from agentic.core.process.application.ports import IssueComment, IssueState, RawResult
from agentic.core.process.application.resume import state_from
from agentic.core.process.domain import pull_request
from agentic.core.process.domain.ci import CiCheck
from agentic.core.process.domain.landing import LandingStep
from agentic.core.process.domain.presubmit import ApiBreakLine
from tests.core.process.application.test_landing_fix import NOW, _doc, _land
from tests.core.process.application.test_merge_ci import _report
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _ports, _pr
from tests.core.process.landing_fixtures import API_BREAK_LINE
from tests.fakes import FakeLauncher, MemoryStack, ScriptedRunner

REASON = "The /orders route drops the legacy field."
RUN = "https://github.com/o/r/actions/runs/{}/job/1"
BODY = "### Why\n\nA change."


def _red_docs(step: str | None = "api_breaks", conclusion: str | None = "FAILURE") -> CiCheck:
    return CiCheck(
        "docs",
        "COMPLETED" if conclusion else "IN_PROGRESS",
        conclusion,
        RUN.format(1),
        failed_step=step,
    )


def _spent() -> list[dict[str, object]]:
    """Every budget of the landing gone, as in GH-686: the passes, the syncs and the rerun."""
    other = "d" * 40
    done = {"refusal": "ci_failed", "resolved_at": NOW}
    return [
        *({"head": other, "step": "fix", **done} for _ in range(3)),
        *({"head": other, "step": "sync_master", **done} for _ in range(6)),
        {"head": HEAD, "step": "rerun", **done},
    ]


def _world(
    *,
    reason: str = REASON,
    step: str | None = "api_breaks",
    live: bool = False,
    line: ApiBreakLine | None = API_BREAK_LINE,
):
    stack = MemoryStack(checks={"tests": "t"}, ci=("docs",))
    stack.declared_api_break_line = line
    ports = dataclasses.replace(
        _ports(5, checks=False, api_break=reason, landing=_spent()), stack=stack
    )
    ports.tracker.failed_runs = [RUN.format(1)]
    red = dataclasses.replace(_report(_red_docs(step)), runs_completed=None if live else True)
    ports.tracker.ci_reports = [red] * 6
    ports.tracker.bodies[URL.format(5)] = BODY
    return ports


def _code_failure(ports):
    """The role a code failure ends in, so a round that is not a body failure can run."""
    ports.documents.record_closure("GH-5", {"title": "t", "description": "d"})
    verdict = {"markdown": "### Why\n\nx", "reason": "stale", "verdict": "unlandable"}
    return dataclasses.replace(
        ports, runner=ScriptedRunner(landing_diagnoser=[RawResult(verdict, 0.4, 3, "s")])
    )


def _entries(ports, step):
    return [e for e in _doc(ports).get("landing", []) if e["step"] == step]


def test_a_body_without_the_line_is_fixed_closed_and_reopened_never_diagnosed(
    tmp_path, monkeypatch
):
    ports = _world()
    seen: list[list[str]] = []
    original = ports.tracker.update_pull_request_body

    def spy(number, body):
        seen.append([e["step"] for e in _doc(ports).get("landing", [])])
        original(number, body)

    monkeypatch.setattr(ports.tracker, "update_pull_request_body", spy)

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "fix_pr_body"
    assert len(seen) == 1 and seen[0][-1] == "fix_pr_body"  # registered before its first call
    calls = [
        e
        for e in ports.tracker.events
        if e in {"update_pull_request_body", "close_pull_request", "reopen_pull_request"}
    ]
    assert calls == ["update_pull_request_body", "close_pull_request", "reopen_pull_request"]
    assert pull_request.declares(ports.tracker.bodies[URL.format(5)], "Breaking-API:")
    assert REASON in ports.tracker.bodies[URL.format(5)]
    (entry,) = _entries(ports, "fix_pr_body")
    assert entry["closed_at"] and entry["resolved_at"]
    assert not _entries(ports, "diagnose") and not _entries(ports, "land_slice")
    assert ports.tracker.merge_calls == []


def test_the_fix_runs_once_per_head_and_the_next_round_waits_for_the_fresh_run(tmp_path):
    ports = _world()
    _land(ports, tmp_path, FakeLauncher())
    fresh = dataclasses.replace(_report(_red_docs(conclusion=None)), runs_completed=None)
    ports.tracker.ci_reports = [fresh] * 3

    second, _ = _land(ports, tmp_path, FakeLauncher())

    assert second.step != "fix_pr_body" and second.step != "diagnose"
    assert len(_entries(ports, "fix_pr_body")) == 1


def test_a_body_that_already_has_the_line_is_not_edited_but_still_gets_a_fresh_run(tmp_path):
    ports = _world()
    ports.tracker.bodies[URL.format(5)] = pull_request.with_required_lines(
        BODY, "Breaking-API:", REASON
    )

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "fix_pr_body"
    assert ports.tracker.updated_bodies == []
    assert "close_pull_request" in ports.tracker.events
    assert "reopen_pull_request" in ports.tracker.events


def test_a_reopening_that_is_refused_escalates_as_system_delivery(tmp_path):
    ports = _world()
    ports.tracker.reopen_error = "the head branch was deleted"

    _land(ports, tmp_path, FakeLauncher())

    assert _doc(ports)["escalation_class"] == "system_delivery"
    assert not _entries(ports, "diagnose")


def test_live_runs_wait_and_never_fix_the_body(tmp_path):
    ports = _world(live=True)

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step != "fix_pr_body"
    assert ports.tracker.updated_bodies == []
    assert "close_pull_request" not in ports.tracker.events


def test_a_failing_step_other_than_the_api_one_is_not_a_body_failure(tmp_path):
    ports = _code_failure(_world(step="lint"))

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "diagnose"
    assert ports.tracker.updated_bodies == []


def test_without_a_declared_line_the_body_is_left_alone(tmp_path):
    ports = _code_failure(_world(line=None))

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "diagnose"
    assert ports.tracker.updated_bodies == []


def test_with_no_recorded_reason_the_owner_is_asked_once_per_head(tmp_path):
    ports = _world(reason="")

    first, _ = _land(ports, tmp_path, FakeLauncher())
    second, _ = _land(ports, tmp_path, FakeLauncher())

    assert first.step == "escalate_api_break"
    assert second.step != "escalate_api_break"
    assert len(_entries(ports, "escalate_api_break")) == 1
    asked = [text for _, text in ports.tracker.comments if "Breaking-API:" in text]
    assert len(asked) == 1
    assert ports.tracker.updated_bodies == []


def _asked(tmp_path, answer):
    ports = _world(reason="")
    _land(ports, tmp_path, FakeLauncher())
    ports.tracker.timestamped["GH-5"] = [
        IssueComment("Escalated: api break", "2026-10-14T00:00:10+00:00"),
        IssueComment(answer, "2026-10-14T01:00:00+00:00"),
    ]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, ())
    ports.documents.record_closure(
        "GH-5",
        {"title": "t", "description": "d", "sections": {"Human": f"### Decision\n\n{answer}"}},
    )
    return ports


def test_the_owners_valid_answer_is_recorded_and_the_body_is_then_fixed(tmp_path):
    ports = _asked(tmp_path, "Breaking-API: The field was removed on purpose.")

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "fix_pr_body"
    assert "The field was removed on purpose." in ports.tracker.bodies[URL.format(5)]
    assert NOW  # the clock is the landing's own


def _open_fix(ports, step="fix_pr_body"):
    ports.documents.record_closure(
        "GH-5",
        {"landing": [*_spent(), {"head": HEAD, "step": step, "refusal": "ci_failed", "at": NOW}]},
    )


def test_a_close_left_without_a_reopen_is_taken_again_from_what_is_observed(tmp_path):
    ports = _world()
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    _open_fix(ports)

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "fix_pr_body"
    assert "close_pull_request" not in ports.tracker.events  # already closed: only reopens
    assert "reopen_pull_request" in ports.tracker.events
    assert pull_request.declares(ports.tracker.bodies[URL.format(5)], "Breaking-API:")
    (entry,) = _entries(ports, "fix_pr_body")
    assert entry["resolved_at"]


def test_a_pull_request_closed_by_an_open_fix_is_not_an_abandoned_result(tmp_path):
    ports = _world()
    ports.tracker.prs[URL.format(5)] = _pr("CLOSED")
    raw = state_from(_doc(ports))
    assert isinstance(poll.assess_merge(ports, "GH-5", raw, 5, None, MASTER), poll.Abandoned)

    _open_fix(ports)
    raw = state_from(_doc(ports))
    found = poll.assess_merge(ports, "GH-5", raw, 5, None, MASTER)

    assert not isinstance(found, poll.Abandoned)
    assert found.facts.pr_state == "OPEN"


def _invalid(tmp_path, answer):
    ports = _asked(tmp_path, answer)
    return ports, [text for _, text in ports.tracker.comments if "Breaking-API:" in text]


def test_an_invalid_owner_answer_is_asked_again_once_with_the_reason(tmp_path):
    ports, before = _invalid(tmp_path, "Breaking-API: has a `backtick` in it")
    assert len(before) == 1

    first, _ = _land(ports, tmp_path, FakeLauncher())
    asked = [text for _, text in ports.tracker.comments if "Breaking-API:" in text]

    assert first.kind == "dispatched" and len(asked) == 2
    assert "not taken" in asked[1]
    assert ports.tracker.updated_bodies == []

    ports.tracker.timestamped["GH-5"] = [
        *ports.tracker.timestamped["GH-5"],
        IssueComment("Breaking-API: still `bad`", "2026-10-14T02:00:00+00:00"),
    ]
    _land(ports, tmp_path, FakeLauncher())
    again = [text for _, text in ports.tracker.comments if "Breaking-API:" in text]
    assert len(again) == 2  # the re-ask is spent: nothing more is published
    assert ports.tracker.updated_bodies == []


def test_a_diagnosis_on_a_body_failure_escalates_and_never_restarts(tmp_path, monkeypatch):
    ports = _code_failure(_world())
    # A diagnosis dispatched before the cause was read (or resumed after it appeared): the
    # verdict `unlandable` must not turn into a restart that closes the pull request.
    monkeypatch.setattr(
        landing_plan,
        "step_for",
        lambda raw, refusal, *a, **k: LandingStep("diagnose", refusal),
    )

    result, _ = _land(ports, tmp_path, FakeLauncher())

    assert result.step == "diagnose"
    assert _doc(ports)["escalation_class"] is not None
    assert "close_pull_request" not in ports.tracker.events
    assert ports.tracker.updated_bodies == []
