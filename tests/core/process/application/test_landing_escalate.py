"""GH-371, subtask landing-escalate: the landing's `escalate` step on in-memory ports. A required
check red on the head and on the base branch ends in an escalation of the existing `questions`
class that names the checks and both run links, with no rerun, Developer, diagnosis or restart."""

from __future__ import annotations

import dataclasses

import pytest

from agentic.core.process.application import landing, landing_fix, landing_plan
from agentic.core.process.application.poll import assess_merge
from agentic.core.process.domain import landing as landing_rules
from tests.core.process.application.test_landing_fix import _doc, _land
from tests.core.process.application.test_merge_ci import _check, _report
from tests.core.process.application.test_poll_merge import HEAD, _ports
from tests.fakes import FakeLauncher, MemoryStack

HEAD_RUN = "https://github.com/o/r/actions/runs/101"
BASE_RUN = "https://github.com/o/r/actions/runs/202"


def _world(monkeypatch, *, on_base: tuple[str, ...]):
    """A red `web` on the head. The facts carry `on_base` as the names also red on the base
    (what the merge wiring reads), and the base branch's report holds the run link."""
    ports = dataclasses.replace(
        _ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}, ci=("web",))
    )
    ports.tracker.failed_runs = [HEAD_RUN]
    ports.tracker.ci_reports = [
        _report(dataclasses.replace(_check(conclusion="FAILURE"), link=HEAD_RUN))
    ]
    ports.tracker.branch_reports["master"] = _report(
        dataclasses.replace(_check(conclusion="FAILURE"), link=BASE_RUN)
    )

    def assessed(*args, **kwargs):
        result = assess_merge(*args, **kwargs)
        facts = dataclasses.replace(result.facts, ci_red_on_base=on_base)
        return dataclasses.replace(result, facts=facts)

    monkeypatch.setattr(landing, "assess_merge", assessed)
    return ports


def test_a_check_red_on_the_head_and_the_base_escalates_without_running_anything(
    tmp_path, monkeypatch
):
    ports = _world(monkeypatch, on_base=("web",))
    launcher = FakeLauncher()

    result, _ = _land(ports, tmp_path, launcher)

    assert result.kind == "dispatched" and result.step == "escalate"
    document = _doc(ports)
    assert document["escalation_class"] == "questions"
    reason = document["escalated"]
    assert "also red on the base branch" in reason
    assert "`web`" in reason and HEAD_RUN in reason and BASE_RUN in reason
    assert "external" not in reason and "proven" not in reason
    assert ports.tracker.labels == [("GH-5", "needs-human")]
    [(_, comment)] = ports.tracker.comments
    assert "also red on the base branch" in comment
    [entry] = landing_plan.entries(document)
    assert entry["step"] == "escalate" and entry["refusal"] == "ci_failed"
    assert entry["head"] == HEAD
    # no model: nothing rerun, nothing spawned, no role run, no restart
    assert ports.tracker.rerun_calls == [] and launcher.spawned == []
    assert (
        not document.get("runs")
        and document.get("subtasks", {}).get("only", {}).get("status") != "returned"
    )
    assert not [e for e in landing_plan.entries(document) if e["step"] == "diagnose"]


def test_the_escalation_is_taken_once_per_head(tmp_path, monkeypatch):
    ports = _world(monkeypatch, on_base=("web",))
    _land(ports, tmp_path)

    again, _ = _land(ports, tmp_path)

    assert again.kind == "skipped" and again.reason == "already_dispatched"
    assert len(ports.tracker.comments) == 1


def test_a_check_red_only_on_the_head_is_rerun_as_before(tmp_path, monkeypatch):
    ports = _world(monkeypatch, on_base=())

    result, _ = _land(ports, tmp_path)

    assert result.step == "rerun"
    assert "escalated" not in _doc(ports)


def test_the_escalate_step_is_taken_whatever_the_live_set_says_and_spends_no_model():
    assert landing_plan.takes_action("escalate", frozenset())
    assert not landing_plan.takes_action("wait", frozenset({"wait"}))
    assert landing_rules.run_kind_of("escalate", "ci_failed") == "land_checks"


def test_the_reason_names_the_base_link_as_unread_when_the_base_report_cannot_be_read(
    tmp_path, monkeypatch
):
    ports = _world(monkeypatch, on_base=("web",))
    ports.tracker.branch_reports.clear()

    _land(ports, tmp_path)

    reason = _doc(ports)["escalated"]
    assert "also red on the base branch" in reason and HEAD_RUN in reason
    assert "base run link not read" in reason


@pytest.mark.parametrize("names", [("web",), ("web", "e2e")])
def test_the_reason_lists_every_name(names):
    head = {n: f"https://ci/h/{n}" for n in names}
    base = {n: f"https://ci/b/{n}" for n in names}
    text = landing_fix.red_on_base_reason(names, head, base)
    for n in names:
        assert f"`{n}`" in text and head[n] in text and base[n] in text
    assert text.count("also red on the base branch") == 1
