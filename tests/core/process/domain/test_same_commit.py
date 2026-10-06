"""`rules.same_commit` and `rules.full_commit_id` (GH-199): the one comparison of commit
ids that may involve a recorded `reviewed_head` or `base`, and the one test of a full id.
Pure: one row per input and the answer it must give (ADR-0023)."""

import pytest

from agentic.core.process.domain import rules

FULL = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"
FULL64 = "0258d6f" + "1" * 57
OTHER_SAME_7 = "0258d6f" + "e" * 33
OTHER64_SAME_7 = "0258d6f" + "2" * 57

SAME_COMMIT = [
    (None, None, False),
    (None, FULL, False),
    (FULL, None, False),
    ("", "", False),
    ("", FULL, False),
    (FULL, "", False),
    ("\n", "\n", False),
    ("  ", "  ", False),
    (" abc1234 ", "abc1234", True),
    (FULL[:6], FULL, False),
    (FULL, FULL[:6], False),
    (FULL[:7], FULL, True),
    (FULL, FULL[:7], True),
    (FULL.upper(), FULL, True),
    (FULL, FULL.upper(), True),
    (FULL[:7].upper(), FULL, True),
    (FULL, FULL[:7].upper(), True),
    (FULL, OTHER_SAME_7, False),
    (FULL64, FULL64[:7], True),
    (FULL64[:7], FULL64, True),
    (FULL64, OTHER64_SAME_7, False),
    ("base", "base", True),
    ("BASE", "base", True),
    ("base", "head1", False),
    ("head1", "head", False),
    ("zzzzzzzz", "zzzzzzzzzz", False),
]


@pytest.mark.parametrize(("a", "b", "same"), SAME_COMMIT)
def test_same_commit(a, b, same):
    assert rules.same_commit(a, b) is same


FULL_COMMIT_ID = [
    (FULL, True),
    (FULL.upper(), True),
    (f" {FULL}\n", True),
    (FULL64, True),
    (FULL[:39], False),
    (FULL + "1", False),
    (FULL64[:63], False),
    (FULL[:7], False),
    ("g" * 40, False),
    ("", False),
    ("  ", False),
    (None, False),
]


@pytest.mark.parametrize(("value", "full"), FULL_COMMIT_ID)
def test_full_commit_id(value, full):
    assert rules.full_commit_id(value) is full


def test_the_lengths_of_a_full_id_are_written_once():
    assert rules.FULL_SHA_LENGTHS == (40, 64)
    assert rules.MIN_SHA_PREFIX == 7
