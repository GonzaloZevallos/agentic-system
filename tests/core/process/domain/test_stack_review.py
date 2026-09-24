"""Pure text algebra of a stack review's issue (ADR-0032): the marker, the assembled
body, its cap, and the open-proposal lookup -- no port, no filesystem."""

from agentic.core.process.domain import stack_review as texts


def test_marker_carries_the_anchor():
    assert texts.marker("abc123") == "<!-- agentic:stack-review anchor=abc123 -->"


def test_body_assembles_marker_signals_and_every_non_empty_piece():
    text = texts.body("abc123", "10 tasks merged", "keep the stack", "- a diff", "", "an ADR")
    assert text.startswith(texts.marker("abc123"))
    assert "## Signals\n\n10 tasks merged" in text
    assert "## Verdict\n\nkeep the stack" in text
    assert "## Stack diff\n\n- a diff" in text
    assert "## ADR draft\n\nan ADR" in text
    assert "## Standards" not in text  # empty piece is skipped, not an empty heading


def test_body_refusal_accepts_a_body_under_the_cap():
    text = texts.body("abc123", "signals", "verdict", "diff", "standards", "adr")
    assert texts.body_refusal(text) is None


def test_body_refusal_rejects_an_over_cap_body_naming_the_count_and_a_piece():
    huge = "x" * (texts.ISSUE_BODY_MAX_CHARS + 1)
    text = texts.body("abc123", "signals", "verdict", "", huge, "")
    refusal = texts.body_refusal(text)
    assert refusal is not None
    assert str(len(text)) in refusal
    assert str(texts.ISSUE_BODY_MAX_CHARS) in refusal
    assert "Standards" in refusal


def test_open_proposal_is_none_when_no_open_issue_carries_the_marker():
    assert texts.open_proposal([("https://x/1", "unrelated body")]) is None


def test_open_proposal_matches_the_marker_as_a_plain_substring():
    issues = [
        ("https://x/1", "unrelated"),
        ("https://x/2", f"before\n\n{texts.marker('deadbeef')}\n\nafter"),
    ]
    assert texts.open_proposal(issues) == "https://x/2"


def test_open_proposal_never_needs_the_anchor_to_match_a_stale_one():
    # A stale anchor still suppresses (ADR-0032): two open proposals is never wanted.
    issues = [("https://x/3", texts.marker("old-sha"))]
    assert texts.open_proposal(issues) == "https://x/3"
