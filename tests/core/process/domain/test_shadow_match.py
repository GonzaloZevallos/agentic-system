"""`rules.shadow_match` (GH-473): a shadow finding is matched to the single review's by the file
`finding_file` reads from the `location` and the category family, never by prose; a location with
no repo-relative file is `unlocated`, never `missed`."""

import pytest

from agentic.core.process.domain import rules


def found(location: str, category: str = "spec-behaviour", claim: str = "c") -> dict[str, str]:
    return {"location": location, "category": category, "claim": claim}


@pytest.mark.parametrize(
    ("location", "file"),
    [
        ("agentic/a/b.py:12", "agentic/a/b.py"),
        ("agentic/a/b.py:12-30", "agentic/a/b.py"),
        ("./README.md", "README.md"),
        ("src/app.ts#L10-L20", "src/app.ts"),
    ],
)
def test_finding_file_reads_the_path_before_the_line_suffix(location, file):
    assert rules.finding_file(location) == file


@pytest.mark.parametrize(
    "location",
    [
        "",
        "the whole integration",
        "/etc/passwd:3",
        "../x.py:1",
        "https://x.dev/a.py",
        "integration",
    ],
)
def test_finding_file_gives_none_when_no_repo_relative_file_is_named(location):
    assert rules.finding_file(location) is None


def test_the_same_file_and_family_is_matched_even_when_the_claim_and_line_differ():
    single = [found("a/b.py:99", "spec-scope", "something else entirely")]
    assert (
        rules.shadow_match(found("a/b.py:3", "spec-behaviour", "no relation"), single) == "matched"
    )


def test_another_file_or_family_is_missed():
    single = [found("a/b.py:1", "spec-behaviour")]
    assert rules.shadow_match(found("a/c.py:1", "spec-behaviour"), single) == "missed"
    assert rules.shadow_match(found("a/b.py:1", "standard-seams"), single) == "missed"
    assert rules.shadow_match(found("a/b.py:1"), []) == "missed"


def test_a_shadow_finding_with_no_file_is_unlocated_never_missed():
    single = [found("a/b.py:1")]
    assert rules.shadow_match(found("the plan as a whole"), single) == "unlocated"
    assert rules.shadow_match(found("../escape.py:1"), []) == "unlocated"
