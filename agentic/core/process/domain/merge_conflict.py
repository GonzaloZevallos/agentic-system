"""The conflict git found merging a head with master, kept as a fact (GH-492, ADR-0080).

For a product that declares `ci:` the merge gate builds the head merged with master with git.
When that merge leaves unmerged paths, the conflict is a fact about exactly that head and
that master, and it is recorded as one row, `merge_conflict: {head, master, at}`, that each
write replaces. This module is the pure side: the row (`record`) and the one question it
answers (`applies`), with full commit ids compared by `rules.same_commit` and never by text.
It reads no file and calls no port; `rules.merge_conflicts_from` turns the answer into
`MergeFacts.conflicts`."""

from __future__ import annotations

from collections.abc import Mapping

# The module, not the name: `rules` imports this one too, and reads it at call time.
from . import rules
from .task import MergeConflictRecord


def record(head: str, master: str, at: str) -> MergeConflictRecord:
    """The row a merge that left unmerged paths writes, for the pair it was read on."""
    return {"head": head, "master": master, "at": at}


def applies(record: Mapping[str, object] | None, head: str | None, master: str | None) -> bool:
    """Whether a recorded conflict is the one of `head` merged with `master`: the record is
    well formed (`head`, `master` and `at` all non-empty text) and both of its shas name the
    commits asked about, under `rules.same_commit`. Once either sha moved, a missing,
    malformed or `None` record, it is `False`: a record is never read for another pair."""
    if not isinstance(record, Mapping):
        return False
    recorded_head, recorded_master, at = record.get("head"), record.get("master"), record.get("at")
    if not (isinstance(recorded_head, str) and isinstance(recorded_master, str)):
        return False
    if not (isinstance(at, str) and at):
        return False
    return rules.same_commit(recorded_head, head) and rules.same_commit(recorded_master, master)
