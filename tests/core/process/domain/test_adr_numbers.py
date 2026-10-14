"""`adr_numbers` (GH-229, ADR-0048): the pure rules of an ADR renumber. Each case is a set of
paths or a line and the answer it must give, rejecting inputs next to accepting ones
(ADR-0023); no git, no files."""

import pytest

from agentic.core.process.domain import adr_numbers
from agentic.core.process.domain.adr_numbers import Collision, collision, own_adrs
from agentic.core.process.domain.changes import ChangedPath


def adr(number: int, slug: str = "x") -> str:
    return f"docs/adr/{number:04d}-{slug}.md"


@pytest.mark.parametrize(
    ("path", "number", "slug"),
    [
        ("docs/adr/0048-renumera.md", 48, "renumera"),
        ("docs/adr/0001-a-b-c.md", 1, "a-b-c"),
        ("docs/adr/0048-x.y.md", 48, "x.y"),
        # Not an ADR: another directory, a nested one, a short number, another extension.
        ("docs/adr/48-x.md", None, None),
        ("docs/adr/00480-x.md", None, None),
        ("docs/adr/0048-x.txt", None, None),
        ("docs/adr/sub/0048-x.md", None, None),
        ("other/docs/adr/0048-x.md", None, None),
        ("docs/adr/0048-.md", None, None),
        ("docs/adr/README.md", None, None),
    ],
)
def test_adr_number_and_slug_read_only_a_file_directly_under_the_directory(path, number, slug):
    assert adr_numbers.adr_number(path) == number
    assert adr_numbers.adr_slug(path) == slug


def test_own_adrs_are_those_at_head_and_absent_at_the_merge_base():
    mb = (adr(1), adr(2), "README.md")
    head = (adr(1), adr(2), adr(3, "own"), "README.md", "agentic/x.py")
    assert own_adrs(mb, head) == (adr(3, "own"),)


def test_an_adr_master_merged_into_the_branch_is_not_the_branchs_own():
    # `mb` is the merge-base with master, so what the branch merged in is already there.
    assert own_adrs((adr(1), adr(2, "merged")), (adr(1), adr(2, "merged"), adr(3))) == (adr(3),)


def test_a_branch_that_adds_no_adr_has_no_own_adr():
    assert own_adrs((adr(1),), (adr(1), "x.md")) == ()
    assert own_adrs((), ()) == ()


def test_own_adrs_come_in_number_order_and_ignore_non_adr_paths():
    head = (adr(9, "b"), adr(7, "a"), "docs/adr/notes.md", "docs/adr/sub/0001-x.md")
    assert own_adrs((), head) == (adr(7, "a"), adr(9, "b"))


def test_an_own_adr_whose_number_master_took_after_the_merge_base_collides():
    mb = (adr(36),)
    own = (adr(37, "mine"),)
    head = (adr(36), adr(37, "mine"))
    master = (adr(36), adr(37, "theirs"))
    assert collision(own, head, mb, master) == Collision(colliding=(adr(37, "mine"),))


def test_an_own_adr_whose_number_is_free_at_master_does_not_collide():
    mb = (adr(36),)
    own = (adr(37, "mine"),)
    assert collision(own, (adr(36), *own), mb, (adr(36), adr(38, "theirs"))) == Collision()


def test_only_the_colliding_ones_of_several_own_adrs_are_reported():
    own = (adr(37, "a"), adr(38, "b"))
    head = (adr(36), *own)
    master = (adr(36), adr(37, "theirs"))
    assert collision(own, head, (adr(36),), master).colliding == (adr(37, "a"),)


def test_two_files_at_head_with_one_number_are_a_duplicate_not_a_collision():
    own = (adr(37, "a"), adr(37, "b"))
    master = (adr(37, "theirs"),)
    assert collision(own, own, (), master) == Collision(duplicate=True)


def test_an_own_adr_sharing_a_number_with_a_file_the_branch_already_had_is_a_duplicate():
    mb = (adr(37, "old"),)
    own = (adr(37, "mine"),)
    assert collision(own, (*mb, *own), mb, mb).duplicate is True


def test_a_duplicate_that_master_already_had_and_the_branch_did_not_add_is_not_refused():
    mb = (adr(5, "a"), adr(5, "b"))
    assert collision((), mb, mb, mb) == Collision()


def test_no_own_adr_never_collides():
    assert collision((), (adr(1),), (adr(1),), (adr(1), adr(2))) == Collision()


def test_the_collision_value_is_false_only_when_empty():
    assert not Collision()
    assert Collision(duplicate=True)
    assert Collision(colliding=(adr(1),))


def test_the_new_number_is_one_past_master_and_keeps_the_slug_and_padding():
    plan = adr_numbers.renumber_plan((adr(37, "mine"),), (adr(37, "mine"),), (adr(36), adr(37)))
    assert plan == {adr(37, "mine"): adr(38, "mine")}


def test_the_new_number_clears_the_branchs_other_own_adrs():
    colliding = (adr(37, "a"),)
    own = (adr(37, "a"), adr(40, "keeps"))
    assert adr_numbers.renumber_plan(colliding, own, (adr(37),)) == {adr(37, "a"): adr(41, "a")}


def test_several_colliding_adrs_take_consecutive_numbers_in_their_own_order():
    colliding = (adr(37, "a"), adr(38, "b"))
    master = (adr(37, "t1"), adr(38, "t2"), adr(39, "t3"))
    assert adr_numbers.renumber_plan(colliding, colliding, master) == {
        adr(37, "a"): adr(40, "a"),
        adr(38, "b"): adr(41, "b"),
    }


def test_the_numbers_of_the_colliding_adrs_themselves_do_not_raise_the_floor():
    # 0099 is colliding and leaves; only master and the kept ones set the maximum.
    colliding = (adr(99, "a"),)
    assert adr_numbers.renumber_plan(colliding, colliding, (adr(99), adr(5))) == {
        adr(99, "a"): adr(100, "a")
    }


def test_nothing_colliding_makes_an_empty_plan():
    assert adr_numbers.renumber_plan((), (adr(1),), (adr(1),)) == {}


PLAN = {adr(48, "mi-decision"): adr(50, "mi-decision")}
OLD_FILE = adr(48, "mi-decision")


def rewrite(line, path="docs/process.md", *, whole=False, renamed=False, plan=None):
    return adr_numbers.rewrite_line(
        path, line, PLAN if plan is None else plan, added_whole=whole, is_renamed_file=renamed
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Shape 2: the file name with the exact slug, wherever it stands.
        ("see 0048-mi-decision.md", "see 0050-mi-decision.md"),
        ("[x](docs/adr/0048-mi-decision.md)", "[x](docs/adr/0050-mi-decision.md)"),
        ("[x](../adr/0048-mi-decision.md#why)", "[x](../adr/0050-mi-decision.md#why)"),
        # Shape 3 on an edited line: the bare token with the slug on the same line.
        ("ADR-0048 (0048-mi-decision) is why", "ADR-0050 (0048-mi-decision) is why"),
        ("ADR-0048 `mi-decision`", "ADR-0050 `mi-decision`"),
        # Both shapes on one line.
        ("ADR-0048: 0048-mi-decision.md", "ADR-0050: 0050-mi-decision.md"),
    ],
)
def test_rewrite_applies_the_file_name_and_the_bare_token_with_the_slug(line, expected):
    assert rewrite(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "ADR-0048 is cited without its slug",  # bare token, edited line: left for the human
        "ADR-00480 and 0048-mi-decision-more.md",  # longer number, longer slug
        "see ADR-10048",
        "0048-other-slug.md",  # master's ADR of that number, another slug
        "version 10048 and 00480",
        "nothing to do here",
        "adr-0048 in lowercase mi-decision",
    ],
)
def test_rewrite_leaves_a_line_it_has_no_shape_for(line):
    assert rewrite(line) == line


def test_rewrite_never_matches_inside_a_longer_number_even_with_the_slug():
    line = "ADR-00480 mi-decision and 10048-mi-decision.md and 0048-mi-decision.mdx"
    assert rewrite(line) == line


def test_a_file_added_whole_gets_its_bare_tokens_rewritten_without_the_slug():
    assert rewrite("ADR-0048 explains it", whole=True) == "ADR-0050 explains it"
    assert rewrite("ADR-00480 and ADR-0048.", whole=True) == "ADR-00480 and ADR-0050."


def test_the_same_bare_token_in_a_file_edited_not_added_is_left():
    assert rewrite("ADR-0048 explains it", whole=False) == "ADR-0048 explains it"


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_x.py",
        "tests/core/test_adr.py",
        "agentic/tests/test_x.py",
        "docs/tests/notes.md",
    ],
)
def test_a_line_under_a_tests_segment_is_never_rewritten(path):
    line = "ADR-0048 mi-decision 0048-mi-decision.md"
    assert rewrite(line, path, whole=True) == line


def test_a_file_named_like_tests_but_not_a_directory_segment_is_rewritten():
    assert rewrite("0048-mi-decision.md", "docs/tests.md") == "0050-mi-decision.md"
    assert rewrite("0048-mi-decision.md", "docs/latests/x.md") == "0050-mi-decision.md"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# 0048 Mi decisión", "# 0050 Mi decisión"),
        ("# ADR-0048: Mi decisión", "# ADR-0050: Mi decisión"),
        ("number: 0048", "number: 0050"),
        ("id: ADR-0048", "id: ADR-0050"),
        ("adr: 48", "adr: 50"),
        ("Compare [this](0048-mi-decision.md).", "Compare [this](0050-mi-decision.md)."),
        # A number that is not the file's own, or is longer, stays.
        ("# 00480 Mi decisión", "# 00480 Mi decisión"),
        ("number: 0049", "number: 0049"),
        ("status: accepted", "status: accepted"),
    ],
)
def test_the_renamed_file_gets_its_own_number_in_heading_frontmatter_and_links(line, expected):
    assert rewrite(line, OLD_FILE, whole=True, renamed=True) == expected


def test_the_own_number_outside_a_heading_or_frontmatter_key_is_not_a_shape_of_the_file():
    # Not whole and not renamed-file shape: a plain 0048 in prose is nobody's reference.
    line = "the year 0048 was long ago"
    assert rewrite(line, OLD_FILE, whole=False, renamed=True) == line


def test_a_rename_identified_by_its_new_path_is_recognised_too():
    new_file = adr(50, "mi-decision")
    assert rewrite("# 0048 Mi decisión", new_file, renamed=True) == "# 0050 Mi decisión"


def test_the_renamed_file_flag_does_not_extend_to_a_tests_path():
    assert rewrite("# 0048 x", "tests/" + OLD_FILE, whole=True, renamed=True) == "# 0048 x"


def test_each_old_number_goes_to_its_own_new_one_in_one_pass():
    plan = {adr(37, "a"): adr(39, "a"), adr(38, "b"): adr(40, "b")}
    line = "0037-a.md and 0038-b.md and ADR-0037 a, ADR-0038 b"
    assert rewrite(line, plan=plan) == "0039-a.md and 0040-b.md and ADR-0039 a, ADR-0040 b"


def test_an_empty_plan_rewrites_nothing():
    assert rewrite("ADR-0048 mi-decision 0048-mi-decision.md", plan={}, whole=True) == (
        "ADR-0048 mi-decision 0048-mi-decision.md"
    )


def test_unrewritten_lists_the_added_lines_that_still_name_an_old_number():
    lines = [
        ("docs/process.md", 3, "ADR-0048 stays"),
        ("docs/process.md", 4, "ADR-0050 is the new one"),
        ("tests/test_x.py", 9, "assert 'ADR-0048'"),
        ("docs/other.md", 1, "0048-other-slug.md"),
        ("docs/other.md", 2, "clean"),
        ("docs/other.md", 5, "ADR-00480 and 10048"),
    ]
    assert adr_numbers.unrewritten(lines, PLAN) == [
        "docs/process.md:3",
        "tests/test_x.py:9",
        "docs/other.md:1",
    ]


def test_unrewritten_is_empty_when_everything_was_rewritten_or_nothing_names_a_number():
    assert adr_numbers.unrewritten([], PLAN) == []
    assert adr_numbers.unrewritten([("a.md", 1, "ADR-0050")], PLAN) == []
    assert adr_numbers.unrewritten([("a.md", 1, "ADR-0048")], {}) == []


def test_a_line_rewritten_by_the_rules_is_no_longer_unrewritten():
    line = "ADR-0048 mi-decision, 0048-mi-decision.md"
    done = rewrite(line)
    assert adr_numbers.unrewritten([("docs/a.md", 1, line)], PLAN) == ["docs/a.md:1"]
    assert adr_numbers.unrewritten([("docs/a.md", 1, done)], PLAN) == []


def test_an_unpadded_number_is_the_files_own_only_in_a_number_key_not_in_a_heading():
    assert rewrite("# Fase 48 del plan", OLD_FILE, whole=True, renamed=True) == "# Fase 48 del plan"
    assert rewrite("adr: 48", OLD_FILE, whole=True, renamed=True) == "adr: 50"


def test_only_an_added_adr_starts_the_read():
    path = "docs/adr/0050-x.md"
    assert adr_numbers.adds_adr([ChangedPath("src/a.py", "A"), ChangedPath(path, "A")])
    for status in ("M", "D", "R", "C", "T"):
        assert not adr_numbers.adds_adr([ChangedPath(path, status)])
    assert not adr_numbers.adds_adr([ChangedPath("docs/adr/sub/0050-x.md", "A")])
    assert not adr_numbers.adds_adr([])
