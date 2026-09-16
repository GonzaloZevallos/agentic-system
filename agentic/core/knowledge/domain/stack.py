"""The product's stack: how its code is built, prepared and verified, as the product
declares it.

The system imposes the practices (hexagonal, DDD, SOLID, tests at agreed seams, strict
typing); which tools meet them is the product's to say, and may change over the
product's life. That declaration is `.agentic/stack.yaml` in the product repo, and the
system ships a default (`stack.yaml`, next to this module) with the one check every
product has. Unlike the flag vocabulary, the file that holds is the one at the task's
base commit, never the branch's: a task that edits it does not change the bar it is
measured against, and the change applies from the next task, after the merge.

Today it declares the **checks**: the commands with which the product says its code is
sound. Loading is the `ProductStack` port's job; running the checks (the presubmit)
and turning them into the reviewer's permission rules are `core.process`'s.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "stack.yaml"
OVERLAY = Path(".agentic/stack.yaml")
# Every key the file may declare. An unknown one is a typo, not an extension: a product
# that misspells `checks` would otherwise run with the default and never know.
KEYS = ("checks",)


def checks(data: object, source: object) -> dict[str, str]:
    """Check name -> command, in the file's order. A product that declares no check has
    declared nothing, which is an error, not the default."""
    declared = _mapping(data, source)
    if unknown := [key for key in declared if key not in KEYS]:
        raise ValueError(f"{source}: unknown key(s) {', '.join(sorted(unknown))}; expected {KEYS}")
    found = _mapping(declared.get("checks"), source, "checks")
    for name, command in found.items():
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{source}: expected a command for check {name!r}")
    return {str(name): command.strip() for name, command in found.items()}


def _mapping(data: object, source: object, key: str | None = None) -> dict:
    if not isinstance(data, dict) or not data:
        what = f"`{key}`, a non-empty mapping" if key else "a non-empty mapping"
        raise ValueError(f"{source}: expected {what}")
    return data
