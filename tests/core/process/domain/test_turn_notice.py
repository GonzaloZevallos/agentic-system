from agentic.core.process.domain import turn_notice
from agentic.core.process.domain.roles import (
    DEVELOPER,
    DEVELOPER_INTEGRATOR,
    PJM,
    TECH_LEAD_REVIEWER,
)


def test_margin_is_declared_for_the_two_roles_that_execute():
    assert turn_notice.margin_for(DEVELOPER) == 39
    assert turn_notice.margin_for(DEVELOPER_INTEGRATOR) == 39


def test_margin_is_none_for_a_role_the_notice_does_not_cover():
    # The conservative default (ADR-0004, seventh addendum's own arm 3): a role added
    # later inherits no live notice unless named here.
    assert turn_notice.margin_for(PJM) is None
    assert turn_notice.margin_for(TECH_LEAD_REVIEWER) is None


def test_should_notice_at_the_margin_and_not_one_turn_before_it():
    # `DEVELOPER.max_turns` is 80, margin 39: the gate opens at hook_turns 41
    # (80 - 41 == 39 remaining) and not at 40 (80 - 40 == 40 remaining, still above).
    assert not turn_notice.should_notice(DEVELOPER, 40)
    assert turn_notice.should_notice(DEVELOPER, 41)
    assert turn_notice.should_notice(DEVELOPER, 80)  # at or past the cap: still notices


def test_should_notice_is_false_for_a_role_with_no_margin():
    assert not turn_notice.should_notice(PJM, 0)


def test_notice_context_names_the_remaining_turns_and_orders_commit_before_deliver():
    text = turn_notice.notice_context(5)
    assert "5 turns left" in text
    commit_at = text.index("Commit what you have")
    deliver_at = text.index("deliver")
    assert commit_at < deliver_at
    assert "checks are red" in text
    assert "name the gap in `summary`" in text


def test_notice_context_uses_the_singular_for_one_turn_left():
    text = turn_notice.notice_context(1)
    assert "1 turn left" in text
    assert "1 turns left" not in text


def test_notice_note_is_one_system_written_line():
    from agentic.core.process.domain.rules import system_note

    assert turn_notice.notice_note() == system_note(
        "the system warned this attempt the turn cap was close; it kept going and delivered"
    )
