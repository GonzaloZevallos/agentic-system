"""The one place a collision between a branch's ADR numbers and master's is assembled
(GH-229, ADR-0049): the git reads and the pure rule's answer.

What it reads, per call (each count is a counter asserted in
`tests/core/process/application/test_adr_collision_cost.py`): `added_adrs` always, one `git
log` bounded by the branch's own commits; a branch that added none stops there. Then
`tree_names(master)` once, and when no added ADR can collide that is the whole cost. Only
when one can (a number master already holds under another file name, or a number two of them
share) does it read `tree_names(head)` a second time, to drop what a later commit removed.
The lines, the trees of the commits that added them and the texts of the rewrite are read
only on a real collision. All of it is bounded by the branch's own commits and ADR files;
there is no cache. `adr_decided` alone adds one `paths_naming` (a `git grep -l -F`) per moved
file, and reads whole only the files that name it (GH-576); `adr_collision` never does.
On a real collision both add (GH-579) one `merge_base`, one `changed_paths`, one `paths_naming`
per old number and, for each file the branch changed that names one (a moved ADR file is not
one), its text at the merge base, plus its texts at master and at head when no own line
already brought it in: what classifies a reference no non-merge commit of the branch added.
No collision, none of it.
GH-611: a branch that added an `NNNN-` ADR reads `adr_naming` at master once; on a numbered
product that is the whole difference (the stack, no git). On a dated one the numeric reads are
not made: `tree_names(master)` and `tree_names(head)` once each, and the answer is `Undated`.
Which numbers collide, what they become and what is rewritten is `domain.adr_numbers`'s; this
module reads and hands over, it compares no number."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..domain import adr_numbers
from ..domain.adr_dated import Undated
from ..domain.adr_numbers import (
    ADR_DIR,
    Ambiguous,
    DecidedRenaming,
    OwnAdr,
    OwnLine,
    Renaming,
)
from .ports import AddedAdr, Ports


def touches_adrs(paths: Sequence[str] | None) -> bool:
    """Whether a diff touches a path under `docs/adr/`: the trigger for the read, never the
    branch's own ADRs (those are what its own commits added)."""
    return any(path.startswith(f"{ADR_DIR}/") for path in paths or ())


@dataclass(frozen=True)
class _Inputs:
    """What `renumber` and `renumber_decided` are given, read once from git."""

    moves: Mapping[str, str]
    lines: Sequence[OwnLine]
    trees: Mapping[str, Sequence[str]]
    texts: Mapping[str, tuple[str | None, str]]
    bases: Mapping[str, str | None]


def _inputs(
    ports: Ports,
    master: str,
    head: str,
    *,
    named_anywhere: bool = False,
    added: Sequence[AddedAdr] | None = None,
) -> _Inputs | Ambiguous | None:
    """The reads of `adr_collision` (see the module docstring for what each costs): `None`
    when no own ADR takes a number master holds, an `Ambiguous` for a duplicate own number.
    With `named_anywhere` (the owner-decision path only, GH-576) it also reads every file at
    `head` that names a moved file, whoever's commit put the line there (a merge resolution
    included): one `paths_naming` per moved file. `added` is `added_adrs` when the caller has
    read it already (GH-611)."""
    workspace = ports.workspace
    if added is None:
        added = workspace.added_adrs(master, head)
    if not added:
        return None
    master_names = workspace.tree_names(master, ADR_DIR)
    if adr_numbers.collisions(master_names, [OwnAdr(a.path, a.commit) for a in added]) == {}:
        return None
    at_head = set(workspace.tree_names(head, ADR_DIR))
    own = [OwnAdr(a.path, a.commit) for a in added if a.path.rsplit("/", 1)[-1] in at_head]
    if not own:
        return None
    moves = adr_numbers.collisions(master_names, own)
    if isinstance(moves, Ambiguous):
        return moves
    if not moves:
        return None
    lines = [
        OwnLine(found.path, found.commit, found.text)
        for found in workspace.added_lines(master, head, adr_numbers.old_numbers(moves))
    ]
    trees = {
        commit: workspace.tree_names(commit, ADR_DIR)
        for commit in sorted({x.commit for x in lines})
    }
    texts: dict[str, tuple[str | None, str]] = {}
    paths = {x.path for x in lines} | set(moves)
    if named_anywhere:
        for old in sorted(moves):
            paths.update(workspace.paths_naming(head, old.rsplit("/", 1)[-1]))
    # GH-579: a file the branch changed that names an old number is a candidate for the
    # references no non-merge commit of the branch added; its merge-base text is master's too.
    base = workspace.merge_base(master, head)
    changed = set(workspace.changed_paths(base, head))
    naming: set[str] = set()
    for number in adr_numbers.old_numbers(moves):
        naming.update(workspace.paths_naming(head, number))
    candidates = sorted((changed & naming) - set(moves))  # a moved ADR is all the branch's
    for path in sorted(paths | set(candidates)):
        text = workspace.show(head, path)
        if text is not None:
            texts[path] = (workspace.show(master, path), text)
    bases = {path: workspace.show(base, path) for path in candidates if path in texts}
    return _Inputs(moves, lines, trees, texts, bases)


def _undated(ports: Ports, master: str, head: str, added: Sequence[AddedAdr]) -> Undated | None:
    """GH-611: every own `NNNN-` ADR still at `head` that master's tree does not hold under
    that same name, whether or not its number collides: what a product that names ADRs by date
    renames. `added` is `added_adrs`, already read. One `tree_names(master)` and one
    `tree_names(head)`; `None` when there is no such ADR."""
    workspace = ports.workspace
    master_names = set(workspace.tree_names(master, ADR_DIR))
    at_head = set(workspace.tree_names(head, ADR_DIR))
    own = tuple(
        OwnAdr(a.path, a.commit)
        for a in added
        if _numbered(a.path)
        and (name := a.path.rsplit("/", 1)[-1]) in at_head
        and name not in master_names
    )
    return Undated(own) if own else None


def _numbered(path: str) -> bool:
    return adr_numbers.number_of(path.rsplit("/", 1)[-1]) is not None


def adr_collision(ports: Ports, master: str, head: str) -> Renaming | Ambiguous | Undated | None:
    """What the branch at `head` has to renumber to land on `master`, `Ambiguous` when that
    cannot be decided mechanically, `None` when no own ADR takes a number master holds. On a
    product that names ADRs by date, `Undated` instead of any of the numeric answers (GH-611).

    An own ADR is a file one of the branch's non-merge commits added in `master..head`; one a
    later commit of the branch removed or renamed (a renumber already done) is not, so it
    must still be in the tree at `head`.

    The first pass asks `collisions` about every added ADR against master's tree: a subset
    has neither a duplicate number nor a number master holds under another name unless the
    whole has it, so `{}` there is `None` for any head, after one tree read. That result only
    filters, it is never returned: the answer is computed on the ADRs still at `head`."""
    added = ports.workspace.added_adrs(master, head)
    if any(_numbered(a.path) for a in added) and ports.stack.adr_naming(master) == "dated":
        return _undated(ports, master, head, added)
    read = _inputs(ports, master, head, added=added)
    if not isinstance(read, _Inputs):
        return read
    return adr_numbers.renumber(read.moves, read.lines, read.trees, read.texts, bases=read.bases)


def adr_decided(ports: Ports, master: str, head: str) -> DecidedRenaming | Ambiguous | None:
    """`adr_collision` for an owner's decision (GH-544): the same reads (`_inputs`), then
    `renumber_decided`, which applies every rewrite the grammar can make and lists the own lines
    it cannot (`spots`). A `DecidedRenaming` with spots is the `Ambiguous` `adr_collision` calls so;
    one with none is not always a `Renaming` there (GH-582): rule (d) runs only here, so a link
    label `adr_collision` leaves an `unclassified_token` is rewritten, and the collision it called
    `Ambiguous` has no spot left. It also reads the files that name a moved ADR without an own
    line (`named_anywhere`), and applies rule (d) to them."""
    read = _inputs(ports, master, head, named_anywhere=True)
    if not isinstance(read, _Inputs):
        return read
    return adr_numbers.renumber_decided(
        read.moves, read.lines, read.trees, read.texts, bases=read.bases
    )
