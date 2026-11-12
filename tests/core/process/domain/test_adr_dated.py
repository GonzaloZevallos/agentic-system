"""The dated ADR name (GH-611, the owner's rule): what a numbered ADR of a branch becomes on a
product that names its ADRs by date, and the references that follow. Pure: names and texts in,
a `DatedRenaming` or a `Declined` out."""

from datetime import date

import pytest

from agentic.core.process.domain import adr_dated, adr_numbers
from agentic.core.process.domain.adr_dated import DatedRenaming, Declined, Undated, dated_name
from agentic.core.process.domain.adr_numbers import OwnAdr, OwnLine

ADR = "docs/adr/"
MINE = f"{ADR}0079-foo.md"
FRONT = "---\nstatus: accepted\ndate: 2026-11-10\n---\n\n# ADR-0079: foo\n"
FIRST = date(2026, 11, 3)


def test_the_date_of_a_name_is_the_front_matter_date_without_dashes():
    assert dated_name(MINE, FRONT, FIRST) == f"{ADR}20261110-foo.md"


@pytest.mark.parametrize(
    "front",
    [
        "# ADR-0079: foo\n",  # no front matter
        "---\nstatus: accepted\n---\n# t\n",  # no date line
        "---\ndate: 2026-02-30\n---\n# t\n",  # not a calendar day
        "---\ndate: 2026-1-3\n---\n# t\n",  # not YYYY-MM-DD
        "---\ndate: soon\n---\n# t\n",
        "date: 2026-11-10\n# a date line outside any front matter\n",
        "# t\n---\ndate: 2026-11-10\n---\n",  # front matter is only the top of the file
    ],
)
def test_without_a_real_calendar_date_in_the_front_matter_the_first_date_names_it(front):
    assert dated_name(MINE, front, FIRST) == f"{ADR}20261103-foo.md"


def test_a_quoted_front_matter_date_counts():
    assert dated_name(MINE, "---\ndate: '2026-11-10'\n---\n", FIRST) == f"{ADR}20261110-foo.md"


def test_with_neither_a_date_line_nor_a_first_date_there_is_no_name():
    with pytest.raises(ValueError, match="no date"):
        dated_name(MINE, "# t\n", None)


def test_a_dated_name_never_reads_as_a_number():
    assert adr_numbers.number_of("20261111-foo.md") is None
    assert adr_numbers.number_of(dated_name(MINE, FRONT, FIRST).rsplit("/", 1)[-1]) is None


def _rename(texts, *, own=(MINE,), dates=None, master=(), kept=(), lines=(), trees=None):
    """`texts` maps a path to its text at the head, or to `(text at master, text at head)`."""
    return adr_dated.rename(
        [OwnAdr(path, "c" * 40) for path in own],
        dates if dates is not None else {path: FIRST for path in own},
        {p: t if isinstance(t, tuple) else (None, t) for p, t in texts.items()},
        master_names=master,
        kept_names=kept,
        lines=lines,
        trees=trees or {},
    )


def test_the_moved_file_and_every_reference_to_it_follow_the_new_name():
    texts = {
        MINE: FRONT,
        "docs/notes.md": (
            "see ADR-0079 and ADR 0079 and ADR-0001\n"
            "path docs/adr/0079-foo.md and docs/adr/0079 and adr/0079-foo.md\n"
            "link [0079](../adr/0079-foo.md) done\n"
            "unrelated 0001 and ADR-0001\n"
        ),
    }

    result = _rename(texts)

    assert isinstance(result, DatedRenaming)
    assert result.moves == ((MINE, f"{ADR}20261110-foo.md"),)
    assert result.texts[f"{ADR}20261110-foo.md"] == FRONT.replace("ADR-0079", "ADR-20261110-foo")
    assert result.texts["docs/notes.md"] == (
        "see ADR-20261110-foo and ADR-20261110-foo and ADR-0001\n"
        "path docs/adr/20261110-foo.md and docs/adr/20261110-foo and adr/20261110-foo.md\n"
        "link [ADR-20261110-foo](../adr/20261110-foo.md) done\n"
        "unrelated 0001 and ADR-0001\n"
    )
    assert MINE not in result.texts


def test_a_text_that_names_no_moved_number_is_not_written():
    result = _rename({MINE: FRONT, "docs/other.md": "ADR-0001 only\n"})
    assert isinstance(result, DatedRenaming)
    assert set(result.texts) == {f"{ADR}20261110-foo.md"}


@pytest.mark.parametrize(
    "line",
    [
        "ADRs 0078, 0079",
        "ADR-0078 and 0079",
        "ADRs 0078–0079",
        "ADR-0079…ADR-0080",
        "ADR 0077 to ADR 0079",
        "the old number 0079 alone",
    ],
)
def test_a_list_a_range_or_a_bare_number_holding_a_moved_number_cannot_be_rewritten(line):
    result = _rename({MINE: FRONT, "docs/notes.md": f"{line}\n"}, master=("0078-a.md",))
    assert isinstance(result, Declined)
    assert result.cause == "unrewritable"
    assert "docs/notes.md:1" in result.detail


def test_a_list_that_holds_no_moved_number_is_left_alone():
    result = _rename({MINE: FRONT, "docs/notes.md": "ADRs 0001, 0002\n0001–0003\n"})
    assert isinstance(result, DatedRenaming)
    assert "docs/notes.md" not in result.texts


def test_a_slug_a_dated_adr_on_master_already_has_is_taken():
    result = _rename({MINE: FRONT}, master=("20260901-foo.md", "0001-a.md"))
    assert isinstance(result, Declined)
    assert result.cause == "slug_taken"
    assert "foo" in result.detail


def test_a_slug_another_own_adr_has_is_taken():
    other = f"{ADR}0080-foo.md"
    for result in (
        _rename({MINE: FRONT, other: "# b\n"}, own=(MINE, other)),
        _rename({MINE: FRONT}, kept=("20261001-foo.md",)),
    ):
        assert isinstance(result, Declined) and result.cause == "slug_taken"


def test_a_numbered_adr_on_master_with_the_same_slug_is_no_clash():
    assert isinstance(_rename({MINE: FRONT}, master=("0050-foo.md",)), DatedRenaming)


def test_two_own_adrs_with_the_same_number_each_get_their_own_dated_name():
    a, b = f"{ADR}0079-foo.md", f"{ADR}0079-bar.md"
    texts = {
        a: FRONT,
        b: "# bar\n",
        "docs/n.md": "see docs/adr/0079-foo.md and docs/adr/0079-bar.md\n",
    }

    result = _rename(texts, own=(a, b), dates={a: FIRST, b: date(2026, 11, 4)})

    assert isinstance(result, DatedRenaming)
    assert dict(result.moves) == {a: f"{ADR}20261110-foo.md", b: f"{ADR}20261104-bar.md"}
    assert result.texts["docs/n.md"] == (
        "see docs/adr/20261110-foo.md and docs/adr/20261104-bar.md\n"
    )


def test_a_line_master_holds_is_master_s_and_a_citation_of_a_colliding_number_is_not_guessed():
    notes = "ADR-0079 is master's here\nADR-0079 is mine\n"
    master_notes = "ADR-0079 is master's here\n"
    texts = {MINE: FRONT, "docs/notes.md": (master_notes, notes)}

    # Master holds 0079 under another name and no commit tree tells which the line meant.
    result = _rename(texts, master=("0079-bar.md",))
    assert isinstance(result, Declined) and result.cause == "unrewritable"
    assert "docs/notes.md:2" in result.detail

    # The commit that wrote the line had no 0079 but the branch's own: it meant the branch's.
    line = OwnLine("docs/notes.md", "c" * 40, "ADR-0079 is mine")
    result = _rename(texts, master=("0079-bar.md",), lines=(line,), trees={"c" * 40: ()})
    assert isinstance(result, DatedRenaming)
    assert result.texts["docs/notes.md"] == "ADR-0079 is master's here\nADR-20261110-foo is mine\n"

    # The commit's tree already held master's 0079: the line cited master's.
    result = _rename(
        texts, master=("0079-bar.md",), lines=(line,), trees={"c" * 40: ("0079-bar.md",)}
    )
    assert isinstance(result, Declined) and result.cause == "unrewritable"


def test_a_citation_of_a_number_two_own_adrs_share_cannot_be_told_apart():
    a, b = f"{ADR}0079-foo.md", f"{ADR}0079-bar.md"
    result = _rename({a: FRONT, b: "# bar\n", "docs/n.md": "see ADR-0079\n"}, own=(a, b))
    assert isinstance(result, Declined) and result.cause == "unrewritable"


def test_undated_carries_the_own_adrs():
    own = (OwnAdr(MINE, "c" * 40),)
    assert Undated(own).own == own
