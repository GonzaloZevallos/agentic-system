"""GH-989 part 2: the poller retakes a GitHub rate-limit pause and, after 12 h, escalates it.

Poll rounds over the fake Ports with a fake clock: `gather_resumes` reads the pause's own `at`
(no `Escalated:` comment), `rate_limit_escalations` persists and notifies from the document
alone. Every instant is passed in; nothing reads a clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentic.core.process.application import poll
from agentic.core.process.application.ports import HeldListMayBeCut, IssueComment
from agentic.core.process.domain import rate_limit, retakes, rules
from agentic.core.process.domain.rate_limit import RATE_LIMIT_PERSISTED
from agentic.core.process.domain.retakes import RESUMED_BY_POLLER
from agentic.core.process.domain.task import apply
from tests.core.process.application.test_poll import _cfg, _Locks, _round
from tests.fakes import FakeLauncher, MemoryRunRegistry, memory_ports

AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PRODUCT = "demo"
OFFSET = rate_limit.reset_offset(PRODUCT)
PAUSE_TEXT = "GitHub rate limit outlasted the wait"
NOTICE = f"Escalated: {RATE_LIMIT_PERSISTED}"


def _stamp(instant: datetime) -> str:
    return instant.isoformat().replace("+00:00", "Z")


def _ports(**more):
    """Fake Ports whose clock reads five seconds after the 12 h are up, where a write stamps."""
    return memory_ports(now=lambda: _stamp(AT + timedelta(hours=12, seconds=5)), **more)


def _pause(ports, *, resets_at: datetime | None = None, number: int = 7, paused_at=AT) -> str:
    """The document `rate_limit_pause_patch` writes for a run whose wait ran out at `paused_at`."""
    task_id = f"GH-{number}"
    ports.stack.declared_product = PRODUCT
    patch = rate_limit.rate_limit_pause_patch(
        {"pause_log": []}, resets_at=resets_at, at=paused_at.isoformat()
    )
    ports.documents.save({"id": task_id, **patch})
    return task_id


def _say(ports, task_id: str, body: str, at: datetime) -> None:
    ports.tracker.timestamped.setdefault(task_id, []).append(IssueComment(body, _stamp(at)))


def _go(tmp_path, ports, now, *, lock=None, dry_run=False):
    launcher = FakeLauncher()
    lines = _round(
        _cfg(tmp_path),
        ports,
        MemoryRunRegistry(),
        launcher,
        now,
        lock=lock or _Locks(),
        dry_run=dry_run,
    )
    return lines, launcher


def _resumed(launcher) -> list[str]:
    return [argv[3] for argv in launcher.spawned if argv[1] == "resume"]


def _written(ports) -> tuple[list, list, int]:
    return ports.tracker.labels, ports.tracker.comments, len(ports.documents.saved)


# -- the retakes: from the pause's own instant, with no Escalated: comment ----------------------


def test_a_pause_without_a_reset_is_retaken_at_the_hourly_slot_and_not_before(tmp_path):
    ports = _ports()
    _pause(ports)
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=59, seconds=59))
    assert _resumed(launcher) == []
    lines, launcher = _go(tmp_path, ports, AT + timedelta(minutes=60))
    assert _resumed(launcher) == ["7"] and "--by-poller" in launcher.spawned[0]
    assert lines == ["agentic: poll: resume #7 -> pid 4242"]


def test_a_stated_reset_is_honoured_at_the_reset_plus_the_products_offset(tmp_path):
    ports = _ports()
    reset = AT + timedelta(minutes=20)
    _pause(ports, resets_at=reset)
    due = reset + OFFSET
    if OFFSET > timedelta(0):
        _, launcher = _go(tmp_path, ports, due - timedelta(seconds=1))
        assert _resumed(launcher) == []
    _, launcher = _go(tmp_path, ports, due)
    assert _resumed(launcher) == ["7"]


def test_a_poller_comment_after_the_pause_means_no_retake_before_an_hour_even_with_a_reset(
    tmp_path,
):
    # `prior` stuck at 0 would retake at the stated reset again: the count is what stops it.
    ports = _ports()
    task_id = _pause(ports, resets_at=AT + timedelta(minutes=10))
    _say(ports, task_id, f"{RESUMED_BY_POLLER}the poller resumed", AT + timedelta(minutes=5))
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=59))
    assert _resumed(launcher) == []
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=60))
    assert _resumed(launcher) == ["7"]


def test_a_second_limit_right_after_a_retake_honours_its_own_stated_reset(tmp_path):
    ports = _ports()
    task_id = _pause(ports, resets_at=AT + timedelta(minutes=10))
    _say(ports, task_id, f"{RESUMED_BY_POLLER}retake", AT + timedelta(minutes=11))
    second = AT + timedelta(minutes=12)
    patch = rate_limit.rate_limit_pause_patch(
        ports.documents.load(task_id) or {},
        resets_at=second + timedelta(minutes=5),
        at=second.isoformat(),
    )
    resumed = {"kind": "resumed", "at": _stamp(AT + timedelta(minutes=11))}
    state = ports.documents.load(task_id) or {}
    ports.documents.save(
        {
            **state,
            **{k: v for k, v in patch.items() if k != "pause_log"},
            "pause_log": [*state["pause_log"], resumed, *patch["pause_log"]],
        }
    )
    due = second + timedelta(minutes=5) + OFFSET
    if OFFSET > timedelta(0):
        _, launcher = _go(tmp_path, ports, due - timedelta(seconds=1))
        assert _resumed(launcher) == []
    _, launcher = _go(tmp_path, ports, due)
    assert _resumed(launcher) == ["7"]


def test_a_pause_is_retaken_at_most_twelve_times_in_twelve_hours():
    state = {"pause_log": [{"kind": "escalation", "value": "failure", "at": AT.isoformat()}]}
    assert rate_limit.pause_at(state) == AT
    slots = [
        rate_limit.retake_due_at(None, AT, prior, PRODUCT, AT + timedelta(hours=12))
        for prior in range(14)
    ]
    assert sum(slot is not None for slot in slots) == 12
    assert slots[12] is None and slots[13] is None


@pytest.mark.parametrize(
    ("offset", "spoke"),
    [(0, False), (-1, False), (-119, False), (-121, False), (1, True)],
)
def test_a_persons_comment_stops_the_retakes_only_when_strictly_after_the_pause(
    tmp_path, offset, spoke
):
    ports = _ports()
    task_id = _pause(ports)
    _say(ports, task_id, "please look at this", AT + timedelta(seconds=offset))
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=61))
    assert (_resumed(launcher) == []) is spoke


def test_a_poller_resume_comment_is_not_a_person_speaking(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    _say(ports, task_id, f"{RESUMED_BY_POLLER}retake", AT + timedelta(seconds=1))
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=61))
    assert _resumed(launcher) == ["7"]


def test_a_person_before_the_last_system_comment_does_not_stop_the_retakes(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    _say(ports, task_id, "a person", AT + timedelta(seconds=10))
    _say(ports, task_id, f"{RESUMED_BY_POLLER}retake", AT + timedelta(seconds=20))
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=61))
    assert _resumed(launcher) == ["7"]


def _second_limit(ports, task_id: str, at: datetime, resumed_at: datetime) -> None:
    """The run the poller resumed hits the limit again: a `resumed` entry, then a new pause."""
    state = ports.documents.load(task_id) or {}
    patch = rate_limit.rate_limit_pause_patch(state, resets_at=None, at=at.isoformat())
    resumed = {"kind": "resumed", "at": _stamp(resumed_at)}
    ports.documents.save(
        {
            **state,
            **{k: v for k, v in patch.items() if k != "pause_log"},
            "pause_log": [*state["pause_log"], resumed, *patch["pause_log"]],
        }
    )


def test_a_pause_resumed_three_times_by_the_poller_posts_no_retake_comment_and_keeps_its_budget(
    tmp_path,
):
    ports = _ports()
    task_id = _pause(ports)
    paused = AT
    for _ in range(4):  # one more than RETAKE_MAX and MECHANICAL_RETAKE_MAX allow
        _, launcher = _go(tmp_path, ports, paused + timedelta(minutes=60))
        assert _resumed(launcher) == ["7"] and "--by-poller" in launcher.spawned[0]
        # What `resume --by-poller` itself posts; the poller posts nothing of its own.
        _say(ports, task_id, f"{RESUMED_BY_POLLER}retake", paused + timedelta(minutes=61))
        paused += timedelta(minutes=70)
        _second_limit(ports, task_id, paused, paused - timedelta(minutes=9))
    comments = ports.tracker.comments
    assert comments == [] and ports.tracker.labels == []
    assert not any(
        text.startswith((poll.AUTO_RESUMED, *retakes.RETAKE_PREFIXES)) for _, text in comments
    )
    assert poll._retake_budget(ports.tracker.timestamped_comments(task_id)).used == 0
    state = ports.documents.load(task_id) or {}
    assert (retakes.RETAKE_MAX, rules.MECHANICAL_RETAKE_MAX) == (3, 3)
    assert rules.auto_resume_limit(state, "github_rate_limit") == len(
        rules.GITHUB_RATE_LIMIT_SCHEDULE
    )


def test_other_pauses_still_post_their_auto_resumed_comment(tmp_path):
    ports = _ports()
    ports.documents.save(
        {
            "id": "GH-9",
            "escalated": "Developer failed: API Error: 429",
            "pause_log": [
                {"kind": "failure", "value": "account_limit", "section": "Developer"},
                {"kind": "escalation", "value": "failure"},
            ],
        }
    )
    _say(ports, "GH-9", "Escalated: Developer failed: API Error: 429", AT)
    _, launcher = _go(tmp_path, ports, AT + timedelta(minutes=16))
    assert _resumed(launcher) == ["9"]
    assert ports.tracker.comments == [("GH-9", "Auto-resumed: account limit")]


# -- the 12 h notice -------------------------------------------------------------------------


def test_no_needs_human_before_twelve_hours_and_nothing_is_written(tmp_path):
    ports = _ports()
    _pause(ports)
    before = _written(ports)
    _go(tmp_path, ports, AT + timedelta(hours=12) - timedelta(seconds=1))
    assert _written(ports) == before


def test_after_twelve_hours_the_pause_is_persisted_labelled_and_noticed_once(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    now = AT + timedelta(hours=12)
    lines, launcher = _go(tmp_path, ports, now)
    assert _resumed(launcher) == []
    assert "agentic: poll: #7 github rate limit persisted -> left to the human" in lines
    assert ports.tracker.labels == [(task_id, "needs-human")]
    assert [text for _, text in ports.tracker.comments if text.startswith(NOTICE)] == [
        ports.tracker.comments[0][1]
    ]
    assert len(ports.tracker.comments) == 1
    state = ports.documents.load(task_id) or {}
    assert state["escalated"] == RATE_LIMIT_PERSISTED and state["rate_limit_noticed_at"]
    # The notice landed on the issue too; later rounds write nothing more.
    _say(ports, task_id, ports.tracker.comments[0][1], AT + timedelta(hours=12, seconds=6))
    after = _written(ports)
    _go(tmp_path, ports, now + timedelta(minutes=5))
    assert _written(ports)[:2] == (after[0], after[1]) and _written(ports)[2] == after[2]


def test_the_escalation_counts_from_the_persisted_instant_across_a_document_reload(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    _go(tmp_path, ports, AT + timedelta(hours=12))
    # A fresh process: the same documents, new Ports; the notice is already stamped.
    reloaded = _ports(documents=ports.documents)
    reloaded.stack.declared_product = PRODUCT
    _go(tmp_path, reloaded, AT + timedelta(hours=13))
    assert reloaded.tracker.labels == [] and reloaded.tracker.comments == []
    assert ports.tracker.labels == [(task_id, "needs-human")]
    state = reloaded.documents.load(task_id) or {}
    assert rate_limit.pause_at(state) == AT + timedelta(hours=12, seconds=5)


def test_a_notice_whose_comment_failed_is_retried_until_sent_once(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    ports.tracker.comment_failures = 1
    now = AT + timedelta(hours=12)
    _go(tmp_path, ports, now)
    state = ports.documents.load(task_id) or {}
    assert ports.tracker.comments == [] and not state.get("rate_limit_noticed_at")
    assert [row["target"] for row in state["tracker_failures"]] == ["comment"]
    assert rate_limit.rate_limit_notice_owed(state)
    _go(tmp_path, ports, now + timedelta(minutes=1))
    assert len(ports.tracker.comments) == 1
    state = ports.documents.load(task_id) or {}
    assert state["rate_limit_noticed_at"]
    _say(ports, task_id, ports.tracker.comments[0][1], AT + timedelta(hours=13))
    _go(tmp_path, ports, now + timedelta(minutes=2))
    assert len(ports.tracker.comments) == 1


def test_a_failed_label_is_retried_and_blocks_the_stamp(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    ports.tracker.label_failures = 1
    now = AT + timedelta(hours=12)
    _go(tmp_path, ports, now)
    state = ports.documents.load(task_id) or {}
    assert not state.get("rate_limit_noticed_at")
    assert [row["target"] for row in state["tracker_failures"]] == ["label"]
    _go(tmp_path, ports, now + timedelta(minutes=1))
    assert ports.tracker.labels == [(task_id, "needs-human")]
    assert (ports.documents.load(task_id) or {}).get("rate_limit_noticed_at")


def test_a_comment_that_landed_with_a_failed_stamp_is_not_posted_again(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    persisted_at = AT + timedelta(hours=12)
    state = apply(
        ports.documents.load(task_id) or {},
        rate_limit.rate_limit_persisted_patch(at=persisted_at.isoformat()),
    )
    ports.documents.save(state)
    _say(ports, task_id, f"{NOTICE}\n\nTask document: x", persisted_at + timedelta(seconds=1))
    _go(tmp_path, ports, persisted_at + timedelta(minutes=1))
    assert ports.tracker.comments == [] and ports.tracker.labels == [(task_id, "needs-human")]
    assert (ports.documents.load(task_id) or {}).get("rate_limit_noticed_at")


def test_a_persons_comment_does_not_stop_the_twelve_hour_escalation(tmp_path):
    ports = _ports()
    task_id = _pause(ports)
    _say(ports, task_id, "I am looking", AT + timedelta(hours=1))
    _, launcher = _go(tmp_path, ports, AT + timedelta(hours=2))
    assert _resumed(launcher) == [] and ports.tracker.labels == []
    _go(tmp_path, ports, AT + timedelta(hours=12))
    assert ports.tracker.labels == [(task_id, "needs-human")] and len(ports.tracker.comments) == 1


def test_a_comment_from_before_the_persisted_instant_is_not_the_notice():
    at = AT + timedelta(hours=12)
    stale = [(NOTICE, at - timedelta(seconds=1))]
    assert not rate_limit.notice_posted(stale, at, NOTICE)


def test_a_held_round_and_a_dry_run_write_nothing(tmp_path):
    ports = _ports()
    _pause(ports)
    before = _written(ports)
    _go(tmp_path, ports, AT + timedelta(hours=12), dry_run=True)
    assert _written(ports) == before
    ports.tracker.held_issues_error = HeldListMayBeCut("a page of 500")
    _go(tmp_path, ports, AT + timedelta(hours=12))
    assert _written(ports) == before


def test_a_task_at_work_is_not_written(tmp_path):
    ports = _ports()
    _pause(ports)
    before = _written(ports)
    _go(tmp_path, ports, AT + timedelta(hours=12), lock=_Locks(alive={"GH-7"}))
    assert _written(ports) == before


def test_a_round_with_nothing_owed_makes_no_tracker_write(tmp_path):
    ports = _ports()
    _pause(ports)
    before = _written(ports)
    _go(tmp_path, ports, AT + timedelta(minutes=30))
    assert _written(ports) == before
    assert poll.rate_limit.persist_due(ports.documents.load("GH-7") or {}, AT) is False
