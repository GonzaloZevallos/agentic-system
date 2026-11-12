"""GH-371, subtask landing-escalate: the landing's `escalate` step on in-memory ports. A required
check red on the head and on the base branch ends in an escalation of the existing `questions`
class that names the checks and both run links, with no rerun, Developer, diagnosis or restart."""

from __future__ import annotations

import dataclasses

import pytest

from agentic.core.process.application import landing, landing_fix, landing_plan, poll, resume
from agentic.core.process.application.landing_plan import LandingContext
from agentic.core.process.application.poll import assess_merge
from agentic.core.process.application.ports import GitError, IssueComment, IssueState, Upstream
from agentic.core.process.domain import adr_numbers, ci, rules
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application import gh374_shape as gh374
from tests.core.process.application.test_landing_fix import _doc, _land
from tests.core.process.application.test_merge_ci import _check, _report
from tests.core.process.application.test_poll import _cfg as landing_fix_cfg
from tests.core.process.application.test_poll import _Locks
from tests.core.process.application.test_poll_merge import HEAD, _HeldLocks, _ports
from tests.core.process.application.test_poll_merge import MASTER as MERGE_MASTER
from tests.fakes import FakeLauncher, MemoryStack, MemoryTracker, memory_ports

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


def _matrix_world():
    """`pytest` is a matrix id: `pytest (a)` is red on the head and `pytest (b)` on the base,
    each at its own run link, with nothing patched: the facts come from the real wiring."""
    ports = dataclasses.replace(
        _ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}, ci=("pytest",))
    )
    ports.tracker.failed_runs = [HEAD_RUN]
    ports.tracker.ci_reports = [
        _report(
            dataclasses.replace(
                _check("pytest (a)", conclusion="FAILURE"), link=HEAD_RUN + "/job/1"
            ),
            _check("pytest (z)"),
        )
    ]
    ports.tracker.branch_reports["master"] = _report(
        dataclasses.replace(_check("pytest (b)", conclusion="FAILURE"), link=BASE_RUN)
    )
    return ports


def test_a_red_matrix_expansion_escalates_naming_the_declared_id_with_both_links(tmp_path):
    ports = _matrix_world()

    result, _ = _land(ports, tmp_path)

    assert result.kind == "dispatched" and result.step == "escalate"
    document = _doc(ports)
    assert document["escalation_class"] == "questions"
    reason = document["escalated"]
    assert "also red on the base branch" in reason
    assert "`pytest`" in reason and "pytest (a)" not in reason
    assert HEAD_RUN + "/job/1" in reason and BASE_RUN in reason
    [entry] = landing_plan.entries(document)
    assert entry["step"] == "escalate" and entry["checks"] == ["pytest"]


def _recorded_world(monkeypatch, declared: str, recorded: str, on_base: tuple[str, ...]):
    """The gate's red fresh run is recorded on the document's `merge_ci` pair under the run name
    `recorded`; the facts the landing reads are the ones `poll.with_recorded_red` builds from
    it (the production function, called on the assessed facts), with `on_base` as the names
    also red on the base branch, whose report holds the run link."""
    ports = dataclasses.replace(
        _ports(5, checks=False), stack=MemoryStack(checks={"tests": "t"}, ci=(declared,))
    )
    ports.tracker.failed_runs = [HEAD_RUN]
    ports.tracker.ci_reports = [
        _report(dataclasses.replace(_check(recorded, conclusion="FAILURE"), link=HEAD_RUN))
    ]
    ports.tracker.branch_reports["master"] = _report(
        dataclasses.replace(_check(declared, conclusion="FAILURE"), link=BASE_RUN)
    )
    pair = {
        "head": HEAD,
        "master": MERGE_MASTER,
        "tested": "d" * 40,
        "mode": "push",
        "published_at": "2026-10-13T23:00:00+00:00",
        "republished": False,
        "superseded": [],
        "outcome": "red",
        "failing": [recorded],
    }
    ports.documents.record_closure("GH-5", {"merge_ci": [pair]})

    def assessed(*args, **kwargs):
        result = assess_merge(*args, **kwargs)
        facts = dataclasses.replace(result.facts, merge_ci=[pair], ci_red_on_base=on_base)
        recorded_facts = poll.with_recorded_red(facts)
        assert recorded_facts.ci_subject == "tested"  # the recorded pair was read
        return dataclasses.replace(result, facts=recorded_facts)

    monkeypatch.setattr(landing, "assess_merge", assessed)
    return ports


@pytest.mark.parametrize(("declared", "recorded"), [("web", "web"), ("pytest", "pytest (a)")])
def test_an_escalation_on_a_recorded_red_fresh_run_names_the_declared_check(
    tmp_path, monkeypatch, declared, recorded
):
    ports = _recorded_world(monkeypatch, declared, recorded, on_base=(declared,))

    result, _ = _land(ports, tmp_path)

    assert result.kind == "dispatched" and result.step == "escalate"
    document = _doc(ports)
    [entry] = landing_plan.entries(document)
    assert entry["step"] == "escalate" and entry["checks"] == [declared]
    reason = document["escalated"]
    assert "also red on the base branch" in reason and f"`{declared}`" in reason
    assert BASE_RUN in reason


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
        raw,
        release,
        gh374.facts(),
        "ci_failed",
        at=gh374.RELEASED_AT,
        ancestry=lambda: ancestry,
        turn="head",
    )


@pytest.mark.parametrize(
    ("ancestry", "kind"),
    [(False, "sync_master"), (True, "rerun"), (None, "wait")],
)
def test_the_released_step_of_the_deadlock_is_the_tables_and_never_a_diagnose(ancestry, kind):
    raw = gh374.document()
    before = landing_plan.step_for(raw, "ci_failed", gh374.facts(), turn="head")
    step = _release_view(raw, _ci_world(_green()), ancestry)
    assert (before.kind, step.kind) == ("diagnose", kind)
    assert landing_plan.takes_action(step.kind, landing_plan.LIVE_STEPS) is (kind != "wait")


def test_the_released_step_of_an_unreleased_document_is_the_plain_table():
    raw = gh374.document()
    ports = _ci_world(gh374.report(gh374.web("FAILURE")))
    step = _release_view(raw, ports, True)
    assert step == landing_plan.step_for(raw, "ci_failed", gh374.facts(), turn="head")


def test_the_released_step_after_an_owner_comment_applies_the_human_release():
    raw = gh374.document()
    ports = _ci_world(gh374.report(gh374.web("FAILURE")), comments=("Escalated: x", "go on"))
    raw = gh374.document(cost_usd=10.0)
    release = _ci_release(ports, raw)
    step = landing_plan.released_step(
        raw, release, gh374.facts(), "ci_failed", at=gh374.RELEASED_AT, turn="head"
    )
    assert release.decision == "go on" and step.kind == "diagnose"
    view = apply(raw, landing_rules.released(raw, "go on", gh374.RELEASED_AT))
    assert "go on" in view["sections"]["Human"]


def test_the_gh328_release_is_marked_released():
    from tests.core.process.application.test_landing_plan import _escalated, _release, _world

    assert _release(_world()).released is True
    assert _release(_world(), _escalated(escalation_class="questions")).released is False


# --- GH-442, release-wiring: land, the poller and resume over the released escalation ---


def _escalated_landing(*extra: dict) -> dict:
    """GH-374's landing list on the head the tests' pull request has: three `fix` entries spent,
    an `escalate` for `ci_failed` and a `rerun` after it."""
    fixes = [
        {
            "head": HEAD,
            "step": "fix",
            "refusal": "open_findings_blocking",
            "at": f"2026-10-13T19:0{n}:00+00:00",
        }
        for n in range(3)
    ]
    escalate = {
        "head": HEAD,
        "step": "escalate",
        "refusal": "ci_failed",
        "at": "2026-10-13T20:00:00+00:00",
    }
    rerun = {
        "head": HEAD,
        "step": "rerun",
        "refusal": "ci_failed",
        "at": "2026-10-13T21:14:00+00:00",
    }
    return {
        "escalated": "CI check(s) `web` red on the head and also red on the base branch",
        "escalation_class": "questions",
        "landing": [*fixes, escalate, rerun, *extra],
        "title": "t",
        "description": "d",
    }


def _red_world(monkeypatch, base, *, ancestor: bool, document=None):
    """The `web` check red on the head; `base` is master's report. `ancestor` says whether the
    head already holds the upstream sha. The dispatch is recorded, not run."""
    ports = _world(monkeypatch, on_base=())
    ports.tracker.branch_reports["master"] = base
    ports.documents.save({**ports.documents.last, **(document or _escalated_landing())})
    if ancestor:
        ports.workspace.ancestors.add(MERGE_MASTER)
    dispatched: list[LandingContext] = []
    monkeypatch.setattr(landing, "_dispatch", dispatched.append)
    return ports, dispatched


def _green_base():
    return _report(_check(conclusion="SUCCESS"))


def _red_base():
    return _report(_check(conclusion="FAILURE"))


@pytest.mark.parametrize(("ancestor", "kind"), [(False, "sync_master"), (True, "rerun")])
def test_land_releases_a_ci_escalation_once_the_base_is_green_and_takes_the_tables_step(
    tmp_path, monkeypatch, ancestor, kind
):
    ports, dispatched = _red_world(monkeypatch, _green_base(), ancestor=ancestor)
    # GH-374 before the release: the refusal is `ci_failed`, conflicts are not True, nothing is
    # red on the base, and the escalated document's table step is the diagnose.
    before = poll.assess_merge(ports, "GH-5", _doc(ports), 5, frozenset(), MERGE_MASTER)
    assert before.decision.reason == "ci_failed" and before.facts.conflicts is not True
    assert before.facts.ci_red_on_base == ()
    assert (
        landing_plan.step_for(_doc(ports), "ci_failed", before.facts, turn="head").kind
        == "diagnose"
    )

    result, lines = _land(ports, tmp_path)

    document = _doc(ports)
    assert (result.kind, result.step) == ("dispatched", kind), (result, lines)
    assert [c.step.kind for c in dispatched] == [kind]
    assert not document.get("escalated") and not document.get("escalation_class")
    written = landing_plan.entries(document)
    assert [e["step"] for e in written[-3:]] == ["escalate", "rerun", kind]
    assert written[-3]["ci_released"] == MERGE_MASTER and "released_at" in written[-3]
    assert len(written) == 6
    assert "Human" not in document.get("sections", {})
    assert ports.tracker.unlabeled == [("GH-5", "needs-human")]
    assert any("escalation released, the base branch CI is green" in line for line in lines)
    assert not any("--fresh" in line for line in lines)


def test_land_waits_without_a_restart_line_while_the_base_is_red(tmp_path, monkeypatch):
    ports, dispatched = _red_world(monkeypatch, _red_base(), ancestor=True)
    before = _doc(ports)

    result, lines = _land(ports, tmp_path)

    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_base_ci")
    assert dispatched == [] and _doc(ports) == before and ports.tracker.unlabeled == []
    assert any(
        "waiting for the base branch CI to turn green, or an owner comment" in line
        for line in lines
    )
    assert not any("--fresh" in line for line in lines)


def test_land_waits_and_writes_nothing_when_ancestry_cannot_be_read(tmp_path, monkeypatch):
    ports, dispatched = _red_world(monkeypatch, _green_base(), ancestor=True)

    def broken(*args, **kwargs):
        raise GitError("boom")

    monkeypatch.setattr(ports.workspace, "is_ancestor", broken)
    before = _doc(ports)

    result, _ = _land(ports, tmp_path)

    assert (result.kind, result.reason, result.step) == ("skipped", "not_live", "wait")
    assert dispatched == [] and _doc(ports) == before


def test_land_refuses_a_released_step_the_registered_run_kind_does_not_admit(tmp_path, monkeypatch):
    # GH-544: the run kind is judged on the step the release gives (one block in `land`), so the
    # released step must be one a model kind owns: a head without the upstream is a sync_master.
    ports, dispatched = _red_world(monkeypatch, _green_base(), ancestor=False)
    before = _doc(ports)
    lines: list[str] = []

    result = landing.land(
        "GH-5",
        landing_fix_cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MERGE_MASTER, None),
        frozenset(),
        lines.append,
        clock=lambda: "2026-10-29T09:00:00+00:00",
        run_kind="land_free",
        argv0="agentic",
        turn="head",
    )

    assert (result.kind, result.reason) == ("skipped", "run_kind_mismatch")
    assert dispatched == [] and _doc(ports) == before


def test_a_ci_shape_escalated_document_whose_step_is_a_rerun_is_dispatched_as_before(
    tmp_path, monkeypatch
):
    # No `rerun` yet and a head red only on the head: the table says `rerun`, no release is asked.
    document = _escalated_landing()
    document["landing"] = document["landing"][:-2]
    ports, dispatched = _red_world(monkeypatch, _green_base(), ancestor=True, document=document)
    ports.tracker.branch_reports.clear()

    result, _ = _land(ports, tmp_path)

    assert (result.kind, result.step) == ("dispatched", "rerun")
    assert [c.step.kind for c in dispatched] == ["rerun"]
    assert [e["step"] for e in landing_plan.entries(_doc(ports))][-1] == "rerun"
    assert ports.tracker.unlabeled == []


def test_the_escalate_entry_carries_the_names_and_the_upstream_it_was_about(tmp_path, monkeypatch):
    ports = _world(monkeypatch, on_base=("web",))

    _land(ports, tmp_path)

    [entry] = landing_plan.entries(_doc(ports))
    assert entry["step"] == "escalate"
    assert entry["checks"] == ["web"] and entry["upstream"] == MERGE_MASTER


def _poll(ports, tmp_path):
    lands: list = []
    poll.gather_merges(
        ports,
        frozenset(),
        MERGE_MASTER,
        _Locks(),
        [],
        lands,
        cfg=landing_fix_cfg(tmp_path),
    )
    return lands


def test_the_poller_registers_the_child_under_the_released_steps_run_kind(tmp_path, monkeypatch):
    ports, _ = _red_world(monkeypatch, _green_base(), ancestor=True)
    kind = landing_rules.run_kind_of("rerun", "ci_failed")

    [candidate] = _poll(ports, tmp_path)

    assert candidate.kind == kind
    assert landing_rules.run_kind_of("diagnose", "ci_failed") != kind


def test_the_poller_offers_nothing_for_a_ci_shape_whose_base_is_red(tmp_path, monkeypatch):
    ports, _ = _red_world(monkeypatch, _red_base(), ancestor=True)

    assert _poll(ports, tmp_path) == []


def test_the_poller_reads_the_base_report_for_no_document_outside_the_ci_shape(
    tmp_path, monkeypatch
):
    ports = memory_ports(tracker=_Tracker(), stack=MemoryStack(checks={"tests": "t"}, ci=("web",)))
    ledger = [{"head": HEAD, "step": "escalate_ledger", "refusal": "open_findings_blocking"}]
    ports.documents.save(
        {
            "id": "GH-5",
            "status": "ready_for_review",
            "pull_request": "https://github.com/o/r/pull/5",
            "escalated": "ledger",
            "escalation_class": "questions",
            "landing": ledger,
            "subtasks": {"only": {"id": "only", "status": "approved", "reviewed_head": HEAD}},
        }
    )
    _poll(ports, tmp_path)
    assert "branch_ci" not in ports.tracker.reads


BRANCH = "agentic/GH-5"
APPROVED_AT = "2026-10-13T22:00:00+00:00"
ANSWERED_AT = "2026-10-13T23:00:00+00:00"


def _with_branch(document: dict) -> dict:
    """`document` as `resume --from-document` finds an approved task whose pull request is open:
    the integration approved at the pull request's head, and the task branch past it."""
    unit = {"status": "approved", "branch": BRANCH, "base": "b0", "reviewed_head": HEAD}
    return {
        **document,
        "branch": BRANCH,
        "subtasks": {"only": {"status": "approved"}, "integration": unit},
    }


def _pair(monkeypatch, tmp_path, base, document=None, *, ancestor=True, comments=()):
    """The line `resume --from-document` refuses with (through `seed_increment` and
    `_moved_line`) and what `land` then does, over one document and one world."""
    ports, dispatched = _red_world(
        monkeypatch,
        base,
        ancestor=ancestor,
        document=_with_branch(document or _escalated_landing()),
    )
    ports.workspace.existing_branches[BRANCH] = ("c" * 40, "b0")
    ports.workspace.ancestors.add(HEAD)
    if comments:
        ports.tracker.timestamped["GH-5"] = [IssueComment(b, at) for b, at in comments]
        ports.tracker.states[5] = IssueState("OPEN", "", (), None, ())
    with pytest.raises(ValueError) as refused:
        resume.seed_increment("GH-5", landing_fix_cfg(tmp_path), ports)
    result, lines = _land(ports, tmp_path)
    return str(refused.value).splitlines()[-1].removeprefix("- "), result, lines


def test_resume_names_land_only_when_land_moves_the_ci_shape(tmp_path, monkeypatch):
    red_line, red_result, _ = _pair(monkeypatch, tmp_path, _red_base())
    assert red_result.kind == "skipped" and "agentic land" not in red_line
    assert "base branch CI" in red_line and "--fresh" not in red_line

    green_line, green_result, _ = _pair(monkeypatch, tmp_path, _green_base())
    assert green_result.kind == "dispatched" and "agentic land --issue 5" in green_line


def test_resume_does_not_tell_a_ci_shape_whose_step_is_a_rerun_to_wait(tmp_path, monkeypatch):
    document = _escalated_landing()
    document["landing"] = document["landing"][:-2]
    line, result, _ = _pair(monkeypatch, tmp_path, _green_base(), document)
    assert result.step == "rerun" and "agentic land --issue 5" in line
    assert "waits" not in line


def _tail(entry: dict) -> dict:
    document = _escalated_landing()
    document["landing"] = [*document["landing"], entry]
    return document


TODAY = (
    "GH-5 has a pull request: its branch moved after approval, which the landing owns; "
    "run `agentic land --issue 5`."
)


def test_a_depth_one_diagnose_escalation_is_not_sent_to_land_that_does_not_move_it(
    tmp_path, monkeypatch
):
    entry = {
        "head": HEAD,
        "step": "diagnose",
        "refusal": "ci_failed",
        "delivered": True,
        "verdict": "unlandable",
        "at": "2026-10-13T20:00:00+00:00",
    }
    line, result, _ = _pair(monkeypatch, tmp_path, _green_base(), _tail(entry))
    # land answers `not_live` and writes nothing, so resume must not name it (invariant 11).
    assert (result.kind, result.reason) == ("skipped", "not_live")
    assert "agentic land" not in line and "only a restart moves" in line


def test_a_ledger_escalation_is_not_sent_to_land_that_does_not_move_it(tmp_path, monkeypatch):
    ledger = {
        "head": HEAD,
        "step": "escalate_ledger",
        "refusal": "open_findings_blocking",
        "at": "2026-10-13T20:00:00+00:00",
    }
    document = _tail(ledger)
    document["escalated"] = "open findings the integrator cannot clear"
    line, result, _ = _pair(monkeypatch, tmp_path, _green_base(), document)
    # land holds the step back for the release and answers `needs_restart`: resume says the same.
    assert (result.kind, result.reason) == ("skipped", "escalated_needs_restart")
    assert "agentic land" not in line and "only a restart moves" in line


def test_a_ci_release_is_one_write_holding_the_stamp_and_the_next_entry(tmp_path, monkeypatch):
    ports, _ = _red_world(monkeypatch, _green_base(), ancestor=True)
    writes: list[dict] = []
    save, record = ports.documents.save, ports.documents.record_closure
    ports.documents.save = lambda state: (writes.append(dict(state)), save(state))[1]
    ports.documents.record_closure = lambda t, v: (writes.append(dict(v)), record(t, v))[1]

    result, _ = _land(ports, tmp_path)

    assert result.kind == "dispatched"
    stamped = [w for w in writes if any("ci_released" in e for e in landing_plan.entries(w))]
    assert len(writes) == 1 and stamped == writes
    entries = landing_plan.entries(writes[0])
    escalate = next(e for e in entries if e["step"] == "escalate")
    assert entries[-1]["step"] == "rerun" and escalate["ci_released"] == MERGE_MASTER
    assert len(entries) == 6
    assert not writes[0].get("escalated")


def _gh328() -> dict:
    """GH-320's shape: the diagnoser never delivered, so the task escalated as `failure`."""
    document = _escalated_landing()
    document["escalation_class"] = "failure"
    document["escalated"] = "Landing Diagnoser did not deliver"
    document["landing"] = [
        *document["landing"][:3],
        document["landing"][-1],
        {
            "head": HEAD,
            "step": "diagnose",
            "refusal": "ci_failed",
            "failure": "max_turns",
            "at": "2026-10-13T21:30:00+00:00",
        },
    ]
    return document


def test_the_gh328_shape_waits_for_a_human_and_resume_does_not_name_land(tmp_path, monkeypatch):
    line, result, _ = _pair(monkeypatch, tmp_path, _green_base(), _gh328())
    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_human")
    assert "agentic land" not in line and "--fresh" not in line and "human comment" in line


def test_the_gh328_shape_with_an_owner_comment_is_moved_by_land_and_resume_names_it(
    tmp_path, monkeypatch
):
    comments = [
        ("Escalated: Landing Diagnoser did not deliver", APPROVED_AT),
        ("go on", ANSWERED_AT),
    ]
    line, result, _ = _pair(monkeypatch, tmp_path, _green_base(), _gh328(), comments=comments)
    assert (result.kind, result.step) == ("dispatched", "diagnose")
    assert "agentic land --issue 5" in line


def test_resume_keeps_naming_land_for_a_document_that_is_not_escalated(tmp_path, monkeypatch):
    ports, _ = _red_world(monkeypatch, _red_base(), ancestor=True)
    state = {**_doc(ports), "escalated": None}
    line = resume._landing_owns_line("GH-5", ports, landing_fix_cfg(tmp_path), state)
    assert "agentic land --issue 5" in line


# --- GH-544: an owner's decision on an `escalate_adr` question makes `land` apply it ---

ASKED_AT = "2026-10-13T20:00:00+00:00"
ESCALATED_COMMENT_AT = "2026-10-13T20:00:10+00:00"
DECIDED_AT = "2026-10-13T23:00:00+00:00"
AMBIGUOUS = adr_numbers.Ambiguous("unclassified_token", "app/main.py:228", (("a", "b"),))
ADR_COMMENTS = [("Escalated: ADR collision", ESCALATED_COMMENT_AT), ("renumber it", DECIDED_AT)]


def _adr_document(*, resolved: bool = True) -> dict:
    """An escalated landing whose last entry is the resolved `escalate_adr` question on HEAD."""
    entry = {"head": HEAD, "step": "escalate_adr", "refusal": "adr_number_taken", "at": ASKED_AT}
    if resolved:
        entry["resolved_at"] = ASKED_AT
    return {
        "escalated": "ADR collision",
        "escalation_class": "questions",
        "landing": [entry],
        "title": "t",
        "description": "d",
    }


def _adr_world(monkeypatch, *, comments=ADR_COMMENTS, labels=(), document=None):
    """GH-5 refused with `adr_number_taken` on an `Ambiguous` collision, the owner's `comments`
    on the issue. The dispatch is recorded, not run."""
    ports, dispatched = _red_world(
        monkeypatch, _green_base(), ancestor=True, document=document or _adr_document()
    )
    ports.tracker.timestamped["GH-5"] = [IssueComment(b, at) for b, at in comments]
    ports.tracker.states[5] = IssueState("OPEN", "", (), None, labels)

    def assessed(*args, **kwargs):
        result = assess_merge(*args, **kwargs)
        return dataclasses.replace(
            result,
            decision=rules.MergeDecision("refuse", "adr_number_taken"),
            facts=dataclasses.replace(result.facts, adr_collision=AMBIGUOUS),
        )

    monkeypatch.setattr(landing, "assess_merge", assessed)
    monkeypatch.setattr(poll, "assess_merge", assessed)
    monkeypatch.setattr(resume, "assess_merge", assessed)
    return ports, dispatched


def _land_as(ports, tmp_path, run_kind=None):
    kwargs = {} if run_kind is None else {"run_kind": run_kind}
    lines: list[str] = []
    result = landing.land(
        "GH-5",
        landing_fix_cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", MERGE_MASTER, None),
        frozenset(),
        lines.append,
        clock=lambda: "2026-10-14T00:00:00+00:00",
        argv0="agentic",
        **kwargs,
        turn="head",
    )
    return result, lines


@pytest.mark.parametrize("run_kind", [None, "land_model"], ids=["default", "land_model"])
def test_an_owner_decision_makes_land_dispatch_apply_adr_decision(tmp_path, monkeypatch, run_kind):
    ports, dispatched = _adr_world(monkeypatch)

    result, lines = _land_as(ports, tmp_path, run_kind)

    document = _doc(ports)
    assert (result.kind, result.step) == ("dispatched", "apply_adr_decision"), (result, lines)
    assert [c.step.kind for c in dispatched] == ["apply_adr_decision"]
    assert not document.get("escalated")
    asked, applied = landing_plan.entries(document)
    assert "released_at" in asked and applied["step"] == "apply_adr_decision"
    assert applied["decision_at"] == asked["released_at"]
    assert "Human" in document["sections"]

    # The decision is taken once: the next round never applies the same one again.
    _land_as(ports, tmp_path, run_kind)
    assert [c.step.kind for c in dispatched].count("apply_adr_decision") == 1


def test_a_land_checks_child_is_refused_and_the_document_is_untouched(tmp_path, monkeypatch):
    ports, dispatched = _adr_world(monkeypatch)
    before = _doc(ports)

    result, _ = _land_as(ports, tmp_path, "land_checks")

    assert (result.kind, result.reason) == ("skipped", "run_kind_mismatch")
    assert dispatched == [] and _doc(ports) == before


@pytest.mark.parametrize(
    "world",
    [
        {"comments": [ADR_COMMENTS[0]]},
        {"comments": [("renumber it", "2026-10-13T19:00:00+00:00"), ADR_COMMENTS[0]]},
        {"labels": ("needs-human",)},
    ],
    ids=["no_decision", "comment_before_the_escalation", "needs_human_still_there"],
)
def test_without_a_released_decision_land_writes_nothing_and_waits(tmp_path, monkeypatch, world):
    ports, dispatched = _adr_world(monkeypatch, **world)
    before = _doc(ports)

    result, _ = _land_as(ports, tmp_path)

    assert (result.kind, result.reason) == ("skipped", "escalated_waiting_on_human")
    assert dispatched == [] and _doc(ports) == before


def test_a_legacy_entry_without_moves_is_released_by_the_decision(tmp_path, monkeypatch):
    document = _adr_document()
    assert "moves" not in document["landing"][0]
    ports, dispatched = _adr_world(monkeypatch, document=document)

    result, _ = _land_as(ports, tmp_path)

    assert result.step == "apply_adr_decision" and len(dispatched) == 1


def test_the_poller_registers_the_released_adr_decision_under_land_model(tmp_path, monkeypatch):
    ports, _ = _adr_world(monkeypatch)

    [candidate] = _poll(ports, tmp_path)

    assert candidate.kind == "land_model"
    assert candidate.kind == landing_rules.run_kind_of("apply_adr_decision", "adr_number_taken")


def test_the_poller_offers_nothing_while_the_decision_is_missing(tmp_path, monkeypatch):
    ports, _ = _adr_world(monkeypatch, comments=[ADR_COMMENTS[0]])

    assert _poll(ports, tmp_path) == []


def test_resume_names_land_after_the_owner_decided_and_not_before(tmp_path, monkeypatch):
    ports, _ = _adr_world(monkeypatch)
    state = _doc(ports)
    line = resume._landing_owns_line("GH-5", ports, landing_fix_cfg(tmp_path), state)
    assert "agentic land --issue 5" in line

    waiting, _ = _adr_world(monkeypatch, comments=[ADR_COMMENTS[0]])
    held = resume._landing_owns_line("GH-5", waiting, landing_fix_cfg(tmp_path), _doc(waiting))
    assert "agentic land" not in held.replace("`agentic land` has nothing", "")
