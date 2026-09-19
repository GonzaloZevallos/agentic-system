"""The live turn-budget notice for a role that executes (ADR-0004, seventh addendum,
"the declared gate for the subtask B"; issue #85).

The spike (`tests/llm/test_turn_notice.py`) established, against the pinned SDK:

- a `PostToolUse` hook's own count, at a deliberate `max_turns` cut, equals `max_turns`
  exactly — two cuts, `max_turns` 2 and 4, both landed on a hook-side count identical
  to it. That is the unit this module's threshold and sentence are both stated in:
  **enforced-cap turns**, the same unit `Role.max_turns` is in.
- the reported `num_turns` at those same two cuts was one more each time (3 and 5): the
  conversion this module uses whenever it reads a figure recorded in that other unit
  (`num_turns`), never compares the two without it.
- a sub-agent's own `PostToolUse` event carried `agent_id`; the main thread's never
  did. `hooks_for` (`adapters/claude_sdk/hooks.py`) counts only the events without it.

Everything here is a pure function or a named constant; the adapter only calls
`margin_for`/`should_notice`/`notice_context` and passes their output as
`additionalContext`, and `application/run_role.py` only calls `notice_note` at the same
seam `rules.denial_note`/`cold_fallback_note` use.
"""

from __future__ import annotations

from .roles import DEVELOPER, DEVELOPER_INTEGRATOR, Role
from .rules import system_note

# The worst measured close-out (ADR-0004, seventh addendum; issue #85, GH-72's
# `Developer: emission`): 38 reported `num_turns` for a Developer resumed and told to
# commit and deliver. Restated in enforced-cap turns by the spike's own conversion
# (-1): 37. Plus the presubmit margin — its own refusals are bounded at two
# (`rules.turn_cut_retry_message`'s own docstring), so up to two more delivery
# cycles, two more enforced-cap turns: 39. One margin for both roles that execute:
# the integration's close-out was never measured separately, and nothing about
# closing out (commit, then deliver) differs between a subtask's Developer and the
# integrator's.
TURN_NOTICE_MARGIN_TURNS: dict[str, int] = {
    DEVELOPER.slug: 39,
    DEVELOPER_INTEGRATOR.slug: 39,
}


def margin_for(role: Role) -> int | None:
    """The margin, in enforced-cap turns, at which `role`'s attempt gets the live
    notice; `None` for every role but the two `TURN_NOTICE_MARGIN_TURNS` names. The
    conservative default of `rules.turn_cut_retry_message`'s own three arms applies
    here too: only an explicit entry turns the notice on, so a role a future task adds
    inherits no notice in silence."""
    return TURN_NOTICE_MARGIN_TURNS.get(role.slug)


def should_notice(role: Role, hook_turns: int) -> bool:
    """Whether `role`'s attempt, `hook_turns` enforced-cap turns into its `max_turns`
    (a `PostToolUse` count, main-thread events only), has crossed into the close-out
    margin. `False` for a role `margin_for` does not cover."""
    margin = margin_for(role)
    return margin is not None and role.max_turns - hook_turns <= margin


def notice_context(remaining: int) -> str:
    """The `additionalContext` a `PostToolUse` hook adds once `should_notice` turns
    true: `remaining` enforced-cap turns, and the same close-then-deliver order
    `rules.turn_cut_retry_message` gives the retry after a cut already happened — here,
    before it happens, so the run never needs that retry at all."""
    plural = "" if remaining == 1 else "s"
    return (
        f"The turn cap is close: about {remaining} turn{plural} left before the "
        "harness cuts this attempt. Commit what you have before you deliver — a "
        "delivery with nothing committed still spends turns, and so does one the "
        "presubmit refuses: if the checks are red, commit and deliver anyway, and "
        "name the gap in `summary`."
    )


def notice_note() -> str:
    """The trace a fired notice leaves on the run's section (glossary: Sección): one
    system-written line, at the same seam `rules.denial_note`/`cold_fallback_note` use,
    composed by the same helper (`rules.system_note`) — the role never reports its own
    turn budget in its own words."""
    return system_note(
        "the system warned this attempt the turn cap was close; it kept going and delivered"
    )
