"""The endings with no pull request write `lead_time: {unknown: no_merge}` (GH-599)."""

import pytest

from agentic.core.process.application.discard import discard
from agentic.core.process.application.split import split
from agentic.core.process.application.tracker_only import tracker_only
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_split import STATE as SPLIT_STATE
from tests.core.process.application.test_split import ports_with_numbered_issues
from tests.fakes import memory_ports

pytestmark = pytest.mark.anyio

NO_MERGE = {"unknown": "no_merge"}


async def test_a_discarded_task_ends_with_no_merge(tmp_path):
    state = {"id": "GH-5", "title": "t", "description": "d", "status": "planned"}
    patch = await discard(state, Configuration(product_dir=tmp_path), memory_ports())
    assert patch["status"] == "discarded" and patch["lead_time"] == NO_MERGE


async def test_a_split_task_ends_with_no_merge(tmp_path):
    patch = await split(
        SPLIT_STATE, Configuration(product_dir=tmp_path), ports_with_numbered_issues()
    )
    assert patch["status"] == "split" and patch["lead_time"] == NO_MERGE


async def test_a_tracker_only_task_ends_with_no_merge(tmp_path):
    state = {
        "id": "GH-6",
        "title": "t",
        "description": "d",
        "status": "planned",
        "tracker_checklist": ["Rotate the key"],
    }
    patch = await tracker_only(state, Configuration(product_dir=tmp_path), memory_ports())
    assert patch["status"] == "tracker_only" and patch["lead_time"] == NO_MERGE


async def test_a_split_that_stops_on_a_failed_comment_writes_no_lead_time(tmp_path):
    ports = ports_with_numbered_issues()

    def broken(*_args):
        raise ValueError("gh: boom")

    ports.tracker.comment = broken
    patch = await split(SPLIT_STATE, Configuration(product_dir=tmp_path), ports)
    assert "lead_time" not in patch
