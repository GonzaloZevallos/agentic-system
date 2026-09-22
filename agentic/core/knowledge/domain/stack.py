"""The product's stack: how its code is prepared and verified, as the product declares it.

The system imposes the practices (hexagonal, DDD, SOLID, tests at agreed seams, strict
typing); which tools meet them is the product's to say, and may change over the
product's life. That declaration is `.agentic/stack.yaml` in the product repo, and it is
mandatory: a product that does not declare it does not run, and `preflight` says so
before a single role is paid (issue #36). Unlike the flag vocabulary, the file that holds
is the one at the task's base commit, never the branch's: a task that edits it does not
change the bar it is measured against, and the change applies from the next task, after
the merge.

Three keys today. **`product`**: the name the product goes by, which every span and every
metric of a run carries as a label (issue #47). **`checks`**: the commands with which the
product says its code is sound. **`setup`**: the commands that prepare a fresh checkout to
run them, optional, because a product with no dependencies prepares nothing. Loading is
the `ProductStack` port's job; running the setup when a checkout is born and the checks
when a Developer delivers is `core.process`'s.

The name is the one part that is not read at the task's base. It says *who* the repository
is, not what held at a commit: read at the base, a task older than the key would resume
into a run with no name, and a rename mid-task would split one task's samples across two
labels. It is also the only key the product cannot derive from anything else — a directory
name changes when someone clones under another name, and a remote changes on a fork —
which is why it is declared and not guessed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# What a product copies to start, shown by the error when it declared nothing. It is a
# template and no longer a default: no run ever falls back to it (issue #36).
TEMPLATE = Path(__file__).resolve().parent / "stack.yaml"
OVERLAY = Path(".agentic/stack.yaml")
# Every key the file may declare. An unknown one is a typo, not an extension: a product
# that misspells `checks` would otherwise run with half a stack and never know.
KEYS = ("product", "setup", "checks")
# The name is a label value in Prometheus and a variable in the dashboard: a slug keeps
# one product from arriving under two spellings and keeps the panels' regexes honest.
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class NotDeclared(ValueError):
    """The product declares no stack at the commit that decides. A `ValueError`, so the
    CLI refuses with it."""


def not_declared(source: object) -> NotDeclared:
    return NotDeclared(
        f"{source}: the product declares no stack, and the system has no default to fall "
        f"back on (issue #36). Write {OVERLAY} and commit it:\n\n"
        + TEMPLATE.read_text(encoding="utf-8")
    )


def product(data: object, source: object) -> str:
    """The name the product goes by, the label every run of it carries (issue #47).

    Required: a run whose samples land under no name, or under a name that changed when
    the checkout moved, poisons the very comparison the label exists for (README.md, 9)."""
    declared = _declared(data, source)
    name = declared.get("product")
    if not isinstance(name, str) or not SLUG.match(name.strip()):
        raise ValueError(
            f"{source}: expected `product`, the name this product goes by, as a slug "
            "(lowercase letters, digits and dashes). It labels every span and every "
            "metric of a run, so two products never add up (issue #47)."
        )
    return name.strip()


def checks(data: object, source: object) -> dict[str, str]:
    """Check name -> command, in the file's order. A product that declares no check has
    declared nothing, which is an error, not the default."""
    return _commands(_declared(data, source), source, "checks")


def setup(data: object, source: object) -> dict[str, str]:
    """Setup step -> command, in the file's order, run once when a checkout is born.

    Empty when the product declares none, which is a product that prepares nothing, not
    an error: a Go product or one without dependencies has nothing to do here. Declared
    but empty is a half-written file, and that does fail."""
    declared = _declared(data, source)
    return _commands(declared, source, "setup") if "setup" in declared else {}


def _declared(data: object, source: object) -> dict[str, Any]:
    declared = _mapping(data, source)
    if unknown := [key for key in declared if key not in KEYS]:
        raise ValueError(f"{source}: unknown key(s) {', '.join(sorted(unknown))}; expected {KEYS}")
    return declared


def _commands(declared: dict[str, Any], source: object, key: str) -> dict[str, str]:
    found = _mapping(declared.get(key), source, key)
    for name, command in found.items():
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{source}: expected a command for {name!r} under `{key}`")
    return {str(name): command.strip() for name, command in found.items()}


def _mapping(data: object, source: object, key: str | None = None) -> dict[str, Any]:
    if not isinstance(data, dict) or not data:
        what = f"`{key}`, a non-empty mapping" if key else "a non-empty mapping"
        raise ValueError(f"{source}: expected {what}")
    return data
