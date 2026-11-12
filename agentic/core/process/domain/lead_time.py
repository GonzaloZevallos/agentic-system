"""The lead time a closed task's document keeps (GH-599). Pure: no I/O.

Either the two instants it is measured between, `{labeled_at, merged_at}`, or why it is not
known, `{unknown: no_merge | unreadable}`. This module is the one builder of that value: no
caller writes `labeled_at`, `merged_at` or `unknown` by hand. `merged_at` is the pull request's
own `mergedAt`, never a local clock; a value that could not be read is `unreadable`, never a guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal, TypedDict

LEAD_TIME_KEY: Final = "lead_time"
Unknown = Literal["no_merge", "unreadable"]


class Known(TypedDict):
    labeled_at: str
    merged_at: str


class Unreadable(TypedDict):
    unknown: Unknown


LeadTime = Known | Unreadable

# The patch of an ending with no merged pull request: discarded, split, tracker only, or a
# pull request closed without merging.
NO_MERGE: Final[Mapping[str, LeadTime]] = {LEAD_TIME_KEY: {"unknown": "no_merge"}}


def from_read(labeled_at: str | None, merged_at: str | None) -> LeadTime:
    """The value for a merged pull request: the two instants when both were read, else
    `unreadable`."""
    if labeled_at and merged_at:
        return {"labeled_at": labeled_at, "merged_at": merged_at}
    return {"unknown": "unreadable"}


def replaceable(existing: object) -> bool:
    """Whether a stored value may still be replaced: only `{unknown: no_merge}`, which a later
    merge of the same pull request turns into a real reading."""
    return isinstance(existing, Mapping) and dict(existing) == {"unknown": "no_merge"}
