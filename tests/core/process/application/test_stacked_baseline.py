"""GH-897, characterization: what a document WITHOUT `stack_parent` saves, returns and renders
today at the three places the stacked records and the PR line hook into, compared whole.

The only difference the stacking records may ever make is the `runs_base` key on an entry
reopened through `landing.with_cost_base`; `_without_runs_base` strips exactly that key and
nothing else, and the snapshots below never contain a `stack_restacks` or `stack_pauses` key."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentic.core.process.application import deliver as deliver_node
from agentic.core.process.application import landing_sync
from agentic.core.process.application.deliver import deliver
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_landing_sync import (
    HEAD,
    MASTER,
    NEW,
    _behind,
    _diffs,
    _document,
)
from tests.core.process.application.test_landing_unsynced import _land
from tests.fakes import MemoryStack, memory_ports

AT = "2026-11-17T00:00:00+00:00"


class _Clock:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 11, 17, tzinfo=UTC)


def _without_runs_base(entries: list[dict]) -> list[dict]:
    """The one allowed difference: `runs_base` on an entry reopened through `with_cost_base`."""
    return [{k: v for k, v in entry.items() if k != "runs_base"} for entry in entries]


def _saved(tmp_path, monkeypatch, *, brings_nothing: bool) -> dict:
    monkeypatch.setattr(landing_sync, "_now", lambda: AT)
    ports, _ = _behind(tmp_path)
    _diffs(ports, merged=("upstream.py",), own=("feature.py",))
    ports.workspace.merge_brings_nothing = brings_nothing
    _land(ports, tmp_path)
    return dict(_document(ports))


def test_the_clean_sync_saves_this_whole_document(tmp_path, monkeypatch):
    document = _saved(tmp_path, monkeypatch, brings_nothing=False)

    assert {**document, "landing": _without_runs_base(document["landing"])} == {
        "id": "GH-5",
        "landing": [
            {
                "at": "2026-10-14T00:00:00+00:00",
                "cost_usd": 0.0,
                "head": HEAD,
                "refusal": "attempts_exhausted",
                "resolved_at": AT,
                "step": "sync_master",
                "upstream": MASTER,
            }
        ],
        "pull_request": "https://github.com/o/r/pull/5",
        "review_scopes": [
            {"at": AT, "base": HEAD, "cause": "clean", "head": NEW, "scope": "carried"}
            | {"unit": "only"}
        ],
        "status": "ready_for_review",
        "subtasks": {
            "only": {
                "id": "only",
                "reviewed_head": NEW,
                "reviewed_head_carried_from": HEAD,
                "status": "approved",
            }
        },
    }


def test_the_unsynced_sync_saves_this_whole_document(tmp_path, monkeypatch):
    document = _saved(tmp_path, monkeypatch, brings_nothing=True)

    assert {**document, "landing": _without_runs_base(document["landing"])} == {
        "id": "GH-5",
        "landing": [
            {
                "at": "2026-10-14T00:00:00+00:00",
                "closed_at": AT,
                "cost_usd": 0.0,
                "head": HEAD,
                "lacks": MASTER,
                "refusal": "attempts_exhausted",
                "step": "sync_master",
                "upstream": MASTER,
            }
        ],
        "pull_request": "https://github.com/o/r/pull/5",
        "status": "ready_for_review",
        "subtasks": {"only": {"id": "only", "reviewed_head": HEAD, "status": "approved"}},
    }


def _entries():
    return [
        {
            "head": HEAD,
            "step": "sync_master",
            "refusal": "merge_conflict",
            "at": "2026-10-14T00:00:00+00:00",
            "upstream": MASTER,
            "cost_base": 1.0,
        }
    ]


def test_closed_landing_resolves_a_sync_whose_head_holds_the_upstream(monkeypatch, tmp_path):
    monkeypatch.setattr(deliver_node, "datetime", _Clock)
    ports = memory_ports()
    ports.workspace.ancestors.add(MASTER)

    closed = deliver_node._closed_landing(_entries(), {"cost_usd": 3.5}, tmp_path, ports)

    assert closed == [{**_entries()[0], "resolved_at": AT, "cost_usd": 2.5}]


def test_closed_landing_closes_a_sync_whose_head_lacks_the_upstream(monkeypatch, tmp_path):
    monkeypatch.setattr(deliver_node, "datetime", _Clock)
    ports = memory_ports()

    closed = deliver_node._closed_landing(_entries(), {"cost_usd": 3.5}, tmp_path, ports)

    assert closed == [{**_entries()[0], "closed_at": AT, "lacks": MASTER, "cost_usd": 2.5}]


@pytest.mark.anyio
async def test_the_body_of_a_document_without_stack_parent_is_this_whole_text(tmp_path):
    ports = memory_ports(stack=MemoryStack())
    state = {
        "id": "GH-12",
        "title": "Dashboard",
        "description": "How is my gym doing.\n\n<!-- agentic-closes: #12 -->",
        "type": "feature",
        "sections": {"PM": "### Consolidated definition\n\nSix numbers."},
    }

    await deliver(state, Configuration(product_dir=tmp_path), ports)

    assert ports.tracker.pull_requests[0][2] == (
        "Closes #12\n\n## PM\n\n### Consolidated definition\n\nSix numbers."
    )
