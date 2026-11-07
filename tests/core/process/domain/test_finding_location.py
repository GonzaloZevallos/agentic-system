"""`finding_location` (GH-473): the file a finding's `location` names and the family of its
category, the two inputs the shadow report matches on. A location with no repo-relative file
gives `None`, so a shadow finding there is `unlocated`, never `missed`."""

from typing import get_args

import pytest

from agentic.core.process.domain import finding_location
from agentic.core.process.domain.task import CopyCategory, FindingCategory


@pytest.mark.parametrize(
    ("location", "file"),
    [
        ("agentic/a/b.py:12", "agentic/a/b.py"),
        ("a.py:3-9", "a.py"),
        ("a.py#L3", "a.py"),
        ("src/app.ts#L10-L20", "src/app.ts"),
        ("./a.py", "a.py"),
        ("./README.md", "README.md"),
        ("tests/x.py::test_a", "tests/x.py"),
        ("tests/x.py::T::t[a b]", "tests/x.py"),
        ("tests/x.py::t:12", "tests/x.py"),
        ("docs/adr", "docs/adr"),
        # Pinned residual: one-token slash prose localizes, the list of extensions cannot stop it.
        ("input/output", "input/output"),
    ],
)
def test_finding_file_reads_the_path_before_the_suffixes(location, file):
    assert finding_location.finding_file(location) == file


@pytest.mark.parametrize(
    "location",
    [
        "",
        "   ",
        "full_checks:ruff",
        "copy:1.2:loc",
        "/abs/a.py",
        "~/a.py",
        "../a.py",
        "a/../b.py",
        "https://x/a.py",
        "the whole integration",
        "integration",
        "Makefile",
        "a.unknownext",
    ],
)
def test_finding_file_gives_none_when_no_repo_relative_file_is_named(location):
    assert finding_location.finding_file(location) is None


def test_category_family_is_the_text_before_the_first_dash():
    assert finding_location.category_family("standard-typing-silenced") == "standard"
    assert finding_location.category_family("plain") == "plain"


def test_the_families_of_every_category_are_exactly_the_known_ones():
    families = {
        finding_location.category_family(c)
        for c in (*get_args(FindingCategory), *get_args(CopyCategory))
    }
    assert families == {"standard", "spec", "process", "plan", "integration", "copy"}
