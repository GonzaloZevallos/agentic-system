"""The product's stack: how its code is prepared and verified, as the product declares it.

The system imposes the practices (hexagonal, DDD, SOLID, tests at agreed seams, strict
typing); which tools meet them is the product's to say, and may change over the
product's life. That declaration is `.agentic/stack.yaml` in the product repo, and it is
mandatory: a product that does not declare it does not run, and `preflight` says so
before a single role is paid (issue #36). Unlike the flag vocabulary, the file that holds
is the one at the task's base commit, never the branch's: a task that edits it does not
change the bar it is measured against, and the change applies from the next task, after
the merge.

**`product`**: the name the product goes by, which every span and every metric of a run
carries as a label (issue #47). **`checks`**: the commands with which the product says its
code is sound. **`setup`**: the commands that prepare a fresh checkout to run them,
optional, because a product with no dependencies prepares nothing. **`witness`**: optional,
the command with which the product says where the code its checks just measured lives.
**`forbidden`**: optional (issue #38), the suppressions -- a comment syntax, a linter
directive, whatever the product's own tools spell -- an Edit or a Write may not add: a
pattern, the extensions it applies to and the reason a denial carries verbatim. The system
itself knows none of this; every spelling lives here, in the product's own file. Loading is
the `ProductStack` port's job; running the setup when a checkout is born, the checks when a
Developer delivers, and the suppression cage while it writes, is `core.process`'s.

**`parallel_checks`**: optional, a strict boolean (ADR-0044), absent meaning `False`. `true`
is the product saying that no two of its declared checks share a port, a database file or a
cache, so the delivery gate may run them concurrently; without it the gate runs them one
after the other, as it always has. Anything that is not a bool (`"true"`, `1`, `null`) is a
declaration that is there but broken, and fails at load instead of being read as a no.

**`affected_checks`** and **`affected_fallback`**: optional (ADR-0046). The first maps a
check's name, which must also be under `checks`, to its affected-only command: it carries the
literal token `{base}`, never at its start, and the delivery gate and the reviewer -- never
the merge -- run it in place of the full one. The second lists the globs of the paths whose
change forces the full form. Absent they read as `{}` and `()`; present and broken they fail
at load.

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

from agentic.core.process.domain.design import Design
from agentic.core.process.domain.suppressions import Forbidden

# What a product copies to start, shown by the error when it declared nothing. It is a
# template and no longer a default: no run ever falls back to it (issue #36).
TEMPLATE = Path(__file__).resolve().parent / "stack.yaml"
OVERLAY = Path(".agentic/stack.yaml")
# Every key the file may declare. An unknown one is a typo, not an extension: a product
# that misspells `checks` would otherwise run with half a stack and never know.
KEYS = (
    "product",
    "setup",
    "checks",
    "witness",
    "parallel_checks",
    "affected_checks",
    "affected_fallback",
    "forbidden",
    "design",
    "ci",
)
# The token an affected-only command carries where the task's base sha goes (ADR-0046).
BASE_TOKEN = "{base}"
# Every sub-key `design` may declare (ADR-0040): the same "unknown is a typo" rule,
# one level down.
DESIGN_KEYS = ("prototype", "entry", "sample_app", "compare")
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


def witness(data: object, source: object) -> str | None:
    """The command with which the product says where the code its checks just measured
    lives (process glossary: Testigo del checkout), run by the presubmit in the
    Developer's checkout before them. Optional: a key absent is a product that declares
    none, and `None` -- never `""`, which this never returns -- is that answer's only
    spelling. A scalar, not a mapping like `checks` and `setup`: one testigo, one
    verdict, no name to declare, so it does not go through `_commands`, which is shaped
    for mappings. Declared but blank or not a string is the same half-written-file
    error `setup` raises for an empty step."""
    declared = _declared(data, source)
    if "witness" not in declared:
        return None
    value = declared["witness"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: expected a command for `witness`, a non-empty string")
    return value.strip()


def parallel_checks(data: object, source: object) -> bool:
    """Whether the product lets its declared checks run concurrently (ADR-0044).

    Opt-in: a key absent is `False`, the serial gate of today. Present, it must be a real
    bool -- `isinstance`, so `1` and `"true"` are refused and not coerced -- the same
    "declared but broken" rule `witness` follows: a typo that quietly read as a no would
    leave a product serial while it believes it is parallel, and one that read as a yes
    would race checks that share a port."""
    declared = _declared(data, source)
    if "parallel_checks" not in declared:
        return False
    value = declared["parallel_checks"]
    if not isinstance(value, bool):
        raise ValueError(f"{source}: expected `parallel_checks` to be true or false, a boolean")
    return value


def ci(data: object, source: object) -> tuple[str, ...]:
    """The check-run names that gate a merge when the product trusts its GitHub CI
    (ADR-0045, GH-216 amendment), in the file's order.

    Opt-in: a key absent is `()`, a product that keeps the local merge gate as it is.
    Present, it is a non-empty list of distinct non-empty strings: a declared name that
    can never match, or that matches twice, is a gate that quietly waits forever or counts
    one run twice, so every shape that is not that fails at load, naming the key."""
    declared = _declared(data, source)
    if "ci" not in declared:
        return ()
    found = declared["ci"]
    if not isinstance(found, list) or not found:
        raise ValueError(f"{source}: expected `ci`, a non-empty list of check-run names")
    names: list[str] = []
    for item in found:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{source}: expected non-empty check-run names under `ci`")
        names.append(item.strip())
    if len(set(names)) != len(names):
        raise ValueError(f"{source}: `ci` declares the same check-run name twice")
    return tuple(names)


def affected_checks(data: object, source: object) -> dict[str, str]:
    """Check name -> its affected-only command, in the file's order (ADR-0046).

    Empty when the product declares none: the gate of today, byte for byte. Present, every
    entry is a command that the gate can substitute `{base}` into and the reviewer's
    permission rule can prefix: it carries the literal token and does not start with it.
    A name that is not a declared check is refused, because it would never run and the
    product would believe it did."""
    declared = _declared(data, source)
    if "affected_checks" not in declared:
        return {}
    found = _commands(declared, source, "affected_checks")
    known = _commands(declared, source, "checks")
    for name, command in found.items():
        if name not in known:
            raise ValueError(
                f"{source}: {name!r} under `affected_checks` is not a check declared under "
                f"`checks`; expected one of {', '.join(known)}"
            )
        if BASE_TOKEN not in command:
            raise ValueError(
                f"{source}: the command for {name!r} under `affected_checks` has no "
                f"{BASE_TOKEN} token to carry the task's base"
            )
        if command.startswith(BASE_TOKEN):
            raise ValueError(
                f"{source}: the command for {name!r} under `affected_checks` must not "
                f"start with {BASE_TOKEN}: the reviewer is allowed the prefix before it"
            )
    return found


def affected_fallback(data: object, source: object) -> tuple[str, ...]:
    """The globs whose change forces the full form of every check (ADR-0046), in file
    order. Absent is `()`; an empty list is the same answer, spelled out. Anything that is
    not a list of non-blank strings is declared but broken, and fails here."""
    declared = _declared(data, source)
    if "affected_fallback" not in declared:
        return ()
    found = declared["affected_fallback"]
    if not isinstance(found, list) or not all(
        isinstance(glob, str) and glob.strip() for glob in found
    ):
        raise ValueError(
            f"{source}: expected `affected_fallback` to be a list of non-blank glob strings"
        )
    return tuple(glob.strip() for glob in found)


def forbidden(data: object, source: object) -> tuple[Forbidden, ...]:
    """The suppressions this product forbids a Developer's Edit or Write from adding,
    declared under `forbidden`: a rule per entry, in file order -- the order the
    process domain's pure predicate (`process.domain.suppressions.denial`) tries them
    in, first match wins, so the reason a Developer reads is deterministic.

    Absent is a product that forbids none, legitimate like `setup`'s absence: a Go
    product, or one that genuinely forbids nothing, is not a half-written file.
    Present and empty, or any entry missing a piece, is: every failure below is a
    `ValueError` raised here, at load, so a broken declaration never reaches a hook at
    run time to fail open or fail closed on it."""
    declared = _declared(data, source)
    if "forbidden" not in declared:
        return ()
    found = declared["forbidden"]
    if not isinstance(found, list) or not found:
        raise ValueError(f"{source}: expected `forbidden`, a non-empty list of rules")
    return tuple(_forbidden_rule(entry, source, index) for index, entry in enumerate(found))


def design(data: object, source: object) -> Design | None:
    """The optional `design` block (ADR-0040, glossary: Diseño del producto): the
    static prototype's directory and entry screen, and the two optional commands --
    `sample_app` and `compare` -- a design role may run instead of a browser.

    Absent is a product that has not handed the human a prototype yet, the same
    legitimate absence `witness` has: `None`, never an error. Present, it validates
    like `forbidden`'s own entries: a non-mapping, a blank `prototype` or `entry`, a
    missing `entry`, a non-string or blank command, or an unknown sub-key all raise
    here, at load, so a broken declaration never reaches a lead mid-task."""
    declared = _declared(data, source)
    if "design" not in declared:
        return None
    found = _mapping(declared["design"], source, "design")
    if unknown := [key for key in found if key not in DESIGN_KEYS]:
        raise ValueError(
            f"{source}: unknown key(s) {', '.join(sorted(unknown))} under `design`; "
            f"expected {DESIGN_KEYS}"
        )
    prototype = found.get("prototype")
    if not isinstance(prototype, str) or not prototype.strip():
        raise ValueError(f"{source}: expected a non-empty `prototype` under `design`")
    entry = found.get("entry")
    if not isinstance(entry, str) or not entry.strip():
        raise ValueError(f"{source}: expected a non-empty `entry` under `design`")
    return Design(
        prototype=prototype.strip(),
        entry=entry.strip(),
        sample_app=_design_command(found, source, "sample_app"),
        compare=_design_command(found, source, "compare"),
    )


def _design_command(found: dict[str, Any], source: object, key: str) -> str | None:
    if key not in found:
        return None
    value = found[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: expected a command for `{key}` under `design`")
    return value.strip()


def _forbidden_rule(entry: object, source: object, index: int) -> Forbidden:
    where = f"`forbidden[{index}]`"
    if not isinstance(entry, dict):
        raise ValueError(f"{source}: expected a mapping for {where}")
    pattern = entry.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"{source}: expected a non-empty `pattern` for {where}")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{source}: {where}'s `pattern` does not compile: {exc}") from exc
    extensions = entry.get("extensions")
    if not isinstance(extensions, list) or not extensions:
        raise ValueError(f"{source}: expected a non-empty `extensions` list for {where}")
    cleaned = []
    for ext in extensions:
        if not isinstance(ext, str) or not ext.strip():
            raise ValueError(f"{source}: expected non-empty extension strings for {where}")
        cleaned.append(ext.strip())
    reason = entry.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"{source}: expected a non-empty `reason` for {where}")
    return Forbidden(pattern=compiled, extensions=frozenset(cleaned), reason=reason.strip())


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
