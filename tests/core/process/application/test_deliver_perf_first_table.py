"""`_perf_preface` copies only the system's measurement table, not the role's own rows."""

from agentic.core.process.application import deliver


def test_only_the_first_table_block_is_copied() -> None:
    section = (
        "Measured head: `abc`\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nrole text\n\n| role | row |"
    )
    assert deliver._first_table(section) == ["| a | b |", "| - | - |", "| 1 | 2 |"]


def test_no_table_gives_no_rows() -> None:
    assert deliver._first_table("only prose\nmore prose") == []
