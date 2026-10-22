"""An item `reflect_task` writes is judged by `playbook_window.compare` (ADR-0043).

The two halves were built apart: the reflect use case stamps an item's `since`, and the
window matches items by `since` against the segment key of the tasks that ran with the
section. This pins that they agree, end to end: a lesson produced by the reflect path,
then a worse category after it, is flagged for retirement; a better one is kept."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentic.core.measurement.domain import playbook_window as window
from agentic.core.process.application.ports import RawResult
from agentic.core.process.application.reflect import reflect_task
from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain.roles import prompt_hash
from agentic.shared.configuration import Configuration
from tests.fakes import ScriptedRunner, memory_ports

pytestmark = pytest.mark.anyio

CATEGORY = "standard-other"
PAIR = "Tech Lead→Developer:parser"
LESSON = "Run every declared check before delivering, not only the single test file."


def reflected_document() -> dict[str, Any]:
    return {
        "id": "GH-1",
        "type": "feature",
        "closed_at": "2026-10-01T00:00:00+00:00",
        "delivery_outcome": "merged",
        "change_history": [{"pair": PAIR, "category": CATEGORY, "reason": "unrun"}],
        "sections": {"Tech Lead: review parser": "### Verdict\n\nreview"},
    }


async def written_playbook(tmp_path: Path, existing: pb.Playbook | None = None) -> pb.Playbook:
    reply = RawResult(
        {
            "markdown": "### Reflection\n\nDone.",
            "deltas": [
                {
                    "op": "add",
                    "slug": "developer",
                    "category": CATEGORY,
                    "lesson": LESSON,
                    "item_id": "",
                    "tasks": ["GH-1"],
                    "confidence": "high",
                }
            ],
        },
        0.1,
        1,
        "r1",
    )
    result = await reflect_task(
        Configuration(product_dir=tmp_path),
        memory_ports(ScriptedRunner(reflector=[reply])),
        reflected_document(),
        {"developer": existing} if existing else {},
        [],
        now="2026-10-05",
    )
    assert result.applied, result.refused
    return result.playbooks["developer"]


def task(n: int, key: str | None, returns: int) -> dict[str, Any]:
    document: dict[str, Any] = {
        "id": f"T-{n}",
        "type": "feature",
        "closed_at": f"2026-10-01T00:{n // 60:02d}:{n % 60:02d}+00:00",
        "return_history": [{"pair": PAIR, "category": CATEGORY, "reason": "r"}] * returns,
    }
    if key is not None:
        document["playbooks"] = {"developer": key}
    return document


def around(playbook: pb.Playbook, before: int, after: int) -> list[dict[str, Any]]:
    """10 tasks before the lesson (no playbook), 10 under the section it creates; `before`
    and `after` of them carry one return of the lesson's category."""
    key = prompt_hash(pb.section(playbook))
    return [task(i, None, int(i < before)) for i in range(10)] + [
        task(10 + i, key, int(i < after)) for i in range(10)
    ]


async def test_a_worse_category_after_a_reflected_lesson_flags_it_for_retirement(tmp_path):
    playbook = await written_playbook(tmp_path)

    [comparison] = window.compare(around(playbook, before=2, after=5), playbook)

    assert comparison.conclusive
    assert comparison.retire == (playbook.items[0].id,)


async def test_a_better_category_after_a_reflected_lesson_keeps_it(tmp_path):
    playbook = await written_playbook(tmp_path)

    [comparison] = window.compare(around(playbook, before=5, after=2), playbook)

    assert comparison.conclusive
    assert comparison.retire == ()


async def test_a_lesson_added_to_a_non_empty_playbook_is_judged_at_its_own_boundary(tmp_path):
    first = pb.Item("developer-1", "Keep diffs small.", "spec-behaviour", "old", ("GH-0",), "d")
    playbook = await written_playbook(tmp_path, pb.Playbook("developer", (first,)))
    new = next(i for i in playbook.items if i.lesson == LESSON)
    old_key = prompt_hash(pb.section(pb.Playbook("developer", (first,))))
    docs = [task(i, old_key, int(i < 2)) for i in range(10)] + [
        task(10 + i, prompt_hash(pb.section(playbook)), int(i < 5)) for i in range(10)
    ]

    [comparison] = window.compare(docs, playbook)

    assert comparison.retire == (new.id,)
