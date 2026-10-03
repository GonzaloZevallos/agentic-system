"""`deliver`'s pull request body (ADR-0011), and the design branch's own
"Observations on the design" section (ADR-0040): present only when at least one
`observation` design item exists, absent otherwise, byte for byte as today."""

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

pytestmark = pytest.mark.anyio

TASK = {
    "id": "GH-12",
    "title": "Dashboard",
    "description": "How is my gym doing.",
    "type": "feature",
    "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_a_pull_request_with_no_design_items_is_byte_identical_to_today(cfg):
    ports = memory_ports()
    await deliver(TASK, cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert body == "## PM\n\n### Consolidated definition\n\nSix numbers."
    assert "design" not in body.lower()


async def test_an_observation_item_lands_in_its_own_pr_section_blocking_items_do_not(cfg):
    state = {
        **TASK,
        "design_items": [
            {"role_slug": "ux_lead", "kind": "observation", "step": "Pay", "text": "slow"},
            {
                "role_slug": "design_lead",
                "kind": "blocking",
                "step": "Cart",
                "text": "never here",
            },
        ],
    }
    ports = memory_ports()
    await deliver(state, cfg, ports)
    body = ports.tracker.pull_requests[0][2]
    assert "## Observations on the design" in body
    assert "| ux_lead | Pay | slow |" in body
    # A `blocking` item already escalated as a question for the human, or settled by
    # the integration review (`rules.return_section`'s own subtask): it never reaches
    # the pull request body as an observation.
    assert "never here" not in body
