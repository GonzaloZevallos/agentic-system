import re
from dataclasses import replace

from agentic.core.process.domain import turn_notice
from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, PJM, ROLES


def test_threshold_is_declared_for_the_two_roles_that_execute():
    assert turn_notice.threshold_for(DEVELOPER) == (45, 65)
    assert turn_notice.threshold_for(DEVELOPER_INTEGRATOR) == (45, 65)


def test_threshold_is_none_for_a_role_the_notice_does_not_cover():
    # The conservative default (`threshold_for`'s own docstring): a role added later
    # inherits no live notice unless named in `TURN_NOTICE_AT`.
    assert turn_notice.threshold_for(PJM) is None


def _last_threshold_violations(roles):
    """Every `(slug, last_threshold, max_turns)` in `TURN_NOTICE_AT` whose *last*
    position does not stay strictly below its own role's cap -- a position at or past
    the cap could never open a close-out window before the harness cuts the attempt."""
    by_slug = {role.slug: role for role in roles}
    return [
        (slug, windows[-1], by_slug[slug].max_turns)
        for slug, windows in turn_notice.TURN_NOTICE_AT.items()
        if not windows[-1] < by_slug[slug].max_turns
    ]


def test_the_last_threshold_stays_strictly_below_max_turns():
    assert _last_threshold_violations(ROLES) == []


def test_the_last_threshold_check_is_not_vacuous():
    # Proof the check rejects a bad input, run through the very same function: a role
    # whose cap sank to the last position itself must be reported, not silently
    # accepted.
    last = turn_notice.TURN_NOTICE_AT[DEVELOPER.slug][-1]
    shrunk = replace(DEVELOPER, max_turns=last)
    roles = [shrunk if role.slug == DEVELOPER.slug else role for role in ROLES]
    assert _last_threshold_violations(roles) == [(DEVELOPER.slug, last, shrunk.max_turns)]


def _shape_violations(pairs):
    """Every violation of the two guarantees a covered role's own `(role, windows)`
    pair must hold: `windows` is strictly increasing, and its first position leaves at
    least `GUARANTEED_HEADROOM_TURNS` of headroom against the role's own `max_turns`.
    The *last* position staying strictly below the cap is a separate guarantee, checked
    by `_last_threshold_violations` above, so the two tests say different things
    instead of duplicating one comparison."""
    violations = []
    for role, windows in pairs:
        if list(windows) != sorted(set(windows)):
            violations.append((role.slug, windows, "not strictly increasing"))
        elif role.max_turns - windows[0] < turn_notice.GUARANTEED_HEADROOM_TURNS:
            violations.append((role.slug, windows, role.max_turns))
    return violations


def _covered_pairs(roles):
    by_slug = {role.slug: role for role in roles}
    return [(by_slug[slug], windows) for slug, windows in turn_notice.TURN_NOTICE_AT.items()]


def test_every_first_threshold_gives_the_guaranteed_headroom():
    # The declared check the acceptance criteria names: `role.max_turns -
    # TURN_NOTICE_AT[slug][0] >= GUARANTEED_HEADROOM_TURNS` for every covered role.
    assert _shape_violations(_covered_pairs(ROLES)) == []


def test_the_headroom_check_is_not_vacuous():
    # A role whose cap leaves one turn less than the guaranteed headroom must be
    # reported, through the same helper the check above runs.
    windows = turn_notice.TURN_NOTICE_AT[DEVELOPER.slug]
    shrunk = replace(DEVELOPER, max_turns=windows[0] + turn_notice.GUARANTEED_HEADROOM_TURNS - 1)
    expected = [(DEVELOPER.slug, windows, shrunk.max_turns)]
    assert _shape_violations([(shrunk, windows)]) == expected


def test_the_headroom_check_flags_a_tuple_that_is_not_strictly_increasing():
    # A declared tuple out of order (or with a repeat) is rejected by the same helper,
    # never silently accepted because its first element alone still looks fine.
    role = replace(DEVELOPER, max_turns=200)
    expected = [(DEVELOPER.slug, (65, 45), "not strictly increasing")]
    assert _shape_violations([(role, (65, 45))]) == expected


def test_should_notice_at_the_threshold_and_not_one_call_before_it():
    # `windows_crossed` replaces `should_notice` (SOLID: one function decides how many
    # windows a count has crossed, not two that could disagree). Rejected input: 44,
    # one call before the first window.
    assert turn_notice.windows_crossed(DEVELOPER, 44) == 0
    assert turn_notice.windows_crossed(DEVELOPER, 45) == 1


def test_should_notice_stays_true_past_the_threshold_it_does_not_latch():
    # Inverted from the deleted one-shot-turned-always-refiring predicate this
    # replaces: a window count does not keep climbing forever. There are only two
    # declared positions, so a count far past the last one (80, the rejected input) is
    # not "more crossed" than 65 -- it still reads as 2.
    assert turn_notice.windows_crossed(DEVELOPER, 65) == 2
    assert turn_notice.windows_crossed(DEVELOPER, 80) == 2


def test_windows_crossed_one_below_the_second_threshold_is_still_one():
    assert turn_notice.windows_crossed(DEVELOPER, 64) == 1


def test_windows_crossed_is_zero_for_a_role_the_notice_does_not_cover():
    assert turn_notice.windows_crossed(PJM, 1000) == 0


def test_notice_context_window_one_does_not_say_deliver_now():
    text = turn_notice.notice_context(1)
    assert "deliver now" not in text.lower()
    assert "commit" in text.lower()
    assert "stop opening new work" in text.lower()


def test_notice_context_window_two_orders_commit_before_deliver_and_covers_the_red_case():
    text = turn_notice.notice_context(2)
    commit_at = text.lower().index("commit")
    deliver_at = text.lower().index("deliver")
    assert commit_at < deliver_at
    assert "checks are red" in text
    assert "name the gap in `summary`" in text


def test_notice_context_differs_between_the_two_windows_by_more_than_an_ordinal():
    # The invariant this seam has to prove: the two windows are not the identical
    # sentence with a number swapped in.
    first, second = turn_notice.notice_context(1), turn_notice.notice_context(2)
    assert first != second

    def without_digits(text: str) -> str:
        return re.sub(r"\d+", "", text)

    assert without_digits(first) != without_digits(second)


def test_notice_context_names_no_turn_figure():
    # This module's own docstring: no reading of the counter is offered as a turn
    # count, since the counter and `role.max_turns`/`num_turns` are not interchangeable.
    for window in (1, 2):
        text = turn_notice.notice_context(window)
        assert "turns left" not in text
        assert "max_turns" not in text
        assert not re.search(r"\d+\s*turns?\b", text)


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
