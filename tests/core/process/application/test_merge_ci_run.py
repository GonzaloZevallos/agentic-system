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
from agentic.core.process.application.merge import MergeResult
from agentic.core.process.application.ports import GitError, Upstream
from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiReport, CiUnreadable, DispatchedRun
from agentic.core.process.domain.ci_push import PUSH_ATTEMPTS, PUSH_BACKOFF_S, scrub
from agentic.core.process.domain.presubmit import CiDispatch
from agentic.core.process.domain.task import MergeCiRecord
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, _ports
from tests.fakes import MERGE_BASE, MemoryStack, MemoryWorkspace

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
        # What git says to each push, in order; once spent, `always` (or nothing) is said.
        self.push_says: list[str] = []
        self.always: str | None = None

    def full_head(self, path: Path) -> str:
        return self.built.pop(0)

    def _push_fails(self) -> None:
        said = self.push_says.pop(0) if self.push_says else self.always
        if said is not None:
            raise GitError("git push failed", stderr=said)

    def publish_ci_ref(self, path: Path, ref: str) -> None:
        super().publish_ci_ref(path, ref)
        self._push_fails()

    def publish_dispatch_ref(self, path: Path, ref: str) -> None:
        super().publish_dispatch_ref(path, ref)
        self._push_fails()


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


def _call(ports, tmp_path, clock, lines=None, sleeps=None):
    """`sleeps` records the waits of a push's retry: a wait costs no real time here."""
    return merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        (lines if lines is not None else []).append,
        clock,
        (sleeps if sleeps is not None else []).append,
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
    ports.stack.declared_ci_dispatch_at[MASTER] = CiDispatch("ci.yml", "sha")

    first = _call(ports, tmp_path, clock)
    assert ports.workspace.diff_reads == [(MERGE_BASE, HEAD)]  # GH-547: the merge's own range

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
    assert ports.workspace.diff_reads == [(MERGE_BASE, HEAD)]
    assert ports.workspace.merge_base_asks == [(HEAD, MASTER)]
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


def test_a_head_the_merge_is_refused_for_without_a_conflict_stops_and_records_nothing(
    tmp_path,
):
    """GH-492: a `MergeFailed` is no conflict: it stops `ci_publish_failed`, never recorded."""
    ports, clock = _world(), _Clock()
    ports.workspace.failed_merges.add(MASTER)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("stopped", "ci_publish_failed")
    assert "merge_conflict" not in (ports.documents.load("GH-5") or {})
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


# GH-487: every push of the fresh run is retried by what git said, one step at a time.
TRANSIENT = (
    "fatal: unable to access 'https://x-access-token:ghp_secret1234@github.com/o/r/': "
    "Could not resolve host: github.com"
)
PERMANENT = (
    "remote: Permission to o/r.git denied to workflow. https://u:ghp_secret1234@github.com/o/r"
)
STOPPED = MergeResult("stopped", "ci_publish_failed")


def _dispatching(ports) -> None:
    ports.workspace.diff_paths = (".github/workflows/ci.yml",)
    ports.stack.declared_ci_dispatch = CiDispatch("ci.yml", "sha")


def _publishes(ports) -> list[str]:
    return [call[0] for call in ports.workspace.ci_ref_calls]


def _released_and_removed(monkeypatch, ports, tmp_path) -> tuple[int, int]:
    """How many times one `merge_task` call released and removed a checkout."""
    released: list[str] = []
    real = merge.release_checkout
    monkeypatch.setattr(
        merge, "release_checkout", lambda p, path: (released.append(str(path)), real(p, path))
    )
    before = len(ports.workspace.removed_checkouts)
    _call(ports, tmp_path, _Clock())
    return len(released), len(ports.workspace.removed_checkouts) - before


@pytest.mark.parametrize("dispatch", [False, True])
def test_a_permanent_refusal_of_the_fresh_push_stops_after_one_attempt(tmp_path, dispatch):
    ports, lines, sleeps = _world(), [], []
    if dispatch:
        _dispatching(ports)
    ports.workspace.always = PERMANENT

    result = _call(ports, tmp_path, _Clock(), lines, sleeps)

    assert result == STOPPED
    assert _publishes(ports) == ["publish_dispatch" if dispatch else "publish"]
    assert sleeps == [] and ports.tracker.dispatch_calls == []
    [line] = [line for line in lines if "could not publish" in line]
    assert "after 1 attempt(s) (permanent)" in line and scrub(PERMANENT) in line
    assert "ghp_secret1234" not in line


@pytest.mark.parametrize("dispatch", [False, True])
def test_a_transient_failure_that_never_heals_stops_after_every_attempt(tmp_path, dispatch):
    ports, lines, sleeps = _world(), [], []
    if dispatch:
        _dispatching(ports)
    ports.workspace.always = TRANSIENT

    result = _call(ports, tmp_path, _Clock(), lines, sleeps)

    assert result == STOPPED
    assert len(_publishes(ports)) == PUSH_ATTEMPTS
    assert sleeps == list(PUSH_BACKOFF_S)
    [line] = [line for line in lines if "could not publish" in line]
    assert f"after {PUSH_ATTEMPTS} attempt(s) (transient)" in line and scrub(TRANSIENT) in line
    assert "ghp_secret1234" not in line


@pytest.mark.parametrize("dispatch", [False, True])
def test_a_transient_failure_that_heals_goes_on_to_wait_for_the_run(
    tmp_path, monkeypatch, dispatch
):
    baseline = _world()
    if dispatch:
        _dispatching(baseline)
    expected = _released_and_removed(monkeypatch, baseline, tmp_path)
    ports, lines, sleeps = _world(), [], []
    if dispatch:
        _dispatching(ports)
    ports.workspace.push_says = [TRANSIENT]

    result = _call(ports, tmp_path, _Clock(), lines, sleeps)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert len(_publishes(ports)) == 2 and sleeps == [PUSH_BACKOFF_S[0]]
    assert not [line for line in lines if "could not publish" in line]
    assert len(_pairs(ports)) == 1
    if dispatch:
        assert ports.tracker.dispatch_calls == [("ci.yml", "", {"sha": TESTED[0]})]
    # the checkout is given back once per call, however many tries the push took
    assert _released_and_removed(monkeypatch, _healing(dispatch), tmp_path) == expected


def _healing(dispatch: bool):
    ports = _world()
    if dispatch:
        _dispatching(ports)
    ports.workspace.push_says = [TRANSIENT]
    return ports


def test_a_republish_retries_its_create_without_repeating_its_delete(tmp_path):
    ports, clock, sleeps = _world(), _Clock(), []
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _branch(OLD_MASTER)  # a run of another commit
    clock.advance(_cfg(tmp_path).ci_start_grace_s + 1)
    ports.workspace.push_says = [TRANSIENT]

    result = _call(ports, tmp_path, clock, sleeps=sleeps)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert _publishes(ports) == ["publish", "delete_ci", "publish", "publish"]
    assert sleeps == [PUSH_BACKOFF_S[0]]


def test_a_decided_pair_carries_the_instants_of_its_wait_and_a_green_one_still_merges(tmp_path):
    # GH-467: the pair is timed from its own `published_at`; recording it decides nothing.
    ports, clock = _world(), _Clock()
    _call(ports, tmp_path, clock)
    timed = CiCheck(
        "web",
        "COMPLETED",
        "SUCCESS",
        RUN.format(42),
        started_at=(NOW + timedelta(seconds=30)).isoformat(),
        completed_at=(NOW + timedelta(seconds=150)).isoformat(),
    )
    ports.tracker.branch_reports[REF] = CiReport(TESTED[0], None, None, None, (timed,))
    clock.advance(200)

    assert _call(ports, tmp_path, clock).kind == "merged"

    [pair] = _pairs(ports)
    assert pair["ci_timing"]["pushed_at"] == pair["published_at"]
    assert pair["ci_timing"]["node_started_at"] == pair["published_at"]
    assert pair["ci_timing"]["verdict"] == "green"
    assert ci.waited_seconds(pair["ci_timing"]) == (150.0, False)


def test_a_red_pair_and_a_no_verdict_pair_say_which_they_were(tmp_path):
    red, clock = _world(), _Clock()
    _call(red, tmp_path, clock)
    red.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion="FAILURE")
    _call(red, tmp_path, clock)
    [pair] = _pairs(red)
    assert pair["ci_timing"]["verdict"] == "red" and pair["ci_timing"]["run_ended_at"] is None

    stuck, clock = _world(), _Clock()
    _call(stuck, tmp_path, clock)
    stuck.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion=None)
    clock.advance(_cfg(tmp_path).check_timeout_s + 1)
    _call(stuck, tmp_path, clock)
    [pair] = _pairs(stuck)
    assert pair["outcome"] == "no_verdict"
    assert pair["ci_timing"]["verdict"] == "no_verdict"
    assert pair["ci_timing"]["run_started_at"] is None


# GH-539: a fresh run cancelled by a newer push is never a red pair.
def _cancelled(tested: str, run: int = 42) -> CiReport:
    check = _check(conclusion="CANCELLED", run=run)
    return CiReport(tested, None, None, None, (check,), True, frozenset({str(run)}))


def test_a_cancelled_fresh_run_is_rerun_once_and_the_gate_keeps_waiting(tmp_path):
    ports, clock = _world(), _Clock()
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _cancelled(TESTED[0])
    clock.advance(60)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("stopped", "ci_wait")
    assert ports.tracker.run_reruns == ["42"]
    [pair] = _pairs(ports)
    assert pair["rerun_at"] == clock() and "outcome" not in pair and "failing" not in pair

    clock.advance(10)  # the stale read of the cancelled run: the rerun has not restarted it yet
    assert _call(ports, tmp_path, clock).reason == "ci_wait"
    assert ports.tracker.run_reruns == ["42"]


def test_a_cancelled_fresh_run_after_the_rerun_and_its_grace_is_no_verdict_never_red(tmp_path):
    ports, clock = _world(), _Clock()
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _cancelled(TESTED[0])
    clock.advance(60)
    _call(ports, tmp_path, clock)
    clock.advance(_cfg(tmp_path).ci_start_grace_s + 1)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_no_verdict")
    assert ports.tracker.run_reruns == ["42"]
    assert all(pair.get("outcome") != "red" for pair in _pairs(ports))
    [pair] = _pairs(ports)
    assert (pair["outcome"], pair["cause"]) == ("no_verdict", "not_started")


def test_a_rerun_the_platform_refuses_is_no_verdict_never_red(tmp_path):
    ports, clock = _world(), _Clock()
    _call(ports, tmp_path, clock)
    ports.tracker.branch_reports[REF] = _cancelled(TESTED[0])
    ports.tracker.rerun_run_error = CiUnreadable("dispatch_refused")
    clock.advance(60)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_no_verdict")
    [pair] = _pairs(ports)
    assert pair["outcome"] == "no_verdict" and "rerun_at" not in pair


# GH-552: a no_verdict pair of the current master is reread once before ci_no_verdict.
def _no_verdict_pair(tested: str = TESTED[0], **over) -> dict:
    return {
        **_pair(master=MASTER, tested=tested, republished=True, outcome="no_verdict"),
        "cause": "no_run",
        **over,
    }


def _stuck(tmp_path, **over):
    """The document of GH-396: pairs spent up to the bound, the last on the current master, and
    a clock far past the pair's grace."""
    older = {
        **_pair(master=OLD_MASTER, tested=TESTED[1], republished=True, outcome="no_verdict"),
        "cause": "no_run",
    }
    ports, clock = _world(merge_ci=[older, _no_verdict_pair(**over)]), _Clock()
    clock.advance(_cfg(tmp_path).ci_start_grace_s + 600)
    return ports, clock


def _quiet(ports) -> None:
    """Nothing was published, rerun, pushed or dispatched."""
    assert ports.workspace.ci_ref_calls == [] and ports.workspace.detached == []
    assert ports.tracker.dispatch_calls == [] and ports.tracker.run_reruns == []


def test_a_green_run_that_finished_after_the_grace_merges_from_a_spent_no_verdict_pair(tmp_path):
    ports, clock = _stuck(tmp_path)
    ports.tracker.branch_reports[REF] = _branch(TESTED[0])

    result = _call(ports, tmp_path, clock)

    assert result.kind == "merged"
    _quiet(ports)
    pair = _pairs(ports)[-1]
    assert (pair["outcome"], pair["run_id"]) == ("green", "42")
    assert pair["reread_at"] == clock() and "released_at" not in pair and "cause" not in pair
    assert pair["republished"] is True


def test_a_red_run_found_by_the_reread_is_refused_as_red_and_never_merged(tmp_path):
    ports, clock = _stuck(tmp_path)
    ports.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion="FAILURE", run=9)

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_failed")
    _quiet(ports)
    pair = _pairs(ports)[-1]
    assert (pair["outcome"], pair["run_id"], pair["failing"]) == ("red", "9", ["web"])
    assert "reread_at" in pair


@pytest.mark.parametrize("reading", ["absent", "waiting", "unreadable"])
def test_a_reread_with_no_verdict_stamps_the_pair_and_triggers_nothing(tmp_path, reading):
    ports, clock = _stuck(tmp_path)
    if reading == "absent":
        ports.tracker.branch_reports[REF] = _branch(OLD_MASTER)
    elif reading == "waiting":
        ports.tracker.branch_reports[REF] = _branch(TESTED[0], conclusion=None)
    before = [dict(p) for p in _pairs(ports)]

    result = _call(ports, tmp_path, clock)

    assert (result.kind, result.reason) == ("refused", "ci_no_verdict")
    _quiet(ports)
    after = _pairs(ports)
    assert after[0] == before[0]
    assert after[-1] == {**before[-1], "reread_at": clock()}
    again = _call(ports, tmp_path, clock)
    assert (again.kind, again.reason) == ("refused", "ci_no_verdict")
    assert _pairs(ports) == after and len(ports.tracker.branch_reads) == 1
    _quiet(ports)


def test_the_stamp_is_written_before_github_is_asked(tmp_path, monkeypatch):
    ports, clock = _stuck(tmp_path)
    seen: list[bool] = []

    def failing(branch):
        seen.append("reread_at" in _pairs(ports)[-1])
        raise OSError("gh blew up")

    with monkeypatch.context() as patch:
        patch.setattr(ports.tracker, "branch_ci", failing)
        with pytest.raises(OSError):
            _call(ports, tmp_path, clock)

    assert seen == [True] and "reread_at" in _pairs(ports)[-1]
    assert _call(ports, tmp_path, clock).reason == "ci_no_verdict"
    assert ports.tracker.branch_reads == []


def test_a_no_verdict_pair_of_an_older_master_is_never_reread(tmp_path):
    older = {
        **_pair(master=OLD_MASTER, tested=TESTED[1], republished=True, outcome="no_verdict"),
        "cause": "no_run",
    }
    ports, clock = _world(merge_ci=[older]), _Clock()
    ports.tracker.branch_reports[REF] = _branch(TESTED[1])

    result = _call(ports, tmp_path, clock)

    assert result.kind != "merged"
    assert ports.tracker.branch_reads == [] and "reread_at" not in _pairs(ports)[0]


def test_a_released_pair_is_never_reread(tmp_path):
    ports, clock = _stuck(tmp_path, released_at="2026-11-03T10:30:00+00:00")
    ports.tracker.branch_reports[REF] = _branch(TESTED[0])

    result = _call(ports, tmp_path, clock)

    assert result.kind != "merged" and ports.tracker.branch_reads == []
    assert "reread_at" not in _pairs(ports)[-1]


def test_a_pair_that_never_gets_a_run_is_refused_when_it_ends_and_read_once_more_after(tmp_path):
    ports, clock = _world(), _Clock()
    grace = _cfg(tmp_path).ci_start_grace_s
    ports.tracker.branch_reports[REF] = _branch(OLD_MASTER)
    outcomes = []
    for step in (0, 0, grace + 1, grace + 1, grace + 1, 0):
        clock.advance(step)
        result = _call(ports, tmp_path, clock)
        outcomes.append((result.kind, result.reason))
    # published, waiting, republished, ended no_verdict (refused as it always was), then the
    # one reread, which finds nothing and refuses again, and no more reads after it.
    assert outcomes[-1] == ("refused", "ci_no_verdict")
    assert [pair.get("outcome") for pair in _pairs(ports)] == ["no_verdict"]
    assert "reread_at" in _pairs(ports)[0]
    reads = len(ports.tracker.branch_reads)
    _call(ports, tmp_path, clock)
    assert len(ports.tracker.branch_reads) == reads
    assert (
        len(ports.workspace.ci_ref_calls) == 3
    )  # publish, delete, publish: no push for the reread


def test_an_escalated_ci_no_verdict_task_merges_on_the_reread_of_a_green_run(tmp_path):
    entry = {"step": "escalate", "refusal": "ci_no_verdict", "head": HEAD, "cause": "no_run"}
    ports, clock = _world(merge_ci=[_no_verdict_pair()], landing=[entry], escalated=True), _Clock()
    clock.advance(_cfg(tmp_path).ci_start_grace_s + 600)
    ports.tracker.branch_reports[REF] = _branch(TESTED[0])

    result = _call(ports, tmp_path, clock)

    assert result.kind == "merged"
    assert _pairs(ports)[-1]["outcome"] == "green"
