"""The cost history the Tech Lead's plan is calibrated against (ADR-0054), pure."""

import pytest

from agentic.core.process.domain import cost_history, rules, size_budget


def doc(n_subtasks, cost, **more):
    subtasks = {f"s{i}": {"id": f"s{i}"} for i in range(n_subtasks)}
    return {"id": f"T-{cost}", "subtasks": subtasks, "cost_usd": cost, "closed_at": "x", **more}


def test_a_bucket_has_its_count_median_and_p90():
    docs = [doc(3, c) for c in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)]
    bucket = cost_history.build(docs).buckets[3]
    assert (bucket.n, bucket.median, bucket.p90) == (10, 55.0, 90.0)


def test_the_integration_subtask_is_not_counted_and_six_or_more_share_a_bucket():
    with_integration = doc(2, 10.0)
    with_integration["subtasks"]["integration"] = {"id": "integration"}
    history = cost_history.build([with_integration, doc(6, 5.0), doc(9, 7.0)])
    assert history.buckets[2].n == 1
    assert history.buckets[6].n == 2


def test_the_pairs_are_the_last_twenty_tasks_with_an_estimate():
    docs = [
        doc(1, float(i), estimate={"cost_usd": 10.0 + i, "files": 5}, actual_files=4)
        for i in range(25)
    ]
    docs.append(doc(1, 99.0))  # no estimate: no pair
    pairs = cost_history.build(docs).pairs
    assert len(pairs) == 20
    assert pairs[-1].estimated_usd == 34.0 and pairs[-1].actual_files == 4
    assert pairs[0].actual_usd == 5.0


def test_a_pair_without_actual_files_keeps_none():
    pairs = cost_history.build([doc(1, 5.0, estimate={"cost_usd": 6.0, "files": 3})]).pairs
    assert pairs[0].actual_files is None


def test_reference_is_the_p90_only_for_a_bucket_of_at_least_five():
    five = cost_history.build([doc(2, c) for c in (10, 20, 30, 40, 100)])
    assert cost_history.reference_usd(five, 2) == 100.0
    four = cost_history.build([doc(2, c) for c in (10, 20, 30, 40)])
    assert cost_history.reference_usd(four, 2) is None
    assert cost_history.reference_usd(five, 3) is None
    # 6+ share a bucket.
    assert cost_history.reference_usd(cost_history.build([doc(7, c) for c in range(5)]), 12)


def test_median_floor_is_none_below_five_tasks():
    five = cost_history.build([doc(2, c) for c in (10, 20, 30, 40, 100)])
    assert cost_history.bucket_median(five, 2) == 30.0
    assert cost_history.bucket_median(cost_history.build([doc(2, 10.0)]), 2) is None


def test_the_text_is_one_fixed_line_when_the_history_is_empty():
    assert cost_history.build([]).text() == cost_history.EMPTY
    assert "\n" not in cost_history.EMPTY


def test_the_text_lists_the_buckets_and_the_pairs():
    docs = [
        doc(2, 10.0 * i, estimate={"cost_usd": 12.0, "files": 7}, actual_files=9) for i in (1, 2)
    ]
    text = cost_history.build(docs).text()
    assert "2 subtasks" in text and "n=2" in text
    assert "estimated 12.00 USD / 7 files, actual 10.00 USD / 9 files" in text


def test_documents_without_a_cost_are_ignored():
    assert cost_history.build([{"id": "T-1", "subtasks": {"a": {}}}]).buckets == {}


# --- the raised estimate (GH-529) ---


def _history(n_subtasks=3, costs=(10, 20, 30, 40, 50)):
    return cost_history.build([doc(n_subtasks, float(c)) for c in costs])


def test_no_floor_binds_without_spend_or_a_qualifying_bucket():
    assert cost_history.estimate_floor(_history(costs=(10, 20)), 0.0, 3) is None
    assert cost_history.estimate_floor(_history(), 0.0, None) is None


def test_the_floor_is_the_larger_of_spent_and_half_the_median_and_spent_wins_a_tie():
    history = _history()  # median 30: half is 15
    assert cost_history.estimate_floor(history, 4.0, 3) == cost_history.Floor(15.0, "median")
    assert cost_history.estimate_floor(history, 20.0, 3) == cost_history.Floor(20.0, "spent")
    assert cost_history.estimate_floor(history, 15.0, 3) == cost_history.Floor(15.0, "spent")


def test_a_tracker_only_plan_has_the_spent_floor_alone():
    history = _history()
    assert cost_history.estimate_floor(history, 4.0, None) == cost_history.Floor(4.0, "spent")
    assert cost_history.raised_estimate(2.0, 0.0, history, None) is None


def test_a_declared_figure_that_clears_both_floors_is_not_raised():
    assert cost_history.raised_estimate(15.0, 10.0, _history(), 3) is None
    assert cost_history.raised_estimate(1.0, 0.0, _history(costs=(10, 20)), 3) is None


def test_below_spent_the_declared_figure_is_added_to_spent():
    raised = cost_history.raised_estimate(6.0, 10.01, _history(costs=(2, 4, 6, 8, 10)), 3)
    assert raised == cost_history.Raise(6.0, pytest.approx(16.01), "spent")


def test_below_spent_the_bucket_median_lifts_a_smaller_sum():
    raised = cost_history.raised_estimate(3.0, 9.91, _history(), 3)  # median 30
    assert raised == cost_history.Raise(3.0, 30.0, "spent")


def test_the_third_incident_is_spent_plus_declared():
    raised = cost_history.raised_estimate(3.0, 3.61, cost_history.build([]), 3)
    assert raised == cost_history.Raise(3.0, pytest.approx(6.61), "spent")


def test_below_half_the_median_the_estimate_becomes_the_median():
    history = _history(costs=(17.1,) * 5)
    raised = cost_history.raised_estimate(7.5, 0.0, history, 3)
    assert raised == cost_history.Raise(7.5, 17.1, "median")


def test_both_floors_binding_name_spent():
    raised = cost_history.raised_estimate(1.0, 5.0, _history(), 3)
    assert raised is not None and raised.cause == "spent"


@pytest.mark.parametrize("n_subtasks", [None, 3])
@pytest.mark.parametrize("spent", [0.0, 3.61, 15.0, 40.0])
def test_a_raise_keeps_every_floor_and_is_monotonic_in_declared(spent, n_subtasks):
    history = _history()
    median = None if n_subtasks is None else 30.0
    declared_grid = [0.5 * step for step in range(1, 120)]
    previous = None
    for declared in declared_grid:
        raised = cost_history.raised_estimate(declared, spent, history, n_subtasks)
        final = declared if raised is None else raised.cost_usd
        assert final >= declared and final >= spent
        if median is not None:
            assert final >= 0.5 * median
        if raised is None:
            assert declared >= spent and (median is None or declared >= 0.5 * median)
        else:
            assert raised.declared_usd == declared and raised.cost_usd > declared
        # Non-decreasing on each side of the floor: the step at the bound is the only drop.
        bound = max(spent, 0.0 if median is None else 0.5 * median)
        if previous is not None and (declared - 0.5 < bound) == (declared < bound):
            assert final >= previous
        previous = final


@pytest.mark.parametrize(("declared", "spent"), [(6.0, 10.01), (3.0, 9.91), (3.0, 3.61)])
def test_a_raised_plan_does_not_reach_the_estimate_cap_at_the_raised_spend(declared, spent):
    raised = cost_history.raised_estimate(declared, spent, _history(), 3)
    assert raised is not None
    state = {"estimate": {"cost_usd": raised.cost_usd, "files": 3}}
    assert not rules.estimate_cap_reached(raised.cost_usd, state)


def test_declared_estimate_prefers_declared_usd():
    assert (
        cost_history.declared_estimate({"cost_usd": 16.0, "files": 3, "declared_usd": 6.0}) == 6.0
    )
    assert cost_history.declared_estimate({"cost_usd": 16.0, "files": 3}) == 16.0


def test_the_pairs_read_the_declared_figure_when_the_system_raised_it():
    raised = {"cost_usd": 16.0, "declared_usd": 6.0, "files": 3}
    pairs = cost_history.build([doc(1, 9.0, estimate=raised)]).pairs
    assert pairs[0].estimated_usd == 6.0


def sized(planned_files, planned_lines, actual_files, actual_lines, cost=10, **more):
    """A closed document with both size records, built through the records themselves."""
    planned = size_budget.PlanSize(planned_files, planned_lines, 0).to_frontmatter()
    actual = size_budget.ActualSize(actual_files, actual_lines, 0, 0).to_frontmatter()
    return doc(1, cost, **planned, **actual, **more)


def test_nine_sized_tasks_are_not_a_ratio():
    history = cost_history.build([sized(10, 100, 12, 150)] * (size_budget.PROVEN_PAIRS - 1))
    assert cost_history.size_ratio(history) is None


def test_ten_sized_tasks_give_the_p90_of_the_ratio_per_field():
    # Ratios 1.1 .. 2.0 on files, 3.0 .. 12.0 on lines: the p90 of ten is the ninth.
    documents = [sized(10, 100, 10 + i, 100 * (i + 2)) for i in range(1, 11)]
    files, lines = cost_history.size_ratio(cost_history.build(documents)) or (0, 0)
    assert files == pytest.approx(1.9)
    assert lines == pytest.approx(11.0)


def test_a_ratio_below_one_is_clamped_to_one():
    history = cost_history.build([sized(10, 100, 5, 50)] * size_budget.PROVEN_PAIRS)
    assert cost_history.size_ratio(history) == (1.0, 1.0)


def test_a_task_that_planned_zero_in_either_field_is_skipped_and_counts_no_pair():
    with_zero = [sized(0, 0, 3, 30)] * size_budget.PROVEN_PAIRS
    assert cost_history.size_ratio(cost_history.build(with_zero)) is None
    mixed = [sized(0, 100, 3, 200)] * 5 + [sized(10, 100, 20, 100)] * 5
    assert cost_history.size_ratio(cost_history.build(mixed)) is None


def test_documents_without_both_records_are_ignored():
    planned_only = doc(1, 10, **size_budget.PlanSize(10, 100, 0).to_frontmatter())
    old = [doc(1, 10)] * 20 + [planned_only] * 20
    assert cost_history.size_ratio(cost_history.build(old)) is None
    ten = [sized(10, 100, 20, 200)] * size_budget.PROVEN_PAIRS
    assert cost_history.size_ratio(cost_history.build(old + ten)) == (2.0, 2.0)


def test_a_document_of_another_plan_shape_does_not_move_the_ratio():
    other = sized(10, 100, 40, 400, experiment={"plan_shape": "single"})
    history = cost_history.build([other] * 20 + [sized(10, 100, 15, 150)] * 10)
    assert cost_history.size_ratio(history) == (1.5, 1.5)


def _sized(planned, actual, **more):
    document = doc(1, 5.0, **more)
    document.update(size_budget.PlanSize(*planned).to_frontmatter())
    document.update(size_budget.ActualSize(*actual).to_frontmatter())
    return document


def test_the_history_table_shows_the_planned_size_against_the_actual_with_the_divergence():
    text = cost_history.render(cost_history.build([_sized((10, 200, 100), (12, 300, 90, 0))]))

    assert "planned 10 files / 200 lines, actual 12 files / 300 lines" in text
    assert "(files x1.20, lines x1.50)" in text


def test_a_plan_that_declared_nothing_shows_a_question_mark_for_that_divergence():
    text = cost_history.render(cost_history.build([_sized((0, 0, 0), (3, 40, 0, 0))]))

    assert "(files ?, lines ?)" in text


def test_a_history_without_sizes_prints_no_size_block():
    text = cost_history.render(cost_history.build([doc(1, 5.0)]))

    assert "planned hand-written size" not in text
