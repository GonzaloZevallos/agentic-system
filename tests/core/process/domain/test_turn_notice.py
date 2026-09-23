from dataclasses import replace

from agentic.core.process.domain import turn_notice
from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, PJM, ROLES


def test_threshold_is_declared_for_the_two_roles_that_execute():
    assert turn_notice.threshold_for(DEVELOPER) == 15
    assert turn_notice.threshold_for(DEVELOPER_INTEGRATOR) == 15


def test_threshold_is_none_for_a_role_the_notice_does_not_cover():
    # The conservative default (`threshold_for`'s own docstring): a role added later
    # inherits no live notice unless named in `TURN_NOTICE_AT`.
    assert turn_notice.threshold_for(PJM) is None


def _threshold_violations(roles):
    """Every `(slug, threshold, max_turns)` in `TURN_NOTICE_AT` whose threshold does
    not stay strictly below its own role's cap -- the one comparison both the declared
    check below and its non-vacuity companion run, so the companion proves the same
    code that guards the real roles also rejects a bad one, instead of re-typing the
    comparison by hand on operands picked to already agree."""
    by_slug = {role.slug: role for role in roles}
    return [
        (slug, threshold, by_slug[slug].max_turns)
        for slug, threshold in turn_notice.TURN_NOTICE_AT.items()
        if not threshold < by_slug[slug].max_turns
    ]


def test_every_threshold_is_below_the_max_turns_of_every_role_it_covers():
    # The declared check the acceptance criteria names: a threshold that reaches or
    # exceeds its role's own cap could never open a close-out window before the cut.
    assert _threshold_violations(ROLES) == []


def test_the_declared_check_above_is_not_vacuous():
    # Proof the check rejects a bad input, run through the very same function: a role
    # whose cap sank to the threshold itself must be reported, not silently accepted.
    shrunk = replace(DEVELOPER, max_turns=turn_notice.TURN_NOTICE_AT[DEVELOPER.slug])
    roles = [shrunk if role.slug == DEVELOPER.slug else role for role in ROLES]
    assert _threshold_violations(roles) == [
        (DEVELOPER.slug, turn_notice.TURN_NOTICE_AT[DEVELOPER.slug], shrunk.max_turns)
    ]


def test_should_notice_at_the_threshold_and_not_one_call_before_it():
    assert not turn_notice.should_notice(DEVELOPER, 14)
    assert turn_notice.should_notice(DEVELOPER, 15)


def test_should_notice_stays_true_past_the_threshold_it_does_not_latch():
    # A repeating close-out window, not a one-shot: every later count keeps qualifying,
    # unlike the deleted one-shot advisory this revises.
    assert turn_notice.should_notice(DEVELOPER, 16)
    assert turn_notice.should_notice(DEVELOPER, 80)


def test_should_notice_is_false_for_a_role_with_no_threshold():
    assert not turn_notice.should_notice(PJM, 1000)


def test_notice_context_carries_the_firing_index_and_orders_commit_before_deliver():
    text = turn_notice.notice_context(3)
    assert "#3" in text
    commit_at = text.index("Commit what you have")
    deliver_at = text.index("deliver")
    assert commit_at < deliver_at
    assert "checks are red" in text
    assert "name the gap in `summary`" in text


def test_notice_context_differs_between_firings_of_the_same_attempt():
    # The invariant this seam has to prove: two firings of the same attempt are not
    # the identical message re-sent.
    first, second = turn_notice.notice_context(1), turn_notice.notice_context(2)
    assert first != second


def test_notice_context_names_no_turn_figure():
    # This module's own docstring: no reading of the counter is offered as a turn
    # count, since the counter and `role.max_turns`/`num_turns` are not interchangeable.
    assert "turns left" not in turn_notice.notice_context(1)


def test_notice_note_distinguishes_delivered_from_cut_and_carries_the_firing_count():
    from agentic.core.process.domain.rules import system_note

    delivered = turn_notice.notice_note(cut=False, firings=2)
    cut = turn_notice.notice_note(cut=True, firings=2)
    assert delivered != cut
    assert delivered == system_note(
        "the system warned this attempt the turn cap was close (2 close-out notices "
        "in total); it kept going and delivered"
    )
    assert cut == system_note(
        "the system warned this attempt the turn cap was close (2 close-out notices "
        "in total); the harness cut an earlier attempt before it could deliver, and a "
        "later attempt delivered"
    )


def test_notice_note_uses_the_singular_for_one_firing():
    text = turn_notice.notice_note(cut=False, firings=1)
    assert "1 close-out notice in total)" in text
    assert "1 close-out notices" not in text
