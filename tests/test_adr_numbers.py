"""The backstop of the ADR numbering rule (GH-229, ADR-0049): no two ADR files in
`docs/adr` share a number. It runs on every branch that touches the directory, and on the
merged head the merge gate checks, so a collision the landing missed turns that merge red
instead of master. It does not check that references resolve."""

from pathlib import Path

from agentic.core.process.domain import adr_numbers

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"


def test_no_two_adr_files_share_a_number():
    names = sorted(p.name for p in ADR_DIR.iterdir())
    assert names, "docs/adr lists nothing"
    assert adr_numbers.duplicate_numbers(names) == []


def test_the_check_rejects_a_directory_where_two_files_share_a_number():
    names = ["0036-a.md", "0037-b.md", "0037-c.md"]
    assert adr_numbers.duplicate_numbers(names) == ["0037"]
