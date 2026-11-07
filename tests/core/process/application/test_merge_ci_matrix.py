"""GH-546: the merge gate of a `ci:` product whose plan requires a matrix job id (`pytest`) by
that id, a reduced GH-399 report. #541 made `ci.runs_of` the one matcher of a required name with
its runs; these tests pin that the merge verb (`merge_task`, the `merge_ci` harness) and the
fresh-run path (`merge.with_ci_run` + `rules.auto_merge_decision`) read it, so the merge gate
never refuses `ci_unknown_names` for a name whose expansions `pytest (<values>)` reported."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.application import landing_plan, merge
from agentic.core.process.domain import ci, rules
from agentic.core.process.domain.ci import CiCheck, CiReport
from agentic.core.process.domain.task import MergeCiRecord
from tests.core.process.application.test_merge_ci import _doc, _run, _world
from tests.core.process.application.test_poll_merge import HEAD, MASTER

RUN = "https://github.com/o/r/actions/runs/{}"
STACK = ("checks", "docs", "coverage")
PLAN = ["checks", "pytest", "docs", "coverage"]
SHARDS = (
    "pytest (always, tests/architecture)",
    "pytest (areas-1, tests/customers)",
    "pytest (areas-2, tests/catalog)",
    "pytest (areas-3, tests/accounts)",
)
OTHERS = ("changes", "checks", "docs", "coverage")
TESTED = "d" * 40


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    """The merge verb's refusal is what is pinned here, not the landing that follows it."""
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


def _ago(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def _checks(names, red=()) -> tuple[CiCheck, ...]:
    return tuple(
        CiCheck(name, "COMPLETED", "FAILURE" if name in red else "SUCCESS", RUN.format(i))
        for i, name in enumerate(names, start=1)
    )


def _report(names, *, red=(), completed: bool | None = True) -> CiReport:
    """The head's own report: complete unless `completed` says the matrix may still expand."""
    return CiReport(
        HEAD, "master", MASTER, _ago(300), _checks(names, red), runs_completed=completed
    )


def _matrix_world(report: CiReport, plan: list[str] = PLAN):
    """A `ci:` product whose plan requires `plan`, the wait already past the confirmation
    (`2 * ci.CI_POLL_S`) and inside the bound."""
    return _world(report, declared=STACK, ci_required=plan, pushed_head=HEAD, pushed_at=_ago(120))


def test_an_all_green_matrix_job_id_is_met_by_its_expansions_like_an_exact_name(tmp_path):
    matrix = _matrix_world(_report((*OTHERS, *SHARDS)))
    exact = _matrix_world(_report((*OTHERS, "pytest")))

    got, _ = _run(matrix, tmp_path)
    want, _ = _run(exact, tmp_path)

    assert got.kind == want.kind == "merged"
    assert got.reason == want.reason
    assert not any("ci_unknown_names" in text for _, text in matrix.tracker.comments)


def test_one_red_shard_makes_the_matrix_job_id_red_and_names_that_shard(tmp_path):
    red = "pytest (areas-2, tests/catalog)"
    ports = _matrix_world(_report((*OTHERS, *SHARDS), red=(red,)))

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_failed")
    [(_, comment)] = ports.tracker.comments
    assert f"- {red} FAILURE " in comment and "areas-1" not in comment
    assert ports.tracker.merge_calls == []


@pytest.mark.parametrize("completed", [None, False])
def test_a_partial_matrix_on_an_incomplete_report_waits_and_is_never_unknown(tmp_path, completed):
    report = _report((*OTHERS, *SHARDS[:2]), completed=completed)
    ports = _matrix_world(report)
    before = dict(_doc(ports))

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("stopped", "ci_wait")
    assert _doc(ports) == before and ports.tracker.comments == []
    assert ports.tracker.merge_calls == []


def test_a_wrong_matrix_job_id_is_still_refused_as_unknown_with_the_shared_text(tmp_path):
    report = _report((*OTHERS, *SHARDS))
    ports = _matrix_world(report, plan=["checks", "pytst", "docs", "coverage"])

    result, _ = _run(ports, tmp_path)

    assert result == merge.MergeResult("refused", "ci_unknown_names")
    [comment] = [
        text for _, text in ports.tracker.comments if text.startswith("Auto-merge refused")
    ]
    text = ci.unreported_text(("pytst",), ci.reported_names(report), HEAD, ("pytst",))
    assert f"Planning defect, not a CI failure: {text}" in comment
    assert ports.tracker.merge_calls == []


# --- the fresh run's report on the `tested` path ------------------------------------------------


def _fresh_facts() -> rules.MergeFacts:
    pair: MergeCiRecord = {
        "head": HEAD,
        "master": MASTER,
        "tested": TESTED,
        "mode": "push",
        "published_at": "2026-11-03T10:00:00.000Z",
        "republished": False,
        "superseded": [],
    }
    return rules.MergeFacts(
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
        ci_required=tuple(PLAN),
        ci_plan_only=("pytest",),
        ci_untrusted=False,
        ci_waited_s=120.0,
        merge_ci=(pair,),
    )


def _fresh_report(names, *, red=()) -> CiReport:
    return CiReport(TESTED, None, None, None, _checks(names, red), runs_completed=True)


def _decide_on_fresh(report: CiReport) -> rules.MergeDecision:
    facts = merge.with_ci_run(_fresh_facts(), report, tuple(PLAN))
    return rules.auto_merge_decision(facts)


def test_a_green_fresh_run_with_a_matrix_job_id_merges_like_an_exact_name():
    matrix = _decide_on_fresh(_fresh_report((*OTHERS, *SHARDS)))
    exact = _decide_on_fresh(_fresh_report((*OTHERS, "pytest")))

    assert matrix == exact == rules.MergeDecision("merge", None)


def test_a_red_shard_of_a_fresh_run_is_a_failed_ci_never_unknown_names():
    red = _fresh_report((*OTHERS, *SHARDS), red=("pytest (areas-3, tests/accounts)",))

    assert _decide_on_fresh(red) == rules.MergeDecision("refuse", "ci_failed")


def test_the_fresh_run_verdict_holds_the_matrix_job_id_as_one_green_name():
    facts = merge.with_ci_run(_fresh_facts(), _fresh_report((*OTHERS, *SHARDS)), tuple(PLAN))

    assert facts.ci_verdict == ci.CiVerdict("green", (), ())
