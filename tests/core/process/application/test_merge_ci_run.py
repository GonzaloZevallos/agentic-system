"""GH-463: the merge gate of a `ci:` product gets one fresh GitHub verdict for the head merged
with the current master instead of running the suite locally, through `merge_task` on in-memory
ports. Every test calls the verb two or three times over one stored document, as the poller
does, with local-check machinery that raises if it is ever touched."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic.core.process.application import landing_plan, merge
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, DispatchedRun
from agentic.core.process.domain.presubmit import CiDispatch
from agentic.core.process.domain.task import MergeCiRecord
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _ports
from tests.fakes import MemoryStack, MemoryWorkspace

TESTED = ("1" * 40, "2" * 40, "3" * 40)
OLD_MASTER = "9" * 40
RUN = "https://github.com/o/r/actions/runs/{}/job/5"
REF = "agentic-ci/GH-5"
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


class _Workspace(MemoryWorkspace):
    """The merge commit each checkout builds is the next of `TESTED`."""

    def __init__(self) -> None:
        super().__init__()
        self.built = list(TESTED)

    def full_head(self, path: Path) -> str:
        return self.built.pop(0)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> str:
        return self.now.isoformat()

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _never(*_args, **_kwargs):
    raise AssertionError("a ci: product's merge gate ran a local check")


@pytest.fixture(autouse=True)
def _no_local_checks(monkeypatch):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())
    monkeypatch.setattr(merge, "CheckoutPresubmit", _never)
    monkeypatch.setattr(merge, "_setup", _never)
    monkeypatch.setattr(merge, "run_checks", _never)


def _check(name="web", conclusion: str | None = "SUCCESS", run=1) -> CiCheck:
    return CiCheck(name, "COMPLETED" if conclusion else "IN_PROGRESS", conclusion, RUN.format(run))


def _report(*checks: CiCheck, head: str = HEAD, base: str = OLD_MASTER) -> CiReport:
    return CiReport(head, "master", base, "2026-11-03T09:00:00+00:00", checks)


def _world(**document):
    """A ready task whose own CI ran on an older master the head does not contain: stale."""
    ports = _ports(5, checks=False, **document)
    workspace = _Workspace()
    workspace.upstream_answer = ports.workspace.upstream_answer
    workspace.conflict_answers = ports.workspace.conflict_answers
    workspace.contains_answer = "no"
    ports = dataclasses.replace(
        ports, workspace=workspace, stack=MemoryStack(checks={"tests": "x"}, ci=("web",))
    )
    ports.tracker.ci_reports = [_report(_check())]
    return ports


def _call(ports, tmp_path, clock):
    return merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
        clock,
    )


def _pairs(ports) -> list[dict]:
    return list((ports.documents.load("GH-5") or {}).get("merge_ci", []))


def _refusals(ports) -> list[str]:
    """The gate's own refusal comments (the landing's escalation notes are not these)."""
    return [text for _, text in ports.tracker.comments if text.startswith("Auto-merge refused")]


def _branch(tested: str, conclusion: str | None = "SUCCESS", run: int = 42) -> CiReport:
    return CiReport(tested, None, None, None, (_check(conclusion=conclusion, run=run),))


def _dispatched(tested: str, run_id: int = 77) -> DispatchedRun:
    return DispatchedRun(
        run_id,
        "workflow_dispatch",
        f"CI {tested}",
        "completed",
        "2026-11-03T10:01:00Z",
        "https://x/runs/77",
        (_check(run=run_id),),
    )


def _pair(**over) -> dict:
    return {
        "head": HEAD,
        "master": OLD_MASTER,
        "tested": TESTED[2],
        "mode": "push",
        "published_at": NOW.isoformat(),
        "republished": False,
        "superseded": [],
        "outcome": "green",
        "run_id": "5",
        **over,
    }


def test_a_stale_verdict_triggers_one_run_on_the_merged_head_and_that_run_decides(tmp_path):
    ports, clock = _world(), _Clock()

    first = _call(ports, tmp_path, clock)

    assert (first.kind, first.reason) == ("stopped", "ci_wait")
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    assert ports.workspace.merges[0][1] == MASTER
    assert ports.workspace.merge_messages[0]
    assert "[skip ci]" not in (ports.workspace.merge_messages[0] or "")
    [pair] = _pairs(ports)
    assert (pair["head"], pair["master"], pair["tested"]) == (HEAD, MASTER, TESTED[0])
    assert (pair["mode"], pair["republished"], pair["superseded"]) == ("push", False, [])
    assert pair["published_at"] == clock()
    assert set(ports.workspace.removed_checkouts) == {"merge-GH-5"} and ports.commands.ran == []

    ports.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion=None)
    clock.advance(60)
    assert _call(ports, tmp_path, clock).reason == "ci_wait"

    ports.tracker.branch_reports[REF] = _branch(TESTED[0])
    clock.advance(60)
    done = _call(ports, tmp_path, clock)

    assert done.kind == "merged"
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    [pair] = _pairs(ports)
    assert (pair["outcome"], pair["run_id"]) == ("green", "42")
    document = ports.documents.load("GH-5") or {}
    assert document["auto_merge_via"] == "ci" and "merge_checks" not in document


def test_a_red_fresh_run_is_refused_with_its_run_id_and_no_local_check(tmp_path):
    ports, clock = _world(), _Clock()
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion="FAILURE", run=9)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_failed")
    [pair] = _pairs(ports)
    assert (pair["outcome"], pair["run_id"]) == ("red", "9")
    assert len(ports.workspace.ci_ref_calls) == 1


def test_an_untrusted_head_is_judged_by_one_dispatched_run_and_never_pushed_to_a_branch(tmp_path):
    ports, clock = _world(), _Clock()
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)
    ports.stack.declared_ci_dispatch = CiDispatch("ci.yml", "sha")

    first = _call(ports, tmp_path, clock)

    assert (first.kind, first.reason) == ("stopped", "ci_wait")
    assert ports.workspace.ci_ref_calls == [("publish_dispatch", "GH-5")]
    assert ports.tracker.dispatch_calls == [("ci.yml", "", {"sha": TESTED[0]})]
    assert "[skip ci]" in (ports.workspace.merge_messages[0] or "")
    [pair] = _pairs(ports)
    assert pair["mode"] == "dispatch" and pair["tested"] == TESTED[0]

    ports.tracker.dispatched = [(_dispatched(TESTED[0]),)]
    clock.advance(90)
    done = _call(ports, tmp_path, clock)

    assert done.kind == "merged"
    assert ports.workspace.ci_refs == {}
    assert ports.tracker.dispatch_calls == [("ci.yml", "", {"sha": TESTED[0]})]
    assert [call[0] for call in ports.workspace.ci_ref_calls] == ["publish_dispatch"]
    [pair] = _pairs(ports)
    assert (pair["outcome"], pair["run_id"]) == ("green", "77")


def test_an_untrusted_head_without_a_declared_dispatch_is_refused_at_once(tmp_path):
    ports, clock = _world(), _Clock()
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_untrusted_no_dispatch")
    assert ports.workspace.ci_ref_calls == [] and ports.tracker.dispatch_calls == []
    assert ports.workspace.detached == [] and _pairs(ports) == []
    [comment] = _refusals(ports)
    assert "ci_untrusted_no_dispatch" in comment and "ci_dispatch" in comment


def _ran_on_ci(ran_on: str = "ci") -> dict:
    return {
        "version": 1,
        "head": HEAD,
        "passed": True,
        "duration_s": 5.0,
        "declared": {"name": "ci"},
        "results": [{"name": "web", "passed": True}],
        "witness": None,
        "ran_on": ran_on,
    }


def test_a_ci_pass_recorded_for_a_head_that_contains_master_merges_with_no_trigger(tmp_path):
    ports, clock = _world(full_checks=_ran_on_ci()), _Clock()
    ports.workspace.merge_bases[(HEAD, MASTER)] = MASTER

    result = _call(ports, tmp_path, clock)

    assert result.kind == "merged"
    assert ports.workspace.ci_ref_calls == [] and ports.tracker.dispatch_calls == []
    assert ports.workspace.detached == [] and _pairs(ports) == []


def test_a_local_pass_is_never_enough_for_a_ci_product(tmp_path):
    ports, clock = _world(full_checks=_ran_on_ci("local")), _Clock()
    ports.workspace.merge_bases[(HEAD, MASTER)] = MASTER

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert ports.workspace.ci_ref_calls == [("publish", REF)]


def test_a_fresh_pass_on_an_older_master_stands_when_master_gained_only_inert_paths(tmp_path):
    ports, clock = _world(merge_ci=[_pair()]), _Clock()
    ports.stack.declared_merge_inert = ("notes/**",)
    ports.workspace.containment[(MASTER, OLD_MASTER)] = "yes"
    ports.workspace.gains[(OLD_MASTER, MASTER)] = ("notes/a.md",)

    result = _call(ports, tmp_path, clock)

    assert result.kind == "merged"
    assert ports.workspace.ci_ref_calls == [] and ports.workspace.detached == []
    assert ports.workspace.gain_reads == [(OLD_MASTER, MASTER)]


def test_a_master_that_moved_past_a_decided_run_starts_one_new_pair_and_marks_the_old_stale(
    tmp_path,
):
    ports, clock = _world(merge_ci=[_pair()]), _Clock()
    ports.stack.declared_merge_inert = ("notes/**",)
    ports.workspace.containment[(MASTER, OLD_MASTER)] = "yes"
    ports.workspace.gains[(OLD_MASTER, MASTER)] = ("src/a.py",)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert ports.workspace.ci_ref_calls == [("publish", REF)]
    old, new = _pairs(ports)
    assert old["master"] == OLD_MASTER and old["outcome"] == "stale"
    assert new["master"] == MASTER and "outcome" not in new
    again = _call(ports, tmp_path, clock)
    assert again.reason == "ci_wait" and len(ports.workspace.ci_ref_calls) == 1


def test_a_run_that_never_starts_is_republished_once_then_refused_with_its_comment(tmp_path):
    ports, clock = _world(), _Clock()
    grace = _cfg(tmp_path).ci_start_grace_s
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _branch(
        OLD_MASTER
    )  # a run of another commit: none for ours

    clock.advance(grace - 1)
    assert _call(ports, tmp_path, clock).reason == "ci_wait"
    assert len(ports.workspace.ci_ref_calls) == 1

    clock.advance(2)
    assert _call(ports, tmp_path, clock).reason == "ci_wait"
    assert ports.workspace.ci_ref_calls == [("publish", REF), ("delete_ci", REF), ("publish", REF)]
    [pair] = _pairs(ports)
    assert (pair["tested"], pair["superseded"], pair["republished"]) == (
        TESTED[1],
        [TESTED[0]],
        True,
    )
    assert pair["published_at"] == clock()

    ports.tracker.branch_reports[REF] = _branch(TESTED[0])  # the superseded commit's run
    clock.advance(grace + 1)
    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_no_verdict")
    assert _pairs(ports)[0]["outcome"] == "no_verdict"
    [comment] = _refusals(ports)
    assert "ci_no_verdict" in comment and "no_run" in comment
    again = _call(ports, tmp_path, clock)
    assert (again.kind, again.reason) == ("refused", "ci_no_verdict")
    assert len(ports.workspace.ci_ref_calls) == 3 and len(_refusals(ports)) == 1


def test_a_run_still_undecided_at_the_check_timeout_is_no_verdict(tmp_path):
    ports, clock = _world(), _Clock()
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion=None)
    clock.advance(_cfg(tmp_path).check_timeout_s + 1)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_no_verdict")
    assert len(ports.workspace.ci_ref_calls) == 1


def test_a_refused_dispatch_is_no_verdict_without_a_second_dispatch(tmp_path):
    ports, clock = _world(), _Clock()
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)
    ports.stack.declared_ci_dispatch = CiDispatch("ci.yml", "sha")
    ports.tracker.dispatch_result = CiUnreadable("dispatch_refused")

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_no_verdict")
    assert len(ports.tracker.dispatch_calls) == 1
    [comment] = _refusals(ports)
    assert "dispatch_refused" in comment
    assert _call(ports, tmp_path, clock).reason == "ci_no_verdict"
    assert len(ports.tracker.dispatch_calls) == 1


def test_a_head_that_conflicts_with_master_publishes_nothing(tmp_path):
    ports, clock = _world(), _Clock()
    ports.workspace.conflicts.add(MASTER)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "merge_conflict")
    assert ports.workspace.ci_ref_calls == [] and _pairs(ports) == []
    assert set(ports.workspace.removed_checkouts) == {"merge-GH-5"}


def test_a_spent_stale_bound_refuses_with_the_count_in_its_comment(tmp_path):
    pairs = [
        _pair(master=f"{n}" * 40, outcome="stale") for n in range(4, 4 + ci.CI_STALE_PAIRS_MAX)
    ]
    ports, clock = _world(merge_ci=pairs), _Clock()

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_stale_exhausted")
    assert ports.workspace.ci_ref_calls == []
    [comment] = _refusals(ports)
    assert f"{ci.CI_STALE_PAIRS_MAX} of {ci.CI_STALE_PAIRS_MAX}" in comment


def test_with_ci_run_reads_the_fresh_report_as_the_verdict_of_the_tested_commit():
    pair: MergeCiRecord = {
        "head": HEAD,
        "master": MASTER,
        "tested": TESTED[0],
        "mode": "push",
        "published_at": NOW.isoformat(),
        "republished": False,
        "superseded": [],
    }
    facts = rules.MergeFacts(
        task_id="GH-5",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
        ci_required=("web",),
        merge_ci=(pair,),
    )

    got = merge.with_ci_run(facts, _branch(TESTED[0]), ("web",))

    assert got.ci_subject == "tested" and got.ci_head == TESTED[0]
    assert got.ci_verdict is not None and got.ci_verdict.state == "green"
    assert got.ci_fresh is True
