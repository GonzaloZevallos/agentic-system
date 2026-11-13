"""The prune of a stage0 rotation (GH-632, part 2b): removes the stages `prunable` allows,
sparing `current`, the stage of the checkout running this (`own`), the switch record's
`previous` and `new`, every stage a process uses and the 2 newest. A `/proc` that cannot be
read prunes nothing. The printed lines are fixed text: no pid, command line or path."""

from __future__ import annotations

from pathlib import Path

from ..domain.stage0_rotation import Unreadable, prunable
from .ports import Stage0Store, Stage0Users
from .stage0_rotate import Out, Prune


def make_prune(root: Path) -> Prune:
    """The `Prune` for the rotation root `root`, the one the store and the users read."""
    base = root.resolve()

    def prune(store: Stage0Store, users: Stage0Users, own: Path, out: Out) -> None:
        try:
            parts = own.resolve().relative_to(base).parts
        except ValueError:
            parts = ()
        try:
            record = store.switch_record()
            previous, new = (record[0], record[1]) if record is not None else (None, None)
            in_use = users.users()
            if not isinstance(in_use, Unreadable) and new is not None:
                in_use = in_use | {new}
            doomed = prunable(
                store.stages(),
                store.current(),
                parts[0] if parts else None,
                in_use,
                keep=2,
                previous=previous,
            )
        except OSError:  # its message may carry a path: say only the fixed line
            doomed = Unreadable()
        if isinstance(doomed, Unreadable):
            out("prune: unreadable")
            return
        failed = removed = 0
        for name in sorted(doomed):
            try:
                store.remove(name)
            except OSError:
                failed += 1
            else:
                removed += 1
        if failed:
            out(f"prune: failed {failed}")
        out(f"pruned: {removed}")

    return prune
