"""The CI state of one commit of the stage0 window (GH-632, part 2c): the translation of a
commit's CI read into the `CiState` the rotation rules take. Pure. The names are resolved by
`ci.name_states`, the one joint pass the merge gate also uses, so there is one meaning of green:
`SKIPPED` is red here as it is there."""

from __future__ import annotations

from . import ci
from .ci import CiReport, CiUnreadable
from .stage0_rotation import CiState

# The worst of the names wins: a red name makes the commit red whatever the others say.
_WORST: tuple[ci.NameState, ...] = ("red", "pending", "cancelled", "green")


def ci_state(report: CiReport | CiUnreadable, names: frozenset[str] | None) -> CiState:
    """The CI state of a commit over the check names its stack declares there. `unknown` when
    the stack has no `ci:` (`names` None or empty) or the CI could not be read. Otherwise the
    worst of the names: red, then pending (a run not done, or no run yet), then cancelled (the
    runs of that name were all cancelled), else green. Cancelled is never red."""
    if names is None or not names or isinstance(report, CiUnreadable):
        return "unknown"
    states = set(ci.name_states(report, tuple(sorted(names))).values())
    return next(state for state in _WORST if state in states)
