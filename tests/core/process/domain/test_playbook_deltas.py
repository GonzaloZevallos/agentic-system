"""The merge, noop and contradicts deltas and the citation check of the playbook domain:
a merge reword only inside the grace period, `touched` as the only writer of
`last_applied`, and a record whose cited tasks are gone neither restored nor counted.

Seam: `playbook.apply`, `playbook.in_grace`, `playbook.touched`, `playbook.citation_missing`,
`playbook.merge_into / noop / contradicts`."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import playbook_candidates as pc
from agentic.core.process.domain.playbook import (
    GRACE_TASKS,
    Applied,
    Archived,
    CitationFacts,
    Delta,
    Evidence,
    Item,
    Pending,
    Playbook,
    apply,
    citation_missing,
    contradicts,
    in_grace,
    merge_into,
    noop,
    parse,
    render_file,
    touched,
)

NOW = "2026-10-05"
ADDED = "2026-09-01"
LESSON = "Run the whole suite before delivering, never only the file you touched."
CATEGORY = "process-suite-red"


def evidence(*, closed: bool = True, category: str = CATEGORY) -> Evidence:
    entry = {"pair": f"{pb.REVIEW_PAIR}:s1", "category": category, "reason": "r"}
    return Evidence(closed=closed, task_type="feature", return_history=(entry,))


EVIDENCE = {
    "GH-1": evidence(),
    "GH-2": evidence(),
    "GH-3": evidence(),
    "GH-9": evidence(closed=False),
}


def item(
    n: int = 1,
    lesson: str = LESSON,
    seen_in: tuple[str, ...] = ("GH-1",),
    last_applied: str = "",
    in_prompt_since: str = "",
) -> Item:
    return Item(
        id=f"developer-{n}",
        lesson=lesson,
        category=CATEGORY,
        since="empty",
        seen_in=seen_in,
        added_at=ADDED,
        last_applied=last_applied,
        in_prompt_since=in_prompt_since,
    )


LEGACY = item()
NEVER_LANDED = item(last_applied=NOW)
LANDED = item(last_applied=NOW, in_prompt_since="2026-09-10")


def playbook(*items: Item) -> Playbook:
    return Playbook(slug="developer", items=items)


def run(
    deltas: list[Delta],
    start: Playbook,
    *,
    ev: dict[str, Evidence] | None = None,
    counts: dict[str, int] | None = None,
    facts: CitationFacts | None = None,
    candidates: tuple[Pending, ...] = (),
    archived: tuple[Archived, ...] = (),
) -> Applied:
    return apply(
        start,
        deltas,
        EVIDENCE if ev is None else ev,
        (),
        section_hash="abc123",
        now=NOW,
        candidates=candidates,
        archived=archived,
        role_tasks_since=counts or {},
        facts=facts,
    )


def reword(item_id: str = "developer-1", task: str = "GH-2") -> Delta:
    return merge_into(item_id, task, slug="developer", wording="A reworded lesson.")


# ---- grace ----------------------------------------------------------------------------


def test_in_grace_for_a_merge_reads_never_landed_legacy_and_the_count():
    def young(item: Item, *count: int) -> bool:
        return in_grace(item, {item.id: c for c in count}, rewording=True)

    assert young(NEVER_LANDED) is True
    assert young(LEGACY) is False
    assert young(LANDED) is False  # no count for its id
    assert young(LANDED, GRACE_TASKS - 1) is True
    assert young(LANDED, GRACE_TASKS) is False


def test_the_demotion_reading_of_in_grace_keeps_an_item_with_no_count_young():
    assert in_grace(LEGACY, {}) is True
    assert in_grace(LANDED, {}) is True
    assert in_grace(LANDED, {LANDED.id: GRACE_TASKS}) is False


def test_an_out_of_grace_merge_keeps_the_lesson_byte_identical_and_grows_seen_in():
    result = run([reword()], playbook(LANDED), counts={"developer-1": GRACE_TASKS})
    merged = result.playbook.items[0]
    assert merged.lesson == LESSON
    assert merged.seen_in == ("GH-1", "GH-2")
    assert result.applied[0].lesson == ""  # the effective delta carries no wording
    assert not result.refused


def test_a_merge_inside_the_grace_period_rewords():
    result = run([reword()], playbook(LANDED), counts={"developer-1": 2})
    assert result.playbook.items[0].lesson == "A reworded lesson."
    assert result.applied[0].lesson == "A reworded lesson."


def test_a_never_landed_item_is_reworded():
    result = run([reword()], playbook(NEVER_LANDED))
    assert result.playbook.items[0].lesson == "A reworded lesson."


def test_a_legacy_item_keeps_its_wording():
    result = run([reword()], playbook(LEGACY))
    assert result.playbook.items[0].lesson == LESSON


def test_an_item_missing_from_the_mapping_is_out_of_grace():
    result = run([reword()], playbook(LANDED), counts={"developer-9": 0})
    assert result.playbook.items[0].lesson == LESSON


def test_a_legacy_item_bumped_once_is_not_unfrozen_for_a_later_merge():
    bump = Delta(op="bump", slug="developer", item_id="developer-1", tasks=("GH-2",))
    bumped = run([bump], playbook(LEGACY)).playbook
    assert bumped.items[0].in_prompt_since == ADDED
    assert bumped.items[0].last_applied == NOW
    merged = run([reword(task="GH-3")], bumped).playbook.items[0]
    assert merged.lesson == LESSON
    assert merged.seen_in == ("GH-1", "GH-2", "GH-3")


def test_touched_writes_the_clock_and_starts_a_legacy_item_aging_from_added_at():
    assert touched(LEGACY, NOW) == replace(LEGACY, last_applied=NOW, in_prompt_since=ADDED)
    assert touched(NEVER_LANDED, "2026-10-09") == replace(NEVER_LANDED, last_applied="2026-10-09")
    assert touched(LANDED, "2026-10-09").in_prompt_since == "2026-09-10"


def test_an_add_that_bumps_an_item_goes_through_touched():
    add = Delta(op="add", slug="developer", category=CATEGORY, lesson=LESSON, tasks=("GH-2",))
    bumped = run([add], playbook(LEGACY)).playbook.items[0]
    assert (bumped.last_applied, bumped.in_prompt_since) == (NOW, ADDED)


# ---- empty wording --------------------------------------------------------------------


@pytest.mark.parametrize("wording", [None, "", "   \n "])
def test_a_merge_without_wording_adds_its_new_task(wording):
    delta = merge_into("developer-1", "GH-2", slug="developer", wording=wording)
    if wording:  # whitespace only: built by hand, the constructor keeps it as given
        assert delta.lesson == wording
    result = run([delta], playbook(NEVER_LANDED))
    assert result.playbook.items[0].lesson == LESSON
    assert result.playbook.items[0].seen_in == ("GH-1", "GH-2")


@pytest.mark.parametrize("wording", [None, "", "   \n "])
def test_a_merge_without_wording_or_new_task_is_refused(wording):
    delta = merge_into("developer-1", "GH-1", slug="developer", wording=wording)
    result = run([delta], playbook(NEVER_LANDED))
    assert result.refused[0].reason == (
        "nothing to merge: no wording and every cited task is already counted"
    )
    assert result.playbook == playbook(NEVER_LANDED)


def test_an_out_of_grace_merge_with_wording_and_no_new_task_is_refused():
    result = run([reword(task="GH-1")], playbook(LEGACY))
    assert "nothing to merge" in result.refused[0].reason


# ---- refusals -------------------------------------------------------------------------

CONSTRUCTORS = {
    "merge": lambda item_id, task, slug: merge_into(item_id, task, slug=slug),
    "noop": lambda item_id, task, slug: noop(item_id, task, slug=slug),
    "contradicts": lambda item_id, task, slug: contradicts(item_id, task, slug=slug),
}


def test_refused_wording_refuses_the_whole_delta_even_with_a_new_task():
    delta = merge_into("developer-1", "GH-2", slug="developer", wording="two\nlines")
    result = run([delta], playbook(NEVER_LANDED))
    assert result.playbook == playbook(NEVER_LANDED)
    assert len(result.refused) == 1


def test_refused_wording_is_refused_even_where_it_would_have_been_dropped():
    delta = merge_into("developer-1", "GH-2", slug="developer", wording="two\nlines")
    assert run([delta], playbook(LEGACY)).refused


@pytest.mark.parametrize("op", CONSTRUCTORS)
def test_a_delta_for_another_slug_is_refused_first(op):
    delta = CONSTRUCTORS[op]("developer-1", "GH-2", "tech_lead")
    result = run([delta], playbook(NEVER_LANDED))
    assert "slug tech_lead" in result.refused[0].reason


@pytest.mark.parametrize("op", CONSTRUCTORS)
def test_an_unknown_id_is_refused_and_so_is_a_candidate_text(op):
    waiting = Pending("developer", CATEGORY, "A waiting lesson.", "low", ("GH-1",), (), NOW)
    start = playbook(NEVER_LANDED)
    for target in ("developer-9", "A waiting lesson.", "tech_lead-1"):
        result = run([CONSTRUCTORS[op](target, "GH-2", "developer")], start, candidates=(waiting,))
        assert f"there is no item {target!r}" == result.refused[0].reason
        assert result.playbook == start


@pytest.mark.parametrize("op", CONSTRUCTORS)
def test_an_unclosed_or_unknown_task_is_refused(op):
    start = playbook(NEVER_LANDED)
    open_task = run([CONSTRUCTORS[op]("developer-1", "GH-9", "developer")], start)
    assert "task GH-9 is not closed" in open_task.refused[0].reason
    unknown = run([CONSTRUCTORS[op]("developer-1", "GH-77", "developer")], start)
    assert "task GH-77 has no evidence" in unknown.refused[0].reason


# ---- noop and contradicts -------------------------------------------------------------


@pytest.mark.parametrize("make", [noop, contradicts])
def test_noop_and_contradicts_change_no_state_and_are_applied(make):
    start = playbook(LANDED, item(2, "Another lesson."))
    waiting = Pending("developer", CATEGORY, "A waiting lesson.", "low", ("GH-1",), (), NOW)
    delta = make("developer-1", "GH-2", slug="developer")
    result = run([delta], start, candidates=(waiting,))
    assert result.playbook == start
    assert result.candidates == (waiting,)
    assert result.applied == (delta,)
    assert not result.refused and not result.held


@pytest.mark.parametrize("make", [noop, contradicts])
def test_noop_and_contradicts_need_no_backing_of_the_items_category(make):
    ev = {"GH-5": evidence(category="spec-scope")}
    result = run([make("developer-1", "GH-5", slug="developer")], playbook(LANDED), ev=ev)
    assert len(result.applied) == 1


def test_a_merge_still_needs_a_backing_for_its_new_task():
    ev = {"GH-5": evidence(category="spec-scope")}
    result = run([merge_into("developer-1", "GH-5", slug="developer")], playbook(LANDED), ev=ev)
    assert "backing" in result.refused[0].reason


# ---- citation_missing -----------------------------------------------------------------


def facts(*tasks: str, escalations: tuple[str, ...] = (), readable: bool = True) -> CitationFacts:
    return CitationFacts(readable, frozenset(tasks), frozenset(escalations))


def test_citation_missing_is_true_only_for_a_readable_store_without_any_cited_task():
    cited = item(seen_in=("GH-1", "GH-2"))
    assert citation_missing(cited, facts("GH-7")) is True
    assert citation_missing(cited, facts("GH-7", readable=False)) is False
    assert citation_missing(cited, None) is False


def test_citation_missing_is_false_for_an_item_that_cites_nothing():
    assert citation_missing(item(seen_in=()), facts("GH-7")) is False


def test_one_surviving_citation_or_a_closed_escalation_is_present():
    cited = item(seen_in=("GH-1", "GH-2"))
    assert citation_missing(cited, facts("GH-2")) is False
    assert citation_missing(cited, facts("GH-7", escalations=("GH-1",))) is False


def test_citation_missing_reads_a_pending_too():
    waiting = Pending("developer", CATEGORY, LESSON, "low", ("GH-1",), (), NOW)
    assert citation_missing(waiting, facts()) is True


# ---- restore --------------------------------------------------------------------------


def shelved(n: int, seen_in: tuple[str, ...]) -> Archived:
    return Archived(item(n, seen_in=seen_in, in_prompt_since="2026-08-01"), "stale", ADDED)


def add_of(*tasks: str) -> Delta:
    return Delta(op="add", slug="developer", category=CATEGORY, lesson=LESSON, tasks=tasks)


def test_a_live_archived_record_is_restored_over_a_dead_one_that_stays():
    dead, live = shelved(1, ("GH-70",)), shelved(2, ("GH-1",))
    result = run([add_of("GH-3")], playbook(), facts=facts("GH-1"), archived=(dead, live))
    restored = result.playbook.items[0]
    assert (restored.id, restored.seen_in) == ("developer-2", ("GH-1", "GH-3"))
    assert result.archived == (dead,)


def test_a_dead_record_leaves_a_new_item_numbered_after_it_with_only_the_new_evidence():
    dead = shelved(1, ("GH-70", "GH-71"))
    result = run([add_of("GH-3")], playbook(), facts=facts("GH-1"), archived=(dead,))
    assert result.archived == (dead,)
    assert [(i.id, i.seen_in) for i in result.playbook.items] == [("developer-2", ("GH-3",))]


def test_an_earlier_task_absent_from_facts_but_closed_in_evidence_is_present():
    shelf = shelved(1, ("GH-2",))
    result = run([add_of("GH-3")], playbook(), facts=facts("GH-1"), archived=(shelf,))
    assert result.playbook.items[0].id == "developer-1"
    assert result.archived == ()


def test_an_earlier_task_absent_from_facts_and_from_evidence_is_not_restored():
    shelf = shelved(1, ("GH-70",))
    result = run([add_of("GH-3")], playbook(), facts=facts("GH-1"), archived=(shelf,))
    assert result.playbook.items[0].id == "developer-2"
    assert result.archived == (shelf,)


def test_a_restore_without_facts_is_as_before_and_stays_never_landed():
    shelf = shelved(1, ("GH-70",))
    result = run([add_of("GH-3")], playbook(), archived=(shelf,))
    restored = result.playbook.items[0]
    assert (restored.id, restored.seen_in) == ("developer-1", ("GH-70", "GH-3"))
    assert (restored.last_applied, restored.in_prompt_since) == (NOW, "")


# ---- candidates -----------------------------------------------------------------------


def test_a_candidate_whose_only_citation_is_gone_stays_one_with_only_the_new_task():
    waiting = Pending("developer", CATEGORY, LESSON, "low", ("GH-70",), ("review",), ADDED)
    delta = replace(add_of("GH-3"), confidence="low")
    result = run([delta], playbook(), facts=facts("GH-1"), candidates=(waiting,))
    assert not result.playbook.items
    assert len(result.held) == 1
    [kept] = result.candidates
    assert kept.seen_in == ("GH-3",)
    assert "GH-70" not in kept.seen_in


def test_a_candidate_with_a_surviving_citation_is_promoted_with_both():
    waiting = Pending("developer", CATEGORY, LESSON, "low", ("GH-1",), ("review",), ADDED)
    delta = replace(add_of("GH-2"), confidence="low")
    result = run([delta], playbook(), facts=facts("GH-1"), candidates=(waiting,))
    assert result.playbook.items[0].seen_in == ("GH-1", "GH-2")
    assert result.candidates == ()


def test_an_unreadable_store_drops_no_candidate_evidence():
    waiting = Pending("developer", CATEGORY, LESSON, "low", ("GH-70",), ("review",), ADDED)
    delta = replace(add_of("GH-3"), confidence="low")
    unread = run([delta], playbook(), facts=facts("GH-1", readable=False), candidates=(waiting,))
    assert unread.playbook.items[0].seen_in == ("GH-70", "GH-3")  # unreadable drops nothing


# ---- compatibility --------------------------------------------------------------------


def test_apply_without_facts_or_counts_behaves_as_before_for_add_bump_and_retire():
    start = playbook(LANDED, item(2, "Another lesson."))
    deltas = [
        add_of("GH-2"),
        Delta(op="bump", slug="developer", item_id="developer-2", tasks=("GH-2",)),
        Delta(op="retire", slug="developer", item_id="developer-2"),
    ]
    result = run(deltas, start)
    assert [i.id for i in result.playbook.items] == ["developer-1"]
    assert result.playbook.items[0].seen_in == ("GH-1", "GH-2")
    assert not result.refused


def test_files_using_nothing_new_round_trip_byte_identical():
    text = render_file(playbook(LEGACY, item(2, "Another lesson.", ("GH-1", "GH-2"))))
    assert render_file(parse(text, "developer")) == text
    assert "in_prompt_since" not in text and "last_applied" not in text
    landed = render_file(playbook(LANDED))
    assert render_file(parse(landed, "developer")) == landed


def test_candidates_and_archived_files_using_nothing_new_round_trip_byte_identical():
    waiting = Pending("developer", CATEGORY, LESSON, "low", ("GH-1",), ("review",), ADDED)
    candidates = pc.render_candidates([waiting, replace(waiting, lesson="Another lesson.")])
    assert pc.render_candidates(pc.parse_candidates(candidates)) == candidates
    archived = pc.render_archived([shelved(1, ("GH-1",)), Archived(LEGACY, "stale", ADDED)])
    assert pc.render_archived(pc.parse_archived(archived)) == archived
    legacy_only = pc.render_archived([Archived(LEGACY, "stale", ADDED)])
    assert "last_applied" not in legacy_only and "in_prompt_since" not in legacy_only
    assert pc.render_archived(pc.parse_archived(legacy_only)) == legacy_only


def test_a_demoted_candidate_whose_evidence_is_gone_restarts_as_a_plain_candidate():
    demoted = Pending(
        "developer", CATEGORY, LESSON, "low", ("GH-70", "GH-71"), (), ADDED, demoted_at_seen=2
    )
    delta = replace(add_of("GH-3"), confidence="low")
    result = run([delta], playbook(), facts=facts("GH-1"), candidates=(demoted,))
    [kept] = result.candidates
    assert (kept.seen_in, kept.demoted_at_seen) == (("GH-3",), None)
