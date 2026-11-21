"""`spec_delivery.draft_reading`, `draft_path` and `draft_text` (GH-594 part 5a): the pure rules of
the draft Spec the system writes."""

import pytest

from agentic.core.process.domain import spec_delivery
from agentic.core.process.domain.spec_delivery import (
    SpecContract,
    body_sha256,
    draft_path,
    draft_reading,
    draft_text,
    spec_path_ok,
)

CONTRACT: SpecContract = {
    "path": "docs/specs/2026-11-17-x.md",
    "slice": "x",
    "commit": "a" * 40,
    "sha256": "b" * 64,
    "criteria": [],
    "screens": [],
    "risks": [],
    "touches_ui": False,
    "touches_copy": False,
}


@pytest.mark.parametrize(
    ("result", "valid"),
    [
        ("spec_unapproved", True),
        ("spec_invalid", False),
        ("slice_unknown", False),
        ("slice_missing", False),
        (CONTRACT, False),  # an approved spec is not a draft
    ],
)
def test_only_unapproved_counts_as_a_valid_draft(result, valid):
    assert draft_reading(result, None).ok is valid


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Export report", "export-report"),
        ("  Café: Ñandú & más!! ", "cafe-nandu-mas"),
        ("日本語", "spec"),  # nothing ASCII left: the fallback slug
        ("", "spec"),
        ("a" * 80, "a" * 60),
        ("a" * 59 + " b", "a" * 59),  # cut at 60, no dangling hyphen
    ],
)
def test_the_draft_path_is_dated_and_slugged_from_the_title(title, slug):
    path = draft_path("2026-11-17", title, "GH-1", frozenset())
    assert path == f"docs/specs/2026-11-17-{slug}.md"
    assert spec_path_ok(path)


def test_a_taken_path_gets_the_task_id_appended():
    taken = frozenset({"docs/specs/2026-11-17-export-report.md"})
    path = draft_path("2026-11-17", "Export report", "GH-647", taken)
    assert path == "docs/specs/2026-11-17-export-report-gh-647.md"
    assert spec_path_ok(path)


def test_a_date_that_is_not_a_day_is_refused():
    with pytest.raises(ValueError, match="date"):
        draft_path("17/11/2026", "x", "GH-1", frozenset())


def test_the_draft_text_is_the_draft_front_matter_then_the_body_and_hashes_the_body():
    text = draft_text("# Title\n")
    assert text == spec_delivery.render_draft_front_matter() + "# Title\n"
    assert text.startswith("---\nstatus: draft\n---\n")
    assert body_sha256(text) == body_sha256(draft_text("# Title\n"))
    assert body_sha256(text) != body_sha256(draft_text("# Title"))
