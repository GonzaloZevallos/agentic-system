"""Whether declared signals justify a stack review (ADR-0034, ADR-0038). Pure: no port, no
`Path`, no `Ports` — every fact arrives as a plain value, the caller's own read. Order is
a position on the main line's first-parent chain, never a date."""

from agentic.core.process.domain.stack_signals import (
    MERGED_TASKS_THRESHOLD,
    Signals,
    description,
    fired,
    needs_merge_commit,
    signals,
)

ANCHOR = 10


def _merged(n: int, pos: int | None = ANCHOR + 5) -> list[tuple[int | None, str]]:
    return [(pos, "merged") for _ in range(n)]


def _quiet(**kw):
    args = {"anchor": "abc123", "anchor_pos": ANCHOR, "brief_pos": ANCHOR, "deliveries": []}
    args.update(kw)
    return signals(reason="", **args)


def test_fired_on_greenfield_with_no_anchor_at_all():
    s = signals(anchor="", anchor_pos=None, brief_pos=None, deliveries=[], reason="")
    assert not s.brief_changed
    assert s.merged_since == 0
    assert fired(s)


def test_not_fired_with_an_anchor_and_nothing_else():
    assert not fired(_quiet())


def test_brief_landing_after_the_anchor_fires():
    # The issue's table: the Brief was written first but reached the main line later.
    s = _quiet(brief_pos=ANCHOR + 1)
    assert s.brief_changed
    assert fired(s)


def test_brief_landing_before_the_anchor_does_not_fire():
    s = _quiet(brief_pos=ANCHOR - 1)
    assert not s.brief_changed
    assert not fired(s)


def test_one_commit_bringing_both_is_not_a_change_since():
    assert not _quiet(brief_pos=ANCHOR).brief_changed


def test_no_brief_on_the_main_line_is_not_a_change():
    assert not _quiet(brief_pos=None).brief_changed


def test_brief_with_no_anchor_at_all_is_a_change():
    s = signals(anchor="", anchor_pos=None, brief_pos=0, deliveries=[], reason="")
    assert s.brief_changed


def test_not_fired_one_merged_task_below_the_threshold():
    s = _quiet(deliveries=_merged(MERGED_TASKS_THRESHOLD - 1))
    assert s.merged_since == MERGED_TASKS_THRESHOLD - 1
    assert not fired(s)


def test_fired_at_the_merged_threshold_exactly():
    s = _quiet(deliveries=_merged(MERGED_TASKS_THRESHOLD))
    assert s.merged_since == MERGED_TASKS_THRESHOLD
    assert fired(s)


def test_merged_since_ignores_an_outcome_not_in_merged_outcomes():
    s = _quiet(deliveries=[(ANCHOR + 5, "closed")] * MERGED_TASKS_THRESHOLD)
    assert s.merged_since == 0
    assert not fired(s)


def test_merged_since_ignores_a_delivery_landed_before_the_anchor():
    s = _quiet(deliveries=_merged(MERGED_TASKS_THRESHOLD, ANCHOR - 1))
    assert s.merged_since == 0
    assert not fired(s)


def test_merged_since_ignores_a_delivery_landed_on_the_anchor_itself():
    assert _quiet(deliveries=_merged(MERGED_TASKS_THRESHOLD, ANCHOR)).merged_since == 0


def test_merged_since_ignores_a_delivery_with_no_position():
    assert _quiet(deliveries=_merged(MERGED_TASKS_THRESHOLD, None)).merged_since == 0


def test_merged_since_counts_every_positioned_delivery_when_there_is_no_anchor():
    s = signals(anchor="", anchor_pos=None, brief_pos=None, deliveries=_merged(3, 0), reason="")
    assert s.merged_since == 3


def test_merged_since_counts_merged_with_changes_too():
    s = _quiet(deliveries=[(ANCHOR + 5, "merged_with_changes")] * MERGED_TASKS_THRESHOLD)
    assert s.merged_since == MERGED_TASKS_THRESHOLD


def test_fired_on_a_reason_alone():
    s = signals(
        anchor="abc123",
        anchor_pos=ANCHOR,
        brief_pos=ANCHOR,
        deliveries=[],
        reason="the CI's Python pin broke the build",
    )
    assert fired(s)


def test_needs_merge_commit_accepts_a_closed_merged_document_without_it():
    assert needs_merge_commit({"closed_at": "t", "delivery_outcome": "merged"})
    assert needs_merge_commit({"closed_at": "t", "delivery_outcome": "merged_with_changes"})


def test_needs_merge_commit_rejects_everything_else():
    assert not needs_merge_commit({"delivery_outcome": "merged"})  # not closed
    assert not needs_merge_commit({"closed_at": "t", "delivery_outcome": "closed"})
    assert not needs_merge_commit({"closed_at": "t"})
    assert not needs_merge_commit(
        {"closed_at": "t", "delivery_outcome": "merged", "merge_commit": "abc"}
    )


def test_description_carries_the_reason_text_byte_for_byte():
    reason = "the CI's Python pin broke the build, not the code"
    s = Signals(anchor="abc123", brief_changed=False, merged_since=0, reason=reason)
    assert reason in description(s)


def test_description_carries_the_anchor_and_the_merged_count():
    s = Signals(anchor="abc123", brief_changed=True, merged_since=12, reason="")
    text = description(s)
    assert "abc123" in text
    assert "12" in text
