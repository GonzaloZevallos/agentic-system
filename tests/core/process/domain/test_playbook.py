from typing import Any, get_args

import pytest

from agentic.core.process.domain import roles
from agentic.core.process.domain.playbook import (
    ACCOUNTABLE,
    ARCHIVE_AFTER,
    KEEP_CONFIDENCE,
    KEEP_MIN_TASKS,
    PLAYBOOK_MAX_ITEMS,
    PLAYBOOK_ROLES,
    REVIEW_PAIR,
    Applied,
    Backing,
    Confidence,
    Delta,
    Evidence,
    Item,
    Playbook,
    Rejected,
    accountable,
    apply,
    attribute,
    keep,
    lesson_refusal,
    normalise,
    parse,
    render_file,
    return_backing,
    section,
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
) -> Applied:
    return apply(
        playbook or empty(),
        deltas,
        ev or EVIDENCE,
        rejected,
        section_hash="abc123",
        now=NOW,
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
    if origin == "review" and category in PLANNER:
        return "tech_lead_platform" if task_type == "platform_enhancement" else "tech_lead"
    return "developer_integrator" if qualifier == "integration" else "developer"


def test_the_table_has_a_row_for_every_origin_task_type_category_and_qualifier():
    per_type = len(CATEGORIES) * 2 * 2 + 1 + 2  # review, final_validation, definition, design
    assert len(ACCOUNTABLE) == len(TASK_TYPES) * per_type
    assert {key[2] for key in ACCOUNTABLE if key[0] == "review"} == set(CATEGORIES)
    assert {key[0] for key in ACCOUNTABLE} == {"review", "final_validation", "definition", "design"}


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
    assert accountable(returned, "feature") == "product_analyst"
    assert return_backing(returned, "feature") == Backing("return", "product_analyst", "definition")


@pytest.mark.parametrize(
    ("pair", "slug"),
    [("UX Lead→UX Designer", "ux_designer"), ("UI Lead→UI Designer", "ui_designer")],
)
def test_a_lead_return_is_its_designers_and_the_pairs_are_the_roles_modules(
    pair: str, slug: str
) -> None:
    assert accountable({"pair": pair, "category": "design"}, "feature") == slug
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


def test_the_cap_refuses_an_add_with_merge_or_retire_first():
    full = full_playbook()
    result = run([add("One more distinct lesson.")], full)
    assert len(result.playbook.items) == PLAYBOOK_MAX_ITEMS
    assert "merge or retire first" in result.refused[0].reason


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


def test_merge_replaces_the_lesson_and_unions_seen_in_leaving_the_rest_alone():
    other = item(2, "Untouched lesson.")
    start = Playbook(slug="developer", items=(item(), other))
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


def test_merge_rechecks_the_text_rules():
    start = Playbook(slug="developer", items=(item(),))
    merge = Delta(op="merge", slug="developer", item_id="developer-1", lesson="a\nb")
    result = run([merge], start)
    assert result.playbook == start
    assert len(result.refused) == 1


def test_merge_into_a_rejected_text_or_another_items_text_is_refused():
    other = item(2, "Untouched lesson.")
    start = Playbook(slug="developer", items=(item(), other))
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
