"""The seam GH-625 part 3a prepares under `tracker_only` and `deliver`, pinned before it moves.

A plan with a legacy `tracker_checklist` and a plan with only `human` items behave today in
one way, and they must behave so after the per-kind table exists: the state patches and the
comment bodies are literals, and a counting tracker and inbox prove that no plan without a
system item opens an issue, creates a label, lists the open issues or reads an issue's
comments a second time.

Seams: `application.tracker_only` and `application.deliver` with `MemoryTracker`/`MemoryInbox`
doubles, the same two the other tests of those nodes use.
"""

from collections import Counter
from collections.abc import Sequence

import pytest

from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.ports import IssueRef
from agentic.core.process.application.tracker_only import tracker_only
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryInbox, MemoryTracker, memory_ports

pytestmark = pytest.mark.anyio

LEGACY = ["Post the verdict table on #307", "Close the follow-ups that are done"]
HUMAN = [{"kind": "human", "question": "Ship it?", "recommendation": "Yes"}]
BASE = {
    "id": "GH-307",
    "title": "Triage the follow-ups",
    "description": "Triage.",
    "type": "feature",
    "status": "planned",
    "sections": {"PM": "### Consolidated definition\n\nX."},
}
RECORD_HASH = "b79d12a83a29"
ITEM_HASH = "5cd4fee0b385"
RAISED_AT = "2026-11-06T12:00:01.000+00:00"
CHECKLIST_COMMENT = (
    "Tracker work for you: the system has no access to do this part, so it is yours.\n\n"
    "- [ ] Post the verdict table on #307\n"
    "- [ ] Close the follow-ups that are done\n\n"
    "Task document: `.agentic/tasks/GH-307.md`\n\n"
    f"<!-- agentic-tracker: GH-307 {RECORD_HASH} -->"
)
READY_COMMENT = (
    "Ready for review: https://github.com/o/r/pull/7\n\nTask document: `.agentic/tasks/GH-307.md`"
)
ESCALATION = {
    "current_role": "PjM",
    "escalated": "the plan leaves 1 decision(s) to the owner",
    "escalation_cause": None,
    "escalation_class": "questions",
    "questions_for_human": ["Ship it? — Recommended: Yes"],
    "status": "waiting_on_human",
    "tracker_human": [ITEM_HASH],
}
# What the clock gives the patch: it moves with every run, so the tests drop it and check
# only that it is there.
CLOCK_KEYS = ("finished_at", "pushed_at")


class CountingTracker(MemoryTracker):
    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()

    def issue_comments(self, task_id: str) -> list[str]:
        self.calls["issue_comments"] += 1
        return super().issue_comments(task_id)

    def open_issue(
        self,
        title: str,
        body: str,
        *,
        label: str | None = None,
        labels: Sequence[str] = (),
    ) -> str:
        self.calls["open_issue"] += 1
        return super().open_issue(title, body, label=label, labels=labels)

    def ensure_label(self, name: str) -> None:
        self.calls["ensure_label"] += 1
        super().ensure_label(name)


class CountingInbox(MemoryInbox):
    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()

    def open_issue_refs(self) -> list[IssueRef]:
        self.calls["open_issue_refs"] += 1
        return super().open_issue_refs()


def _without_clock(patch):
    assert all(patch[key] for key in CLOCK_KEYS if key in patch)
    return {k: v for k, v in patch.items() if k not in CLOCK_KEYS}


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


async def test_tracker_only_with_a_legacy_checklist_patches_and_posts_these_literals(cfg):
    ports = memory_ports()
    patch = await tracker_only({**BASE, "tracker_checklist": LEGACY}, cfg, ports)
    record = patch["tracker_checklist_posted"]
    assert record["hash"] == RECORD_HASH and record["at"]
    assert _without_clock(patch) == {
        "current_role": "PjM",
        "lead_time": {"unknown": "no_merge"},
        "status": "tracker_only",
        "tracker_checklist_posted": record,
    }
    assert ports.tracker.comments == [("GH-307", CHECKLIST_COMMENT)]
    assert ports.tracker.pull_requests == []


async def test_tracker_only_with_only_human_items_escalates_with_these_literals(cfg):
    ports = memory_ports()
    patch = await tracker_only({**BASE, "tracker_items": HUMAN}, cfg, ports)
    assert patch == {
        **ESCALATION,
        "pause_log": [{"at": RAISED_AT, "kind": "escalation", "value": "questions"}],
        "tracker_actions_done": {ITEM_HASH: {"at": RAISED_AT}},
    }
    assert ports.tracker.comments == [] and ports.tracker.pull_requests == []


async def test_deliver_with_a_legacy_checklist_patches_and_posts_these_literals(cfg):
    ports = memory_ports()
    patch = await deliver({**BASE, "tracker_checklist": LEGACY}, cfg, ports)
    record = patch["tracker_checklist_posted"]
    assert record["hash"] == RECORD_HASH and record["at"]
    assert _without_clock(patch) == {
        "branch": "agentic/GH-307",
        "current_role": "PjM",
        "landing_queued_at": RAISED_AT,
        "pull_request": "https://github.com/o/r/pull/7",
        "pushed_head": "base",
        "status": "ready_for_review",
        "tracker_checklist_posted": record,
    }
    assert ports.tracker.comments == [("GH-307", CHECKLIST_COMMENT), ("GH-307", READY_COMMENT)]
    assert ports.tracker.pull_requests == [
        ("agentic/GH-307", "Triage the follow-ups", "## PM\n\n### Consolidated definition\n\nX.")
    ]


async def test_deliver_with_only_human_items_opens_the_pr_then_escalates_with_these_literals(cfg):
    ports = memory_ports()
    patch = await deliver({**BASE, "tracker_items": HUMAN}, cfg, ports)
    later = "2026-11-06T12:00:02.000+00:00"
    assert _without_clock(patch) == {
        **ESCALATION,
        "branch": "agentic/GH-307",
        "landing_queued_at": RAISED_AT,
        "pause_log": [{"at": later, "kind": "escalation", "value": "questions"}],
        "pull_request": "https://github.com/o/r/pull/7",
        "pushed_head": "base",
        "tracker_actions_done": {ITEM_HASH: {"at": later}},
    }
    assert ports.tracker.comments == [("GH-307", READY_COMMENT)]
    assert ports.tracker.pull_requests == [
        ("agentic/GH-307", "Triage the follow-ups", "## PM\n\n### Consolidated definition\n\nX.")
    ]


@pytest.mark.parametrize("node", [tracker_only, deliver], ids=["tracker_only", "deliver"])
@pytest.mark.parametrize(
    ("extra", "reads"),
    [({"tracker_checklist": LEGACY}, 1), ({"tracker_items": HUMAN}, 0)],
    ids=["legacy checklist", "human items only"],
)
async def test_a_plan_without_system_items_touches_no_issue_and_reads_comments_as_before(
    cfg, node, extra, reads
):
    tracker, inbox = CountingTracker(), CountingInbox()
    await node({**BASE, **extra}, cfg, memory_ports(tracker=tracker, inbox=inbox))
    assert tracker.calls == Counter({"issue_comments": reads} if reads else {})
    assert inbox.calls == Counter()
