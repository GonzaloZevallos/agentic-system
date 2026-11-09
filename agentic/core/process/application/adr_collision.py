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
there is no cache. Which numbers collide, what they become and what is rewritten is
`domain.adr_numbers`'s; this module reads and hands over, it compares no number."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..domain import adr_numbers
from ..domain.adr_numbers import (
    ADR_DIR,
    Ambiguous,
    DecidedRenaming,
    OwnAdr,
    OwnLine,
    Renaming,
)
from .ports import Ports


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


def _inputs(
    ports: Ports, master: str, head: str, *, named_anywhere: bool = False
) -> _Inputs | Ambiguous | None:
    """The reads of `adr_collision` (see the module docstring for what each costs): `None`
    when no own ADR takes a number master holds, an `Ambiguous` for a duplicate own number.
    With `named_anywhere` (the owner-decision path only, GH-576) it also reads every file at
    `head` that names a moved file, whoever's commit put the line there (a merge resolution
    included): one `paths_naming` per moved file."""
    workspace = ports.workspace
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
    for path in sorted(paths):
        text = workspace.show(head, path)
        if text is not None:
            texts[path] = (workspace.show(master, path), text)
    return _Inputs(moves, lines, trees, texts)


def adr_collision(ports: Ports, master: str, head: str) -> Renaming | Ambiguous | None:
    """What the branch at `head` has to renumber to land on `master`, `Ambiguous` when that
    cannot be decided mechanically, `None` when no own ADR takes a number master holds.

    An own ADR is a file one of the branch's non-merge commits added in `master..head`; one a
    later commit of the branch removed or renamed (a renumber already done) is not, so it
    must still be in the tree at `head`.

    The first pass asks `collisions` about every added ADR against master's tree: a subset
    has neither a duplicate number nor a number master holds under another name unless the
    whole has it, so `{}` there is `None` for any head, after one tree read. That result only
    filters, it is never returned: the answer is computed on the ADRs still at `head`."""
    read = _inputs(ports, master, head)
    if not isinstance(read, _Inputs):
        return read
    return adr_numbers.renumber(read.moves, read.lines, read.trees, read.texts)


def adr_decided(ports: Ports, master: str, head: str) -> DecidedRenaming | Ambiguous | None:
    """`adr_collision` for an owner's decision (GH-544): the same reads (`_inputs`), then
    `renumber_decided`, which applies every rewrite the grammar can make and lists the own lines
    it cannot (`spots`). A `DecidedRenaming` with no spots is a collision `adr_collision` calls
    a `Renaming`; one with spots is the `Ambiguous` it calls so. It also reads the files that
    name a moved ADR without an own line (`named_anywhere`), and applies rule (d) to them."""
    read = _inputs(ports, master, head, named_anywhere=True)
    if not isinstance(read, _Inputs):
        return read
    return adr_numbers.renumber_decided(read.moves, read.lines, read.trees, read.texts)
