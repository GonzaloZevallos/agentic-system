"""The Developer asks for a promotion, it never applies one (GH-591, part 2): the request is
stored on the Developer's own subtask, under its section's name, and a `risk_area` request
already there is never replaced. The node writes no `path` and no `promotion`; a refusal the
serialized code reports reaches the Developer's section through `promotion.refusal_notice`."""

from __future__ import annotations

import pytest

from agentic.core.process.application import promotion
from agentic.core.process.application.develop import develop
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import apply
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_delivery_tier import building
from tests.fakes import MemoryStack, RawResult, ScriptedRunner, delivered, memory_ports

pytestmark = pytest.mark.anyio

ASK = "the diff converts stored data"
RISK = {"kind": "risk_area", "from_role": "system", "text": "data-migration"}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def _asking(promote: str | None = ASK) -> RawResult:
    result = delivered()
    if promote is not None:
        result.structured_output["promote"] = promote
    return result


async def _develop(cfg, result: RawResult, **api):
    ports = memory_ports(ScriptedRunner(developer=[result]), stack=MemoryStack())
    state, _ = building(ports)
    state = {**state, "path": "light", "initial_path": "light"}
    state["subtasks"]["api"].update(api)
    return state, await develop(state, cfg, ports)


async def test_the_request_is_stored_on_the_developers_own_subtask_only(cfg):
    state, patch = await _develop(cfg, _asking())
    assert patch["subtasks"]["api"]["promote_request"] == {
        "kind": "role",
        "from_role": rules.developer_section("api"),
        "text": ASK,
    }
    assert set(patch["subtasks"]) == {"api"}
    assert "path" not in patch and "promotion" not in patch and "plan_promote_request" not in patch
    assert "promote_request" not in apply(state, patch)["subtasks"]["domain"]


async def test_a_developer_that_asks_nothing_leaves_the_subtask_without_the_key(cfg):
    _, patch = await _develop(cfg, _asking(promote=None))
    assert "promote_request" not in patch["subtasks"]["api"]


async def test_a_risk_area_request_already_stored_is_never_replaced(cfg):
    _, patch = await _develop(cfg, _asking(), promote_request=RISK)
    assert patch["subtasks"]["api"]["promote_request"] == RISK


async def test_a_delivery_that_asks_nothing_keeps_a_request_already_stored(cfg):
    _, patch = await _develop(cfg, _asking(promote=None), promote_request=RISK)
    assert patch["subtasks"]["api"]["promote_request"] == RISK


async def test_a_refusal_notice_lands_in_the_developers_section(cfg):
    state, patch = await _develop(cfg, _asking())
    after = apply(state, patch)
    section = patch["subtasks"]["api"]["promote_request"]["from_role"]
    notice = "The owner label `path:light` keeps the light path: promotion (role) not applied"
    noted = apply(after, promotion.refusal_notice(after, section, notice))
    assert noted["sections"][section].endswith(notice)
    assert noted["sections"][section].startswith(after["sections"][section].rstrip())
