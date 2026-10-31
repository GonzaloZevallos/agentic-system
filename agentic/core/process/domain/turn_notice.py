"""The live turn-budget notice for a role that executes (ADR-0004, seventh addendum;
revised by its 2026-09-22 addendum, issue #102; recalibrated by ADR-0031, issue #117).

The counted quantity (ADR-0004's 2026-09-22 addendum): the runner's own main-thread
`AssistantMessage` count (`ClaudeSdkRunner.run_once`'s stream loop, incremented once
inside the existing `if message.parent_tool_use_id is None:` branch). Not a
`PostToolUse` tally kept by the hook itself: a turn can carry several tool calls
(harmless, the count only runs ahead) or none at all (fatal -- a sub-agent tail the
Developer's own `tdd`/`resolving-merge-conflicts` skills reach for burns turns with no
main-thread `PostToolUse` event at all). A main-thread `AssistantMessage` is >=1 per
harness turn by construction, so this counter cannot lag the cap the way a
`PostToolUse` tally could.

`TURN_NOTICE_AT` is stated in that same unit, main-thread `AssistantMessage`s, never
turns: `role.max_turns` does not appear anywhere in this module, and neither does a
comparison that subtracts the counted quantity from a figure it is not itself in -- a
delivering run has been recorded with `turns: 98` under `max_turns: 80` (ADR-0004's
2026-09-22 addendum), so `num_turns` and this counter are not interchangeable and a
`role.max_turns - count <= margin` predicate would mix three quantities that do not
share a unit. Every predicate here is absolute: it compares the counted quantity
against a position stated in the same unit, never against `role.max_turns`.

**Two positions, not one.** GH-112 (issue #117) showed the single-threshold, always-
refiring window this module shipped with (2026-09-22) firing 109 times across an
80-turn attempt that still exhausted `max_turns`: a directive repeated on almost every
main-thread tool result stopped reading as a directive at all. `TURN_NOTICE_AT` is now
a tuple of absolute positions per role, `(45, 65)`: a first, softer window that asks
the role to stop opening new work, and a second, harder one that asks it to deliver on
this turn. Two facts, never one sentence with an ordinal swapped in.

**The three named constants.** `WORST_CLOSE_OUT_TURNS = 27` is a *citation*, not a
derivation: the recorded 6-27 turn range of an ordinary close-out (commit, then
deliver) on a resumed `max_turns` cut, issue #102's own evidence. `PRESUBMIT_
ALLOWANCE_TURNS = 8` is *declared, not measured*: two refused delivery cycles at four
turns each (ADR-0011, second addendum) -- the reading that would revise it is
`main_thread_turns` recorded at delivery on runs with `presubmit_denials > 0`, once
that figure exists to read. `GUARANTEED_HEADROOM_TURNS = WORST_CLOSE_OUT_TURNS +
PRESUBMIT_ALLOWANCE_TURNS = 35` is why the first window sits at 45 and not, say, 60:
`80 - 60 = 20` sits below the 27-turn worst case the constant exists to contain, so 60
could not guarantee an ordinary close-out fits inside the window it opens; 45 leaves
`80 - 45 = 35 >= 35`.

**The collapsing predicate.** `windows_crossed(role, count) -> int` is how many of
`role`'s positions are at or below `count` -- `0` for a role `TURN_NOTICE_AT` does not
cover. It is the single function that decides how many windows a count has crossed;
nothing else in this module or in `adapters/claude_sdk/hooks.py` recomputes that
question. The hook (`hooks._turn_notice`) calls it once per `PostToolUse` event, keeps
its own `fired` count of windows already reported, and emits `additionalContext`
exactly when `crossed > fired` -- one message, `notice_context(crossed)` -- then sets
`fired = crossed`, never `fired + 1`. Without collapsing, a text-only turn or a
sub-agent tail (neither of which produces a main-thread `PostToolUse` event) can let
the count jump from under 45 straight past 65 between two consecutive main-thread tool
results, and an increment-by-one scheme would spend both windows, back to back, on the
same pair of events -- the repeated-directive failure this module exists to remove, now
at two messages instead of 109. `should_notice`, the boolean predicate this replaces,
is gone: a count and a role decide one thing, how many windows are behind it, not two
functions (`should_notice`/`threshold_for`) that could disagree about the same fact.

**Two distinct texts.** `notice_context(window)` returns window 1's text or window 2's,
never a shared sentence with a number swapped in. Window 1 asks the role to stop
opening new work and finish the unit already in flight -- it does not say "deliver
now", which at 45 (fully half the budget still ahead of a role covered here) would be
false, and a directive that is false once is discounted the next time it is read.
Window 2 asks for delivery on this turn, in the close-then-deliver order `rules.
turn_cut_retry_message` already uses for the retry after a cut happens: here, before
it happens, so the run never needs that retry at all. Neither text names a turn
figure, a remaining count or `role.max_turns`: no reading of this counter converts to
either without risking the unit mixing this module's docstring warns against.

**The assumption, and its falsifier, named rather than left implicit.** Every turn the
CLI counts against `role.max_turns` produces at least one main-thread `AssistantMessage`,
so `count >= consumed turns`. Nothing in this repository verifies that claim; it is an
assumption about the SDK, and the headroom guarantee above holds only under it. Its
falsifier: an attempt cut by `max_turns` whose recorded `main_thread_turns` (the
counter's own final value, carried through `RawResult` -> `AttemptFailed` -> `Failure`
-> `RoleRun` -> the task document frontmatter, on every attempt and not only the
delivering one) is below the cap that same attempt ran with (`Failure.max_turns`; the
role's own `max_turns` for a record without the field). Such a record would mean the
counter can lag the cap after all, and would invalidate both positions above.

**What `turn_notice_firings` now measures, and what replaces it as the forward
calibration instrument.** Under a two-position tuple, `turn_notice_firings` is bounded
by the number of windows (at most 2 per attempt), so a count of "how many times it
fired" stops being informative about whether the positions are well placed -- it
answers a question this module no longer asks in the same way the single, always-
refiring window's firing count once did. `main_thread_turns`, once recorded across
enough runs, is what replaces it as the forward-calibration read: on a delivering run,
a value far above 65 says both windows opened long before the close-out and the
positions are too low; a value below 45 says the run never entered the window at all,
the healthy case; on a run cut by `max_turns`, how far past the last window (65) the
attempt went before the harness stopped it is what would justify a third window --
never an unbounded re-fire, which is the mechanism this revision removed.

Everything here is a pure function or a named constant; the adapter
(`adapters/claude_sdk/hooks.py`'s `_turn_notice`) only calls `threshold_for`/
`windows_crossed`/`notice_context` and passes the last one's output as
`additionalContext`, and `application/run_role.py` only calls `notice_note` at the
same seam `rules.denial_note`/`cold_fallback_note` use.
"""

from __future__ import annotations

from .roles import DEVELOPER, DEVELOPER_INTEGRATOR, Role
from .rules import system_note

# See the module docstring for the derivation of each constant below. One tuple of
# positions for both roles that execute: the ordinary close-out (commit, then deliver)
# does not differ between a subtask's Developer and the integrator's, and neither
# role's own close-out cost has ever been measured separately.
TURN_NOTICE_AT: dict[str, tuple[int, ...]] = {
    DEVELOPER.slug: (45, 65),
    DEVELOPER_INTEGRATOR.slug: (45, 65),
}

# A citation of the recorded 6-27 turn range of an ordinary close-out (issue #102's
# own evidence, a resumed `max_turns` cut), not a derivation: this module's own
# docstring names it as such.
WORST_CLOSE_OUT_TURNS = 27
# Two refused delivery cycles at four turns each (ADR-0011, second addendum) --
# declared, not measured. The reading that would revise it: `main_thread_turns`
# recorded at delivery on runs with `presubmit_denials > 0`.
PRESUBMIT_ALLOWANCE_TURNS = 8
# The headroom the first window guarantees against `role.max_turns`, for every role
# `TURN_NOTICE_AT` covers (a relation the tests assert; this module never computes it
# against `role.max_turns` itself -- `role.max_turns` names no threshold here).
GUARANTEED_HEADROOM_TURNS = WORST_CLOSE_OUT_TURNS + PRESUBMIT_ALLOWANCE_TURNS


def threshold_for(role: Role) -> tuple[int, ...] | None:
    """The close-out windows' own positions, in main-thread `AssistantMessage`s, at
    which `role`'s attempt gets the live notice; `None` for every role but the two
    `TURN_NOTICE_AT` names. The conservative default of `rules.turn_cut_retry_message`'s
    own three arms applies here too: only an explicit entry turns the notice on, so a
    role a future task adds inherits no notice in silence."""
    return TURN_NOTICE_AT.get(role.slug)


def windows_crossed(role: Role, count: int) -> int:
    """How many of `role`'s positions in `TURN_NOTICE_AT` are at or below `count`: `0`
    for a role `TURN_NOTICE_AT` does not cover, and `0` for a count before the first
    position. The single function that answers "how many windows has this count
    crossed" -- `hooks._turn_notice` compares this against its own running `fired`
    count and never recomputes the question a second way. Never grows past the number
    of declared positions: a count far past the last one still reads as that many, not
    more, so a hook that only fires on a rising value from this function fires at most
    once per position, never once per event."""
    windows = TURN_NOTICE_AT.get(role.slug)
    if windows is None:
        return 0
    return sum(1 for window in windows if count >= window)


def notice_context(window: int) -> str:
    """The `additionalContext` a `PostToolUse` hook adds when `windows_crossed` reports
    a new window reached: `window` is 1 or 2, which position was just crossed, an
    ordinal that picks between two genuinely different directives rather than a shared
    sentence with a number swapped in. Neither names a turn figure, a remaining count
    or `role.max_turns` -- no reading of the counter this module tracks converts to
    either without risking the unit mixing the module docstring warns against.

    Window 1: stop opening new work, finish the unit already in flight, commit what
    exists -- never "deliver now", which at the first position (roughly the midpoint
    of a covered role's budget) would be false, and a directive that is false once is
    discounted the next time it is read. Window 2: deliver on this turn, in the
    close-then-deliver order `rules.turn_cut_retry_message` already gives the retry
    after a cut happens -- here, before it happens, so the run never needs that retry
    at all."""
    if window <= 1:
        return (
            "The turn cap is getting close. Stop opening new work: finish the unit "
            "already in flight and commit what exists before you start anything else."
        )
    return (
        "The turn cap is very close. Commit what you have and deliver on this turn -- "
        "a delivery with nothing committed still spends turns, and so does one the "
        "presubmit refuses: if the checks are red, commit and deliver anyway, and "
        "name the gap in `summary`."
    )


def notice_note(cut: bool, firings: int) -> str:
    """The trace a fired notice leaves on the run's section (glossary: Sección): one
    system-written line, at the same seam `rules.denial_note`/`cold_fallback_note`
    use, composed by the same helper (`rules.system_note`) -- the role never reports
    its own turn budget in its own words. `firings` is the run's total (folded across
    every recorded failure and the delivering attempt, the same fold `denial_note`'s
    `count` already has). `cut` distinguishes a run that entered a window and
    delivered in the same attempt from one that entered it, was cut by the harness,
    and only delivered on a later attempt -- the two read differently on the page
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
