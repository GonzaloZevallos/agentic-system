"""The work split of a task's cycle: `stages.work_split` over the segments `stage_split` sums
(GH-907, part 1). Fixtures are in minutes from ORIGIN over a cycle of 100 minutes with the pull
request open from minute 90."""

import random
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest

from agentic.core.measurement.domain import stages
from agentic.core.measurement.domain.stages import (
    Generations,
    NoRows,
    WorkSplit,
    WorkSplitUnknown,
)
from agentic.core.measurement.domain.task_scores import members

ORIGIN = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
CREATED, PR_CREATED, MERGED = 0, 90, 100


def when(minutes):
    return ORIGIN + timedelta(minutes=minutes)


def at(minutes):
    return when(minutes).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(start, end, *, session="s", slug="developer"):
    record = {"slug": slug, "started_at": at(start), "ended_at": at(end)}
    return record if session is None else record | {"session_id": session}


def failure(start, end, *, session="s"):
    return run(start, end, session=session)


def doc(runs=(), failures=()):
    return {"runs": list(runs), "failures": list(failures)}


def gens(*spans):
    return Generations(tuple((when(a), when(b)) for a, b in spans))


def split(document, generations, *, merged=MERGED, pr=PR_CREATED):
    return stages.work_split(
        document,
        issue_created_at=when(CREATED),
        pr_created_at=when(pr),
        pr_merged_at=when(merged),
        generations=generations,
    )


def minutes(seconds):
    return seconds / 60


def test_work_and_wait_stages_are_disjoint_and_cover_the_cycle_stages():
    assert set(stages.WORK_STAGES).isdisjoint(stages.WAIT_STAGES)
    assert set(stages.WORK_STAGES) | set(stages.WAIT_STAGES) == set(members(stages.CycleStage))
    order = get_args(stages.CycleStage)
    for group in (stages.WORK_STAGES, stages.WAIT_STAGES):
        assert list(group) == sorted(group, key=order.index)


def synthetic_document(rng):
    cuts = sorted(rng.sample(range(0, 100), 6))
    slugs = ["developer", "tech_lead", "pm", "reflector", "mystery"]
    records = [
        run(a, b, slug=rng.choice(slugs), session=rng.choice(["s", "t"]))
        for a, b in zip(cuts[::2], cuts[1::2], strict=True)
    ]
    extra = [run(rng.randrange(0, 50), rng.randrange(50, 100)) for _ in range(rng.randrange(3))]
    return doc(records + extra, [failure(rng.randrange(0, 90), rng.randrange(90, 100))])


@pytest.mark.parametrize("seed", range(40))
def test_segments_are_ordered_disjoint_cover_the_cycle_and_sum_to_it(seed):
    document = synthetic_document(random.Random(seed))
    found = stages.claims(
        document,
        issue_created_at=when(CREATED),
        pr_created_at=when(PR_CREATED),
        pr_merged_at=when(MERGED),
    )
    segments = list(stages._segments(found, when(CREATED), when(MERGED)))
    assert segments
    assert segments[0][0] == when(CREATED) and segments[-1][1] == when(MERGED)
    for (_, end, *_), (start, *_) in zip(segments, segments[1:], strict=False):
        assert end == start
    assert all(start < end for start, end, *_ in segments)
    total = sum((end - start).total_seconds() for start, end, *_ in segments)
    assert total == pytest.approx(MERGED * 60)
    whole = stages.stage_split(
        document,
        issue_created_at=when(CREATED),
        pr_created_at=when(PR_CREATED),
        pr_merged_at=when(MERGED),
    )
    assert sum(whole.seconds[s] for s in stages.STAGE_PRECEDENCE if s != "other") + (
        whole.seconds["other"]
    ) == pytest.approx(total)


@pytest.mark.parametrize("seed", range(40))
def test_the_three_groups_are_not_negative_and_sum_to_the_work(seed):
    rng = random.Random(seed)
    document = synthetic_document(rng)
    generations = {
        "s": gens(*[(a, a + rng.randrange(1, 40)) for a in rng.sample(range(0, 80), 3)]),
        "t": NoRows() if seed % 3 == 0 else gens((rng.randrange(0, 50), rng.randrange(50, 100))),
    }
    result = split(document, generations)
    if isinstance(result, WorkSplitUnknown):
        assert result.cause in {"no_rows", "no_session"}
        return
    assert min(result.llm_s, result.tools_s, result.outside_s) >= 0
    assert result.llm_s + result.tools_s + result.outside_s == pytest.approx(result.work_s)
    assert result.work_s <= MERGED * 60 + 1e-6


def test_a_run_with_generations_is_llm_and_the_rest_of_it_tools():
    result = split(doc([run(10, 50)]), {"s": gens((10, 20), (30, 40))})
    assert isinstance(result, WorkSplit)
    assert minutes(result.llm_s) == 20
    assert minutes(result.tools_s) == 20
    # the queue before minute 10 is a wait, the PR window 90-100 is landing work no run covers
    assert minutes(result.outside_s) == minutes(result.work_s) - 40


def test_a_failure_with_a_readable_session_does_not_inflate_outside():
    with_failure = split(doc([run(10, 30)], [failure(30, 50)]), {"s": gens((30, 40))})
    without = split(doc([run(10, 30)]), {"s": gens((30, 40))})
    assert isinstance(with_failure, WorkSplit) and isinstance(without, WorkSplit)
    assert minutes(with_failure.llm_s) == 10
    assert minutes(with_failure.tools_s) == 30
    assert with_failure.outside_s == pytest.approx(without.outside_s - 20 * 60)
    assert with_failure.outside_s < without.outside_s


@pytest.mark.parametrize("session", [None, ""])
@pytest.mark.parametrize("start,end", [(30, 50), (40, 40)])
def test_a_failure_without_a_session_inside_development_is_unknown_even_at_zero_seconds(
    session, start, end
):
    result = split(doc([run(10, 60)], [failure(start, end, session=session)]), {"s": gens()})
    assert result == WorkSplitUnknown("no_session")


def test_a_run_without_a_session_after_the_merge_leaves_the_split_known():
    document = doc([run(10, 20), run(105, 110, session=None)])
    assert isinstance(split(document, {"s": gens((10, 15))}), WorkSplit)


def test_a_run_without_a_session_inside_a_human_pause_leaves_the_split_known():
    from agentic.core.process.domain import rules

    document = doc([run(10, 20)])
    document["pause_log"] = rules.logged(rules.escalation("questions", "q"), [], at=at(40))[
        "pause_log"
    ] + [rules.resumed_entry(at=at(60))]
    document["runs"].append(run(45, 55, session=None))
    assert isinstance(split(document, {"s": gens((10, 15))}), WorkSplit)


def test_a_norows_session_of_a_record_outside_work_does_not_give_no_rows():
    document = doc([run(10, 20), run(105, 110, session="gone")])
    assert isinstance(split(document, {"s": gens((10, 15)), "gone": NoRows()}), WorkSplit)


def test_a_session_in_norows_or_absent_inside_work_gives_no_rows():
    assert split(doc([run(10, 20)]), {"s": NoRows()}) == WorkSplitUnknown("no_rows")
    assert split(doc([run(10, 20)]), {}) == WorkSplitUnknown("no_rows")


def test_no_session_is_checked_before_no_rows():
    document = doc([run(10, 20), run(30, 40, session=None)])
    assert split(document, {"s": NoRows()}) == WorkSplitUnknown("no_session")


def test_an_unknown_stage_split_is_stage_unknown():
    assert split(doc([run(10, 20)]), {"s": gens()}, merged=-5) == WorkSplitUnknown("stage_unknown")
    result = stages.work_split(
        doc(),
        issue_created_at=when(0),
        pr_created_at=when(90),
        pr_merged_at=None,
        generations={},
    )
    assert result == WorkSplitUnknown("stage_unknown")


def test_a_generation_under_human_wait_or_queue_does_not_count():
    from agentic.core.process.domain import rules

    document = doc([run(10, 20)])
    document["pause_log"] = rules.logged(rules.escalation("questions", "q"), [], at=at(40))[
        "pause_log"
    ] + [rules.resumed_entry(at=at(60))]
    document["runs"].append(run(30, 70))
    # 40-60 is a human pause although the run spans it; 0-10 is the queue before the first run
    result = split(document, {"s": gens((0, 10), (40, 60), (15, 18))})
    assert isinstance(result, WorkSplit)
    assert minutes(result.llm_s) == 3


def test_a_boundary_generation_between_a_failure_and_the_run_of_its_session_counts_once():
    document = doc([run(30, 50)], [failure(10, 30)])
    result = split(document, {"s": gens((20, 40))})
    assert isinstance(result, WorkSplit)
    assert minutes(result.llm_s) == 20


def test_a_generation_two_records_share_counts_once():
    document = doc([run(10, 40), run(20, 50)])
    result = split(document, {"s": gens((15, 35))})
    assert isinstance(result, WorkSplit)
    assert minutes(result.llm_s) == 20


def test_malformed_generation_intervals_never_raise_and_a_naive_one_counts_as_utc():
    naive = (ORIGIN.replace(tzinfo=None) + timedelta(minutes=10), when(20).replace(tzinfo=None))
    held = Generations(
        (
            naive,
            (when(40), when(30)),  # inverted: dropped
            ("x", when(30)),  # not a datetime: dropped
            (when(30), None),
            (when(30),),  # not a pair: dropped
        )
    )
    result = split(doc([run(0, 60)]), {"s": held})
    assert isinstance(result, WorkSplit)
    assert minutes(result.llm_s) == 10


def test_a_value_that_is_neither_generations_nor_norows_reads_as_no_rows():
    assert split(doc([run(10, 20)]), {"s": stages.Unreachable("x")}) == WorkSplitUnknown("no_rows")


def test_a_pr_open_two_days_with_one_short_run_has_outside_about_two_days_minus_the_run():
    days = 2 * 24 * 60
    document = doc([run(10, 20)])
    result = stages.work_split(
        document,
        issue_created_at=when(0),
        pr_created_at=when(30),
        pr_merged_at=when(days),
        generations={"s": gens((10, 15))},
    )
    assert isinstance(result, WorkSplit)
    assert minutes(result.llm_s) == 5
    assert minutes(result.tools_s) == 5
    # the 10 minutes of queue before the run are a wait, everything else is work
    assert minutes(result.outside_s) == pytest.approx(days - 10 - 10)
