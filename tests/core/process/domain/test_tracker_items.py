import hashlib
import subprocess
import sys

import pytest

from agentic.core.process.domain import rules, task, tracker_items

ITEM = {"kind": "human", "question": "Ship it?", "recommendation": "Yes, ship."}


WAY_OUT = "{kind: human, question, recommendation}"


def test_parse_accepts_a_valid_item_and_returns_a_new_dict_with_exactly_the_three_keys():
    raw = [dict(ITEM)]
    parsed = tracker_items.parse(raw)
    assert parsed == [ITEM]
    assert parsed[0] is not raw[0]


def test_parse_refuses_a_key_outside_the_three_and_names_it():
    with pytest.raises(ValueError, match="urgent") as error:
        tracker_items.parse([{**ITEM, "urgent": "yes"}])
    assert WAY_OUT in str(error.value)


def test_parse_names_the_extra_key_before_it_judges_the_kind():
    with pytest.raises(ValueError, match="urgent") as error:
        tracker_items.parse([{**ITEM, "kind": "close", "urgent": "yes"}])
    assert WAY_OUT in str(error.value)


def test_parse_returns_the_stripped_strings_so_the_hash_and_the_line_agree():
    padded = {"kind": "human", "question": "  Ship it?  ", "recommendation": " Yes, ship.\t"}
    assert tracker_items.parse([padded]) == [ITEM]


def test_the_limits_are_defined_here_once():
    assert tracker_items.MAX_ITEMS == 5
    assert tracker_items.MAX_FIELD_CHARS == 240


def _distinct(count: int) -> list[dict[str, str]]:
    return [{**ITEM, "question": f"Question {n}?"} for n in range(count)]


def test_parse_takes_five_items_and_refuses_six():
    assert len(tracker_items.parse(_distinct(tracker_items.MAX_ITEMS))) == 5
    with pytest.raises(ValueError, match="at most 5") as error:
        tracker_items.parse(_distinct(tracker_items.MAX_ITEMS + 1))
    assert WAY_OUT in str(error.value)


@pytest.mark.parametrize("field", ["question", "recommendation"])
def test_parse_takes_a_field_of_240_characters_after_strip_and_refuses_241(field):
    limit = tracker_items.MAX_FIELD_CHARS
    assert tracker_items.parse([{**ITEM, field: f"  {'x' * limit}  "}])[0][field] == "x" * limit
    with pytest.raises(ValueError, match="at most 240") as error:
        tracker_items.parse([{**ITEM, field: "x" * (limit + 1)}])
    assert field in str(error.value) and WAY_OUT in str(error.value)


@pytest.mark.parametrize("marker", ["<!--", "-->", "{{"])
@pytest.mark.parametrize("field", ["question", "recommendation"])
def test_parse_refuses_a_comment_or_template_marker(field, marker):
    with pytest.raises(ValueError, match="marker") as error:
        tracker_items.parse([{**ITEM, field: f"close it {marker} now"}])
    assert WAY_OUT in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "uses actions/checkout@v4",
        "bump pkg@1.2",
        "mail a@b.com",
        "x.@bob",  # passes on purpose: the pattern excludes a `.` before the `@`
        "an @ alone, and 3 @ 4",
    ],
)
def test_parse_lets_a_version_pin_an_email_and_a_bare_at_through(text):
    assert tracker_items.parse([{**ITEM, "question": text}])[0]["question"] == text


@pytest.mark.parametrize("text", ["@bob", "(@bob)", "x @bob", "ask @org/team", "a @Bob"])
@pytest.mark.parametrize("field", ["question", "recommendation"])
def test_parse_refuses_a_mention(field, text):
    with pytest.raises(ValueError, match="mention") as error:
        tracker_items.parse([{**ITEM, field: text}])
    assert WAY_OUT in str(error.value)


def test_parse_refuses_two_items_equal_after_strip_and_takes_ones_differing_in_the_advice():
    twin = {**ITEM, "question": f" {ITEM['question']} "}
    with pytest.raises(ValueError, match="twice") as error:
        tracker_items.parse([ITEM, twin])
    assert WAY_OUT in str(error.value)
    assert len(tracker_items.parse([ITEM, {**ITEM, "recommendation": "No."}])) == 2


def test_parse_judges_the_count_before_the_items_and_the_items_before_the_repeat():
    broken = {**ITEM, "question": "@bob"}
    with pytest.raises(ValueError, match="at most 5"):
        tracker_items.parse([broken] * 6)
    with pytest.raises(ValueError, match="mention"):
        tracker_items.parse([ITEM, broken, ITEM])


def test_kinds_holds_only_human_and_comment():
    assert tracker_items.KINDS == frozenset({"human", "comment"})


@pytest.mark.parametrize("kind", ["open_issue", "", None])
def test_parse_rejects_an_unknown_kind_and_says_to_write_it_as_human(kind):
    with pytest.raises(ValueError, match="human") as error:
        tracker_items.parse([{**ITEM, "kind": kind}])
    assert "{kind: human, question, recommendation}" in str(error.value)
    if kind:
        assert kind in str(error.value)


def test_parse_rejects_a_missing_kind():
    with pytest.raises(ValueError, match="human"):
        tracker_items.parse([{"question": "q", "recommendation": "r"}])


@pytest.mark.parametrize("missing", ["question", "recommendation"])
def test_parse_rejects_a_missing_field(missing):
    item = {k: v for k, v in ITEM.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        tracker_items.parse([item])


@pytest.mark.parametrize("field", ["question", "recommendation"])
@pytest.mark.parametrize("value", ["one\ntwo", "   ", ""])
def test_parse_rejects_a_field_that_is_not_one_line(field, value):
    with pytest.raises(ValueError, match="one non-empty line"):
        tracker_items.parse([{**ITEM, field: value}])


@pytest.mark.parametrize("raw", ["text", {"kind": "human"}, None, 3])
def test_parse_rejects_a_non_list(raw):
    with pytest.raises(ValueError, match="list"):
        tracker_items.parse(raw)


def test_parse_rejects_a_non_mapping_item():
    with pytest.raises(ValueError, match="human"):
        tracker_items.parse(["do the thing"])


def test_parse_of_an_empty_list_is_empty():
    assert tracker_items.parse([]) == []


def test_item_hash_is_pinned_and_covers_the_kind():
    assert tracker_items.item_hash(ITEM) == "28383afe2b80"
    assert tracker_items.item_hash(ITEM) == tracker_items.digest(
        ["human", "Ship it?", "Yes, ship."]
    )


def test_digest_gives_the_bytes_of_the_old_tracker_hash_formula():
    lines = ["  a ", "b", "c d"]
    expected = hashlib.sha256("\n".join(s.strip() for s in lines).encode()).hexdigest()[:12]
    assert tracker_items.digest(lines) == expected


OTHER = {"kind": "human", "question": "Rename it?", "recommendation": "No."}
AT = "2026-11-13T10:00:00+00:00"
H = tracker_items.item_hash(ITEM)
H2 = tracker_items.item_hash(OTHER)


def test_unanswered_counts_no_record_and_a_record_of_only_at_but_not_answered():
    items = [ITEM, OTHER]
    assert tracker_items.unanswered(items, {}) == items
    assert tracker_items.unanswered(items, {H: {"at": "t1"}}) == items
    assert tracker_items.unanswered(items, {H: {"at": "t1", "answered": "t2"}}) == [OTHER]


def test_question_line_is_the_question_a_dash_and_the_recommendation():
    assert tracker_items.question_line(ITEM) == "Ship it? — Recommended: Yes, ship."


def test_escalate_human_returns_nothing_with_no_items_all_answered_or_already_pending():
    assert tracker_items.escalate_human({}, AT) == {}
    answered = {"tracker_items": [ITEM], "tracker_actions_done": {H: {"at": "a", "answered": "b"}}}
    assert tracker_items.escalate_human(answered, AT) == {}
    pending = {"tracker_items": [ITEM], "tracker_human": [H]}
    assert tracker_items.escalate_human(pending, AT) == {}


def test_escalate_human_gives_the_whole_patch_for_one_item():
    patch = tracker_items.escalate_human({"tracker_items": [ITEM]}, AT)
    assert patch == {
        "status": "waiting_on_human",
        "escalated": "the plan leaves 1 decision(s) to the owner",
        "escalation_class": "questions",
        "escalation_cause": None,
        "tracker_human": [H],
        "questions_for_human": ["Ship it? — Recommended: Yes, ship."],
        "tracker_actions_done": {H: {"at": AT}},
        "pause_log": [{"kind": "escalation", "value": "questions", "at": AT}],
    }


def test_escalate_human_adds_only_the_new_item_and_keeps_the_pending_one():
    state = {"tracker_items": [ITEM, OTHER], "tracker_human": [H]}
    patch = tracker_items.escalate_human(state, AT)
    assert patch["tracker_human"] == [H, H2]
    assert patch["questions_for_human"] == [tracker_items.question_line(OTHER)]
    assert patch["tracker_actions_done"] == {H2: {"at": AT}}


def test_a_later_ci_escalation_keeps_the_items_pending_and_the_pause_a_humans():
    state = task.apply(
        {"tracker_items": [ITEM]}, tracker_items.escalate_human({"tracker_items": [ITEM]}, AT)
    )
    ci = rules.logged(rules.escalation("ci", "red", cause="ci_gh_unavailable"), at=AT)
    state = task.apply(state, ci)
    assert state["tracker_human"] == [H]
    assert rules.tracker_item_pause(state)
    assert rules.pause_kind(rules._current_epoch(state)) == "human"
    assert rules.auto_resumable(state) is None
    resumed = rules.resumed(state, "Ship it.", at="2026-11-13T11:00:00+00:00")
    state = task.apply(state, resumed)
    assert state["tracker_actions_done"][H] == {
        "at": AT,
        "answered": "2026-11-13T11:00:00+00:00",
    }
    assert tracker_items.escalate_human(state, AT) == {}


def test_importing_the_module_does_not_import_rules():
    code = (
        "import sys, agentic.core.process.domain.tracker_items;"
        "sys.exit('agentic.core.process.domain.rules' in sys.modules)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_a_reworded_item_is_asked_again_and_the_answered_one_is_not():
    """Accepted behaviour (GH-663): the hash covers the text, so a reworded item A' is a new
    item and is raised, while A stays answered. There is no fuzzy matching."""
    reworded = {**ITEM, "question": "Ship it now?"}
    state = {
        "tracker_items": [reworded],
        "tracker_actions_done": {H: {"at": "a", "answered": "b"}},
    }
    patch = tracker_items.escalate_human(state, AT)
    assert patch["tracker_human"] == [tracker_items.item_hash(reworded)]
    assert H not in patch["tracker_actions_done"]


def test_parse_names_an_unknown_key_before_an_unknown_kind_in_the_same_item():
    with pytest.raises(ValueError, match="urgent"):
        tracker_items.parse([{**ITEM, "kind": "comment", "urgent": "yes"}])


# --- GH-625 part 3a: one table of kinds, two filters, the other kinds' shapes ---------------

COMMENT = {"kind": "comment", "text": "Superseded by #12."}
OPEN_ISSUE = {"kind": "open_issue", "title": "Track the retry", "body": "Line one.\nLine two."}
MIXED = [ITEM, COMMENT, OPEN_ISSUE, {**ITEM, "question": "Rename it?"}]
EVERY_KIND = frozenset({"human", "comment", "open_issue"})


@pytest.fixture
def every_kind(monkeypatch):
    """The kinds the table knows, switched on: no kind but `human` is performed yet."""
    monkeypatch.setattr(tracker_items, "KINDS", EVERY_KIND)


@pytest.mark.parametrize(
    ("item", "expected"),
    [(ITEM, "28383afe2b80"), (COMMENT, "2ea9e9565f9e"), (OPEN_ISSUE, "50ec67333944")],
    ids=["human", "comment", "open_issue"],
)
def test_item_hash_is_pinned_for_every_kind(item, expected):
    assert tracker_items.item_hash(item) == expected


def test_parse_still_refuses_a_kind_that_is_not_performed_yet():
    with pytest.raises(ValueError, match="unknown tracker item kind") as error:
        tracker_items.parse([OPEN_ISSUE])
    assert "open_issue" in str(error.value) and WAY_OUT in str(error.value)


def test_human_items_and_system_items_split_a_mixed_list_and_keep_its_order():
    assert tracker_items.human_items(MIXED) == [MIXED[0], MIXED[3]]
    assert tracker_items.system_items(MIXED) == [COMMENT, OPEN_ISSUE]
    assert tracker_items.human_items([]) == [] and tracker_items.system_items([]) == []


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (ITEM, "human: Ship it?"),
        (COMMENT, "comment: Superseded by #12."),
        ({"kind": "comment", "text": "x" * 80}, f"comment: {'x' * 80}"),
        (
            {"kind": "comment", "text": f"{'x' * 40}\n{'y' * 40}"},
            f"comment: {'x' * 40} {'y' * 39}…",
        ),
        (OPEN_ISSUE, "open_issue: Track the retry"),
    ],
    ids=["human", "comment", "80 characters", "81 with a newline", "open_issue"],
)
def test_summary_line_is_the_kind_and_the_title_or_the_first_80_characters(item, expected):
    assert tracker_items.summary_line(item) == expected


def test_the_duplicate_error_names_the_item_by_its_summary():
    with pytest.raises(ValueError, match="twice") as error:
        tracker_items.parse([ITEM, ITEM])
    assert "'human: Ship it?'" in str(error.value)


def test_kinds_text_names_only_the_kinds_in_kinds_and_in_the_tables_order(monkeypatch):
    monkeypatch.setattr(tracker_items, "KINDS", frozenset({"human"}))
    sole = tracker_items.kinds_text()
    assert "and no other kind exists" in sole and "`comment`" not in sole
    monkeypatch.setattr(tracker_items, "KINDS", frozenset({"open_issue", "human"}))
    both = tracker_items.kinds_text()
    assert "no other kind exists" not in both
    assert both.index("`human`") < both.index("`open_issue`") and "`comment`" not in both


def test_a_several_lines_field_takes_lines_and_every_other_rule_still_holds(every_kind):
    assert tracker_items.parse([OPEN_ISSUE]) == [OPEN_ISSUE]
    body = tracker_items.MAX_BODY_CHARS
    assert tracker_items.parse([{**OPEN_ISSUE, "body": "x" * body}])[0]["body"] == "x" * body
    for bad, match in [
        ({**OPEN_ISSUE, "body": "x" * (body + 1)}, "at most 2000"),
        ({**OPEN_ISSUE, "body": "one\n<!-- two"}, "marker"),
        ({**OPEN_ISSUE, "body": "one\n{{two"}, "marker"),
        ({**OPEN_ISSUE, "body": "one\nask @bob"}, "mention"),
        ({**OPEN_ISSUE, "title": "one\ntwo"}, "one non-empty line"),
        ({**OPEN_ISSUE, "title": "x" * 241}, "at most 240"),
    ]:
        with pytest.raises(ValueError, match=match):
            tracker_items.parse([bad])


def test_the_keys_of_the_kind_are_the_only_ones_a_performed_kind_takes(every_kind):
    with pytest.raises(ValueError, match="no key question"):
        tracker_items.parse([{**COMMENT, "question": "q"}])
    with pytest.raises(ValueError, match="missing its body"):
        tracker_items.parse([{"kind": "open_issue", "title": "t"}])


# --- GH-689: the comment kind, the system-field rejections, marker, report, failure_block ----


def test_the_human_item_is_byte_for_byte_what_it_was_whatever_KINDS_holds(monkeypatch):
    """Invariant 1: pins `parse`, `item_hash` and `question_line` of a human item, under both
    the performed set of today and the single kind of before."""
    for kinds in (frozenset({"human"}), tracker_items.KINDS):
        monkeypatch.setattr(tracker_items, "KINDS", kinds)
        hard = {**ITEM, "question": "Close #12 as superseded? See https://x.io and www.y.io <b>"}
        assert tracker_items.parse([hard]) == [hard]
        assert tracker_items.parse([ITEM]) == [ITEM]
        assert tracker_items.item_hash(ITEM) == "28383afe2b80"
        assert tracker_items.question_line(ITEM) == "Ship it? — Recommended: Yes, ship."


NOTE = {"kind": "comment", "text": "Superseded by the newer item."}


def test_a_one_line_comment_parses_and_a_several_lines_one_does_not():
    assert tracker_items.parse([NOTE]) == [NOTE]
    with pytest.raises(ValueError, match="one non-empty line"):
        tracker_items.parse([{**COMMENT, "text": "a\nb"}])
    with pytest.raises(ValueError, match="at most 240"):
        tracker_items.parse([{**COMMENT, "text": "x" * 241}])


def _comment(text):
    return {"kind": "comment", "text": text}


@pytest.mark.parametrize(
    "text",
    [
        "see https://x",
        "see HTTP://x",
        "see git+ssh://x",
        "see www.x.io",
        "see (www.x.io)",
        "see ![a]",
        "see [a](b)",
        "see ](b)",
        "see <b>",
        "see </x",
        "see #12",
        "#12 first",
        "owner/repo#12",
        "a.b-c/d_e#3",
        "see GH-12",
        "see gh-7",
    ],
)
def test_a_comment_text_that_could_link_outside_the_issue_is_refused(text):
    with pytest.raises(ValueError) as error:
        tracker_items.parse([_comment(text)])
    assert WAY_OUT in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "uses actions/checkout@v4",
        "bump pkg@1.2",
        "issue 12",
        "a < b and 3 > 2",
        "the www is wide",
        "a&#12 entity-ish",
    ],
)
def test_a_comment_text_without_a_link_shape_passes(text):
    assert tracker_items.parse([_comment(text)])[0]["text"] == text


def test_the_link_rejections_apply_only_to_system_fields():
    with pytest.raises(ValueError, match="link|reference"):
        tracker_items.parse([_comment("see #12")])
    assert tracker_items.parse([{**ITEM, "recommendation": "Do #12, see GH-3"}])


def test_system_items_over_a_non_empty_parsed_list():
    parsed = tracker_items.parse([ITEM, NOTE, OTHER])
    assert tracker_items.system_items(parsed) == [NOTE]
    assert tracker_items.human_items(parsed) == [ITEM, OTHER]


def test_the_marker_names_the_task_and_the_item_hash():
    assert tracker_items.marker(COMMENT, "GH-7") == (
        f"<!-- agentic-item: GH-7 {tracker_items.item_hash(COMMENT)} -->"
    )


def test_the_two_prefixes_are_the_ones_the_resume_knows():
    assert tracker_items.PLAN_NOTE == "Plan note: "
    assert tracker_items.DONE == "Tracker work done by the system: "


def test_report_lists_only_the_system_items_with_a_record_and_a_url_only_when_it_has_one():
    other = _comment("Second note.")
    h1, h2 = tracker_items.item_hash(COMMENT), tracker_items.item_hash(other)
    items = [ITEM, COMMENT, other]
    done = {H: {"at": AT}, h1: {"at": AT, "kind": "comment"}}
    assert tracker_items.report(items, done) == "- comment posted on this issue"
    done[h2] = {"at": AT, "kind": "comment", "url": "https://x.io/c/1"}
    assert tracker_items.report(items, done) == (
        "- comment posted on this issue\n- comment posted on this issue (https://x.io/c/1)"
    )
    assert tracker_items.report(items, {H: {"at": AT}}) == ""
    assert tracker_items.report([], {}) == ""


def test_failure_block_is_capped_never_a_body_and_ends_with_the_pointer():
    many = [_comment(f"Note {n}.") for n in range(tracker_items.MAX_ITEMS)]
    block = tracker_items.failure_block(many, "ValueError", ".agentic/tasks/GH-7.md")
    lines = block.splitlines()
    assert "ValueError" in lines[0]
    assert lines[-1] == "The task document at `.agentic/tasks/GH-7.md` holds them whole."
    assert [line for line in lines if line.startswith("- ")] == [
        f"- {tracker_items.summary_line(item)}" for item in many
    ]
    long_item = _comment("x" * 200)
    block = tracker_items.failure_block([long_item], "OSError", "d.md")
    assert "x" * 200 not in block and "x" * 80 in block


def test_failure_block_holds_at_most_max_items_lines():
    seven = [_comment(f"Note {n}.") for n in range(7)]
    block = tracker_items.failure_block(seven, "ValueError", "d.md")
    assert len([line for line in block.splitlines() if line.startswith("- ")]) == 5


def test_escalate_human_under_a_spec_raises_instead_of_asking_the_owner():
    spec = {"path": "docs/specs/a.md"}
    with pytest.raises(ValueError, match="spec_cause"):
        tracker_items.escalate_human({"tracker_items": [ITEM], "spec": spec}, AT)


def test_escalate_human_with_the_graphs_empty_spec_is_the_patch_of_today():
    plain = tracker_items.escalate_human({"tracker_items": [ITEM]}, AT)
    assert tracker_items.escalate_human({"tracker_items": [ITEM], "spec": {}}, AT) == plain
    assert "spec_cause" not in plain
    assert plain["pause_log"] == [{"kind": "escalation", "value": "questions", "at": AT}]


def test_done_marker_names_the_task_and_the_digest_of_the_sorted_hashes():
    a, b = tracker_items.item_hash(COMMENT), tracker_items.item_hash(_comment("Second note."))
    assert tracker_items.done_marker("GH-7", [a, b]) == (
        f"<!-- agentic-done: GH-7 {tracker_items.digest(sorted([a, b]))} -->"
    )


def test_done_marker_does_not_depend_on_the_order_and_differs_for_another_set():
    a, b = tracker_items.item_hash(COMMENT), tracker_items.item_hash(_comment("Second note."))
    assert tracker_items.done_marker("GH-7", [a, b]) == tracker_items.done_marker("GH-7", [b, a])
    assert tracker_items.done_marker("GH-7", [a]) != tracker_items.done_marker("GH-7", [a, b])
    assert tracker_items.done_marker("GH-7", [a]) != tracker_items.done_marker("GH-8", [a])


# --- GH-690 domain: open_issue rules -------------------------------------------------------

LINKING_WORDS = ["Blocked by: #5", "Closes the loop", "Fixes it", "Resolves it"]


@pytest.mark.parametrize(
    "line",
    [
        *LINKING_WORDS,
        "   Blocked by: #5",
        "BLOCKED BY: #5",
        "closes it",
        "fixes it",
        "resolves it",
        "\tResolves it",
    ],
)
def test_open_issue_body_refuses_a_line_that_starts_with_a_linking_word(every_kind, line):
    with pytest.raises(ValueError, match="body") as error:
        tracker_items.parse([{**OPEN_ISSUE, "body": f"First line.\n{line}"}])
    assert WAY_OUT in str(error.value)


def test_open_issue_body_refuses_the_word_inside_a_code_fence(every_kind):
    body = "Text.\n```\nCloses nothing\n```"
    with pytest.raises(ValueError, match="Closes|closes"):
        tracker_items.parse([{**OPEN_ISSUE, "body": body}])


@pytest.mark.parametrize("separator", ["\x0b", "\x0c", "\x1c", "\x85", " ", " ", "\r"])
def test_open_issue_body_refuses_a_linking_line_after_any_line_boundary(every_kind, separator):
    with pytest.raises(ValueError, match="body"):
        tracker_items.parse([{**OPEN_ISSUE, "body": f"First.{separator}Blocked by: #5"}])


def test_open_issue_body_accepts_the_word_mid_line_or_inside_another_word(every_kind):
    body = "This is Blocked by nothing.\nIt closes nothing, it fixes nothing.\nResolved later."
    assert tracker_items.parse([{**OPEN_ISSUE, "body": body}])[0]["body"] == body


def test_the_linking_rule_leaves_the_one_line_title_and_the_comment_alone(every_kind):
    assert tracker_items.parse([{**OPEN_ISSUE, "title": "Closes the gap"}])
    assert tracker_items.parse([{"kind": "comment", "text": "Closes nothing."}])


def test_parse_refuses_two_open_issues_with_the_same_normalized_title(every_kind):
    twin = {**OPEN_ISSUE, "title": "Track  THE retry", "body": "Another body."}
    with pytest.raises(ValueError, match="same title") as error:
        tracker_items.parse([{**OPEN_ISSUE, "title": "track the Retry"}, twin])
    assert WAY_OUT in str(error.value)


def test_parse_refuses_a_repeated_item_before_it_judges_the_title(every_kind):
    with pytest.raises(ValueError, match="twice"):
        tracker_items.parse([OPEN_ISSUE, dict(OPEN_ISSUE)])


def test_triage_label_is_needs_triage():
    assert tracker_items.TRIAGE_LABEL == "needs-triage"


def test_a_comment_marker_keeps_its_bytes():
    # Computed on the code before `marker_key` existed.
    item = {"kind": "comment", "text": "Superseded by this issue."}
    assert tracker_items.marker(item, "GH-690") == "<!-- agentic-item: GH-690 af0ac5890018 -->"
    assert tracker_items.marker_key(item) == tracker_items.item_hash(item)


def test_an_open_issue_marker_follows_the_normalized_title_only(every_kind):
    first = {**OPEN_ISSUE, "title": "Track  the Retry"}
    reworded_body = {**OPEN_ISSUE, "title": "track the retry", "body": "Quite another body."}
    other = {**OPEN_ISSUE, "title": "Track the retries"}
    assert tracker_items.marker(first, "GH-1") == tracker_items.marker(reworded_body, "GH-1")
    assert tracker_items.marker(first, "GH-1") != tracker_items.marker(other, "GH-1")
    assert tracker_items.marker_key(first) == tracker_items.digest(["track the retry"])
    assert tracker_items.marker(first, "GH-1").endswith(f" {tracker_items.marker_key(first)} -->")


def test_report_renders_an_opened_issue_with_its_url():
    done = {tracker_items.item_hash(OPEN_ISSUE): {"kind": "open_issue", "url": "http://x/9"}}
    assert tracker_items.report([OPEN_ISSUE], done) == "- issue opened (http://x/9)"


def test_kinds_text_describes_open_issue_when_it_is_performed(every_kind):
    text = tracker_items.kinds_text()
    assert "{kind: open_issue, title, body}" in text
    assert "needs-triage" in text
    assert f"{tracker_items.MAX_BODY_CHARS} characters over several lines" in text


def test_the_plan_schema_describes_open_issue():
    from agentic.core.process.domain import schemas

    description = schemas.TechPlan.model_fields["tracker_items"].description or ""
    assert tracker_items.shape("open_issue") in description
    assert str(tracker_items.MAX_BODY_CHARS) in description
    assert tracker_items.TRIAGE_LABEL in description
