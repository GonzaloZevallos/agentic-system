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


# --- GH-442: the release of a landing escalation only CI caused ---

from agentic.core.process.application.ports import IssueComment, IssueState, Upstream  # noqa: E402
from agentic.core.process.domain import ci  # noqa: E402
from agentic.core.process.domain.task import apply  # noqa: E402
from agentic.shared.configuration import Configuration  # noqa: E402
from tests.core.process.application import gh374_shape as gh374  # noqa: E402
from tests.fakes import MemoryTracker, memory_ports  # noqa: E402


class _Tracker(MemoryTracker):
    """Remembers what was read, and fails the reads it was told to."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []
        self.fail: set[str] = set()

    def ci_report(self, reference):
        self.reads.append("ci_report")
        return (
            ci.CiUnreadable("gh_unavailable")
            if "ci_report" in self.fail
            else super().ci_report(reference)
        )

    def branch_ci(self, branch):
        self.reads.append("branch_ci")
        return (
            ci.CiUnreadable("gh_unavailable")
            if "branch_ci" in self.fail
            else super().branch_ci(branch)
        )

    def timestamped_comments(self, task_id):
        self.reads.append("comments")
        return super().timestamped_comments(task_id)


def _ci_world(base: ci.CiReport | ci.CiUnreadable | None, *, comments=(), labels=()):
    ports = memory_ports(tracker=_Tracker(), stack=MemoryStack(checks={"tests": "t"}, ci=("web",)))
    ports.tracker.ci_reports = {gh374.REFERENCE: gh374.report(gh374.web("FAILURE"))}
    if base is not None:
        ports.tracker.branch_reports["master"] = base
    ports.tracker.timestamped["GH-374"] = [
        IssueComment(body, "2026-10-29T08:00:00+00:00") for body in comments
    ]
    ports.tracker.states[374] = IssueState("OPEN", "", (), None, labels)
    ports.workspace.upstream_answer = Upstream("origin/master", gh374.NEW_MASTER, None)
    return ports


def _ci_release(ports, raw=None, **kw):
    cfg = Configuration(product_dir="/p")
    return landing_plan.release(ports, cfg, "GH-374", raw or gh374.document(), 374, **kw)


def _green():
    return gh374.report(gh374.web("SUCCESS"))


def test_a_green_base_releases_the_ci_escalation_with_no_decision():
    found = _ci_release(_ci_world(_green()))
    assert (found.released, found.why, found.decision) == (True, "released_by_ci", None)


def test_the_release_reads_the_head_and_the_upstream_when_the_caller_has_none():
    ports = _ci_world(_green())
    other = _ci_release(ports, head="f" * 40)
    assert other.released is False and other.why == "needs_restart"
    assert _ci_release(ports, head=gh374.HEAD, upstream=gh374.NEW_MASTER).released


@pytest.mark.parametrize(
    "base",
    [
        gh374.report(gh374.web("FAILURE")),
        gh374.report(gh374.web(None)),
        gh374.report(),
        ci.CiUnreadable("gh_unavailable"),
        None,
    ],
    ids=["red", "running", "incomplete", "unreadable", "not_read"],
)
def test_a_base_that_is_not_green_is_a_wait_and_releases_nothing(base):
    found = _ci_release(_ci_world(base))
    assert (found.released, found.why, found.decision) == (False, "waiting_on_base_ci", None)


@pytest.mark.parametrize("failing", ["ci_report", "branch_ci"])
def test_a_tracker_that_cannot_answer_is_a_wait(failing):
    ports = _ci_world(_green())
    ports.tracker.fail.add(failing)
    assert _ci_release(ports).why == "waiting_on_base_ci"


def test_a_pair_already_released_waits_and_a_second_pair_is_released_again():
    raw = gh374.document()
    stamped = apply(raw, landing_rules.released_by_ci(raw, gh374.NEW_MASTER, gh374.RELEASED_AT))
    again = {**stamped, "escalated": "again", "escalation_class": "questions"}
    assert _ci_release(_ci_world(_green()), again).why == "waiting_on_base_ci"
    moved = _ci_world(_green())
    moved.workspace.upstream_answer = Upstream("origin/master", "d" * 40, None)
    assert _ci_release(moved, again).released


def test_an_owner_comment_releases_a_ci_shape_through_the_existing_route():
    ports = _ci_world(gh374.report(gh374.web("FAILURE")), comments=("Escalated: x", "go on"))
    cheap = gh374.document(cost_usd=10.0)  # under the default task cap, which the route keeps
    found = _ci_release(ports, cheap)
    assert (found.released, found.why, found.decision) == (True, "released", "go on")
    gone = _ci_world(
        gh374.report(gh374.web("FAILURE")),
        comments=("Escalated: x", "go on"),
        labels=("needs-human",),
    )
    assert _ci_release(gone, cheap).why == "waiting_on_base_ci"


def test_a_stale_comment_before_the_escalation_does_not_release_a_red_base():
    ports = _ci_world(gh374.report(gh374.web("FAILURE")), comments=("go on",))
    assert _ci_release(ports).released is False


def test_an_old_escalate_entry_is_judged_on_the_required_ci_names():
    ports = _ci_world(
        gh374.report(gh374.web("SUCCESS"), ci.CiCheck("api", "COMPLETED", "FAILURE", "x"))
    )
    ports = dataclasses.replace(ports, stack=MemoryStack(checks={"tests": "t"}, ci=("web", "api")))
    assert _ci_release(ports).why == "waiting_on_base_ci"
    raw = gh374.document()
    raw["landing"][-2] = {**raw["landing"][-2], "checks": ["web"]}
    assert _ci_release(ports, raw).released


@pytest.mark.parametrize(
    "landing_entries",
    [
        [{**gh374.ESCALATE, "step": "escalate_ledger"}],
        [{**gh374.ESCALATE, "step": "escalate_adr", "refusal": "adr_number_taken"}],
        [{"head": gh374.HEAD, "step": "diagnose", "delivered": True, "at": gh374.ESCALATED_AT}],
    ],
    ids=["ledger", "adr", "depth_one_diagnose"],
)
def test_the_other_escalations_keep_todays_answer_and_read_nothing(landing_entries):
    ports = _ci_world(_green())
    found = _ci_release(ports, gh374.document(landing=landing_entries))
    assert (found.released, found.why) == (False, "needs_restart")
    assert ports.tracker.reads == []


def _release_view(raw, ports, ancestry):
    release = _ci_release(ports, raw)
    return landing_plan.released_step(
        raw, release, gh374.facts(), "ci_failed", at=gh374.RELEASED_AT, ancestry=lambda: ancestry
    )


@pytest.mark.parametrize(
    ("ancestry", "kind"),
    [(False, "sync_master"), (True, "rerun"), (None, "wait")],
)
def test_the_released_step_of_the_deadlock_is_the_tables_and_never_a_diagnose(ancestry, kind):
    raw = gh374.document()
    before = landing_plan.step_for(raw, "ci_failed", gh374.facts())
    step = _release_view(raw, _ci_world(_green()), ancestry)
    assert (before.kind, step.kind) == ("diagnose", kind)
    assert landing_plan.takes_action(step.kind, landing_plan.LIVE_STEPS) is (kind != "wait")


def test_the_released_step_of_an_unreleased_document_is_the_plain_table():
    raw = gh374.document()
    ports = _ci_world(gh374.report(gh374.web("FAILURE")))
    step = _release_view(raw, ports, True)
    assert step == landing_plan.step_for(raw, "ci_failed", gh374.facts())


def test_the_released_step_after_an_owner_comment_applies_the_human_release():
    raw = gh374.document()
    ports = _ci_world(gh374.report(gh374.web("FAILURE")), comments=("Escalated: x", "go on"))
    raw = gh374.document(cost_usd=10.0)
    release = _ci_release(ports, raw)
    step = landing_plan.released_step(
        raw, release, gh374.facts(), "ci_failed", at=gh374.RELEASED_AT
    )
    assert release.decision == "go on" and step.kind == "diagnose"
    view = apply(raw, landing_rules.released(raw, "go on", gh374.RELEASED_AT))
    assert "go on" in view["sections"]["Human"]


def test_the_gh328_release_is_marked_released():
    from tests.core.process.application.test_landing_plan import _escalated, _release, _world

    assert _release(_world()).released is True
    assert _release(_world(), _escalated(escalation_class="questions")).released is False
