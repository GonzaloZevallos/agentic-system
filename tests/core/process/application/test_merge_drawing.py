"""The drawing record's readers outside the decision (GH-766 part 5): the merge verb's
`drawing_unsettled` refusal carries one fixed evidence line (`merge._drawing_evidence`), the
poller's and the landing's `MergeFacts` builders read the same three facts off one state
(`rules.drawing_merge_facts`), and the delivery's body block is `rules.drawing_body_lines`.
Pure or on the in-memory ports; never a published pull request."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from agentic.core.process.application import deliver, landing_plan, merge, poll
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import rules
from agentic.core.process.domain.task import TaskState
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import HEAD, MASTER, URL, _ports
from tests.core.process.domain.test_landing_slice import SLICE_HEAD, _slice_facts, _state

OTHER = "c" * 40
CLOSE = "d" * 40


@pytest.fixture(autouse=True)
def _no_live_landing_step(monkeypatch):
    monkeypatch.setattr(landing_plan, "LIVE_STEPS", frozenset())


def _record(head: Any = HEAD, rows: Any = None, open: Any = 1) -> dict[str, Any]:
    rows = (
        [{"subject": "Home", "ref": "home.png", "verdict": "unverified"}] if rows is None else rows
    )
    return {"head": head, "rows": rows, "open": open}


def _facts(**over: Any) -> rules.MergeFacts:
    base = rules.MergeFacts(
        open_escalation=False,
        task_id="GH-9",
        status="ready_for_review",
        reviewed_unit_approved=True,
        reviewed_head=HEAD,
        open_blocking=0,
        open_important=0,
        pr_state="OPEN",
        pr_head=HEAD,
        in_eval_set=False,
        touched_paths=(),
        attempts=0,
        checks_head=None,
        checks_master=None,
        checks_passed=None,
        current_master=MASTER,
    )
    return replace(base, **over)


@pytest.mark.parametrize(
    ("over", "line"),
    [
        ({"drawing_head": HEAD, "drawing_open": 2}, "open=2 head=current"),
        ({"drawing_head": OTHER, "drawing_open": 0}, "open=0 head=moved"),
        ({"drawing_head": HEAD[:10], "drawing_open": 1}, "open=1 head=unreadable"),
        ({"drawing_head": None, "drawing_open": 1}, "open=1 head=unreadable"),
        (
            {
                "drawing_head": HEAD,
                "pr_head": CLOSE,
                "close_docs": {"parent": HEAD, "head": CLOSE, "paths": ["docs/product/a.md"]},
            },
            "open=0 head=current",
        ),
        (
            {
                "drawing_head": HEAD,
                "pr_head": CLOSE,
                "close_docs": {"parent": HEAD, "head": CLOSE, "paths": ["agentic/x.py"]},
            },
            "open=0 head=moved",
        ),
    ],
)
def test_the_evidence_is_one_fixed_line(over, line):
    assert merge._drawing_evidence(_facts(drawing_checked=True, **over)) == line


def test_the_verb_refuses_a_moved_drawing_head_with_the_evidence_and_the_hand_off(tmp_path):
    ports = _ports(5, drawing_checks=_record(OTHER, rows=[], open=0))

    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", MASTER, None),
        frozenset(),
        lambda _line: None,
    )

    assert (result.kind, result.reason) == ("refused", "drawing_unsettled")
    [(_, comment)] = ports.tracker.comments
    assert comment.splitlines()[0] == f"Auto-merge refused: {HEAD} drawing_unsettled"
    assert "open=0 head=moved" in comment.splitlines()


def test_the_poller_and_the_landing_read_the_same_three_facts_off_one_state():
    record = _record(HEAD, rows=[{"subject": "A", "ref": "a", "verdict": "departs"}], open=0)
    ports = _ports(5, drawing_checks=record)
    raw = ports.documents.load("GH-5")
    polled = poll._merge_facts(
        "GH-5", raw, ports.tracker.prs[URL.format(5)], frozenset(), 5, MASTER, comments=[]
    )
    sliced = _slice_facts({**_state(), "drawing_checks": record}, pr_head=SLICE_HEAD)

    expected = (True, HEAD, 1)
    assert rules.drawing_merge_facts(raw) == expected
    assert (polled.drawing_checked, polled.drawing_head, polled.drawing_open) == expected
    assert (sliced.drawing_checked, sliced.drawing_head, sliced.drawing_open) == expected


def test_both_builders_read_no_record_as_unchecked():
    ports = _ports(5)
    polled = poll._merge_facts(
        "GH-5",
        ports.documents.load("GH-5"),
        ports.tracker.prs[URL.format(5)],
        frozenset(),
        5,
        MASTER,
        comments=[],
    )
    sliced = _slice_facts()
    for facts in (polled, sliced):
        assert (facts.drawing_checked, facts.drawing_head, facts.drawing_open) == (False, None, 0)


def test_the_delivery_block_is_the_body_lines_and_empty_without_a_record():
    state = cast(TaskState, {"drawing_checks": _record()})
    assert deliver._drawing_preface(state) == "Drawing not verified: Home home.png"
    assert deliver._drawing_preface(cast(TaskState, {})) == ""
