"""Product checks: the commands with which the product says its code is sound (tests,
linter, type checker).

The system ships a default (`checks.yaml`, next to this module) and a product replaces it
entirely with `.agentic/checks.yaml`, the same pattern as the flag vocabulary. Unlike
the flags, the file that holds is the one at the task's base commit, never the branch's:
a task that edits it does not change the bar it is measured against, and the change
applies from the next task, after the merge. Loading is the `ProductChecks` port's job;
running them (the delivery gate) and turning them into the reviewer's permission rules
are `core.process`'s.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "checks.yaml"
OVERLAY = Path(".agentic/checks.yaml")


def declared(data: object, source: object) -> dict[str, str]:
    """Check name -> command, in the file's order. A product that empties its file has
    declared nothing, which is an error, not the default."""
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{source}: expected a non-empty mapping of check name -> command")
    for name, command in data.items():
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{source}: expected a command for check {name!r}")
    return {str(name): command.strip() for name, command in data.items()}
