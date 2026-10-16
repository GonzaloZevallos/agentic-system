"""The one place a collision between a branch's ADR numbers and master's is assembled
(GH-229, ADR-0049): the git reads, in two phases, and the pure rule's answer.

The first phase asks only for the ADR files the branch's own commits added; a branch that
added none costs that one read. Only a number master already holds under another file name
asks for the lines and the trees the rewrite needs. Which numbers collide, what they become
and what is rewritten is `domain.adr_numbers`'s; this module reads and hands over, it
compares no number."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import adr_numbers
from ..domain.adr_numbers import ADR_DIR, Ambiguous, OwnAdr, OwnLine, Renaming
from .ports import Ports


def touches_adrs(paths: Sequence[str] | None) -> bool:
    """Whether a diff touches a path under `docs/adr/`: the trigger for the read, never the
    branch's own ADRs (those are what its own commits added)."""
    return any(path.startswith(f"{ADR_DIR}/") for path in paths or ())


def adr_collision(ports: Ports, master: str, head: str) -> Renaming | Ambiguous | None:
    """What the branch at `head` has to renumber to land on `master`, `Ambiguous` when that
    cannot be decided mechanically, `None` when no own ADR takes a number master holds.

    An own ADR is a file one of the branch's non-merge commits added in `master..head`; one a
    later commit of the branch removed or renamed (a renumber already done) is not, so it
    must still be in the tree at `head`."""
    workspace = ports.workspace
    added = workspace.added_adrs(master, head)
    if not added:
        return None
    at_head = set(workspace.tree_names(head, ADR_DIR))
    own = [OwnAdr(a.path, a.commit) for a in added if a.path.rsplit("/", 1)[-1] in at_head]
    if not own:
        return None
    moves = adr_numbers.collisions(workspace.tree_names(master, ADR_DIR), own)
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
    for path in sorted({x.path for x in lines} | set(moves)):
        text = workspace.show(head, path)
        if text is not None:
            texts[path] = (workspace.show(master, path), text)
    return adr_numbers.renumber(moves, lines, trees, texts)
