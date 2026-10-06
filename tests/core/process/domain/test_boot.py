"""`boot_verdict`: is a record from this boot, another, or unknowable. Pure: both boot
ids arrive as arguments (GH-176)."""

import pytest

from agentic.core.process.domain.boot import boot_verdict


def test_a_different_boot_id_is_another_boot():
    assert boot_verdict("aaa", "bbb") == "other_boot"


def test_the_same_boot_id_is_the_same_boot():
    assert boot_verdict("aaa", "aaa") == "same_boot"


@pytest.mark.parametrize(("recorded", "current"), [(None, "aaa"), ("aaa", None), (None, None)])
def test_either_side_missing_is_unknown(recorded, current):
    assert boot_verdict(recorded, current) == "unknown"
