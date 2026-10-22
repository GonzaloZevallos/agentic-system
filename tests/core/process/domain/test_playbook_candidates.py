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
            "GH-1@review": pc.Entry(
                0.4, applied=({"op": "add", "slug": "developer", "lesson": LESSON},)
            ),
            "GH-2@review": pc.Entry(0.2, failed="RuntimeError: x"),
        },
        1,
        1,
    )
    assert pc.parse_ledger(pc.render_ledger(ledger)) == ledger
    assert ledger.since_summary == 1
    block_only = candidate("CLOSED", tasks={"GH-1": 9.0, "GH-3": 0.7})
    merged = pc.merge_ledgers([ledger], [block_only])
    assert merged.tasks["GH-1@review"].cost_usd == 0.4
    assert merged.tasks["GH-3@review"].cost_usd == 0.7
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
    docs = [
        document("GH-1", "2026-10-01T09:00:00+00:00"),
        document("GH-2", "2026-10-06T09:00:00+00:00"),
    ]
    assert [
        d["id"] for d in pc.eligible(docs, set(), "2026-10-05T00:00:00+00:00", "2026-10-01")
    ] == [
        "GH-1",
        "GH-2",
    ]
    assert pc.eligible(docs, set(), "", None) == []


@pytest.mark.parametrize(("count", "due"), [(0, False), (9, False), (10, True), (23, True)])
def test_a_summary_is_due_every_ten_reflected_tasks(count, due):
    assert pc.summary_due(count) is due


def test_an_auto_merged_delivery_is_never_a_kept_candidate():
    from agentic.core.process.domain import playbook_candidates as pc

    docs = [
        {"id": "GH-1", "closed_at": "2026-10-06T00:00:00", "delivery_outcome": "auto_merged"},
        {"id": "GH-2", "closed_at": "2026-10-06T00:00:00", "delivery_outcome": "merged"},
    ]
    names = [d["id"] for d in pc.eligible(docs, [], "2026-10-01", None)]
    assert names == ["GH-2"]


# ---- the ledger across ADR-0056 ---------------------------------------------------------

TODAYS_LEDGER = """\
started_at: '2026-10-05T10:00:00+00:00'
summaries: 0
summarised: 0
tasks:
  GH-1:
    cost_usd: 0.4
  GH-2:
    cost_usd: 0.2
    failed: 'RuntimeError: x'
"""


def test_a_ledger_written_before_the_gates_reads_every_key_as_review():
    ledger = pc.parse_ledger(TODAYS_LEDGER)
    assert list(ledger.tasks) == ["GH-1@review", "GH-2@review"]
    assert ledger.tasks["GH-1@review"] == pc.Entry(cost_usd=0.4)
    assert pc.split_key("GH-1") == ("GH-1", "review")
    assert pc.split_key("GH-1@decision") == ("GH-1", "decision")


def test_decisions_and_attempts_round_trip_and_are_omitted_when_zero():
    ledger = pc.Ledger("t", {"GH-3@decision": pc.Entry(0.3, decisions=2, attempts=1)})
    text = pc.render_ledger(ledger)
    assert pc.parse_ledger(text) == ledger
    assert "decisions" not in pc.render_ledger(pc.parse_ledger(TODAYS_LEDGER))


def test_reflected_tasks_counts_distinct_ids_and_not_keys_waiting_for_a_retry():
    ledger = pc.Ledger(
        "t",
        {
            "GH-1@review": pc.Entry(),
            "GH-1@decision": pc.Entry(decisions=1),
            "GH-2@review": pc.Entry(attempts=1),  # transient failure, tries left
            "GH-3@review": pc.Entry(attempts=pc.REFLECT_ATTEMPTS, failed="x"),  # given up: counted
            "GH-4@review": pc.Entry(attempts=pc.REFLECT_ATTEMPTS - 1),
        },
        summarised=1,
    )
    assert pc.reflected_tasks(ledger) == 2
    assert ledger.since_summary == 1
    assert ledger.done_keys == {"GH-1@review", "GH-1@decision", "GH-3@review"}


# ---- the two gates ------------------------------------------------------------------------

CLOCK = "2026-10-05T00:00:00+00:00"
HUMAN_TWO = "### Decision 1\n\nA.\n\n### Decision 2\n\nB."


def ended(task_id: str, status: str, **extra) -> dict:
    return {"id": task_id, "status": status, "sections": {"Human": HUMAN_TWO}, **extra}


def decision_ids(docs, done=(), **kw) -> list[str]:
    return [d["id"] for d in pc.eligible(docs, done, CLOCK, None, gate="decision", **kw)]


def test_the_decision_gate_takes_discarded_split_closed_pr_and_closed_escalations():
    docs = [
        ended("GH-1", "discarded", finished_at="2026-10-06T00:00:00+00:00"),
        ended("GH-2", "split", finished_at="2026-10-07T00:00:00+00:00"),
        ended(
            "GH-3",
            "ready_for_review",
            closed_at="2026-10-08T00:00:00+00:00",
            delivery_outcome="closed",
        ),
        ended(
            "GH-4",
            "waiting_on_human",
            runs=[
                {"ended_at": "2026-10-09T00:00:00+00:00"},
                {"ended_at": "2026-10-09T05:00:00+00:00"},
            ],
        ),
        ended("GH-5", "waiting_on_human", runs=[{"ended_at": "2026-10-09T00:00:00+00:00"}]),
        ended("GH-6", "discarded"),  # no finished_at: no clock yet
        {"id": "GH-7", "status": "discarded", "finished_at": "2026-10-06T00:00:00+00:00"},
        ended("GH-8", "discarded", finished_at="2026-09-01T00:00:00+00:00"),
    ]
    assert decision_ids(docs, closed_escalations={"GH-4"}) == ["GH-1", "GH-2", "GH-3", "GH-4"]


def test_a_kept_delivery_is_not_a_decision_gate_task_and_a_discarded_one_is_not_a_review_task():
    kept = ended(
        "GH-1", "ready_for_review", closed_at="2026-10-06T00:00:00+00:00", delivery_outcome="merged"
    )
    gone = ended("GH-2", "discarded", finished_at="2026-10-06T00:00:00+00:00")
    assert decision_ids([kept, gone]) == ["GH-2"]
    assert [d["id"] for d in pc.eligible([kept, gone], set(), CLOCK, None)] == ["GH-1"]


def test_a_decision_key_returns_only_when_the_task_has_more_decisions_than_it_read():
    doc = ended("GH-1", "discarded", finished_at="2026-10-06T00:00:00+00:00")
    done = {"GH-1@decision"}
    assert decision_ids([doc], done, decisions_read={"GH-1@decision": 2}) == []
    assert decision_ids([doc], done, decisions_read={"GH-1@decision": 1}) == ["GH-1"]
    assert decision_ids([doc], done) == ["GH-1"]  # recorded as having read none


def test_a_review_key_written_before_the_gates_still_counts_as_reflected():
    docs = [document("GH-1", "2026-10-06T00:00:00+00:00")]
    assert pc.eligible(docs, {"GH-1"}, CLOCK, None) == []
    assert pc.eligible(docs, {"GH-1@review"}, CLOCK, None) == []
    assert len(pc.eligible(docs, {"GH-1@decision"}, CLOCK, None)) == 1


def test_a_key_waiting_for_a_retry_is_eligible_again():
    ledger = pc.Ledger("t", {"GH-1@review": pc.Entry(attempts=1)})
    docs = [document("GH-1", "2026-10-06T00:00:00+00:00")]
    assert [d["id"] for d in pc.eligible(docs, ledger.done_keys, CLOCK, None)] == ["GH-1"]


def test_no_clock_means_no_decision_gate_task():
    assert decision_ids([ended("GH-1", "discarded", finished_at="2026-10-06")]) != []
    assert (
        pc.eligible(
            [ended("GH-1", "discarded", finished_at="2026-10-06")], set(), "", None, gate="decision"
        )
        == []
    )


# ---- candidates.yaml and archived.yaml ----------------------------------------------------


def test_candidates_and_archived_round_trip_and_reject_a_bad_lesson():
    pending = pb.Pending("developer", "spec-scope", LESSON, "medium", ("GH-1",), ("finding",), "d")
    assert pc.parse_candidates(pc.render_candidates([pending])) == [pending]
    assert pc.parse_candidates("") == []
    item = pb.Item(
        "developer-3",
        LESSON,
        "spec-scope",
        "empty",
        ("GH-1", "GH-2"),
        "d",
        last_applied="e",
        in_prompt_since="f",
    )
    shelved = pb.Archived(item, "stale", "2026-10-22")
    assert pc.parse_archived(pc.render_archived([shelved])) == [shelved]
    with pytest.raises(ValueError):
        pc.parse_candidates(pc.render_candidates([pending]).replace(LESSON, "see https://x.y"))
    with pytest.raises(ValueError):
        pc.parse_candidates("candidates: [{slug: developer}]")


def test_the_new_record_files_are_named_beside_the_old_ones():
    assert {"candidates", "archived"} <= set(pb.RECORD_FILES)
    assert pc.CANDIDATES_FILE.endswith("/candidates.yaml")
    assert pc.ARCHIVED_FILE.endswith("/archived.yaml")


# ---- in_prompt_since ---------------------------------------------------------------------


def merged(commit: str) -> pc.Candidate:
    return pc.Candidate("MERGED", commit, pc.Record())


def test_in_prompt_since_is_the_first_merge_whose_playbook_holds_the_id():
    books = {
        "c1": playbook_with("developer", "Other."),
        "c2": playbook_with("developer", "Other.", LESSON),
    }
    dates = {"c1": "2026-10-01", "c2": "2026-10-10", "c3": "2026-10-20"}
    books["c3"] = books["c2"]

    def at(commit: str, slug: str) -> pb.Playbook:
        return books[commit]

    got = pc.in_prompt_since(
        "developer-2", [merged("c3"), merged("c2"), merged("c1")], at, dates.get
    )
    assert got == "2026-10-10"
    # the id decides, not the text of the block: a PR whose playbook lacks the id never counts
    assert pc.in_prompt_since("developer-9", [merged("c2")], at, dates.get) is None


def test_in_prompt_since_ignores_unmerged_prs_and_merges_before_a_restore():
    book = playbook_with("developer", LESSON)
    dates = {"c1": "2026-10-01", "c2": "2026-10-15"}
    prs = [
        pc.Candidate("CLOSED", "", pc.Record()),
        pc.Candidate("OPEN", "", pc.Record()),
        merged("c1"),
        merged("c2"),
    ]

    def at(commit: str, slug: str) -> pb.Playbook:
        return book

    assert pc.in_prompt_since("developer-1", prs[:2], at, dates.get) is None
    assert pc.in_prompt_since("developer-1", prs, at, dates.get) == "2026-10-01"
    assert (
        pc.in_prompt_since("developer-1", prs, at, dates.get, not_before="2026-10-10")
        == "2026-10-15"
    )
