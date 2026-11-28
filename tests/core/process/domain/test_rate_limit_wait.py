"""`RateLimitReading` (GH-1157): what `gh api rate_limit` said, as an immutable mapping."""

import dataclasses

import pytest

from agentic.core.process.domain.rate_limit_wait import RateLimitReading


def test_a_reading_exposes_its_resources_and_cannot_be_changed():
    reading = RateLimitReading({"core": (0, 90.0), "graphql": (4999, 10.0)})
    assert reading.resources["core"] == (0, 90.0)
    assert dict(reading.resources) == {"core": (0, 90.0), "graphql": (4999, 10.0)}
    with pytest.raises(TypeError):
        reading.resources["core"] = (5, 1.0)  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.resources = {}  # type: ignore[misc]


def test_a_reading_does_not_follow_the_dict_it_was_built_from():
    source = {"core": (0, 90.0)}
    reading = RateLimitReading(source)
    source["search"] = (1, 2.0)
    assert "search" not in reading.resources
