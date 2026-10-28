"""The ADR numbering rule (GH-229, ADR-0049): which of a branch's own ADRs collide with
the ones master already holds, and the one simultaneous rewrite that moves them and the
references to them. Pure: names and texts in, a mapping or a `Renaming` out."""

import re
from itertools import chain, combinations

import pytest

from agentic.core.process.domain import adr_numbers
from agentic.core.process.domain.adr_numbers import Ambiguous, OwnAdr, OwnLine, Renaming

ADR = "docs/adr/"


def _adr(number: str, slug: str = "x") -> str:
    return f"{number}-{slug}.md"


def test_number_of_reads_the_four_digit_prefix_of_an_adr_name_and_nothing_else():
    assert adr_numbers.number_of("0049-un-titulo.md") == "0049"
    assert adr_numbers.number_of("49-x.md") is None
    assert adr_numbers.number_of("0049.md") is None
    assert adr_numbers.number_of("00490-x.md") is None
    assert adr_numbers.number_of("0049-x.txt") is None
    assert adr_numbers.number_of("README.md") is None


def test_duplicate_numbers_lists_a_number_two_different_files_share():
    names = [_adr("0047"), _adr("0048", "a"), _adr("0048", "b"), "README.md"]
    assert adr_numbers.duplicate_numbers(names) == ["0048"]
    assert adr_numbers.duplicate_numbers([_adr("0047"), _adr("0048")]) == []


def test_a_branch_adr_whose_number_master_does_not_hold_is_no_collision():
    own = [OwnAdr(f"{ADR}{_adr('0049', 'mine')}", "c1")]
    assert adr_numbers.collisions([_adr("0047"), _adr("0048")], own) == {}


def test_a_branch_adr_whose_number_master_holds_moves_after_the_highest_number():
    own = [OwnAdr(f"{ADR}{_adr('0049', 'mine')}", "c1")]
    master = [_adr("0048"), _adr("0049", "theirs")]
    assert adr_numbers.collisions(master, own) == {
        f"{ADR}0049-mine.md": f"{ADR}0050-mine.md",
    }


def test_the_same_file_name_on_master_is_no_collision():
    own = [OwnAdr(f"{ADR}{_adr('0049', 'same')}", "c1")]
    assert adr_numbers.collisions([_adr("0049", "same")], own) == {}


def test_two_colliding_adrs_take_the_next_numbers_in_order_of_old_number_then_path():
    own = [
        OwnAdr(f"{ADR}{_adr('0050', 'b')}", "c2"),
        OwnAdr(f"{ADR}{_adr('0049', 'a')}", "c1"),
    ]
    master = [_adr("0049", "m"), _adr("0050", "n")]
    assert adr_numbers.collisions(master, own) == {
        f"{ADR}0049-a.md": f"{ADR}0051-a.md",
        f"{ADR}0050-b.md": f"{ADR}0052-b.md",
    }


def test_a_non_colliding_own_adr_counts_towards_where_the_new_numbers_start():
    own = [
        OwnAdr(f"{ADR}{_adr('0049', 'a')}", "c1"),
        OwnAdr(f"{ADR}{_adr('0050', 'b')}", "c2"),
    ]
    assert adr_numbers.collisions([_adr("0049", "m")], own) == {
        f"{ADR}0049-a.md": f"{ADR}0051-a.md",
    }


def test_the_same_path_added_twice_is_one_adr():
    own = [OwnAdr(f"{ADR}{_adr('0049', 'a')}", "c1"), OwnAdr(f"{ADR}{_adr('0049', 'a')}", "c2")]
    assert adr_numbers.collisions([_adr("0049", "m")], own) == {
        f"{ADR}0049-a.md": f"{ADR}0050-a.md",
    }


def test_two_own_adrs_with_the_same_number_are_ambiguous_even_without_a_collision():
    own = [OwnAdr(f"{ADR}{_adr('0049', 'a')}", "c1"), OwnAdr(f"{ADR}{_adr('0049', 'b')}", "c2")]
    result = adr_numbers.collisions([_adr("0048")], own)
    assert isinstance(result, Ambiguous)
    assert result.reason == "duplicate_own_number"
    assert "0049-a.md" in result.detail and "0049-b.md" in result.detail


def test_an_own_file_that_is_not_an_adr_name_is_ignored():
    own = [OwnAdr(f"{ADR}README.md", "c1")]
    assert adr_numbers.collisions([_adr("0049")], own) == {}


# -- the rewrite -------------------------------------------------------------------------

OLD = f"{ADR}0049-mine.md"
NEW = f"{ADR}0050-mine.md"


def _line(path: str, text: str, commit: str = "c1") -> OwnLine:
    return OwnLine(path, commit, text)


def _rewrite(moves, texts, lines, trees=None):
    return adr_numbers.renumber(moves, lines, trees or {}, texts)


def test_the_moved_file_and_a_full_file_name_reference_follow_the_number():
    texts = {
        OLD: (None, "# ADR-0049: mine\n\nbody\n"),
        "agentic/x.py": ("a = 1\n", "a = 1\n# see docs/adr/0049-mine.md\n"),
    }
    lines = [_line(OLD, "# ADR-0049: mine")]
    result = _rewrite({OLD: NEW}, texts, lines)
    assert result == Renaming(
        ((OLD, NEW),),
        {NEW: "# ADR-0050: mine\n\nbody\n", "agentic/x.py": "a = 1\n# see docs/adr/0050-mine.md\n"},
    )


def test_a_file_name_is_rewritten_with_or_without_its_directory_prefix_anywhere():
    texts = {
        OLD: (None, "x\n"),
        "docs/a.md": (
            "old 0049-mine.md stays?\n",
            "old 0049-mine.md stays?\n[l](0049-mine.md) [m](adr/0049-mine.md) "
            "docs/adr/0049-mine.md\n",
        ),
    }
    result = _rewrite({OLD: NEW}, texts, [])
    assert isinstance(result, Renaming)
    assert result.texts["docs/a.md"] == (
        "old 0050-mine.md stays?\n[l](0050-mine.md) [m](adr/0050-mine.md) docs/adr/0050-mine.md\n"
    )


def test_a_file_name_that_only_looks_alike_is_left_alone():
    texts = {OLD: (None, "x\n"), "a.md": ("", "x0049-mine.md and 0049-mineral.md\n")}
    result = _rewrite({OLD: NEW}, texts, [])
    assert isinstance(result, Renaming)
    assert "a.md" not in result.texts


def test_adr_tokens_are_rewritten_on_own_lines_only():
    texts = {
        OLD: (None, "x\n"),
        "docs/b.md": (
            "master cites ADR-0049 here\n",
            "master cites ADR-0049 here\nmine cites (ADR-0049) and ADR 0049, then\n",
        ),
    }
    lines = [_line("docs/b.md", "mine cites (ADR-0049) and ADR 0049, then")]
    result = _rewrite({OLD: NEW}, texts, lines)
    assert isinstance(result, Renaming)
    assert result.texts["docs/b.md"] == (
        "master cites ADR-0049 here\nmine cites (ADR-0050) and ADR 0050, then\n"
    )


def test_a_line_the_branch_added_that_master_already_has_is_not_own():
    texts = {OLD: (None, "x\n"), "docs/b.md": ("see ADR-0049\n", "see ADR-0049\n")}
    result = _rewrite({OLD: NEW}, texts, [_line("docs/b.md", "see ADR-0049")])
    assert result == Renaming(((OLD, NEW),), {NEW: "x\n"})


def test_an_adr_list_after_one_head_is_rewritten_item_by_item():
    moves = {OLD: NEW, f"{ADR}0051-other.md": f"{ADR}0052-other.md"}
    text = "ADRs 0010, 0049 y 0051; ADR 0049/0051 and ADR-0049, 0051, and 0012\n"
    texts = {OLD: (None, "x\n"), f"{ADR}0051-other.md": (None, "y\n"), "d.md": ("", text)}
    result = _rewrite(moves, texts, [_line("d.md", text.rstrip("\n"))])
    assert isinstance(result, Renaming)
    assert result.texts["d.md"] == (
        "ADRs 0010, 0050 y 0052; ADR 0050/0052 and ADR-0050, 0052, and 0012\n"
    )


def test_numbers_that_swap_places_never_chain():
    moves = {f"{ADR}0049-a.md": f"{ADR}0050-a.md", f"{ADR}0050-b.md": f"{ADR}0051-b.md"}
    text = "ADR-0049 then ADR-0050 and docs/adr/0049-a.md and 0050-b.md\n"
    texts = {
        f"{ADR}0049-a.md": (None, "a\n"),
        f"{ADR}0050-b.md": (None, "b\n"),
        "n.md": ("", text),
    }
    result = _rewrite(moves, texts, [_line("n.md", text.rstrip("\n"))])
    assert isinstance(result, Renaming)
    assert result.texts["n.md"] == "ADR-0050 then ADR-0051 and docs/adr/0050-a.md and 0051-b.md\n"
    assert set(result.texts) == {f"{ADR}0050-a.md", f"{ADR}0051-b.md", "n.md"}


def test_a_line_that_cites_the_number_after_master_s_file_arrived_is_ambiguous():
    texts = {OLD: (None, "x\n"), "d.md": ("", "ADR-0049 is theirs or mine?\n")}
    lines = [_line("d.md", "ADR-0049 is theirs or mine?", commit="late")]
    trees = {"late": ["0049-theirs.md", "0048-x.md"]}
    result = _rewrite({OLD: NEW}, texts, lines, trees)
    assert isinstance(result, Ambiguous)
    assert result.reason == "cites_master_after_sync"
    assert "d.md" in result.detail and "ADR-0049 is theirs" in result.detail


def test_a_file_name_citation_is_not_ambiguous_after_the_sync():
    texts = {OLD: (None, "x\n"), "d.md": ("", "see 0049-mine.md\n")}
    lines = [_line("d.md", "see 0049-mine.md", commit="late")]
    result = _rewrite({OLD: NEW}, texts, lines, {"late": ["0049-theirs.md"]})
    assert isinstance(result, Renaming)
    assert result.texts["d.md"] == "see 0050-mine.md\n"


def test_a_bare_number_in_prose_on_an_own_line_is_ambiguous():
    texts = {OLD: (None, "x\n"), "d.md": ("", "decision 0049 was taken\n")}
    result = _rewrite({OLD: NEW}, texts, [_line("d.md", "decision 0049 was taken")])
    assert isinstance(result, Ambiguous)
    assert result.reason == "unclassified_token"
    assert "d.md" in result.detail and "decision 0049" in result.detail


def test_a_bare_number_on_a_line_the_branch_did_not_add_is_left_alone():
    texts = {OLD: (None, "x\n"), "d.md": ("year 0049\n", "year 0049\nnew\n")}
    result = _rewrite({OLD: NEW}, texts, [_line("d.md", "new")])
    assert result == Renaming(((OLD, NEW),), {NEW: "x\n"})


def test_a_moved_path_without_a_text_is_refused_loudly():
    with pytest.raises(ValueError, match=OLD):
        _rewrite({OLD: NEW}, {}, [])


# -- GH-384: seven branch ADRs, master gained its own 0054 -------------------------------

GH384_OWN = [
    OwnAdr(f"{ADR}{_adr(f'{n:04d}', 'mine')}", "c1") for n in range(54, 61)
]  # 0054-mine.md .. 0060-mine.md


def test_gh_384_only_the_colliding_adr_moves_and_the_rest_stay_in_place():
    moved = adr_numbers.collisions([_adr("0053"), _adr("0054", "theirs")], GH384_OWN)
    assert moved == {f"{ADR}0054-mine.md": f"{ADR}0061-mine.md"}


def test_master_gaining_two_numbers_moves_both_in_one_call():
    master = [_adr("0053"), _adr("0054", "theirs"), _adr("0055", "theirs")]
    assert adr_numbers.collisions(master, GH384_OWN) == {
        f"{ADR}0054-mine.md": f"{ADR}0061-mine.md",
        f"{ADR}0055-mine.md": f"{ADR}0062-mine.md",
    }


def test_a_second_collision_after_the_first_renumbering_takes_the_next_free_number():
    # Round 1 moved 0054 to 0061; master has since gained a 0055 of its own.
    own = [OwnAdr(f"{ADR}{_adr(f'{n:04d}', 'mine')}", "c1") for n in range(55, 62)]
    master = [_adr("0053"), _adr("0054", "theirs"), _adr("0055", "theirs")]
    assert adr_numbers.collisions(master, own) == {f"{ADR}0055-mine.md": f"{ADR}0062-mine.md"}


GH384_MOVES = {f"{ADR}0054-mine.md": f"{ADR}0061-mine.md"}


def _gh384(line: str, own: bool = True):
    texts = {f"{ADR}0054-mine.md": (None, "x\n"), "d.md": ("", f"{line}\n")}
    return _rewrite(GH384_MOVES, texts, [_line("d.md", line)] if own else [])


@pytest.mark.parametrize("line", ["ADRs 0054, 0055", "ADR-0054", "ADR 0054 and 0055"])
def test_the_grammar_the_rule_decides_is_rewritten_on_own_lines(line):
    result = _gh384(line)
    assert isinstance(result, Renaming)
    assert result.texts["d.md"] == line.replace("0054", "0061") + "\n"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("ADR-0054…ADR-0060", "range"),
        ("ADR-0054...ADR-0060", "range"),
        ("numbers 0054–0060", "range"),
        ("numbers 0054 — 0060", "range"),
        ("numbers 0054-0060", "range"),
        ("ADR-0054 to ADR-0060", "range"),
        ("ADR 0054 through 0060", "range"),
        ("0050 a ADR 0054", "range"),
        ("ADR-0050..ADR-0054", "range"),
        ("| 0054 | configuration per business |", "unclassified_token"),
    ],
)
def test_a_line_the_rule_cannot_decide_is_ambiguous_and_carries_the_moves(line, reason):
    result = _gh384(line)
    assert isinstance(result, Ambiguous)
    assert result.reason == reason
    assert "d.md:1" in result.detail and line in result.detail
    assert result.moves == tuple(sorted(GH384_MOVES.items()))


def test_a_range_between_numbers_that_do_not_move_is_no_range_of_ours():
    result = _gh384("ADR-0010…ADR-0020 and ADR-0054")
    assert isinstance(result, Renaming)
    assert result.texts["d.md"] == "ADR-0010…ADR-0020 and ADR-0061\n"


def test_a_range_on_a_line_the_branch_did_not_add_is_left_untouched():
    result = _gh384("ADR-0054…ADR-0060", own=False)
    assert isinstance(result, Renaming)
    assert "d.md" not in result.texts


def test_the_hyphen_of_a_full_file_name_is_not_a_range():
    result = _gh384("see 0054-mine.md and docs/adr/0054-mine.md")
    assert isinstance(result, Renaming)
    assert result.texts["d.md"] == "see 0061-mine.md and docs/adr/0061-mine.md\n"


def test_a_double_move_carries_the_moves_in_its_ambiguity():
    moves = {f"{ADR}0054-a.md": f"{ADR}0061-a.md", f"{ADR}0054-b.md": f"{ADR}0062-b.md"}
    texts = {f"{ADR}0054-a.md": (None, "a\n"), f"{ADR}0054-b.md": (None, "b\n")}
    result = _rewrite(moves, texts, [])
    assert isinstance(result, Ambiguous)
    assert result.reason == "duplicate_own_number"
    assert result.moves == tuple(sorted(moves.items()))


def test_a_duplicate_own_number_found_by_collisions_carries_no_moves():
    own = [OwnAdr(f"{ADR}{_adr('0049', 'a')}", "c1"), OwnAdr(f"{ADR}{_adr('0049', 'b')}", "c2")]
    result = adr_numbers.collisions([], own)
    assert isinstance(result, Ambiguous)
    assert result.moves == ()


# -- the acceptance: two tasks on one base both add "the next" ADR -----------------------

BASE = {
    f"{ADR}0047-previa.md": "# ADR-0047: previa\n",
    f"{ADR}0048-anterior.md": "# ADR-0048: anterior\n",
    "docs/product/glossary.md": "Ver ADR-0048 para el detalle.\n",
}
# What each task wrote on top of the base: the new ADR, a doc that cites it and a code
# comment that names its file. Every line below is a line the task's own commit added.
TASK_A = {
    f"{ADR}0049-alfa.md": "# ADR-0049: alfa\n\nVer ADR-0048.\n",
    "docs/product/alfa.md": (
        "Ver ADR-0048 para el detalle.\nSe decide en ADR-0049 (0049-alfa.md).\n"
    ),
    "agentic/alfa.py": "# docs/adr/0049-alfa.md explica por que\n",
}
TASK_B = {
    f"{ADR}0049-beta.md": "# ADR-0049: beta\n\nVer ADR-0048.\n",
    "docs/product/beta.md": "Ver ADR-0048 para el detalle.\nVer ADRs 0049 y 0048 y 0049-beta.md.\n",
    "agentic/beta.py": "# docs/adr/0049-beta.md explica por que\n",
}


def _adr_names(tree: dict[str, str]) -> list[str]:
    return [p.rsplit("/", 1)[-1] for p in tree if p.startswith(ADR)]


def _own_lines(task: dict[str, str], base: dict[str, str], commit: str) -> list[OwnLine]:
    return [
        OwnLine(path, commit, line)
        for path, text in task.items()
        for line in text.splitlines()
        if line not in base.get(path, "").splitlines()
    ]


def _land(master: dict[str, str], task: dict[str, str], commit: str) -> dict[str, str]:
    """What the landing does for one task against the master it sees now: nothing when
    nothing collides, otherwise the renumber step. Returns the master after the merge."""
    added = [OwnAdr(p, commit) for p in task if p.startswith(ADR)]
    moved = adr_numbers.collisions(_adr_names(master), added)
    assert not isinstance(moved, Ambiguous)
    if not moved:
        return master | task
    texts = {p: (master.get(p), text) for p, text in task.items()}
    renaming = adr_numbers.renumber(moved, _own_lines(task, BASE, commit), {}, texts)
    assert isinstance(renaming, Renaming)
    kept = {p: t for p, t in task.items() if p not in dict(renaming.moves)}
    return master | kept | renaming.texts


def _resolves(tree: dict[str, str]) -> list[str]:
    """Every ADR file name and `ADR-NNNN` any text names that no ADR file in the tree has."""
    names = _adr_names(tree)
    numbers = {adr_numbers.number_of(n) for n in names}
    missing = []
    for path, text in tree.items():
        for name in re.findall(r"\b\d{4}-[a-z-]+\.md", text):
            if name not in names:
                missing.append(f"{path}: {name}")
        for number in re.findall(r"ADR-(\d{4})", text):
            if number not in numbers:
                missing.append(f"{path}: ADR-{number}")
    return missing


def test_two_tasks_on_one_base_that_both_add_the_next_adr_land_with_unique_numbers():
    naive = BASE | TASK_A | TASK_B
    assert adr_numbers.duplicate_numbers(_adr_names(naive)) == ["0049"]

    master = _land(dict(BASE), TASK_A, "a1")
    assert master == BASE | TASK_A  # the first to land finds no collision and is untouched
    master = _land(master, TASK_B, "b1")

    assert adr_numbers.duplicate_numbers(_adr_names(master)) == []
    assert sorted(_adr_names(master)) == [
        "0047-previa.md",
        "0048-anterior.md",
        "0049-alfa.md",
        "0050-beta.md",
    ]
    assert _resolves(master) == []
    # B's references follow its file; the line that cites master's ADR-0048 does not move,
    # and A's texts are byte-identical to what A landed.
    assert master["docs/product/beta.md"] == (
        "Ver ADR-0048 para el detalle.\nVer ADRs 0050 y 0048 y 0050-beta.md.\n"
    )
    assert master["agentic/beta.py"] == "# docs/adr/0050-beta.md explica por que\n"
    assert master[f"{ADR}0050-beta.md"] == "# ADR-0050: beta\n\nVer ADR-0048.\n"
    assert master["docs/product/glossary.md"] == BASE["docs/product/glossary.md"]
    assert {p: master[p] for p in TASK_A} == TASK_A


def test_no_collision_in_a_set_means_none_in_any_of_its_subsets():
    """The fact `adr_collision`'s first pass relies on (GH-262). The universe, exhaustive: master
    holds `0003-a.md`, `0004-b.md`, `0007-c.md`; the branch's candidates are `0004-x` (a
    collision), `0005-y` and `0005-z` (a shared number), `0007-c` (master's own basename) and
    `0009-w` (none): every subset S of the five and every subset T of S, 243 pairs."""
    master = ("0003-a.md", "0004-b.md", "0007-c.md")
    paths = (
        "docs/adr/0004-x.md",
        "docs/adr/0005-y.md",
        "docs/adr/0005-z.md",
        "docs/adr/0007-c.md",
        "docs/adr/0009-w.md",
    )

    def subsets(items):
        return chain.from_iterable(combinations(items, n) for n in range(len(items) + 1))

    def found(chosen):
        return adr_numbers.collisions(master, [OwnAdr(p, "c" * 40) for p in chosen])

    clear = 0
    for whole in subsets(paths):
        if found(whole) == {}:
            clear += 1
            for part in subsets(whole):
                assert found(part) == {}, (whole, part)
    assert clear > 1  # the empty set and at least one more: the property is not vacuous
    assert found(paths[:1]) != {}  # and a set with a collision is not clear
