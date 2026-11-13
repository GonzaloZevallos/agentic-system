"""The re-point of a split's dependents (GH-619, owner's rule 2026-11-12): a task that ends
`split` never closes its issue, so every open issue whose `Blocked by:` line names it would
wait for ever. The system rewrites those lines itself, to the last part(s) of the split,
and says so with one comment on each issue it edited.

Idempotent over a cut `gh`: each dependent is re-read right before its edit and skipped when
`rules.repoint_blockers` finds nothing left to change, so a second pass edits and comments
nothing. No compare-and-swap exists on an issue body: an edit a human makes between the
re-read and the write is lost (ADR-0062, GH-619 addendum). Part 2 of GH-619 grows this
module with the sweep that finishes what a failed pass left."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from ..domain import rules
from ..domain.landing import IssueRef
from .ports import Ports

Rewrite = Callable[[str, int, Sequence[int]], str | None]


@dataclass(frozen=True)
class RepointResult:
    """What one pass did: the dependents whose listed body names the parent (`considered`),
    how many were edited (`changed`), how many failed (`failed`: a read, an edit, a comment or
    a refused post-condition) and whether the list of open issues could not be read."""

    considered: int = 0
    changed: int = 0
    failed: int = 0
    read_failed: bool = False

    @property
    def complete(self) -> bool:
        return not self.read_failed and self.failed == 0


def repoint_dependents(
    ports: Ports,
    parent: int,
    targets: Sequence[int],
    refs: Sequence[IssueRef],
    excluded: Collection[int],
    rewrite: Rewrite = rules.repoint_blockers,
) -> RepointResult:
    """Re-points the `Blocked by:` lines naming `parent` in every issue of `refs` (from
    `Inbox.open_issue_refs`) that is not in `excluded` (the split's parts and the task's own
    issue). One edit and one comment per issue changed; a failure on one issue never stops
    the others."""
    considered = changed = failed = 0
    for ref in refs:
        number = _issue_number(ref.url)
        if number is None or number in excluded or parent not in rules.blockers_from_body(ref.body):
            continue
        considered += 1
        if _repoint(ports, parent, targets, ref, number, rewrite):
            changed += 1
        else:
            failed += 1
    return RepointResult(considered=considered, changed=changed, failed=failed)


def _repoint(
    ports: Ports, parent: int, targets: Sequence[int], ref: IssueRef, number: int, rewrite: Rewrite
) -> bool:
    """Whether the issue is done: edited and commented, or found already pointing past the
    parent. A failed read, edit or comment, or a refused post-condition, is `False`."""
    try:
        old = ports.inbox.task(number)["description"]
    except (ValueError, OSError):
        return False
    new = rewrite(old, parent, targets)
    if new is None:
        return True
    if not _post_condition(old, new, parent, targets):
        return False
    try:
        ports.tracker.update_issue_body(number, new)
    except (ValueError, OSError):
        return False
    listed = ", ".join(f"#{n}" for n in targets)
    try:
        ports.tracker.comment_on_issue(
            ref.url,
            f"#{parent} was replaced by {listed} in this issue's blockers: #{parent} ended "
            "split into parts and is not closed by them, so this issue waits on the last "
            "part(s) instead.",
        )
    except (ValueError, OSError):
        return False
    return True


def _post_condition(old: str, new: str, parent: int, targets: Sequence[int]) -> bool:
    """The rewrite changed the blockers exactly as asked -- `parent` replaced in place by
    `targets`, without repeats -- and touched only the lines that named `parent`."""
    swapped: list[int] = []
    for number in rules.blockers_from_body(old):
        swapped.extend(targets if number == parent else (number,))
    expected = tuple(dict.fromkeys(swapped))
    return rules.blockers_from_body(new) == expected and rules.repoint_confined(old, new, parent)


def _issue_number(url: str) -> int | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None
