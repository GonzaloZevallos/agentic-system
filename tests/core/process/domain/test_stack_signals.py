"""Whether declared signals justify a stack review (ADR-0034). Pure: no port, no
`Path`, no `Ports` — every fact arrives as a plain value, the caller's own read."""

from agentic.core.process.domain.stack_signals import (
    MERGED_TASKS_THRESHOLD,
    Signals,
    description,
    fired,
    signals,
)

ANCHOR_DATE = "2026-01-01T00:00:00Z"


def _merged(n: int) -> list[tuple[str, str]]:
    return [("2026-06-01T00:00:00Z", "merged") for _ in range(n)]


def test_fired_on_greenfield_with_no_anchor_at_all():
    # Vacuously true: a repo with no `.agentic/stack.yaml` yet has nothing to compare
    # the Brief or the deliveries against, so every signal is satisfied by default.
    # Isolated from the other branches: `anchor_date` equals `brief_date` (so
    # `brief_changed` is false), there are no deliveries and no reason — only
    # `anchor == ""` can be firing this.
    s = signals(anchor="", anchor_date="", brief_date="", deliveries=[], reason="")
    assert not s.brief_changed
    assert s.merged_since == 0
    assert fired(s)


def test_not_fired_with_an_anchor_and_nothing_else():
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=[],
        reason="",
    )
    assert not fired(s)


def test_fired_when_the_brief_changed_since_the_anchor():
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date="2026-06-01T00:00:00Z",
        deliveries=[],
        reason="",
    )
    assert s.brief_changed
    assert fired(s)


def test_not_fired_when_the_brief_is_not_newer_than_the_anchor():
    # Same instant, lexicographically equal: not "changed since".
    s = signals(
        anchor="abc123", anchor_date=ANCHOR_DATE, brief_date=ANCHOR_DATE, deliveries=[], reason=""
    )
    assert not s.brief_changed
    assert not fired(s)


def test_not_fired_one_merged_task_below_the_threshold():
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=_merged(MERGED_TASKS_THRESHOLD - 1),
        reason="",
    )
    assert s.merged_since == MERGED_TASKS_THRESHOLD - 1
    assert not fired(s)


def test_fired_at_the_merged_threshold_exactly():
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=_merged(MERGED_TASKS_THRESHOLD),
        reason="",
    )
    assert s.merged_since == MERGED_TASKS_THRESHOLD
    assert fired(s)


def test_merged_since_ignores_an_outcome_not_in_merged_outcomes():
    deliveries = [("2026-06-01T00:00:00Z", "closed")] * MERGED_TASKS_THRESHOLD
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=deliveries,
        reason="",
    )
    assert s.merged_since == 0
    assert not fired(s)


def test_merged_since_ignores_a_delivery_closed_before_the_anchor():
    deliveries = [("2025-01-01T00:00:00Z", "merged")] * MERGED_TASKS_THRESHOLD
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=deliveries,
        reason="",
    )
    assert s.merged_since == 0
    assert not fired(s)


def test_merged_since_ignores_a_delivery_closed_exactly_on_the_anchor_date():
    # Strict `>`, not `>=`: the anchor's own instant is not "since" it.
    deliveries = [(ANCHOR_DATE, "merged")] * MERGED_TASKS_THRESHOLD
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=deliveries,
        reason="",
    )
    assert s.merged_since == 0
    assert not fired(s)


def test_merged_since_counts_merged_with_changes_too():
    deliveries = [("2026-06-01T00:00:00Z", "merged_with_changes")] * MERGED_TASKS_THRESHOLD
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=deliveries,
        reason="",
    )
    assert s.merged_since == MERGED_TASKS_THRESHOLD


def test_fired_on_a_reason_alone():
    s = signals(
        anchor="abc123",
        anchor_date=ANCHOR_DATE,
        brief_date=ANCHOR_DATE,
        deliveries=[],
        reason="the CI's Python pin broke the build",
    )
    assert fired(s)


def test_description_carries_the_reason_text_byte_for_byte():
    reason = "the CI's Python pin broke the build, not the code"
    s = Signals(anchor="abc123", brief_changed=False, merged_since=0, reason=reason)
    assert reason in description(s)


def test_description_carries_the_anchor_and_the_merged_count():
    s = Signals(anchor="abc123", brief_changed=True, merged_since=12, reason="")
    text = description(s)
    assert "abc123" in text
    assert "12" in text
