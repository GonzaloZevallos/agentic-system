"""GH-690: the agentic-item marker (comment and open_issue) is told apart from every other
marker the system writes, and the other way round. The strings are the ones each module
produces."""

import pytest

from agentic.core.process.application import tracker_only
from agentic.core.process.domain import landing, rules, stack_review, tracker_items

TASK = "GH-690"
COMMENT = {"kind": "comment", "text": "Superseded by this issue."}
OPEN_ISSUE = {"kind": "open_issue", "title": "Track the retry", "body": "Line one.\nLine two."}
ITEM_MARKERS = [
    tracker_items.marker(COMMENT, TASK),
    tracker_items.marker(OPEN_ISSUE, TASK),
]
ITEM_IDS = ["comment", "open_issue"]


def _checklist(text: str) -> bool:
    return tracker_only._EARLIER.search(text) is not None


def _checklist_text(text: str) -> bool:
    return rules._MARKER_TEXT.search(text) is not None


def _followup(text: str) -> bool:
    return landing.marker_lineage(text, TASK) is not None


def _flake(text: str) -> bool:
    ref = landing.IssueRef(url="u", title="Other", body=text)
    return landing.flake_issue_for([ref], "pytest") is not None


def _split(text: str) -> bool:
    return rules.split_part_of(text) is not None


def _stack_review(text: str) -> bool:
    return stack_review.open_proposal([("u", text)]) is not None


MATCHERS = {
    "checklist _EARLIER": (_checklist, f"<!-- agentic-tracker: {TASK} 0123456789ab -->"),
    "rules._MARKER_TEXT": (_checklist_text, "<!-- agentic-tracker: GH-1 0123456789ab -->"),
    "followup": (_followup, landing.followup_marker(TASK, 1)),
    "flake": (_flake, landing.flake_marker("pytest")),
    "split": (_split, rules.split_marker(TASK, 1, 1)),
    "stack-review": (_stack_review, stack_review.marker("abc123")),
}


@pytest.mark.parametrize("name", MATCHERS)
@pytest.mark.parametrize("item_marker", ITEM_MARKERS, ids=ITEM_IDS)
def test_no_other_matcher_matches_an_agentic_item_marker(name, item_marker):
    matcher, _ = MATCHERS[name]
    assert not matcher(f"Some body.\n\n{item_marker}")


@pytest.mark.parametrize("name", MATCHERS)
def test_the_matchers_themselves_do_match_their_own_marker(name):
    # The control of the table above: a matcher that matched nothing would pass it for free.
    matcher, own = MATCHERS[name]
    assert matcher(f"Some body.\n\n{own}")


@pytest.mark.parametrize("name", MATCHERS)
def test_the_agentic_item_marker_text_matches_no_other_marker(name):
    _, own = MATCHERS[name]
    for item_marker in ITEM_MARKERS:
        assert item_marker not in own
        assert not own.startswith("<!-- agentic-item:")


def test_the_agentic_item_marker_matches_none_of_the_done_marker_either():
    done = tracker_items.done_marker(TASK, ["0123456789ab"])
    assert all(item_marker not in done for item_marker in ITEM_MARKERS)
    assert not any(done in item_marker for item_marker in ITEM_MARKERS)


def test_a_flaky_check_title_is_still_adopted_by_the_flake_path():
    # A known bound, pinned and not fixed: `flake_issue_for` also finds its issue by the exact
    # title `Flaky check: <check>`, so an `open_issue` given that title can be adopted by it.
    body = f"Why it flakes.\n\nFrom task: #690\n\n{tracker_items.marker(OPEN_ISSUE, TASK)}"
    ref = landing.IssueRef(url="http://x/5", title="Flaky check: pytest", body=body)
    assert landing.flake_issue_for([ref], "pytest") == ref
