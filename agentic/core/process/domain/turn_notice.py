"""The live turn-budget notice for a role that executes (ADR-0004, seventh addendum;
revised by its 2026-09-22 addendum, issue #102).

The counted quantity (ADR-0004's revising addendum): the runner's own main-thread
`AssistantMessage` count (`ClaudeSdkRunner.run_once`'s stream loop, incremented once
inside the existing `if message.parent_tool_use_id is None:` branch). Not a
`PostToolUse` tally kept by the hook itself, which the seventh addendum's own spike
used: a turn can carry several tool calls (harmless, the count only runs ahead) or
none at all (fatal — a sub-agent tail the Developer's own `tdd`/`resolving-merge-
conflicts` skills reach for burns turns with no main-thread `PostToolUse` event at
all, and the old count could stand still for the whole tail while the cap kept
moving). A main-thread `AssistantMessage` is >=1 per harness turn by construction, so
this counter cannot lag the cap the way the old one could.

`TURN_NOTICE_AT` is stated in that same unit, main-thread `AssistantMessage`s, never
turns: `role.max_turns` does not appear anywhere in this module, and neither does a
comparison that subtracts the counted quantity from a figure it is not itself in — a
delivering run has been recorded with `turns: 98` under `max_turns: 80` (ADR-0004's
revising addendum), so `num_turns` and this counter are not interchangeable and the
old `role.max_turns - count <= margin` mixed three quantities that do not share a
unit. The predicate here is absolute: `count >= TURN_NOTICE_AT[role.slug]`.

The value, 15, is a DECLARED STARTING POINT, not a derivation: this counter did not
exist before this commit, so there is no recorded figure in its own unit to cite, and
writing one dressed as a measurement is the failure this revision exists to undo (the
seventh addendum's own 39 was itself a citation, in a different unit, of a single
worst case). 15 sits under the ordinary ADR-0004-recorded close-out cost of a resumed
`max_turns` cut (6-27 turns, issue #102's own evidence) plus the bounded presubmit
allowance of two refused delivery cycles (ADR-0011, second addendum) — comfortably
past the top of that range, so an ordinary close-out fits inside the window it opens.
Its own forward calibration is `turn_notice_firings`, the count each fired attempt
records: one firing across the whole evaluation set says the window opens too late to
matter, and a firing count in the high teens or more on a delivering run says it opens
too early and should widen. A future change to `Role.max_turns` for either covered
role revisits this constant in the same diff, because nothing here derives it from
that cap automatically.

Everything here is a pure function or a named constant; the adapter
(`adapters/claude_sdk/hooks.py`'s `_turn_notice`) only calls `threshold_for`/
`should_notice`/`notice_context` and passes the last one's output as
`additionalContext`, and `application/run_role.py` only calls `notice_note` at the
same seam `rules.denial_note`/`cold_fallback_note` use.
"""

from __future__ import annotations

from .roles import DEVELOPER, DEVELOPER_INTEGRATOR, Role
from .rules import system_note

# See the module docstring for why 15 and why a declared starting point rather than a
# derivation: the counter is main-thread `AssistantMessage`s, not turns, and
# `role.max_turns` is deliberately absent from this module. One value for both roles
# that execute: the ordinary close-out (commit, then deliver) does not differ between
# a subtask's Developer and the integrator's, and neither run's own close-out cost has
# ever been measured separately.
TURN_NOTICE_AT: dict[str, int] = {
    DEVELOPER.slug: 15,
    DEVELOPER_INTEGRATOR.slug: 15,
}


def threshold_for(role: Role) -> int | None:
    """The close-out window's own threshold, in main-thread `AssistantMessage`s, at
    which `role`'s attempt gets the live notice; `None` for every role but the two
    `TURN_NOTICE_AT` names. The conservative default of `rules.turn_cut_retry_message`'s
    own three arms applies here too: only an explicit entry turns the notice on, so a
    role a future task adds inherits no notice in silence."""
    return TURN_NOTICE_AT.get(role.slug)


def should_notice(role: Role, count: int) -> bool:
    """Whether `role`'s attempt, `count` main-thread `AssistantMessage`s in, has
    reached its close-out window: an absolute comparison against `threshold_for`,
    never a subtraction against `role.max_turns`. `False` for a role `threshold_for`
    does not cover. True at and past the threshold, and stays true for every later
    count in the same attempt — the notice re-fires, it does not latch."""
    threshold = threshold_for(role)
    return threshold is not None and count >= threshold


def notice_context(firing_index: int) -> str:
    """The `additionalContext` a `PostToolUse` hook adds every time `should_notice`
    holds true, once per main-thread tool result past the threshold: `firing_index`
    (this attempt's own 1st, 2nd, ...), never a turn figure — no reading of this
    counter converts to `role.max_turns` or to `num_turns` without the risk of mixing
    units the module docstring warns about, so none is offered here. The firing index
    is what makes each repetition a distinct fact instead of the same sentence
    re-sent: a directive repeated verbatim, unchanged, is the seventh addendum's own
    failure mode at a one-shot advisory's frequency, now at a directive's. Close-then-
    deliver, the same order `rules.turn_cut_retry_message` gives the retry after a cut
    already happened — here, before it happens, so the run never needs that retry at
    all."""
    return (
        f"The turn cap is close (close-out notice #{firing_index} this attempt). "
        "Commit what you have before you deliver — a delivery with nothing committed "
        "still spends turns, and so does one the presubmit refuses: if the checks are "
        "red, commit and deliver anyway, and name the gap in `summary`."
    )


def notice_note(cut: bool, firings: int) -> str:
    """The trace a fired notice leaves on the run's section (glossary: Sección): one
    system-written line, at the same seam `rules.denial_note`/`cold_fallback_note`
    use, composed by the same helper (`rules.system_note`) — the role never reports
    its own turn budget in its own words. `firings` is the run's total (folded across
    every recorded failure and the delivering attempt, the same fold `denial_note`'s
    `count` already has). `cut` distinguishes a run that entered the window and
    delivered in the same attempt from one that entered it, was cut by the harness,
    and only delivered on a later attempt — the two read differently on the page
    because they cost differently."""
    plural = "" if firings == 1 else "s"
    if cut:
        text = (
            f"the system warned this attempt the turn cap was close ({firings} "
            f"close-out notice{plural} in total); the harness cut an earlier attempt "
            "before it could deliver, and a later attempt delivered"
        )
    else:
        text = (
            f"the system warned this attempt the turn cap was close ({firings} "
            f"close-out notice{plural} in total); it kept going and delivered"
        )
    return system_note(text)
