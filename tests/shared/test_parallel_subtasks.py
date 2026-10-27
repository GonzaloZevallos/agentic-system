"""`Configuration.max_parallel_subtasks` and its resolved `parallel_subtasks` (GH-364)."""

import pytest
from pydantic import ValidationError

from agentic.shared.configuration import Configuration

PRODUCT = {"product_dir": "/tmp/p"}


def load(**env):
    return Configuration.from_sources(PRODUCT, env)


def test_unset_derives_as_four_capped_by_the_role_semaphore():
    assert load(AGENTIC_MAX_CONCURRENT_ROLES="2").parallel_subtasks == 2
    assert load(AGENTIC_MAX_CONCURRENT_ROLES="6").parallel_subtasks == 4
    assert load().parallel_subtasks == 4
    assert load().max_parallel_subtasks is None


def test_an_explicit_value_within_the_semaphore_is_kept():
    cfg = load(AGENTIC_MAX_PARALLEL_SUBTASKS="3", AGENTIC_MAX_CONCURRENT_ROLES="4")
    assert cfg.parallel_subtasks == 3
    equal = load(AGENTIC_MAX_PARALLEL_SUBTASKS="4", AGENTIC_MAX_CONCURRENT_ROLES="4")
    assert equal.parallel_subtasks == 4


def test_an_explicit_value_above_the_semaphore_is_rejected():
    with pytest.raises(ValidationError):
        load(AGENTIC_MAX_PARALLEL_SUBTASKS="5", AGENTIC_MAX_CONCURRENT_ROLES="4")


def test_an_explicit_value_below_one_is_rejected():
    with pytest.raises(ValidationError):
        load(AGENTIC_MAX_PARALLEL_SUBTASKS="0")


def test_an_environment_valid_before_the_setting_still_loads():
    # Roles below the derived default of four: nothing the human set is refused.
    cfg = load(AGENTIC_MAX_CONCURRENT_ROLES="1")
    assert cfg.max_concurrent_roles == 1
    assert cfg.parallel_subtasks == 1
