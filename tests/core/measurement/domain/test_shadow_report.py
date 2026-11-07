"""`shadow_report.build` and `render` (GH-473): what the single review missed that its shadow
found, on small synthetic documents. The match is decided only here, from the file and the
category family, never from prose."""

from typing import Any

import pytest

from agentic.core.measurement.domain import scorecard
from agentic.core.measurement.domain import shadow_report as sr
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import Severity


def _row(
    location: str = "a/b.py:1",
    category: str = "spec-behaviour",
    severity: str = "important",
    status: str = "open",
    subtask: str = "integration",
    claim: str = "c",
) -> dict[str, Any]:
    return {
        "subtask": subtask,
        "version": 1,
        "location": location,
        "claim": claim,
        "severity": severity,
        "scope": "subtask",
        "category": category,
        "status": status,
    }


def _shadow(
    findings: list[dict[str, Any]] | None = None,
    status: str = "ran",
    cause: str | None = None,
    cost: float = 0.5,
    lines: int | None = 10,
    subtask: str = "a",
) -> dict[str, Any]:
    return {
        "subtask": subtask,
        "head": "h",
        "status": status,
        "cause": cause,
        "findings": findings or [],
        "cost_usd": cost,
        "changed_lines": lines,
    }


def _doc(
    task_id: str = "T-1",
    single: list[dict[str, Any]] | None = None,
    shadows: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> Any:
    return {
        "id": task_id,
        "title": "t",
        "description": "d",
        "subtasks": {"integration": {}},
        "findings": single or [],
        "shadow_reviews": shadows if shadows is not None else [_shadow()],
        "cost_usd": 4.0,
        "runs": [
            {
                "role": "Developer",
                "version": 1,
                "cost_usd": 4.0,
                "started_at": "2026-10-20T08:00:00+00:00",
            },
        ],
        "closed_at": "2026-10-20T09:00:00+00:00",
        "status": "ready_for_review",
        **extra,
    }


def _ran(*findings: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return _shadow(list(findings), **kw)


def _count(report: sr.ShadowReport, severity: Severity, bucket: sr.Bucket) -> int:
    return report.total.buckets.count(severity, bucket)


def test_a_shadow_finding_with_no_single_row_on_its_key_is_missed():
    doc = _doc(single=[_row("a/b.py:1")], shadows=[_ran(_row("a/c.py:1", severity="blocking"))])
    report = sr.build([doc])
    assert _count(report, "blocking", "missed") == 1
    assert _count(report, "blocking", "caught") == 0


def test_the_same_file_in_another_family_is_missed():
    doc = _doc(
        single=[_row("a/b.py:1", "spec-behaviour")],
        shadows=[_ran(_row("a/b.py:1", "standard-seams"))],
    )
    assert _count(sr.build([doc]), "important", "missed") == 1


def test_the_same_key_at_the_same_severity_is_caught_whatever_the_line_and_claim():
    single = _row("a/b.py:99", "spec-scope", claim="something else")
    shadow = _row("a/b.py:3", "spec-behaviour", claim="no relation")
    report = sr.build([_doc(single=[single], shadows=[_ran(shadow)])])
    assert _count(report, "important", "caught") == 1


def test_the_same_key_at_a_higher_single_severity_is_caught():
    single = _row("a/b.py:1", severity="blocking")
    report = sr.build(
        [_doc(single=[single], shadows=[_ran(_row("a/b.py:2", severity="observation"))])]
    )
    assert _count(report, "observation", "caught") == 1


def test_the_same_key_only_at_a_lower_single_severity_is_caught_lower():
    single = _row("a/b.py:1", severity="observation")
    report = sr.build(
        [_doc(single=[single], shadows=[_ran(_row("a/b.py:2", severity="blocking"))])]
    )
    assert _count(report, "blocking", "caught_lower") == 1
    assert _count(report, "blocking", "missed") == 0


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_the_single_rows_of_a_key_reduce_to_their_max_severity_in_either_order(order):
    rows = [
        _row("a/b.py:1", severity="blocking", claim="x"),
        _row("a/b.py:2", severity="observation", claim="y"),
    ]
    single = [rows[i] for i in order]
    report = sr.build([_doc(single=single, shadows=[_ran(_row("a/b.py:3", severity="blocking"))])])
    assert _count(report, "blocking", "caught") == 1
    assert _count(report, "blocking", "caught_lower") == 0


@pytest.mark.parametrize("status", ["open", "dismissed"])
def test_a_single_row_in_any_stored_status_catches(status):
    single = _row("a/b.py:1", status=status)
    report = sr.build([_doc(single=[single], shadows=[_ran(_row("a/b.py:2"))])])
    assert _count(report, "important", "caught") == 1


def test_a_single_row_the_fold_derives_as_fixed_still_catches():
    single = _row("a/b.py:1")
    doc = _doc(single=[single], shadows=[_ran(_row("a/b.py:2"))], settled_findings=["a/b.py:1"])
    assert [r["status"] for r in rules.findings_rows(doc)] == ["fixed"]
    assert _count(sr.build([doc]), "important", "caught") == 1


def test_only_the_reviewed_units_rows_are_the_single_review():
    other = _row("a/b.py:1", subtask="other")
    doc = _doc(
        single=[other], shadows=[_ran(_row("a/b.py:2"))], subtasks={"integration": {}, "other": {}}
    )
    assert _count(sr.build([doc]), "important", "missed") == 1


def test_a_row_raised_by_the_unit_and_a_sister_is_the_single_reviews():
    both = _row("a/b.py:1", subtask="integration, other")
    doc = _doc(
        single=[both], shadows=[_ran(_row("a/b.py:2"))], subtasks={"integration": {}, "other": {}}
    )
    assert _count(sr.build([doc]), "important", "caught") == 1


@pytest.mark.parametrize(
    "location", ["the plan as a whole", "full_checks:ruff", "copy:1.1:x", "../x.py:1"]
)
def test_a_shadow_finding_with_no_file_is_unlocated_never_missed(location):
    report = sr.build([_doc(shadows=[_ran(_row(location))])])
    assert _count(report, "important", "unlocated") == 1
    assert _count(report, "important", "missed") == 0


def test_a_duplicate_shadow_row_counts_twice():
    report = sr.build([_doc(shadows=[_ran(_row("a/c.py:1"), _row("a/c.py:1"))])])
    assert _count(report, "important", "missed") == 2


def test_a_late_record_that_carries_a_finding_lands_in_no_bucket_and_no_row_count():
    late = _shadow(
        [_row("a/c.py:1", severity="blocking")], status="late", cause="late", subtask="b"
    )
    report = sr.build([_doc(shadows=[_ran(), late])])
    assert all(_count(report, s, b) == 0 for s in sr.SEVERITIES for b in sr.BUCKETS)
    assert report.total.ran_rows == 0
    assert report.late == 1


def test_counts_are_per_task_and_in_total():
    one = _doc("T-1", shadows=[_ran(_row("a/c.py:1", severity="blocking"))])
    two = _doc("T-2", single=[_row("a/b.py:1")], shadows=[_ran(_row("a/b.py:9"), _row("a/d.py:1"))])
    report = sr.build([one, two])
    by_id = {t.id: t for t in report.tasks}
    assert by_id["T-1"].buckets.count("blocking", "missed") == 1
    assert by_id["T-2"].buckets.count("important", "caught") == 1
    assert by_id["T-2"].buckets.count("important", "missed") == 1
    assert _count(report, "blocking", "missed") == 1
    assert _count(report, "important", "missed") == 1
    assert _count(report, "important", "caught") == 1
    assert report.ids == ("T-1", "T-2")


def test_the_missed_rate_is_over_the_located_rows_and_unlocated_is_its_own_share():
    shadow = [_row("a/c.py:1"), _row("a/b.py:1"), _row("a/b.py:2"), _row("prose")]
    report = sr.build([_doc(single=[_row("a/b.py:5")], shadows=[_ran(*shadow)])])
    assert report.total.buckets.rate("important") == pytest.approx(1 / 3)
    assert report.total.buckets.unlocated_share() == pytest.approx(1 / 4)


def test_a_zero_denominator_is_not_available_not_zero():
    report = sr.build([_doc(shadows=[_ran(_row("prose"))])])
    assert report.total.buckets.rate("blocking") is None
    assert "n/a" in sr.render(report)


def test_only_documents_with_shadow_reviews_are_read():
    plain = _doc("T-0")
    del plain["shadow_reviews"]
    report = sr.build([plain, _doc("T-1")])
    assert report.ids == ("T-1",)


def test_status_counts_and_the_dropped_subtasks_with_their_changed_lines():
    shadows = [
        _ran(subtask="a"),
        _shadow(status="late", cause="late", subtask="b", lines=30),
        _shadow(status="unavailable", cause="setup", subtask="c", lines=None),
        _shadow(status="skipped", cause="single_unit", subtask="d", lines=5),
    ]
    report = sr.build([_doc(shadows=shadows)])
    assert (report.ran, report.late, report.unavailable, report.skipped) == (1, 1, 1, 1)
    assert report.dropped == 3
    assert report.dropped_lines == 35
    assert report.dropped_unknown_size == 1


def test_a_task_with_no_ran_record_is_out_of_the_denominators_and_names_its_causes():
    skipped = _doc("T-1", shadows=[_shadow(status="skipped", cause="single_unit")], cost_usd=9.0)
    ran = _doc("T-2", shadows=[_ran()], cost_usd=4.0)
    report = sr.build([skipped, ran])
    assert [t.id for t in report.tasks if t.counted] == ["T-2"]
    assert report.counted_tasks == 1
    assert report.total.cost_gross == pytest.approx(4.0)
    text = sr.render(report)
    assert "T-1: no shadow ran" in text
    assert "single_unit" in text


def test_a_task_with_no_reviewed_unit_is_out_of_the_denominators_with_its_cause():
    doc = _doc("T-1", shadows=[_ran(_row("a/c.py:1"))], subtasks={"a": {}, "b": {}})
    report = sr.build([doc])
    assert report.counted_tasks == 0
    assert _count(report, "important", "missed") == 0
    assert "no_reviewed_unit" in sr.render(report)


def test_the_net_cost_is_cost_without_shadow_and_the_spend_is_the_shadow_cost():
    doc = _doc(
        shadows=[_ran(cost=0.5), _shadow(status="late", cause="late", cost=0.25, subtask="b")]
    )
    report = sr.build([doc])
    task = report.tasks[0]
    assert task.shadow_cost == pytest.approx(rules.shadow_cost(doc))
    assert task.cost_gross == 4.0
    assert task.cost_net == pytest.approx(rules.cost_without_shadow(4.0, doc))
    assert report.total.shadow_cost == pytest.approx(0.75)
    assert report.total.cost_net == pytest.approx(3.25)


def test_a_missing_cost_reads_as_zero_gross():
    doc = _doc()
    del doc["cost_usd"]
    task = sr.build([doc]).tasks[0]
    assert (task.cost_gross, task.cost_net) == (0.0, 0.0)


def test_net_time_subtracts_the_shadow_wait_and_an_absent_wait_is_zero():
    with_wait = _doc("T-1", shadow_wait_s=600.0)
    without = _doc("T-2")
    report = sr.build([with_wait, without])
    by_id = {t.id: t for t in report.tasks}
    assert by_id["T-1"].time_gross == 3600.0
    assert by_id["T-1"].time_net == 3000.0
    assert by_id["T-2"].time_net == 3600.0
    assert report.total.time_gross == 7200.0
    assert report.total.time_net == 6600.0


def test_net_time_never_goes_below_zero():
    assert sr.build([_doc(shadow_wait_s=99999.0)]).tasks[0].time_net == 0.0


def test_a_task_with_no_gross_time_is_left_out_of_both_time_totals_and_counted():
    open_doc = _doc("T-1", status="open", closed_at=None, runs=[{"role": "Developer"}])
    report = sr.build([open_doc, _doc("T-2", shadow_wait_s=60.0)])
    assert report.tasks[0].time_gross is None
    assert report.tasks[0].time_net is None
    assert report.total.time_gross == 3600.0
    assert report.total.time_net == 3540.0
    assert report.time_left_out == 1


def test_the_gross_cost_and_time_are_those_the_scorecard_reads():
    doc = _doc(shadow_wait_s=100.0)
    figures = scorecard.task_figures(doc)
    task = sr.build([doc]).tasks[0]
    assert task.cost_gross == figures.cost_usd
    assert task.time_gross == figures.cycle_s


def test_render_prints_the_zero_findings_line_and_a_tail_for_what_was_dropped():
    shadows = [_ran(subtask="a"), _shadow(status="late", cause="late", subtask="b", lines=40)]
    text = sr.render(sr.build([_doc("T-1", shadows=shadows)]))
    assert "T-1: shadow ran, 0 findings" in text.splitlines()
    assert "late 1" in text
    assert "40 changed line(s)" in text


def test_render_names_the_documents_it_read():
    text = sr.render(sr.build([_doc("T-1"), _doc("T-2")]))
    assert "T-1, T-2" in text
