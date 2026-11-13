"""GH-680: the check-gate priority is picked from the step, in one pure place.
Seam: `gate_priority` and `landing_step_priority`."""

from __future__ import annotations

import pytest

from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.core.process.domain.gate_priority import gate_priority, landing_step_priority
from agentic.core.process.domain.task import INTEGRATION


@pytest.mark.parametrize(
    "unit,in_landing,expected",
    [
        ("api", False, CheckPriority.SUBTASK),
        (INTEGRATION, False, CheckPriority.INTEGRATION),
        ("api", True, CheckPriority.LAND),
        (INTEGRATION, True, CheckPriority.LAND),
    ],
)
def test_the_priority_follows_the_unit_and_the_landing_pass(unit, in_landing, expected):
    assert gate_priority(unit, in_landing) is expected


def test_a_landing_step_outside_develop_asks_as_land():
    assert landing_step_priority() is CheckPriority.LAND
