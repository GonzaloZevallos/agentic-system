"""`merge_hold.open_escalation` (GH-784): whether an owner escalation is still open. Pure: each
case is a list of `(kind, created_at)` marks and the two local-clock marks."""

from __future__ import annotations

import pytest

from agentic.core.process.domain.merge_hold import open_escalation

T0 = "2026-11-14T10:00:00Z"
T1 = "2026-11-14T10:05:00Z"
T2 = "2026-11-14T10:10:00Z"
T1_LOCAL = "2026-11-14T10:05:00+00:00"
T2_LOCAL = "2026-11-14T10:10:00+00:00"
FIVE_BEFORE = "2026-11-14T09:59:55+00:00"


@pytest.mark.parametrize(
    ("comments", "resumed_at", "released_at", "expected"),
    [
        pytest.param([("escalated", T1)], None, [], True, id="live"),
        pytest.param([("ready", T0), ("escalated", T1)], None, [], True, id="live-after-ready"),
        pytest.param([("escalated", T1)], T2_LOCAL, [], False, id="released-by-later-resumed"),
        pytest.param([("escalated", T1)], None, [T2_LOCAL], False, id="released-by-released_at"),
        pytest.param([("escalated", T1), ("ready", T2)], None, [], False, id="fresh-ready"),
        pytest.param([("escalated", T1)], T1_LOCAL, [], True, id="tie-with-resumed-holds"),
        pytest.param([("escalated", T1)], None, [T1_LOCAL], True, id="tie-with-released-holds"),
        pytest.param([("escalated", T1), ("ready", T1)], None, [], True, id="tie-with-ready-holds"),
        pytest.param([("escalated", T1)], FIVE_BEFORE, [], True, id="resumed-5s-earlier-holds"),
        pytest.param([("ready", "junk")], "junk", ["junk"], False, id="no-escalated-reads-nothing"),
        pytest.param([("escalated", "junk")], None, [], None, id="latest-escalated-unparseable"),
        pytest.param(
            [("escalated", "junk"), ("escalated", T1)], None, [], True, id="older-unparseable-ok"
        ),
        pytest.param(
            [("escalated", T1), ("ready", T2)], "junk", [], False, id="legacy-later-ready-wins"
        ),
        pytest.param([("escalated", T1)], "junk", [], None, id="live-with-resumed-garbage"),
        pytest.param([("escalated", T1)], None, [""], None, id="empty-released_at-is-garbage"),
        pytest.param([("escalated", T1), ("ready", "junk")], None, [], None, id="ready-garbage"),
        pytest.param([("escalated", T1)], "junk", [T2_LOCAL], False, id="later-mark-beats-garbage"),
        pytest.param([("escalated", T1)], None, [T0, T2_LOCAL], False, id="any-later-released"),
        pytest.param(None, None, [], None, id="unreadable"),
        pytest.param([], None, [], False, id="empty-thread"),
        pytest.param(
            [("escalated", T1), ("ready", T0)], None, [], True, id="earlier-ready-does-not-clear"
        ),
        pytest.param(
            [("escalated", "2026-11-14T12:05:00+02:00")],
            None,
            [T2_LOCAL],
            False,
            id="offset-compared-as-aware",
        ),
    ],
)
def test_open_escalation(comments, resumed_at, released_at, expected):
    assert open_escalation(comments, resumed_at, released_at) is expected


def test_the_latest_escalated_is_the_last_in_thread_order_not_the_greatest_instant():
    comments = [("escalated", T2), ("escalated", T0)]
    assert open_escalation(comments, T1_LOCAL, []) is False
