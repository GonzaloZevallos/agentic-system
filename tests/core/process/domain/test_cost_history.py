"""The cost history the Tech Lead's plan is calibrated against (ADR-0053), pure."""

from agentic.core.process.domain import cost_history


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
