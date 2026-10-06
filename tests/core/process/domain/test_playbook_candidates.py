"""The candidate PR's pure rules (ADR-0043): the marker block round trip, what people
refused, which closed tasks are worth a reflection and when a summary is due.

Seam: `agentic.core.process.domain.playbook_candidates`."""

from __future__ import annotations

import pytest

from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import playbook_candidates as pc

LESSON = "Run every declared check before delivering, not only the single test file."
OTHER = "Name the seam a test exercises in its docstring."


def add(lesson: str = LESSON, slug: str = "developer", category: str = "standard-other") -> pc.Add:
    return pc.Add(slug, category, pb.normalise(lesson))


def candidate(state: str, *adds: pc.Add, commit: str = "", tasks=None) -> pc.Candidate:
    return pc.Candidate(state, commit, pc.Record(tasks or {"GH-1": 0.4}, adds))


def playbook_with(slug: str, *lessons: str) -> pb.Playbook:
    items = tuple(
        pb.Item(f"{slug}-{n}", lesson, "standard-other", "empty", ("GH-1",), "2026-10-05")
        for n, lesson in enumerate(lessons, 1)
    )
    return pb.Playbook(slug, items)


def document(task_id: str, closed_at: str | None, outcome: str = "merged") -> dict[str, str]:
    doc = {"id": task_id}
    if closed_at:
        doc["closed_at"] = closed_at
        doc["delivery_outcome"] = outcome
    return doc


# ---- the block ------------------------------------------------------------------------


def test_the_block_round_trips_inside_a_body_with_prose_around_it():
    record = pc.Record(
        {"GH-10": 0.4, "GH-11": 0.9},
        (add(), add(OTHER, "tech_lead", "plan-calibration")),
        "2026-10-05T10:00:00+00:00",
        2,
        10,
    )
    body = f"Read every lesson before merging.\n\n{pc.block(record)}\ntrailing text"
    assert pc.parse_block(body) == record


def test_a_body_without_the_marker_is_no_candidate():
    assert pc.parse_block("## Summary\n\n```yaml\ntasks: {GH-1: 1}\n```\n") is None


@pytest.mark.parametrize(
    "tail",
    [
        "\nno fence at all",
        "\n```yaml\n- a list, not a mapping\n```\n",
        "\n```yaml\ntasks: [1, 2]\n```\n",
        "\n```yaml\ntasks: {GH-1: not-a-number}\n```\n",
        "\n```yaml\nadds: [{slug: developer}]\n```\n",
        "\n```yaml\n: : :\n```\n",
    ],
)
def test_a_marker_over_an_unreadable_block_is_no_candidate_and_never_raises(tail):
    assert pc.parse_block(pc.MARKER + tail) is None


# ---- what people refused --------------------------------------------------------------


def at_merge(**playbooks: pb.Playbook):
    def read(commit: str, slug: str) -> pb.Playbook:
        return playbooks.get(f"{slug}@{commit}", pb.Playbook(slug))

    return read


def test_a_pr_closed_unmerged_refuses_every_add_and_its_tasks_count_as_reflected():
    closed = candidate("CLOSED", add(), add(OTHER), tasks={"GH-1": 0.4, "GH-2": 0.5})
    rejected = pc.rejected_from([closed], at_merge())
    assert [r.lesson for r in rejected] == [pb.normalise(LESSON), pb.normalise(OTHER)]
    assert pc.reflected_from([closed]) == {"GH-1": 0.4, "GH-2": 0.5}


def test_a_merged_pr_refuses_only_the_add_absent_from_the_file_at_its_merge_commit():
    merged = candidate("MERGED", add(), add(OTHER), commit="abc")
    kept = playbook_with("developer", LESSON.upper())  # normalised: still the same lesson
    rejected = pc.rejected_from([merged], at_merge(**{"developer@abc": kept}))
    assert [r.lesson for r in rejected] == [pb.normalise(OTHER)]


def test_a_lesson_retired_by_a_later_pr_is_not_a_refusal_of_the_one_that_added_it():
    first = candidate("MERGED", add(), commit="abc")
    later = candidate("MERGED", commit="def", tasks={"GH-9": 0.1})
    # At the first PR's merge commit the lesson was there; at `def` (and HEAD) it is gone.
    read = at_merge(**{"developer@abc": playbook_with("developer", LESSON)})
    assert pc.rejected_from([first, later], read) == []


def test_an_open_pr_refuses_nothing_and_a_merged_one_without_a_commit_neither():
    assert pc.rejected_from([candidate("OPEN", add())], at_merge()) == []
    assert pc.rejected_from([candidate("MERGED", add(), commit="")], at_merge()) == []


def test_rejected_lessons_merge_once_and_survive_the_file_round_trip():
    one = pb.Rejected("developer", "standard-other", pb.normalise(LESSON))
    again = pb.Rejected("developer", "standard-other", "  " + LESSON.upper())
    both = pc.merge_rejected([one], [again, pb.Rejected("developer", "standard-other", OTHER)])
    assert len(both) == 2
    assert pc.parse_rejected(pc.render_rejected(both)) == both
    assert pc.parse_rejected("") == []
    with pytest.raises(ValueError):
        pc.parse_rejected("rejected: [{slug: developer}]")


# ---- the ledger -----------------------------------------------------------------------


def test_the_ledger_round_trips_and_merging_keeps_entries_over_bare_block_costs():
    ledger = pc.Ledger(
        "2026-10-05T10:00:00+00:00",
        {
            "GH-1": pc.Entry(0.4, applied=({"op": "add", "slug": "developer", "lesson": LESSON},)),
            "GH-2": pc.Entry(0.2, failed="RuntimeError: x"),
        },
        1,
        1,
    )
    assert pc.parse_ledger(pc.render_ledger(ledger)) == ledger
    assert ledger.since_summary == 1
    block_only = candidate("CLOSED", tasks={"GH-1": 9.0, "GH-3": 0.7})
    merged = pc.merge_ledgers([ledger], [block_only])
    assert merged.tasks["GH-1"].cost_usd == 0.4
    assert merged.tasks["GH-3"].cost_usd == 0.7
    assert merged.started_at == ledger.started_at
    with pytest.raises(ValueError):
        pc.parse_ledger("tasks: [GH-1]")


# ---- which tasks, and when a summary ---------------------------------------------------


def test_eligible_keeps_closed_kept_deliveries_after_the_clock_oldest_first_and_not_reflected():
    docs = [
        document("GH-5", "2026-10-07T09:00:00+00:00"),
        document("GH-1", "2026-10-01T09:00:00+00:00"),  # before the clock
        document("GH-2", "2026-10-06T09:00:00+00:00", "merged_with_changes"),
        document("GH-3", "2026-10-06T10:00:00+00:00", "closed"),  # nobody kept it
        document("GH-4", None),  # not closed
        document("GH-6", "2026-10-06T11:00:00+00:00"),  # already reflected
    ]
    out = pc.eligible(docs, {"GH-6"}, "2026-10-05T00:00:00+00:00", None)
    assert [d["id"] for d in out] == ["GH-2", "GH-5"]


def test_since_reaches_back_and_with_no_clock_nothing_is_eligible():
    docs = [document("GH-1", "2026-10-01T09:00:00+00:00"), document("GH-2", "2026-10-06T09:00:00+00:00")]
    assert [d["id"] for d in pc.eligible(docs, set(), "2026-10-05T00:00:00+00:00", "2026-10-01")] == [
        "GH-1",
        "GH-2",
    ]
    assert pc.eligible(docs, set(), "", None) == []


@pytest.mark.parametrize(("count", "due"), [(0, False), (9, False), (10, True), (23, True)])
def test_a_summary_is_due_every_ten_reflected_tasks(count, due):
    assert pc.summary_due(count) is due
