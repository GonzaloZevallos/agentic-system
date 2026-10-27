"""`findings_report.build`: each figure on small synthetic documents."""

from datetime import date
from typing import Any

from agentic.core.measurement.domain import findings_report as fr

WINDOW = (date(2026, 10, 13), date(2026, 10, 27))


def _row(
    subtask: str = "a",
    version: int = 1,
    scope: str = "subtask",
    claim: str = "c",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "subtask": subtask,
        "version": version,
        "location": f"x.py:{claim}",
        "claim": claim,
        "severity": "important",
        "scope": scope,
        "category": "spec-behaviour",
        "status": "open",
        **extra,
    }


def _review(subtask: str, version: int, started: str) -> dict[str, Any]:
    return {
        "role": "Tech Lead",
        "section": f"Tech Lead: review {subtask}",
        "version": version,
        "started_at": started,
    }


def _doc(task_id: str = "T-1", **extra: Any) -> Any:
    return {
        "id": task_id,
        "closed_at": "2026-10-20T12:00:00+00:00",
        "delivery_outcome": "merged",
        "subtasks": {"a": {}},
        "runs": [_review("a", 1, "2026-10-20T08:00:00+00:00")],
        **extra,
    }


def test_the_report_names_the_documents_it_read_and_counts_those_outside_the_window():
    old = _doc("T-old", closed_at="2026-09-01T00:00:00+00:00")
    unclosed = {k: v for k, v in _doc("T-open").items() if k != "closed_at"}
    report = fr.build([_doc("T-1"), old, unclosed], *WINDOW)
    assert report.ids == ("T-1",)
    assert report.outside_window == 2


def test_a_row_left_open_counts_towards_the_share_of_prs_opened_with_importants():
    clean = _doc("T-1")
    open_row = _doc("T-2", findings=[_row()])
    lowered = _doc("T-3", findings=[_row(reported_severity="blocking")])
    outside = _doc("T-4", findings=[_row(scope="outside")])
    report = fr.build([clean, open_row, lowered, outside], *WINDOW)
    assert report.figures.share_open_importants == 0.25
    by_task = {line.task: line for line in report.findings}
    assert by_task["T-2"].scope == "owned" and by_task["T-2"].outcome == "accepted_without_fix"
    assert by_task["T-3"].lowered is True
    assert by_task["T-4"].scope == "outside"


def test_a_plan_row_is_a_plan_row_and_an_integration_row_has_the_integration_origin():
    doc = _doc(
        "T-1",
        subtasks={"a": {}, "integration": {}},
        runs=[_review("integration", 1, "2026-10-20T09:00:00+00:00")],
        findings=[_row("integration", scope="plan")],
    )
    (line,) = fr.build([doc], *WINDOW).findings
    assert (line.origin, line.scope) == ("integration_review", "plan")


def test_a_pass_is_counted_by_when_it_ran_and_only_the_one_after_the_pr_leaves_rows_open_at_it():
    after = _doc(
        "T-1",
        findings=[_row()],
        settled_findings=["x.py:c"],
        landing=[{"head": "h", "step": "fix_important", "at": "2026-10-20T13:00:00+00:00"}],
    )
    before = _doc(
        "T-2",
        settled_findings=["x.py:c"],
        findings=[_row()],
        landing=[
            {
                "head": "h",
                "step": "fix_important",
                "at": "2026-10-20T10:00:00+00:00",
                "before_pr": True,
            }
        ],
    )
    report = fr.build([after, before], *WINDOW)
    outcomes = {line.task: line.outcome for line in report.findings}
    assert outcomes == {"T-1": "fixed_after_pr", "T-2": "fixed_before_pr"}
    assert report.figures.share_fix_important_after_pr == 0.5
    assert report.figures.share_open_importants == 0.5


def test_returns_are_counted_by_category_origin_and_subtask_and_a_landing_reopen_is_not_one():
    pair = "Tech Lead→Developer:a"
    history = [
        {"pair": pair, "category": "spec-scope", "reason": "r"},
        {"pair": pair, "category": "process-suite-red", "reason": "ci red"},
        {
            "pair": pair,
            "category": "process-suite-red",
            "reason": "full_checks failed on the integrated head abc: tests",
        },
        {"pair": pair, "category": "spec-scope", "by": "landing", "reason": "r"},
    ]
    doc = _doc("T-1", subtasks={"a": {}, "b": {}}, change_history=history)
    report = fr.build([doc], *WINDOW)
    assert report.returns_by_category == {"process-suite-red": 2, "spec-scope": 1}
    assert report.returns_by_origin == {"full_checks": 1, "review": 1, "suite_red": 1}
    assert report.figures.returns == 3
    assert report.figures.returns_per_subtask == 1.5


def test_the_medians_read_the_pr_clock_and_the_task_clock():
    first = _doc("T-1", time_to_merge_seconds="7200")
    second = _doc("T-2", time_to_merge_seconds="3600", closed_at="2026-10-20T10:00:00+00:00")
    closed = _doc("T-3", delivery_outcome="closed", time_to_merge_seconds="999999")
    figures = fr.build([first, second, closed], *WINDOW).figures
    assert figures.median_pr_open_to_merge_hours == 1.5
    # start 08:00 -> merge 12:00 (4 h) and 08:00 -> 10:00 (2 h)
    assert figures.median_start_to_merge_hours == 3.0


def test_a_window_with_no_pull_request_has_no_figures_instead_of_zeros():
    figures = fr.build([], *WINDOW).figures
    assert figures.share_open_importants is None
    assert figures.median_pr_open_to_merge_hours is None
    assert figures.returns_per_subtask is None


def test_the_markdown_lists_the_ids_and_the_tables():
    doc = _doc("T-9", findings=[_row()])
    text = fr.render(fr.build([doc], *WINDOW), "p", *WINDOW)
    assert "T-9" in text and "| owner_review | owned | spec-behaviour | no |" in text
