from agentic.core.measurement.domain.issue_listing import (
    PAGE_SIZE,
    ListedIssue,
    merge,
)


def issue(number, created="2026-01-01T00:00:00Z"):
    return ListedIssue(number, created, False, None)


def pull(number, created="2026-01-02T00:00:00Z", merged="2026-01-03T00:00:00Z"):
    return ListedIssue(number, created, True, merged)


def keyed(*items):
    return merge({}, items)


def test_the_page_size_is_what_github_is_asked_for():
    assert PAGE_SIZE == 100


def test_merge_keys_by_number_and_a_later_read_wins():
    first = merge({}, [issue(1, "a"), issue(2, "b")])
    second = merge(first, [issue(2, "c"), issue(3, "d")])
    assert {n: i.created_at for n, i in second.items()} == {1: "a", 2: "c", 3: "d"}


def test_merge_leaves_the_items_it_was_given_untouched():
    first = keyed(issue(1))
    merge(first, [issue(2)])
    assert list(first) == [1]
