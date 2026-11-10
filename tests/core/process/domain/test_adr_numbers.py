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


# -- GH-544: renumber_decided, the rewrite that lists what it cannot decide ---------------


def _decided(moves, texts, lines, trees=None):
    return adr_numbers.renumber_decided(moves, lines, trees or {}, texts)


def test_decided_with_nothing_undecidable_is_the_renumber_result_and_no_spots():
    texts = {
        OLD: (None, "# ADR-0049: mine\n"),
        "agentic/x.py": ("a = 1\n", "a = 1\n# see docs/adr/0049-mine.md\n"),
    }
    lines = [_line(OLD, "# ADR-0049: mine")]
    decided = _decided({OLD: NEW}, texts, lines)
    assert decided.renaming == _rewrite({OLD: NEW}, texts, lines)
    assert decided.spots == ()


def test_decided_rewrites_all_it_can_and_lists_every_own_line_it_cannot():
    # The one reference the owner named (app/main.py:228 in apostrophe GH-407) plus a range
    # and a table row: `renumber` stops at the first, `renumber_decided` keeps going.
    texts = {
        OLD: (None, "# ADR-0049: mine\n"),
        "app/main.py": ("", "ok = 1\n# ADR-0049 says\nx = 1  # (docs/adr/0049)\n"),
        "docs/a.md": ("", "numbers 0049–0060\n| 0049 | row |\nsee ADR-0049\n"),
    }
    lines = [
        _line("app/main.py", "# ADR-0049 says"),
        _line("app/main.py", "x = 1  # (docs/adr/0049)"),
        _line("docs/a.md", "numbers 0049–0060"),
        _line("docs/a.md", "| 0049 | row |"),
        _line("docs/a.md", "see ADR-0049"),
    ]
    assert isinstance(_rewrite({OLD: NEW}, texts, lines), Ambiguous)
    decided = _decided({OLD: NEW}, texts, lines)
    assert decided.renaming.moves == ((OLD, NEW),)
    assert decided.renaming.texts["app/main.py"] == (
        "ok = 1\n# ADR-0050 says\nx = 1  # (docs/adr/0049)\n"
    )
    assert decided.renaming.texts["docs/a.md"] == (
        "numbers 0049–0060\n| 0049 | row |\nsee ADR-0050\n"
    )
    assert decided.spots == (
        ("app/main.py", 3, "x = 1  # (docs/adr/0049)"),
        ("docs/a.md", 1, "numbers 0049–0060"),
        ("docs/a.md", 2, "| 0049 | row |"),
    )


def test_decided_lists_a_line_the_branch_cites_after_master_s_file_arrived():
    texts = {OLD: (None, "x\n"), "d.md": ("", "ADR-0049 is theirs or mine?\n")}
    lines = [_line("d.md", "ADR-0049 is theirs or mine?", commit="late")]
    decided = _decided({OLD: NEW}, texts, lines, {"late": ["0049-theirs.md"]})
    assert decided.spots == (("d.md", 1, "ADR-0049 is theirs or mine?"),)
    assert "d.md" not in decided.renaming.texts  # held back for whoever edits the spot


def test_decided_names_a_spot_in_the_moved_file_by_its_new_path():
    texts = {OLD: (None, "# ADR-0049\ndecision 0049 was taken\n")}
    lines = [_line(OLD, "decision 0049 was taken")]
    decided = _decided({OLD: NEW}, texts, lines)
    assert decided.spots == ((NEW, 2, "decision 0049 was taken"),)


def test_decided_does_not_list_a_line_the_branch_did_not_add():
    texts = {OLD: (None, "x\n"), "d.md": ("year 0049\n", "year 0049\nnew\n")}
    assert _decided({OLD: NEW}, texts, [_line("d.md", "new")]).spots == ()


def test_decided_keeps_the_refusals_renumber_makes_loudly():
    with pytest.raises(ValueError, match=OLD):
        _decided({OLD: NEW}, {}, [])
    moves = {f"{ADR}0054-a.md": f"{ADR}0061-a.md", f"{ADR}0054-b.md": f"{ADR}0062-b.md"}
    texts = {f"{ADR}0054-a.md": (None, "a\n"), f"{ADR}0054-b.md": (None, "b\n")}
    result = _decided(moves, texts, [])
    assert isinstance(result, Ambiguous) and result.reason == "duplicate_own_number"


# -- GH-576: rule (d), the bound lines and the content bounds check ------------------------

INDEX = "docs/architecture/decisiones.md"
ROW = "| [0049](../adr/0049-mine.md) | Mine |"
ROW_AFTER = "| [0050](../adr/0050-mine.md) | Mine |"


def test_a_link_label_follows_its_moved_target_on_a_line_the_branch_did_not_add():
    # GH-407: the index row came in by a merge resolution, so no own commit added it.
    texts = {OLD: (None, "x\n"), INDEX: ("", f"| a | b |\n{ROW}\n")}
    decided = _decided({OLD: NEW}, texts, [])
    assert decided.renaming.texts[INDEX] == f"| a | b |\n{ROW_AFTER}\n"
    assert decided.spots == ()
    # `renumber` has no rule (d): the label stays, only the target follows (rule (a)).
    plain = _rewrite({OLD: NEW}, texts, [])
    assert plain.texts[INDEX] == "| a | b |\n| [0049](../adr/0050-mine.md) | Mine |\n"


def test_a_link_to_a_file_that_did_not_move_keeps_its_label_even_with_the_moved_number():
    texts = {OLD: (None, "x\n"), INDEX: ("", "[0049](0049-theirs.md) and 0049\n")}
    decided = _decided({OLD: NEW}, texts, [])
    assert INDEX not in decided.renaming.texts
    assert decided.bound == ()


def test_only_the_link_to_the_moved_file_changes_when_masters_file_has_the_same_number():
    line = "[0049](0049-mine.md) [0049](0049-theirs.md)"
    texts = {OLD: (None, "x\n"), "d.md": ("", f"{line}\n")}
    decided = _decided({OLD: NEW}, texts, [])
    assert decided.renaming.texts["d.md"] == "[0050](0050-mine.md) [0049](0049-theirs.md)\n"


def test_a_label_that_is_not_the_old_number_of_its_target_is_left_as_it_is():
    texts = {OLD: (None, "x\n"), "d.md": ("", "[0048](docs/adr/0049-mine.md) [x](0049-mine.md)\n")}
    decided = _decided({OLD: NEW}, texts, [])
    assert decided.renaming.texts["d.md"] == "[0048](docs/adr/0050-mine.md) [x](0050-mine.md)\n"


def test_gh_601_own_row_is_decided_by_rule_d_and_is_no_spot_any_more():
    texts = {OLD: (None, "x\n"), INDEX: ("", f"{ROW}\n")}
    lines = [_line(INDEX, ROW)]
    plain = _rewrite({OLD: NEW}, texts, lines)
    assert isinstance(plain, Ambiguous) and plain.reason == "unclassified_token"
    decided = _decided({OLD: NEW}, texts, lines)
    assert decided.spots == ()
    assert decided.renaming.texts[INDEX] == f"{ROW_AFTER}\n"
    assert decided.bound == ((INDEX, 1, ROW_AFTER),)


def test_a_label_outside_a_link_to_the_moved_file_is_still_a_spot_on_an_own_line():
    row = "| [0049](other.md) | see 0049-mine.md |"
    texts = {OLD: (None, "x\n"), "d.md": ("", f"{row}\n")}
    decided = _decided({OLD: NEW}, texts, [_line("d.md", row)])
    assert decided.spots == (("d.md", 1, "| [0049](other.md) | see 0050-mine.md |"),)
    assert decided.bound == ()  # a spot is never a bound line


def test_the_bound_lines_are_the_rewritten_ones_and_the_title_of_the_moved_adr():
    texts = {
        OLD: (None, "# ADR-0049: mine\n\nsee docs/adr/0049-mine.md\n"),
        "a.py": ("", "ok\n# docs/adr/0049-mine.md\n"),
        INDEX: ("", f"top\n{ROW}\n"),
    }
    decided = _decided({OLD: NEW}, texts, [])
    assert decided.bound == (
        ("a.py", 2, "# docs/adr/0050-mine.md"),
        (NEW, 1, "# ADR-0049: mine"),
        (NEW, 3, "see docs/adr/0050-mine.md"),
        (INDEX, 2, ROW_AFTER),
    )


def _bounds(spots=(), bound=()):
    return adr_numbers.DecidedRenaming(Renaming((), {}), tuple(spots), tuple(bound))


def test_outside_bounds_accepts_an_in_place_edit_of_a_spot():
    found = _bounds(spots=[("m.py", 2, 'MSG = "ADR-0049"')])
    before = {"m.py": 'a = 1\nMSG = "ADR-0049"\n'}
    after = {"m.py": 'a = 1\nMSG = "ADR-0050"\n'}
    assert adr_numbers.outside_bounds(before, after, found) == ()


def test_outside_bounds_lets_a_bound_row_move_but_not_change():
    rows = ["| [0076](a) |", "| [0077](b) |", "| [0078](c) |"]
    found = _bounds(bound=[(INDEX, 3, rows[2])])
    before = {INDEX: "\n".join([rows[0], rows[2], rows[1]]) + "\n"}
    after = {INDEX: "\n".join(rows) + "\n"}
    assert adr_numbers.outside_bounds(before, after, found) == ()
    edited = {INDEX: "\n".join([rows[0], rows[1], "| [0078](c) edited |"]) + "\n"}
    assert adr_numbers.outside_bounds(before, edited, found) == ((INDEX, 2), (INDEX, 3))


def test_outside_bounds_names_a_foreign_line_and_an_added_or_removed_line():
    found = _bounds(spots=[("m.py", 2, "S = 1")])
    before = {"m.py": "a = 1\nS = 1\nb = 1\n"}
    changed = {"m.py": "a = 2\nS = 2\nb = 1\n"}
    assert adr_numbers.outside_bounds(before, changed, found) == (("m.py", 1),)
    # A spot is one line for one line: a second line in its place is not allowed.
    assert adr_numbers.outside_bounds(before, {"m.py": "a = 1\nS = 2\nS = 3\nb = 1\n"}, found) == (
        ("m.py", 3),
    )
    assert adr_numbers.outside_bounds(before, {"m.py": "a = 1\nS = 1\n"}, found) == (("m.py", 3),)


def test_outside_bounds_names_the_lines_of_a_path_with_neither_a_spot_nor_a_bound_line():
    """GH-579: no `(path, 0)` any more: a two-line edit is reported as those two lines."""
    found = _bounds(spots=[("m.py", 1, "S = 1")])
    assert adr_numbers.outside_bounds({}, {"new.py": "x\n"}, found) == (("new.py", 1),)
    unchanged = {"m.py": "S = 1\n"}
    assert adr_numbers.outside_bounds(unchanged, unchanged, found) == ()
    before = {"sales.md": "# Ventas\nuno\ndos\ntres\n"}
    after = {"sales.md": "# Ventas\nUNO\ndos\nTRES\n"}
    assert adr_numbers.outside_bounds(before, after, found) == (("sales.md", 2), ("sales.md", 4))


def test_outside_bounds_never_reports_line_zero():
    outside = adr_numbers.outside_bounds(
        {"a.md": "x\n"}, {"a.md": "y\n", "b.md": "z\n"}, _bounds()
    )
    assert outside and all(line > 0 for _, line in outside)


def test_outside_bounds_accepts_a_change_of_line_endings_or_of_the_final_newline_only():
    found = _bounds(spots=[("m.py", 1, "S = 1")])
    lf = {"sales.md": "uno\ndos\n", "m.py": "S = 1\nb\n"}
    crlf = {"sales.md": "uno\r\ndos\r\n", "m.py": lf["m.py"]}
    assert adr_numbers.outside_bounds(lf, crlf, found) == ()
    no_newline = {"sales.md": "uno\ndos", "m.py": "S = 1\nb"}
    assert adr_numbers.outside_bounds(lf, no_newline, found) == ()


RULE_B = "see ADR-0049 for why"
RULE_B_AFTER = "see ADR-0050 for why"


def test_a_line_rule_b_rewrote_is_bound_a_pass_may_move_it_but_not_change_it():
    texts = {OLD: (None, "# ADR-0049: mine\n"), "a.md": ("", f"top\n{RULE_B}\nend\n")}
    decided = _decided({OLD: NEW}, texts, [_line("a.md", RULE_B)])
    assert decided.spots == ()
    assert ("a.md", 2, RULE_B_AFTER) in decided.bound
    before = {"a.md": f"top\n{RULE_B_AFTER}\nend\n"}
    moved = {"a.md": f"{RULE_B_AFTER}\ntop\nend\n"}
    assert adr_numbers.outside_bounds(before, moved, decided) == ()
    changed = {"a.md": "top\nsee ADR-0099 for why\nend\n"}
    assert adr_numbers.outside_bounds(before, changed, decided) == (("a.md", 2),)


def test_a_cited_list_rule_c_rewrote_is_bound_too():
    texts = {OLD: (None, "x\n"), "a.md": ("", "ADRs 0007, 0049\n")}
    decided = _decided({OLD: NEW}, texts, [_line("a.md", "ADRs 0007, 0049")])
    assert decided.bound == (("a.md", 1, "ADRs 0007, 0050"),)


def test_a_line_held_as_citing_master_after_sync_stays_a_spot_not_a_bound_line():
    texts = {OLD: (None, "x\n"), "a.md": ("", "see ADR-0049\n")}
    trees = {"c1": ["0049-theirs.md"]}
    decided = _decided({OLD: NEW}, texts, [_line("a.md", "see ADR-0049", "c1")], trees)
    assert decided.spots == (("a.md", 1, "see ADR-0049"),) and decided.bound == ()


def test_kept_spots_reads_only_the_keep_lines_naming_a_spot():
    spots = [("a.md", 3, "x"), ("b.py", 238, "y"), ("c.md", 1, "z")]
    messages = (
        "Point main.py at the new number\n\nkeep a.md:3\nkeep b.py:239\n keep c.md:1\n"
        "keep c.md:1 because\nkeep: c.md:1\nkeep b.py:238\nplain a.md:3\n"
    )
    assert adr_numbers.kept_spots(messages, spots) == {("a.md", 3), ("b.py", 238)}
    assert adr_numbers.kept_spots("", spots) == set()
    assert adr_numbers.kept_spots("keep a.md:3\r\n", spots) == {("a.md", 3)}


# -- GH-579: a reference a merge brought in is classified like an own one ------------------

GONE = f"{ADR}0076-payment-methods.md"
MOVED = f"{ADR}0078-payment-methods.md"
SALES = "docs/product/sales.md"
SALES_MASTER = "# Ventas\n\nunrelated\n"
SALES_HEAD = (
    SALES_MASTER + "puerto OnTheSpotPayments (ADR-0076)\n" + "(ADR-0007, ADR-0076, payments.md)\n"
)


def _gh407(head: str, own: list[OwnLine], *, base: str | None = SALES_MASTER, candidate=True):
    texts = {GONE: (None, "# ADR-0076: payments\n"), SALES: (SALES_MASTER, head)}
    return {
        "moves": {GONE: MOVED},
        "lines": own,
        "trees": {},
        "texts": texts,
        "bases": {SALES: base} if candidate else {},
    }


def _renumber(case):
    return adr_numbers.renumber(
        case["moves"], case["lines"], case["trees"], case["texts"], bases=case["bases"]
    )


def _renumber_decided(case):
    return adr_numbers.renumber_decided(
        case["moves"], case["lines"], case["trees"], case["texts"], bases=case["bases"]
    )


def test_gh_579_an_own_adr_token_in_a_product_doc_is_rewritten_by_rule_b():
    own = [_line(SALES, "puerto OnTheSpotPayments (ADR-0076)")]
    result = _renumber(_gh407(SALES_HEAD.replace("ADR-0076, payments", "ADR-0079, payments"), own))
    assert isinstance(result, Renaming)
    assert "puerto OnTheSpotPayments (ADR-0078)" in result.texts[SALES]


def test_gh_579_an_adr_token_no_commit_of_the_branch_added_is_a_spot_not_silence():
    case = _gh407(SALES_HEAD, [])
    result = _renumber(case)
    assert isinstance(result, Ambiguous)
    assert result.reason == "unclassified_token"
    assert f"{SALES}:4: puerto OnTheSpotPayments (ADR-0076)" in result.detail
    decided = _renumber_decided(case)
    assert not isinstance(decided, Ambiguous)
    assert decided.spots == (
        (SALES, 4, "puerto OnTheSpotPayments (ADR-0076)"),
        (SALES, 5, "(ADR-0007, ADR-0076, payments.md)"),
    )
    assert SALES not in decided.renaming.texts  # never rewritten by code


def test_gh_579_a_path_the_reads_did_not_mark_as_candidate_is_not_classified():
    result = _renumber(_gh407(SALES_HEAD, [], candidate=False))
    assert isinstance(result, Renaming)


def test_gh_579_a_non_own_line_equal_to_the_merge_base_text_is_masters():
    base = SALES_HEAD  # master moved on from the merge base, the line is the base's
    result = _renumber(_gh407(SALES_HEAD, [], base=base))
    assert isinstance(result, Renaming)


def test_gh_579_a_non_own_line_that_master_has_is_not_a_spot():
    case = _gh407(SALES_HEAD, [])
    case["texts"][SALES] = (SALES_HEAD, SALES_HEAD)
    assert isinstance(_renumber(case), Renaming)


def test_gh_579_a_bare_adr_path_on_an_own_line_is_still_a_spot():
    own = [_line("app/main.py", "x = 1  # (docs/adr/0076)")]
    case = _gh407("same\n", own, candidate=False)
    case["texts"]["app/main.py"] = ("", "x = 1  # (docs/adr/0076)\n")
    decided = _renumber_decided(case)
    assert not isinstance(decided, Ambiguous)
    assert decided.spots == (("app/main.py", 1, "x = 1  # (docs/adr/0076)"),)


@pytest.mark.parametrize("form", ["(docs/adr/0076)", "see adr/0076.", "docs/adr/0076-other.md"])
def test_gh_579_a_bare_adr_path_on_a_merged_line_is_a_spot(form):
    head = f"{SALES_MASTER}ref {form}\n"
    result = _renumber(_gh407(head, []))
    assert isinstance(result, Ambiguous) and result.reason == "unclassified_token"


def test_gh_579_the_moved_file_name_on_a_merged_line_is_rewritten_not_a_spot():
    head = f"{SALES_MASTER}ref docs/adr/0076-payment-methods.md\n"
    result = _renumber(_gh407(head, []))
    assert isinstance(result, Renaming)
    assert result.texts[SALES].endswith("ref docs/adr/0078-payment-methods.md\n")


@pytest.mark.parametrize(
    "line", ["0076,Cash,1", "| 0076 | row |", "hash a0076b and 0076", "decision 0076 was taken"]
)
def test_gh_579_a_bare_four_digit_token_on_a_merged_line_is_never_a_spot(line):
    head = f"{SALES_MASTER}{line}\n"
    assert isinstance(_renumber(_gh407(head, [])), Renaming)
    decided = _renumber_decided(_gh407(head, []))
    assert not isinstance(decided, Ambiguous) and decided.spots == ()


def test_gh_579_only_a_final_newline_difference_is_neither_a_spot_nor_a_rewrite():
    head = SALES_MASTER.rstrip("\n")
    result = _renumber(_gh407(head, [], base=SALES_MASTER))
    assert isinstance(result, Renaming) and SALES not in result.texts


def test_gh_579_a_branch_without_stale_non_own_tokens_still_gets_a_renaming():
    head = f"{SALES_MASTER}new line about ADR-0007 only\n"
    result = _renumber(_gh407(head, []))
    assert result == Renaming(((GONE, MOVED),), {MOVED: "# ADR-0076: payments\n"})
