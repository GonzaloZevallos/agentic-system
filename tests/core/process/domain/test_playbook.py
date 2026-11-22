from dataclasses import replace
from typing import Any, get_args

import pytest

from agentic.core.process.domain import roles
from agentic.core.process.domain.playbook import (
    ACCOUNTABLE,
    ARCHIVE_AFTER,
    ARCHIVE_SCORE,
    ARCHIVE_STALE,
    GRACE_TASKS,
    HELPFUL_CAP,
    KEEP_CONFIDENCE,
    KEEP_MIN_TASKS,
    PLAYBOOK_MAX_CHARS,
    PLAYBOOK_MAX_ITEMS,
    PLAYBOOK_ROLES,
    REVIEW_PAIR,
    Applied,
    Archived,
    Backing,
    Confidence,
    Delta,
    Demotion,
    Evidence,
    Item,
    Pending,
    Playbook,
    Rejected,
    Scored,
    ScoredTask,
    accountable,
    apply,
    archive_due,
    archive_stale,
    attribute,
    demote,
    demotion_pick,
    in_grace,
    keep,
    lesson_refusal,
    normalise,
    over_cap,
    parse,
    render_file,
    return_backing,
    returned_categories,
    score,
    score_task,
    section,
    shared_term,
)
from agentic.core.process.domain.task import FindingCategory

CATEGORIES = get_args(FindingCategory)
PLANNER = {"plan-calibration", "integration-crosscut"}
TASK_TYPES = ("feature", "bug", "platform_enhancement")
LESSON = "Run the whole suite before delivering, never only the file you touched."
NOW = "2026-10-05"


def entry(category: str, subtask: str | None = "s1") -> dict[str, str]:
    pair = REVIEW_PAIR if subtask is None else f"{REVIEW_PAIR}:{subtask}"
    return {"pair": pair, "category": category, "reason": "r"}


def evidence(*categories: str, closed: bool = True, task_type: str = "feature") -> Evidence:
    return Evidence(
        closed=closed,
        task_type=task_type,
        return_history=tuple(entry(c) for c in categories),
    )


EVIDENCE = {
    "GH-1": evidence("process-suite-red"),
    "GH-2": evidence("process-suite-red", "spec-scope"),
}


def add(
    lesson: str = LESSON,
    *tasks: str,
    category: str = "process-suite-red",
    slug: str = "developer",
) -> Delta:
    return Delta(op="add", slug=slug, category=category, lesson=lesson, tasks=tasks or ("GH-1",))


def empty(slug: str = "developer") -> Playbook:
    return Playbook(slug=slug)


def run(
    deltas: list[Delta],
    playbook: Playbook | None = None,
    rejected: tuple[Rejected, ...] = (),
    ev: dict[str, Evidence] | None = None,
    candidates: tuple[Pending, ...] = (),
    archived: tuple[Archived, ...] = (),
    counts: dict[str, int] | None = None,
) -> Applied:
    return apply(
        playbook or empty(),
        deltas,
        EVIDENCE if ev is None else ev,
        rejected,
        section_hash="abc123",
        now=NOW,
        candidates=candidates,
        archived=archived,
        role_tasks_since=counts or {},
    )


def item(n: int = 1, lesson: str = LESSON, seen_in: tuple[str, ...] = ("GH-1",)) -> Item:
    return Item(
        id=f"developer-{n}",
        lesson=lesson,
        category="process-suite-red",
        since="empty",
        seen_in=seen_in,
        added_at=NOW,
    )


def full_playbook() -> Playbook:
    return Playbook(
        slug="developer",
        items=tuple(item(n, f"Lesson number {n}.") for n in range(1, PLAYBOOK_MAX_ITEMS + 1)),
    )


# ---- the accountability table --------------------------------------------------------


def expected_slug(origin: str, task_type: str, category: str, qualifier: str) -> str:
    if origin == "definition":
        return "product_analyst"
    if origin == "design":
        return "ux_designer" if qualifier == "ux" else "ui_designer"
    if origin == "copy_review" and category == "copy-criteria-written":
        return "tech_writer"
    if origin == "review" and category in PLANNER:
        return "tech_lead_platform" if task_type == "platform_enhancement" else "tech_lead"
    return "developer_integrator" if qualifier == "integration" else "developer"


def test_the_table_has_a_row_for_every_origin_task_type_category_and_qualifier():
    # review, final_validation, definition, design, copy_review
    per_type = len(CATEGORIES) * 2 * 2 + 1 + 2 + 3 * 2
    assert len(ACCOUNTABLE) == len(TASK_TYPES) * per_type
    assert {key[2] for key in ACCOUNTABLE if key[0] == "review"} == set(CATEGORIES)
    assert {key[0] for key in ACCOUNTABLE} == {
        "review",
        "final_validation",
        "definition",
        "design",
        "copy_review",
    }


@pytest.mark.parametrize(("key", "slug"), sorted(ACCOUNTABLE.items()))
def test_each_accountable_row_names_its_role(key: tuple[str, str, str, str], slug: str) -> None:
    origin, task_type, category, qualifier = key
    assert slug == expected_slug(origin, task_type, category, qualifier)
    assert attribute(origin, task_type, category, qualifier) == slug


@pytest.mark.parametrize(
    ("task_type", "category", "integration"),
    sorted((t, c, i) for t in TASK_TYPES for c in CATEGORIES for i in (False, True)),
)
def test_accountable_reads_a_review_return_through_the_table(
    task_type: str, category: str, integration: bool
) -> None:
    subtask = "integration" if integration else "s1"
    expected = expected_slug("review", task_type, category, "integration" if integration else "")
    assert accountable(entry(category, subtask), task_type) == expected


def test_every_accountable_slug_is_a_role():
    assert PLAYBOOK_ROLES == frozenset(ACCOUNTABLE.values())
    assert PLAYBOOK_ROLES <= {role.slug for role in roles.ROLES}


def test_the_review_pair_is_the_one_the_roles_module_writes():
    assert roles.pair_for("s1").key == f"{REVIEW_PAIR}:s1"


def test_a_definition_return_is_the_product_analysts():
    returned = {"pair": "PM→Product Analyst", "category": "definition"}
    assert accountable(returned, "feature") is None  # `accountable` is the review pair's
    assert return_backing(returned, "feature") == Backing("return", "product_analyst", "definition")


@pytest.mark.parametrize(
    ("pair", "slug"),
    [("UX Lead→UX Designer", "ux_designer"), ("UI Lead→UI Designer", "ui_designer")],
)
def test_a_lead_return_is_its_designers_and_the_pairs_are_the_roles_modules(
    pair: str, slug: str
) -> None:
    backing = return_backing({"pair": pair, "category": "design"}, "feature")
    assert backing == Backing("return", slug, "design")
    assert pair in (roles.PAIR_UX_LEAD.key, roles.PAIR_UI_LEAD.key)
    assert roles.PAIR_PM_PA.key == "PM→Product Analyst"


def test_a_landing_return_is_unattributable_whatever_the_pair():
    assert accountable({**entry("spec-scope"), "by": "landing"}, "feature") is None


def test_an_unknown_task_type_or_pair_is_unattributable():
    assert accountable(entry("spec-scope"), "chore") is None
    other = {"pair": "PM→Tech Lead", "category": "spec-behaviour"}
    assert accountable(other, "feature") is None
    assert accountable({"category": "spec-scope"}, "feature") is None


def test_a_review_pair_without_a_subtask_suffix_is_not_integration():
    assert accountable(entry("spec-scope", None), "bug") == "developer"


# ---- text rules ----------------------------------------------------------------------


def test_a_clean_lesson_has_no_refusal():
    assert lesson_refusal(LESSON) is None
    assert lesson_refusal("x" * 240) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "x" * 241,
        "two\nlines",
        "carriage\rreturn",
        "use `ruff` first",
        "fill {{placeholder}} early",
        "see http://example.com",
        "see https://example.com",
    ],
)
def test_each_forbidden_text_form_is_refused(text: str) -> None:
    assert lesson_refusal(text) is not None
    result = run([add(text)])
    assert result.playbook.items == ()
    assert [r.delta.lesson for r in result.refused] == [text]


def test_normalise_lowercases_and_collapses_whitespace():
    assert normalise("  Run   The\tSuite ") == "run the suite"


# ---- apply: add ----------------------------------------------------------------------


def test_an_add_creates_an_item_with_a_deterministic_id_and_provenance():
    result = run([add(LESSON, "GH-2"), add("Commit before you deliver.")])
    assert [i.id for i in result.playbook.items] == ["developer-1", "developer-2"]
    first = result.playbook.items[0]
    assert (first.lesson, first.category, first.since, first.seen_in, first.added_at) == (
        LESSON,
        "process-suite-red",
        "abc123",
        ("GH-2",),
        NOW,
    )
    assert first.seen == 1
    assert len(result.applied) == 2 and result.refused == ()


def test_apply_never_mutates_the_playbook_it_is_given():
    start = Playbook(slug="developer", items=(item(),))
    result = run([add("Another lesson.")], start)
    assert start.items == (item(),)
    assert result.playbook is not start


def test_an_add_whose_text_is_already_there_becomes_a_bump():
    start = Playbook(slug="developer", items=(item(),))
    again = "  RUN the whole  suite before delivering, never only the file you touched."
    result = run([add(again, "GH-2")], start)
    assert len(result.playbook.items) == 1
    assert result.playbook.items[0].seen_in == ("GH-1", "GH-2")
    assert result.playbook.items[0].lesson == LESSON
    assert [(d.op, d.item_id) for d in result.applied] == [("bump", "developer-1")]


def test_a_dedupe_bump_goes_through_even_when_the_playbook_is_full():
    full = Playbook(slug="developer", items=(*full_playbook().items[:-1], item(12, LESSON)))
    result = run([add(LESSON, "GH-2")], full)
    assert result.refused == ()
    assert result.playbook.items[-1].seen_in == ("GH-1", "GH-2")


def test_a_task_cited_twice_grows_seen_once():
    result = run([add(LESSON, "GH-1", "GH-1")])
    assert result.playbook.items[0].seen == 1
    again = run([add(LESSON, "GH-1")], result.playbook)
    assert again.playbook.items[0].seen == 1
    assert again.applied == ()
    assert "already counted" in again.refused[0].reason


def test_retiring_frees_room_for_a_later_add_in_the_same_batch():
    retire = Delta(op="retire", slug="developer", item_id="developer-3")
    result = run([retire, add("One more distinct lesson.")], full_playbook())
    assert len(result.playbook.items) == PLAYBOOK_MAX_ITEMS
    assert "developer-3" not in {i.id for i in result.playbook.items}
    assert result.refused == ()


def test_an_add_citing_a_task_that_is_not_closed_is_refused():
    ev = {"GH-1": evidence("process-suite-red", closed=False)}
    result = run([add()], ev=ev)
    assert result.playbook.items == ()
    assert "not closed" in result.refused[0].reason


def test_an_add_citing_a_task_without_evidence_is_refused():
    result = run([add(LESSON, "GH-404")])
    assert result.playbook.items == ()
    assert "GH-404" in result.refused[0].reason


def test_an_add_with_no_cited_task_is_refused():
    result = run(
        [Delta(op="add", slug="developer", category="spec-scope", lesson="Stay in scope.")]
    )
    assert result.playbook.items == ()
    assert len(result.refused) == 1


def test_an_add_with_a_category_outside_the_closed_set_is_refused():
    ev = {"GH-1": evidence("made-up")}
    result = run([add(category="made-up")], ev=ev)
    assert result.playbook.items == ()
    assert "category" in result.refused[0].reason


def test_an_add_whose_category_is_not_in_the_cited_return_history_is_refused():
    result = run([add(category="spec-scope", lesson="Stay inside the brief.")])
    assert result.playbook.items == ()
    assert "backing" in result.refused[0].reason


def test_an_add_for_a_role_other_than_the_accountable_one_is_refused():
    # The category is in the history, but it belongs to the integrator, not `developer`.
    ev = {"GH-1": Evidence(True, "feature", (entry("process-suite-red", "integration"),))}
    result = run([add()], ev=ev)
    assert result.playbook.items == ()
    integrator = run([add(slug="developer_integrator")], empty("developer_integrator"), ev=ev)
    assert len(integrator.playbook.items) == 1
    assert integrator.playbook.items[0].id == "developer_integrator-1"


def test_a_definition_return_never_backs_an_add():
    returned = {"pair": "PM→Product Analyst", "category": "definition"}
    ev = {"GH-1": Evidence(True, "feature", (returned,))}
    result = run([add(category="definition")], ev=ev)
    assert result.playbook.items == ()


def test_a_rejected_lesson_for_the_same_slug_and_category_is_refused():
    rejected = (Rejected("developer", "process-suite-red", normalise(LESSON)),)
    result = run([add(LESSON.upper())], rejected=rejected)
    assert result.playbook.items == ()
    assert "rejected" in result.refused[0].reason


def test_a_rejection_for_another_slug_or_category_does_not_block():
    rejected = (
        Rejected("tech_lead", "process-suite-red", normalise(LESSON)),
        Rejected("developer", "spec-scope", normalise(LESSON)),
    )
    assert len(run([add()], rejected=rejected).playbook.items) == 1


def test_a_delta_for_another_slug_is_refused():
    result = run([add(slug="tech_lead")])
    assert result.playbook.items == ()
    assert "slug" in result.refused[0].reason


def test_one_refusal_does_not_stop_the_rest_of_the_batch():
    result = run([add("two\nlines"), add("A fine lesson.")])
    assert [i.lesson for i in result.playbook.items] == ["A fine lesson."]
    assert len(result.refused) == 1 and len(result.applied) == 1


# ---- apply: merge, bump, retire ------------------------------------------------------


def test_merge_replaces_the_lesson_of_a_never_landed_item_and_unions_seen_in():
    other = item(2, "Untouched lesson.")
    never_landed = replace(item(), last_applied=NOW)
    start = Playbook(slug="developer", items=(never_landed, other))
    merge = Delta(
        op="merge",
        slug="developer",
        item_id="developer-1",
        lesson="Run every check before delivering.",
        tasks=("GH-2", "GH-1"),
    )
    result = run([merge], start)
    merged, untouched = result.playbook.items
    assert merged.lesson == "Run every check before delivering."
    assert merged.seen_in == ("GH-1", "GH-2")
    assert (merged.id, merged.since, merged.added_at) == ("developer-1", "empty", NOW)
    assert untouched == other


def test_merge_into_a_legacy_item_keeps_its_wording_and_unions_seen_in():
    start = Playbook(slug="developer", items=(item(),))
    merge = Delta(
        op="merge", slug="developer", item_id="developer-1", lesson="Reworded.", tasks=("GH-2",)
    )
    merged = run([merge], start).playbook.items[0]
    assert merged.lesson == LESSON
    assert merged.seen_in == ("GH-1", "GH-2")


def test_merge_rechecks_the_text_rules():
    start = Playbook(slug="developer", items=(replace(item(), last_applied=NOW),))
    merge = Delta(op="merge", slug="developer", item_id="developer-1", lesson="a\nb")
    result = run([merge], start)
    assert result.playbook == start
    assert len(result.refused) == 1


def test_merge_into_a_rejected_text_or_another_items_text_is_refused():
    other = item(2, "Untouched lesson.")
    start = Playbook(slug="developer", items=(replace(item(), last_applied=NOW), other))
    rejected = (Rejected("developer", "process-suite-red", normalise("Banned text.")),)
    banned = Delta(op="merge", slug="developer", item_id="developer-1", lesson="Banned text.")
    twin = Delta(op="merge", slug="developer", item_id="developer-1", lesson="untouched  lesson.")
    result = run([banned, twin], start, rejected=rejected)
    assert result.playbook == start
    assert len(result.refused) == 2


def test_merge_citing_a_new_open_task_is_refused():
    start = Playbook(slug="developer", items=(item(),))
    ev = {**EVIDENCE, "GH-3": evidence("process-suite-red", closed=False)}
    merge = Delta(
        op="merge", slug="developer", item_id="developer-1", lesson="New.", tasks=("GH-3",)
    )
    result = run([merge], start, ev=ev)
    assert result.playbook == start
    assert "not closed" in result.refused[0].reason


def test_bump_appends_only_new_task_ids():
    start = Playbook(slug="developer", items=(item(),))
    bump = Delta(op="bump", slug="developer", item_id="developer-1", tasks=("GH-1", "GH-2"))
    result = run([bump], start)
    assert result.playbook.items[0].seen_in == ("GH-1", "GH-2")
    assert result.playbook.items[0].seen == 2


def test_bump_citing_evidence_without_the_item_category_is_refused():
    start = Playbook(slug="developer", items=(item(),))
    ev = {"GH-9": evidence("spec-scope")}
    bump = Delta(op="bump", slug="developer", item_id="developer-1", tasks=("GH-9",))
    result = run([bump], start, ev=ev)
    assert result.playbook == start
    assert "backing" in result.refused[0].reason


def test_bump_with_a_category_that_is_not_the_items_is_refused():
    start = Playbook(slug="developer", items=(item(),))
    bump = Delta(
        op="bump", slug="developer", item_id="developer-1", category="spec-scope", tasks=("GH-2",)
    )
    assert run([bump], start).playbook == start


def test_retire_removes_only_that_item():
    start = Playbook(slug="developer", items=(item(1), item(2, "Second."), item(3, "Third.")))
    result = run([Delta(op="retire", slug="developer", item_id="developer-2")], start)
    assert [i.id for i in result.playbook.items] == ["developer-1", "developer-3"]
    assert len(result.applied) == 1


@pytest.mark.parametrize(
    "delta",
    [
        Delta(op="merge", slug="developer", item_id="developer-9", lesson="x."),
        Delta(op="bump", slug="developer", item_id="developer-9", tasks=("GH-1",)),
        Delta(op="retire", slug="developer", item_id="developer-9"),
    ],
)
def test_an_unknown_item_id_is_refused(delta: Delta) -> None:
    start = Playbook(slug="developer", items=(item(),))
    result = run([delta], start)
    assert result.playbook == start
    assert "developer-9" in result.refused[0].reason


# ---- section, parse, render_file -----------------------------------------------------


def test_the_section_of_an_empty_playbook_is_the_empty_string():
    assert section(empty()) == ""


def test_the_section_lists_one_line_per_item_in_id_order():
    pb = Playbook(slug="developer", items=(item(10, "Tenth."), item(2, "Second.")))
    assert (
        section(pb) == "### Playbook\n- Second. (process-suite-red)\n- Tenth. (process-suite-red)"
    )


def test_parse_of_empty_text_is_an_empty_playbook():
    assert parse("", "developer") == empty()
    assert parse("  \n", "developer") == empty()


def test_render_file_then_parse_is_the_identity():
    pb = Playbook(
        slug="developer", items=(item(1), item(2, "Second: with a colon.", ("GH-1", "GH-2")))
    )
    text = render_file(pb)
    assert parse(text, "developer") == pb
    assert render_file(parse(text, "developer")) == text
    assert parse(render_file(empty()), "developer") == empty()


def _file(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "id": "developer-1",
        "lesson": LESSON,
        "category": "process-suite-red",
        "since": "empty",
        "seen_in": ["GH-1"],
        "added_at": NOW,
    }
    body.update(overrides)
    lines = ["items:", f"- id: {body['id']}"]
    lines += [f"  {k}: {v!r}" for k, v in body.items() if k != "id"]
    return "\n".join(lines) + "\n"


def test_parse_accepts_a_hand_written_file():
    assert parse(_file(), "developer").items == (item(),)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("- a\n- b\n", "mapping"),
        ("items: []\nextra: 1\n", "extra"),
        ("items: nope\n", "items"),
        (_file(bogus=1), "bogus"),
        (_file(lesson="use `x`"), "backtick"),
        (_file(category="made-up"), "category"),
        (_file(seen_in="GH-1"), "seen_in"),
        (_file() + _file().split("\n", 1)[1], "duplicate"),
        ("items:\n- id: developer-1\n", "missing"),
    ],
)
def test_parse_names_the_file_problem(text: str, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        parse(text, "developer")


def test_parse_refuses_an_item_that_breaks_the_text_rules_naming_the_id():
    with pytest.raises(ValueError, match="developer-1"):
        parse(_file(lesson="x" * 241), "developer")


# ---- keep and archive_due (GH-296) ---------------------------------------------------


def test_the_thresholds_are_the_named_ones():
    assert (KEEP_CONFIDENCE, KEEP_MIN_TASKS, ARCHIVE_AFTER) == ("high", 2, 20)


@pytest.mark.parametrize(
    ("confidence", "seen", "sources", "kept"),
    [
        ("high", 1, {"finding"}, True),
        ("medium", 1, {"finding"}, False),
        ("low", 1, {"return"}, False),
        ("medium", 2, {"finding"}, True),
        ("low", 2, {"return", "finding"}, True),
        ("high", 1, {"human_decision", "finding"}, True),
        # every source a human decision: one task is never enough, whatever the confidence
        ("high", 1, {"human_decision"}, False),
        ("high", 2, {"human_decision"}, True),
        ("low", 2, {"human_decision"}, True),
    ],
)
def test_keep_is_confidence_or_two_tasks_and_a_lone_human_decision_needs_two(
    confidence: Confidence, seen: int, sources: set[str], kept: bool
) -> None:
    assert keep(confidence, seen, frozenset(sources)) is kept


def test_archive_due_has_its_edges_at_the_threshold_and_never_stale_without_a_prompt_date():
    aged = replace(item(1), in_prompt_since="2026-09-01")
    assert archive_due(aged, ARCHIVE_AFTER - 1) is None
    assert archive_due(aged, ARCHIVE_AFTER) == ARCHIVE_STALE
    assert archive_due(aged, None) is None  # no count: it does not age
    # never in a prompt (its PR did not merge): `added_at` is not a clock
    assert archive_due(item(1), ARCHIVE_AFTER * 3) is None


@pytest.mark.parametrize(
    ("merged_at", "due"),
    [("2026-08-31", True), ("2026-09-01", True), ("2026-09-02", False)],
)
def test_only_a_merge_after_the_day_the_item_entered_the_prompt_backs_it(merged_at, due):
    aged = replace(item(1), in_prompt_since="2026-09-01T10:00:00.000+00:00", last_applied=merged_at)
    assert archive_due(aged, ARCHIVE_AFTER * 2) == (ARCHIVE_STALE if due else None)


@pytest.mark.parametrize(
    ("helpful", "harmful", "cause"),
    [(0, 2, ARCHIVE_SCORE), (0, 5, ARCHIVE_SCORE), (0, 1, None), (3, 0, None)],
)
def test_archive_due_by_score_for_any_item_whatever_its_age(helpful, harmful, cause):
    fresh = replace(item(1), helpful=helpful, harmful=harmful)  # never in a prompt
    assert archive_due(fresh, None) == cause
    assert archive_due(replace(fresh, in_prompt_since="2026-09-01"), 1) == cause


# ---- score, the counters and the new keys ------------------------------------------


def test_a_file_with_the_new_keys_round_trips_and_one_without_them_stays_byte_identical():
    text = (
        "items:\n- id: developer-1\n  lesson: Run the suite.\n  category: process-suite-red\n"
        "  since: empty\n  seen_in:\n  - GH-1\n  added_at: '2026-10-05'\n"
        "  last_applied: '2026-10-20'\n  in_prompt_since: '2026-10-10T00:00:00.000+00:00'\n"
        "  helpful: 3\n  harmful: 1\n  scored_until: 2026-10-21T00:00:00.000+00:00|GH-7\n"
    )
    book = parse(text, "developer")
    assert (book.items[0].helpful, book.items[0].harmful) == (3, 1)
    assert render_file(book) == text
    zeros = text.replace("helpful: 3", "helpful: 0").replace("harmful: 1", "harmful: 0")
    bare = zeros.replace("  helpful: 0\n  harmful: 0\n", "")
    assert render_file(parse(zeros, "developer")) == bare


@pytest.mark.parametrize(
    "overrides",
    [
        {"helpful": True},
        {"harmful": -1},
        {"helpful": "1"},
        {"harmful": 1.0},
        {"scored_until": "no-separator"},
        {"scored_until": 5},
    ],
)
def test_parse_item_rejects_a_counter_or_a_mark_of_the_wrong_kind(overrides):
    with pytest.raises(ValueError, match="developer-1"):
        parse(_file(**overrides), "developer")


def test_the_score_starts_at_two_and_combines_the_counters():
    assert score(item()) == 2
    assert score(replace(item(), helpful=3, harmful=1)) == 4


BEFORE = "2026-10-01T00:00:00.000+00:00"
AFTER = "2026-10-02T00:00:00.000+00:00"


def lesson_book(*categories: str) -> Playbook:
    items = tuple(
        replace(item(n, f"Lesson {n}.", ()), category=c, in_prompt_since=BEFORE)
        for n, c in enumerate(categories, 1)
    )
    return Playbook("developer", items)


def task(
    n: int = 1,
    returned: tuple[str, ...] = (),
    *,
    ended: str | None = None,
    first_run_at: str = AFTER,
    playbook_changed: bool = False,
) -> ScoredTask:
    return ScoredTask(
        f"GH-{n}",
        f"2026-10-{n + 2:02d}T00:00:00.000+00:00" if ended is None else ended,
        first_run_at,
        playbook_changed,
        frozenset(returned),
    )


def counters(scored: Scored) -> list[tuple[int, int]]:
    return [(i.helpful, i.harmful) for i in scored.playbook.items]


def test_an_exclusive_lesson_with_no_return_in_its_category_is_helpful():
    scored = score_task(lesson_book("spec-scope"), task())
    assert counters(scored) == [(1, 0)] and scored.unattributed is False


def test_an_exclusive_lesson_whose_category_came_back_is_harmful():
    scored = score_task(lesson_book("spec-scope"), task(returned=("spec-scope",)))
    assert counters(scored) == [(0, 1)] and scored.unattributed is False


def test_a_category_two_lessons_share_moves_no_counter_and_is_unattributed_once():
    book = lesson_book("spec-scope", "spec-scope", "design")
    scored = score_task(book, task(returned=("spec-scope",)))
    assert counters(scored) == [(0, 0), (0, 0), (1, 0)]
    assert scored.unattributed is True


def test_a_returned_category_no_lesson_covers_moves_nothing():
    scored = score_task(lesson_book("design"), task(returned=("spec-scope",)))
    assert counters(scored) == [(1, 0)] and scored.unattributed is False


@pytest.mark.parametrize(
    "case",
    [
        {"first_run_at": BEFORE},  # ran before the lesson reached a prompt
        {"playbook_changed": True},
    ],
)
def test_a_task_the_lesson_was_not_in_the_prompt_of_or_that_changed_the_playbook_counts_nothing(
    case,
):
    book = lesson_book("spec-scope")
    assert score_task(book, task(**case)).playbook == book
    never = Playbook("developer", (replace(book.items[0], in_prompt_since=""),))
    assert score_task(never, task()).playbook == never


def test_a_task_without_an_end_scores_nothing_and_moves_no_mark():
    book = lesson_book("spec-scope", "spec-scope")
    assert score_task(book, task(ended="")) == Scored(book, False)


def test_helpful_stops_at_the_cap_and_five_exclusive_returns_archive_the_lesson():
    book = lesson_book("spec-scope")
    for n in range(1, 21):
        book = score_task(book, task(n)).playbook
    assert (book.items[0].helpful, score(book.items[0])) == (HELPFUL_CAP, 5)
    for n in range(21, 24):
        book = score_task(book, task(n, ("spec-scope",))).playbook
    assert score(book.items[0]) == 2 and archive_due(book.items[0], None) is None
    for n in range(24, 26):
        book = score_task(book, task(n, ("spec-scope",))).playbook
    assert score(book.items[0]) == 0
    kept, shelf = archive_stale(book, (), {}, now=NOW)
    assert kept.items == () and [a.reason for a in shelf] == [ARCHIVE_SCORE]


def test_the_same_task_twice_counts_once_and_an_older_task_after_a_newer_one_is_ignored():
    book = lesson_book("spec-scope")
    once = score_task(book, task(5)).playbook
    assert score_task(once, task(5)).playbook == once
    assert score_task(once, task(3)).playbook == once
    assert once.items[0].scored_until == "2026-10-07T00:00:00.000+00:00|GH-5"


def test_two_tasks_that_ended_together_both_count_once_in_either_order():
    ended = "2026-10-05T00:00:00.000+00:00"
    first, second = task(1, ended=ended), task(2, ended=ended)
    book = lesson_book("spec-scope")
    both = score_task(score_task(book, first).playbook, second).playbook
    assert counters(Scored(both, False)) == [(2, 0)]
    again = score_task(both, first).playbook
    assert again == both and score_task(both, second).playbook == both
    # the pair is ordered by id, so the other order loses the earlier id, never counts it twice
    reverse = score_task(score_task(book, second).playbook, first).playbook
    assert counters(Scored(reverse, False)) == [(1, 0)]


def test_returned_categories_are_the_returns_attributable_to_the_slug_not_only_reviews():
    entries = [
        entry("spec-scope", None),
        entry("process-suite-red"),
        {"pair": "x", "category": "y"},
    ]
    assert returned_categories(entries, "feature", "developer") == {
        "spec-scope",
        "process-suite-red",
    }
    assert returned_categories(entries, "feature", "product_analyst") == frozenset()
    definition = {"pair": "PM→Product Analyst", "category": "spec-scope", "reason": "r"}
    assert returned_categories([definition], "feature", "product_analyst") == {"definition"}


# ---- evidence from the four sources ----------------------------------------------------

DECISION = "Spanish quotes in the docs are a follow-up, never part of this task."
DLESSON = "Treat Spanish quotes in docs as a follow-up, not as part of the task."


def decided(
    slug: str = "developer", ref: str = "1", text: str = DECISION, ran: frozenset[str] | None = None
) -> Evidence:
    return Evidence(
        True,
        "feature",
        backings=(Backing("human_decision", slug, "human-decision", ref),),
        decisions={int(ref): text},
        ran=frozenset({slug}) if ran is None else ran,
    )


def human_add(
    lesson: str = DLESSON, decision: int = 1, tasks: tuple[str, ...] = ("GH-1",)
) -> Delta:
    return Delta(
        op="add",
        slug="developer",
        category="human-decision",
        lesson=lesson,
        tasks=tasks,
        decision=decision,
        confidence="high",
    )


def test_a_human_decision_lesson_needs_two_tasks_even_with_high_confidence():
    ev = {"GH-1": decided(), "GH-2": decided()}
    first = run([human_add()], ev=ev)
    assert first.playbook.items == ()
    assert [d.lesson for d in first.held] == [DLESSON]
    assert first.candidates[0].seen_in == ("GH-1",)
    second = run([human_add(tasks=("GH-2",))], ev=ev, candidates=first.candidates)
    # promoted, keeping the task the candidate already had; recorded as an add
    assert [i.seen_in for i in second.playbook.items] == [("GH-1", "GH-2")]
    assert [d.op for d in second.applied] == ["add"]
    assert second.candidates == ()


@pytest.mark.parametrize(
    ("make", "why"),
    [
        (lambda: decided(ref="2"), "does not cite"),
        (lambda: decided(ran=frozenset()), "did not run"),
        (
            lambda: decided(text="Postpone the migration until the schema freezes."),
            "shares no term",
        ),
    ],
)
def test_an_invented_human_decision_is_refused(make, why: str) -> None:
    result = run([human_add()], ev={"GH-1": make()})
    assert result.playbook.items == () and result.candidates == ()
    assert why in result.refused[0].reason


def test_a_decision_the_task_does_not_carry_is_refused():
    only_backing = Evidence(
        True,
        "feature",
        backings=(Backing("human_decision", "developer", "human-decision", "3"),),
        ran=frozenset({"developer"}),
    )
    result = run([human_add(decision=3)], ev={"GH-1": only_backing})
    assert "does not exist" in result.refused[0].reason


@pytest.mark.parametrize("source", ["finding", "design", "return"])
def test_a_citation_without_a_backing_is_refused_for_every_source(source) -> None:
    category = "design" if source == "design" else "spec-scope"
    slug = "ux_designer" if source == "design" else "developer"
    backed = Evidence(True, "feature", backings=(Backing(source, slug, category),))
    ok = run(
        [Delta("add", slug, category, LESSON, tasks=("GH-1",))], empty(slug), ev={"GH-1": backed}
    )
    assert [d.category for d in ok.applied] == [category]
    other = Evidence(True, "feature", backings=(Backing(source, slug, "spec-behaviour"),))
    bad = run(
        [Delta("add", slug, category, LESSON, tasks=("GH-1",))], empty(slug), ev={"GH-1": other}
    )
    assert bad.playbook.items == () and "backing" in bad.refused[0].reason
    unknown = run([Delta("add", slug, category, LESSON, tasks=("GH-404",))], empty(slug), ev={})
    assert "no evidence" in unknown.refused[0].reason


def test_a_low_confidence_lesson_waits_as_a_candidate_and_two_tasks_keep_it():
    ev = {"GH-1": evidence("process-suite-red"), "GH-2": evidence("process-suite-red")}
    low = replace(add(LESSON, "GH-1"), confidence="low")
    first = run([low], ev=ev)
    assert first.playbook.items == () and first.held == (low,)
    again = run([replace(low, tasks=("GH-1",))], ev=ev, candidates=first.candidates)
    assert "already counts" in again.refused[0].reason
    second = run([replace(low, tasks=("GH-2",))], ev=ev, candidates=first.candidates)
    assert second.playbook.items[0].seen_in == ("GH-1", "GH-2")
    assert second.playbook.items[0].id == "developer-1"


def test_an_add_that_matches_an_archived_item_restores_it_with_its_id_and_seen_in():
    old = replace(item(4, LESSON, ("GH-1", "GH-2")), in_prompt_since="2026-09-01")
    shelf = (Archived(old, "stale", "2026-10-01"),)
    ev = {"GH-3": evidence("process-suite-red")}
    result = run([add(LESSON, "GH-3")], ev=ev, archived=shelf)
    restored = result.playbook.items[0]
    assert (restored.id, restored.seen_in, restored.in_prompt_since) == (
        "developer-4",
        ("GH-1", "GH-2", "GH-3"),
        "",
    )
    assert [d.op for d in result.applied] == ["add"] and result.archived == ()
    # a new lesson never reuses an archived id
    fresh = run([add("Another lesson entirely.", "GH-3")], ev=ev, archived=shelf)
    assert fresh.playbook.items[0].id == "developer-5"


def test_archive_stale_moves_only_the_due_items_with_their_cause_and_deletes_nothing():
    aged = replace(item(1, "Lesson one."), in_prompt_since="2026-09-01")
    young = replace(item(2, "Lesson two."), in_prompt_since="2026-10-01")
    scored = replace(item(4, "Lesson four."), harmful=2)  # never in a prompt, score 0
    book = Playbook("developer", (aged, young, item(3, "Lesson three."), scored))
    aging = {"developer-1": ARCHIVE_AFTER, "developer-2": ARCHIVE_AFTER - 1, "developer-3": 99}
    kept, shelf = archive_stale(book, (), aging, now=NOW)
    assert [i.id for i in kept.items] == ["developer-2", "developer-3"]
    assert [(a.item, a.reason, a.archived_at) for a in shelf] == [
        (aged, ARCHIVE_STALE, NOW),
        (scored, ARCHIVE_SCORE, NOW),
    ]


def scored_out(**counts: int) -> tuple[Archived, ...]:
    old = replace(
        item(4, LESSON, ("GH-1", "GH-2")),
        in_prompt_since="2026-09-01",
        scored_until="x|GH-9",
        **counts,
    )
    return (Archived(old, ARCHIVE_SCORE, "2026-10-01"),)


def test_a_score_archived_lesson_with_one_new_task_and_low_confidence_stays_a_candidate():
    ev = {"GH-3": evidence("process-suite-red")}
    low = replace(add(LESSON, "GH-3"), confidence="low")
    result = run([low], ev=ev, archived=scored_out(harmful=2))
    assert result.playbook.items == () and result.held == (low,)
    assert [c.seen_in for c in result.candidates] == [("GH-3",)]
    assert len(result.archived) == 1  # still on the shelf, no new id was made


def test_a_task_the_archived_lesson_already_has_is_not_new_evidence():
    ev = {"GH-1": evidence("process-suite-red")}
    result = run([add(LESSON, "GH-1")], ev=ev, archived=scored_out(harmful=2))
    assert result.playbook.items == () and result.candidates == ()
    assert "already counted" in result.refused[0].reason


def test_two_new_tasks_restore_a_score_archived_lesson_with_its_id_and_the_counters_reset():
    ev = {"GH-3": evidence("process-suite-red"), "GH-4": evidence("process-suite-red")}
    low = replace(add(LESSON, "GH-3", "GH-4"), confidence="low")
    result = run([low], ev=ev, archived=scored_out(helpful=1, harmful=2))
    restored = result.playbook.items[0]
    assert (restored.id, restored.seen_in) == ("developer-4", ("GH-1", "GH-2", "GH-3", "GH-4"))
    assert (restored.helpful, restored.harmful, restored.scored_until) == (0, 0, "")
    assert restored.in_prompt_since == "" and result.archived == ()


def test_the_waiting_candidates_tasks_count_as_new_evidence_and_the_candidate_is_consumed():
    ev = {"GH-3": evidence("process-suite-red"), "GH-4": evidence("process-suite-red")}
    low = replace(add(LESSON, "GH-3"), confidence="low")
    first = run([low], ev=ev, archived=scored_out(harmful=2))
    second = run(
        [replace(low, tasks=("GH-4",))], ev=ev, candidates=first.candidates, archived=scored_out()
    )
    assert second.playbook.items[0].seen_in == ("GH-1", "GH-2", "GH-3", "GH-4")
    assert second.candidates == ()


def test_a_waiting_candidates_tasks_the_archived_lesson_already_has_are_not_new_evidence():
    ev = {"GH-1": evidence("process-suite-red"), "GH-3": evidence("process-suite-red")}
    waiting = Pending("developer", "process-suite-red", LESSON, "low", ("GH-1",), ("return",), NOW)
    low = replace(add(LESSON, "GH-3"), confidence="low")
    result = run([low], ev=ev, candidates=(waiting,), archived=scored_out(harmful=2))
    assert result.playbook.items == () and len(result.archived) == 1
    assert [c.seen_in for c in result.candidates] == [("GH-3",)]  # GH-1 is not new


def test_a_score_restore_into_a_playbook_in_grace_at_the_cap_is_held_and_stays_archived():
    full = full_playbook()
    ev = {"GH-3": evidence("process-suite-red")}
    result = run([add(LESSON, "GH-3")], full, ev=ev, archived=scored_out(harmful=2))
    assert result.playbook == full and result.applied == ()
    assert len(result.capped) == 1 and len(result.archived) == 1
    assert result.archived[0].item.harmful == 2  # nothing was reset: it did not come back


def test_high_confidence_restores_a_score_archived_lesson_on_one_new_task():
    result = run(
        [add(LESSON, "GH-3")],
        ev={"GH-3": evidence("process-suite-red")},
        archived=scored_out(harmful=2),
    )
    assert result.playbook.items[0].id == "developer-4" and result.candidates == ()


def test_a_stale_archived_lesson_restores_directly_on_one_task_with_the_counters_reset():
    old = replace(item(4, LESSON, ("GH-1",)), harmful=1, scored_until="x|GH-9")
    shelf = (Archived(old, ARCHIVE_STALE, "2026-10-01"),)
    low = replace(add(LESSON, "GH-3"), confidence="low")
    result = run([low], ev={"GH-3": evidence("process-suite-red")}, archived=shelf)
    restored = result.playbook.items[0]
    assert (restored.id, restored.harmful, restored.scored_until) == ("developer-4", 0, "")


def test_a_playbook_file_of_today_reads_the_same_and_the_new_keys_are_optional():
    today = (
        "items:\n- id: developer-1\n  lesson: Run the suite.\n  category: process-suite-red\n"
        "  since: empty\n  seen_in:\n  - GH-1\n  added_at: '2026-10-05'\n"
    )
    book = parse(today, "developer")
    assert book.items[0].last_applied == "" and book.items[0].in_prompt_since == ""
    assert render_file(book) == today
    stamped = replace(book.items[0], last_applied="2026-10-20", in_prompt_since="2026-10-10")
    assert parse(render_file(Playbook("developer", (stamped,))), "developer").items == (stamped,)
    design = today.replace("process-suite-red", "design")
    assert parse(design, "developer").items[0].category == "design"
    with pytest.raises(ValueError):
        parse(today.replace("process-suite-red", "vibes"), "developer")


def test_shared_term_ignores_trivial_short_and_accented_words():
    assert shared_term("Avoid migrations before schema freeze", "The schema is frozen") is True
    assert shared_term("Valida la decisión", "una decisión humana") is False  # trivial only
    assert shared_term("Usa comillas españolas", "Las COMILLAS españolas son seguimiento") is True
    assert shared_term("Do it now", "Do it later") is False  # under five characters


def test_padding_a_lone_human_decision_with_an_unbacked_task_does_not_keep_it():
    ev = {"GH-1": decided(), "GH-2": Evidence(True, "feature", ran=frozenset({"developer"}))}
    result = run([human_add(tasks=("GH-1", "GH-2"))], ev=ev)
    assert result.playbook.items == ()
    assert [d.lesson for d in result.held] == [DLESSON]
    assert result.candidates[0].seen_in == ("GH-1",)


def test_a_restore_needs_the_same_slug_and_category_as_the_archived_item():
    other = replace(item(4, LESSON, ("GH-1",)), category="spec-scope")
    shelf = (Archived(other, "stale", "2026-10-01"),)
    result = run([add(LESSON, "GH-1")], ev={"GH-1": evidence("process-suite-red")}, archived=shelf)
    assert [i.id for i in result.playbook.items] == ["developer-5"]
    assert result.archived == shelf


# ---- the cap, grace and demotion pick (GH-881 part 1) ---------------------------------


def aged(n: int, since: str = "", **extra: Any) -> Item:
    return replace(item(n, f"Lesson number {n}."), in_prompt_since=since, **extra)


def test_a_file_without_the_new_keys_round_trips_byte_identical_and_empty_has_no_section():
    text = render_file(Playbook("developer", (item(1), item(2, "Another lesson."))))
    assert render_file(parse(text, "developer")) == text
    assert "demoted_at_seen" not in text
    assert section(empty()) == ""
    assert render_file(empty()) == render_file(parse(render_file(empty()), "developer"))


def test_over_cap_on_the_item_axis_is_more_than_the_maximum():
    assert PLAYBOOK_MAX_ITEMS == 15
    assert not over_cap(full_playbook())
    over = Playbook("developer", (*full_playbook().items, item(99, "One more lesson.")))
    assert over_cap(over)


def test_over_cap_on_the_character_axis_with_items_built_directly_not_through_validation():
    # A hand-edited file can hold lessons longer than LESSON_MAX_CHARS; the section is the cap.
    long = "x" * 1500
    few = Playbook("developer", tuple(item(n, f"{n}{long}") for n in range(1, 4)))
    assert len(section(few)) <= PLAYBOOK_MAX_CHARS
    assert not over_cap(few)
    many = Playbook("developer", tuple(item(n, f"{n}{long}") for n in range(1, 6)))
    assert len(many.items) <= PLAYBOOK_MAX_ITEMS
    assert len(section(many)) > PLAYBOOK_MAX_CHARS
    assert over_cap(many)


@pytest.mark.parametrize(
    ("since", "counts", "expected"),
    [
        ("", {"developer-1": 99}, True),  # never landed
        ("2026-10-01", {}, True),  # id missing from the map
        ("2026-10-01", {"developer-1": GRACE_TASKS - 1}, True),
        ("2026-10-01", {"developer-1": GRACE_TASKS}, False),
    ],
)
def test_in_grace(since, counts, expected):
    assert in_grace(aged(1, since), counts) is expected


def test_the_grace_period_is_five_tasks():
    assert GRACE_TASKS == 5


def test_demotion_pick_takes_the_lowest_score_among_items_out_of_grace():
    pb_ = Playbook(
        "developer", (aged(1, "2026-10-01"), aged(2, "2026-10-01"), aged(3, "2026-10-01"))
    )
    counts = {"developer-1": 9, "developer-2": 9, "developer-3": 9}
    scores = {"developer-1": 3, "developer-2": 1, "developer-3": 2}
    assert demotion_pick(pb_, counts, lambda i: scores[i.id]) == "developer-2"


def test_demotion_pick_never_picks_an_item_in_grace_even_with_the_lowest_score():
    pb_ = Playbook("developer", (aged(1, "2026-10-01"), aged(2, "2026-10-01")))
    counts = {"developer-1": 9, "developer-2": 1}
    assert demotion_pick(pb_, counts, lambda i: 0 if i.id == "developer-2" else 5) == "developer-1"


def test_without_a_score_every_item_scores_equal_and_the_oldest_in_prompt_since_goes():
    pb_ = Playbook(
        "developer", (aged(1, "2026-10-05"), aged(2, "2026-10-01"), aged(3, "2026-10-03"))
    )
    counts = {i.id: 9 for i in pb_.items}
    assert demotion_pick(pb_, counts) == "developer-2"


def test_ties_break_by_the_id_number_not_by_string_order():
    pb_ = Playbook("developer", (aged(10, "2026-10-01"), aged(2, "2026-10-01")))
    counts = {i.id: 9 for i in pb_.items}
    assert demotion_pick(pb_, counts) == "developer-2"


def test_demotion_pick_is_none_when_every_item_is_in_grace():
    pb_ = Playbook("developer", (aged(1, "2026-10-01"), aged(2)))
    assert demotion_pick(pb_, {"developer-1": 2}) is None
    assert demotion_pick(empty(), {}) is None


def test_demote_makes_a_low_candidate_that_remembers_how_many_tasks_had_cited_it():
    old = aged(4, "2026-10-01", seen_in=("GH-1", "GH-2", "GH-3"), last_applied="x")
    pending = demote(old, "developer")
    assert pending == Pending(
        slug="developer",
        category=old.category,
        lesson=old.lesson,
        confidence="low",
        seen_in=old.seen_in,
        sources=(),
        added_at=old.added_at,
        demoted_at_seen=3,
    )


def test_keep_refuses_a_demoted_lesson_until_one_more_task_cites_it():
    assert not keep("high", 3, ("finding",), demoted_at_seen=3)
    assert not keep("high", 2, ("finding",), demoted_at_seen=3)
    assert keep("high", 4, ("finding",), demoted_at_seen=3)
    assert keep("low", 4, ("finding",), demoted_at_seen=3)  # four tasks pass the plain rule
    assert not keep("low", 1, ("finding",), demoted_at_seen=0)  # one task, low: today's rule
    assert keep("high", 1, ("finding",), demoted_at_seen=0)


@pytest.mark.parametrize(
    ("confidence", "seen", "sources"),
    [("high", 1, ()), ("low", 1, ()), ("low", 2, ("finding",)), ("high", 1, ("human_decision",))],
)
def test_keep_without_demoted_at_seen_is_unchanged(confidence, seen, sources):
    plain = keep(confidence, seen, sources)
    assert keep(confidence, seen, sources, demoted_at_seen=None) is plain


# ---- apply: the cap demotes instead of refusing -------------------------------------


def seasoned(ids: range) -> Playbook:
    """Items that reached a prompt on rising days: the lowest id is the oldest."""
    items = tuple(
        replace(item(n, f"Lesson number {n}."), in_prompt_since=f"2026-09-{n:02d}") for n in ids
    )
    return Playbook("developer", items)


def ran(book: Playbook) -> dict[str, int]:
    return {i.id: GRACE_TASKS + 4 for i in book.items}


def test_an_add_at_the_cap_demotes_the_lesson_the_pick_names_and_records_why():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    new = add("One more distinct lesson.")
    result = run([new], book, counts=ran(book))
    assert result.refused == () and result.held == () and result.capped == ()
    assert result.applied == (new,)
    assert len(result.playbook.items) == PLAYBOOK_MAX_ITEMS
    ids = {i.id for i in result.playbook.items}
    assert "developer-1" not in ids and "developer-16" in ids
    assert result.demoted == (Demotion("developer-1", new),)
    gone = result.candidates[0]
    assert (gone.lesson, gone.confidence, gone.seen_in, gone.demoted_at_seen) == (
        "Lesson number 1.",
        "low",
        ("GH-1",),
        1,
    )


def test_the_cap_with_every_lesson_in_grace_holds_the_add_and_leaves_the_playbook_intact():
    full = full_playbook()
    new = add("One more distinct lesson.")
    result = run([new], full)
    assert result.playbook == full
    assert result.applied == () and result.refused == () and result.demoted == ()
    assert result.held == (new,) and result.capped == (new,)
    assert [(p.lesson, p.demoted_at_seen) for p in result.candidates] == [
        ("One more distinct lesson.", None)
    ]


def test_a_demotion_that_cannot_make_the_playbook_fit_is_discarded_whole():
    # Two over the cap, only one lesson out of grace: demoting it is not enough.
    over = Playbook(
        "developer",
        (
            *(item(n, f"Lesson number {n}.") for n in range(1, PLAYBOOK_MAX_ITEMS + 1)),
            replace(item(30, "Old lesson."), in_prompt_since="2026-09-01"),
        ),
    )
    result = run([add("One more distinct lesson.")], over, counts={"developer-30": 9})
    assert result.playbook == over and result.demoted == ()
    assert len(result.capped) == 1 and len(result.candidates) == 1


def test_the_loop_demotes_as_many_lessons_as_it_takes_to_fit():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 3))  # a hand-edited file, two over
    new = add("One more distinct lesson.")
    result = run([new], book, counts=ran(book))
    assert [d.item_id for d in result.demoted] == ["developer-1", "developer-2", "developer-3"]
    assert {d.by for d in result.demoted} == {new}
    assert len(result.playbook.items) == PLAYBOOK_MAX_ITEMS
    assert not over_cap(result.playbook)


def test_the_new_lesson_is_in_grace_so_it_is_never_its_own_victim():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    result = run([add("One more distinct lesson.")], book, counts=ran(book))
    assert "One more distinct lesson." in {i.lesson for i in result.playbook.items}


def test_the_score_decides_before_the_age():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    result = apply(
        book,
        [add("One more distinct lesson.")],
        EVIDENCE,
        (),
        section_hash="abc123",
        now=NOW,
        role_tasks_since=ran(book),
        score=lambda i: 0 if i.id == "developer-9" else 5,
    )
    assert [d.item_id for d in result.demoted] == ["developer-9"]


def archived_lesson(n: int) -> tuple[Archived, ...]:
    old = replace(item(n, LESSON, ("GH-1", "GH-2")), in_prompt_since="2026-08-01")
    return (Archived(old, "stale", "2026-10-01"),)


def test_a_restore_at_the_cap_demotes_another_lesson_and_lists_it():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    ev = {"GH-3": evidence("process-suite-red")}
    new = add(LESSON, "GH-3")
    result = run([new], book, ev=ev, archived=archived_lesson(40), counts=ran(book))
    assert result.applied == (new,) and result.archived == ()
    assert result.demoted == (Demotion("developer-1", new),)
    restored = next(i for i in result.playbook.items if i.id == "developer-40")
    assert restored.seen_in == ("GH-1", "GH-2", "GH-3") and restored.in_prompt_since == ""
    assert len(result.playbook.items) == PLAYBOOK_MAX_ITEMS


def test_a_restore_that_cannot_fit_leaves_the_playbook_and_the_archive_identical():
    full = full_playbook()
    shelf = archived_lesson(40)
    ev = {"GH-3": evidence("process-suite-red")}
    new = add(LESSON, "GH-3")
    result = run([new], full, ev=ev, archived=shelf)
    assert result.playbook == full and result.archived == shelf
    assert result.applied == () and result.demoted == ()
    assert result.held == (new,) and result.capped == (new,)
    assert [p.lesson for p in result.candidates] == [LESSON]


def test_a_new_id_never_reuses_the_number_of_a_lesson_demoted_earlier_in_the_apply():
    # developer-16 is the oldest: the restore of developer-1 demotes it, and the add after
    # it must number from 17, not from the 15 left in the playbook.
    book = seasoned(range(2, PLAYBOOK_MAX_ITEMS + 2))
    book = replace(
        book,
        items=tuple(
            replace(i, in_prompt_since="2026-08-01") if i.id == "developer-16" else i
            for i in book.items
        ),
    )
    ev = {"GH-3": evidence("process-suite-red")}
    restore, fresh = add(LESSON, "GH-3"), add("One more distinct lesson.", "GH-3")
    result = run([restore, fresh], book, ev=ev, archived=archived_lesson(1), counts=ran(book))
    assert result.demoted[0] == Demotion("developer-16", restore)
    assert "developer-17" in {i.id for i in result.playbook.items}
    assert "developer-16" not in {i.id for i in result.playbook.items}


def test_two_adds_at_the_cap_in_one_apply_take_two_ids_and_apply_in_delta_order():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    first, second = add("First new lesson."), add("Second new lesson.")
    result = run([first, second], book, counts=ran(book))
    assert result.applied == (first, second)
    assert [d.by for d in result.demoted] == [first, second]
    assert [d.item_id for d in result.demoted] == ["developer-1", "developer-2"]
    new = {
        i.lesson: i.id for i in result.playbook.items if i.id in ("developer-16", "developer-17")
    }
    assert new == {"First new lesson.": "developer-16", "Second new lesson.": "developer-17"}


def test_a_demoted_lesson_folds_into_its_twin_candidate():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    twin = Pending(
        "developer",
        "process-suite-red",
        "  LESSON number 1.",
        "medium",
        ("GH-9",),
        ("finding",),
        NOW,
        demoted_at_seen=0,
    )
    result = run([add("One more distinct lesson.")], book, candidates=(twin,), counts=ran(book))
    assert len(result.candidates) == 1
    merged = result.candidates[0]
    assert merged.lesson == twin.lesson and merged.confidence == "medium"
    assert merged.sources == ("finding",) and merged.seen_in == ("GH-9", "GH-1")
    assert merged.demoted_at_seen == 1


def test_running_again_over_the_output_of_a_demotion_changes_nothing():
    book = seasoned(range(1, PLAYBOOK_MAX_ITEMS + 1))
    new = add("One more distinct lesson.")
    first = run([new], book, counts=ran(book))
    again = run(
        [new],
        first.playbook,
        candidates=first.candidates,
        archived=first.archived,
        counts=ran(first.playbook),
    )
    assert "nothing to bump" in again.refused[0].reason
    assert again.applied == again.held == again.capped == again.demoted == ()
    assert again.playbook == first.playbook and again.candidates == first.candidates


def test_a_held_back_add_twice_is_refused_the_second_time_and_records_no_new_cap():
    full = full_playbook()
    new = add("One more distinct lesson.")
    first = run([new], full)
    again = run([new], full, candidates=first.candidates)
    assert "nothing to add" in again.refused[0].reason
    assert again.applied == again.held == again.capped == again.demoted == ()
    assert again.candidates == first.candidates


def test_a_demoted_lesson_waits_for_a_task_it_had_not_counted_then_returns():
    ev = {"GH-1": evidence("process-suite-red"), "GH-2": evidence("process-suite-red")}
    gone = Pending(
        "developer", "process-suite-red", LESSON, "low", ("GH-1",), (), NOW, demoted_at_seen=1
    )
    same_task = run([add(LESSON, "GH-1")], ev=ev, candidates=(gone,))
    assert "nothing to add" in same_task.refused[0].reason
    back = run([add(LESSON, "GH-2")], ev=ev, candidates=(gone,))
    assert back.playbook.items[0].seen_in == ("GH-1", "GH-2") and back.candidates == ()
    stays = replace(gone, demoted_at_seen=2, seen_in=("GH-1", "GH-2"))
    held = run([add(LESSON, "GH-2")], ev=ev, candidates=(replace(stays, seen_in=("GH-1",)),))
    assert held.playbook.items == () and held.capped == ()
    assert held.candidates[0].demoted_at_seen == 2 and held.candidates[0].seen_in == (
        "GH-1",
        "GH-2",
    )
