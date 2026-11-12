"""GH-591 part 2: the `risk_hits` net between a delivery and its review (branch step)."""

import pytest

from agentic.core.process.application.ports import RiskArea
from agentic.core.process.application.risk_net import risk_net
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, MemoryWorkspace, memory_ports

pytestmark = pytest.mark.anyio

AREAS = (RiskArea("data-migration", "Converts data", ("migrations/**",)),)


def _state(**subtask):
    return {
        "id": "GH-1",
        "subtask_id": "a",
        "subtasks": {"a": {"id": "a", "branch": "agentic/GH-1.a", "base": "abc", **subtask}},
    }


def _ports(touched=(), error=None, areas=AREAS):
    workspace = MemoryWorkspace()
    workspace.touched = touched
    workspace.touched_error = error
    return memory_ports(workspace=workspace, stack=MemoryStack(risk_areas=areas))


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_a_diff_under_a_declared_glob_leaves_a_risk_area_request(cfg):
    ports = _ports(touched=("src/x.py", "migrations/0001.sql"))
    patch = await risk_net(_state(), cfg, ports)
    assert patch["subtasks"]["a"]["promote_request"] == {
        "kind": "risk_area",
        "from_role": "system",
        "text": "data-migration",
    }
    assert ports.workspace.touched_reads == [("abc", "agentic/GH-1.a")]


async def test_a_diff_outside_every_glob_leaves_nothing(cfg):
    assert await risk_net(_state(), cfg, _ports(touched=("src/x.py",))) == {}


async def test_an_unreadable_diff_is_a_request_with_the_text_unreadable(cfg):
    patch = await risk_net(_state(), cfg, _ports(error="boom"))
    request = patch["subtasks"]["a"]["promote_request"]
    assert (request["kind"], request["text"]) == ("risk_area", "unreadable")


async def test_an_invalid_glob_is_unreadable_never_none(cfg):
    areas = (RiskArea("broken", "x", ("[",)),)
    patch = await risk_net(_state(), cfg, _ports(touched=("a.py",), areas=areas))
    assert patch["subtasks"]["a"]["promote_request"]["text"] == "unreadable"


async def test_a_role_request_already_stored_is_replaced_by_the_risk_area_one(cfg):
    stored = {"kind": "role", "from_role": "Developer: a", "text": "bigger"}
    patch = await risk_net(
        _state(promote_request=stored), cfg, _ports(touched=("migrations/1.sql",))
    )
    assert patch["subtasks"]["a"]["promote_request"]["kind"] == "risk_area"
