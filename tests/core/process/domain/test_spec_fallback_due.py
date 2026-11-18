"""`rules.spec_fallback_due` (GH-594 part 5a): the one place that says a draft Spec is due."""

import pytest

from agentic.core.process.domain import rules

FULL_FEATURE = {"type": "feature", "path": "full"}


@pytest.mark.parametrize(
    ("state", "due"),
    [
        (FULL_FEATURE, True),
        ({"type": "feature"}, True),  # a missing path reads as full
        ({"type": "feature", "path": "full", "spec": {}}, True),
        ({"type": "bug", "path": "full"}, False),
        ({"type": "platform_enhancement", "path": "full"}, False),
        ({"type": "feature", "path": "light"}, False),
        ({"type": "feature", "path": "full", "evaluation": {"cohort": "x"}}, False),
        ({"type": "feature", "path": "full", "spec": {"slice": "a slice"}}, False),
        ({}, False),
    ],
    ids=[
        "full feature",
        "feature without a path",
        "empty spec",
        "bug",
        "platform enhancement",
        "light feature",
        "evaluation run",
        "feature with a spec",
        "empty state",
    ],
)
def test_the_draft_is_due_only_for_a_full_feature_without_a_spec_outside_an_evaluation(state, due):
    assert rules.spec_fallback_due(state) is due
