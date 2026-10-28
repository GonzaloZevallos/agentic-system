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
change forces the full form. Absent they read as `{}` and `()`. Present and empty (`{}`,
`[]`, or a key with no value) is a half-written declaration and fails at load, so leave the
key out to declare none; a wrong shape fails too.

**`delivery_checks`**: optional, the fast tier of the two-tier gate. A list of the names of
checks, each also under `checks`, that the delivery gate runs at every subtask delivery; the
rest of `checks` runs once per task, on the integrated head, before the pull request opens.
Absent it reads as `()`: no tiers, every delivery runs all of `checks` as it always has. A
list equal to all of `checks` is valid (tiers are on, and the full tier is the run without
affected forms). Present and empty, a non-list, a blank or non-string entry, a duplicate and
a name that is not under `checks` all fail at load.

**`rerun_checks`**: optional (ADR-0066). A mapping of a check's name, which must also be under
`checks`, to `{command, test_id}`: the "failed tests only" form of the check and the regex whose
single group names a failed test in its output. Only the delivery gate runs the form, and only
after a failure whose output `test_id` found at least one id in; every other rerun is the check's
own command. A form requires `delivery_checks`, so the full run on the integrated head that
tier guarantees always covers what the form skipped. The regex runs inside the system's process,
so it is validated here: it compiles, has exactly one group, is at most 200 characters, has no
quantified group that contains a quantifier and no alternation inside a quantified group (the
shapes that backtrack catastrophically). Absent it reads as `{}`: the gate of today.

**`check_budgets`**: optional (GH-365). A mapping of a check's name, which must also be under
`checks`, to the seconds the check may take: a positive number (an int or a float, never a bool).
The budget is compared only by the once-per-task `full_checks` run on the integrated head, never by
the delivery gate or the merge, so the key requires `delivery_checks`, the tiers that create that
run. It is per check and never per tier: under `parallel_checks` a tier's wall time is not the sum
of its checks. Absent it reads as `{}`: no budget, every verdict as before. Present and empty, or
any shape that is not that, fails at load.

**`performance`**: optional (GH-318, ADR-0059). A mapping of scenario name to the `seed` and
`measure` one-line commands, the `volume` and its `volume_unit` (`rows|days|months|years`)
they must run against, and the `limits` of its metrics (`{limit, unit: ms|queries|kb}`; the
metric's name ends with its unit's suffix). Read only by `agentic perf`: it never joins
`checks`, the presubmit, `affected_checks`, `ci` or the merge gate. Absent it reads as `{}`;
present and empty, or any shape that is not that, fails at load like `design`.

**`merge_inert`**: optional. A non-empty list of path globs the product promises none of its
checks reads: master's gain since a recorded check pass that touches only those paths cannot
change the pass, and the merge reuses it (ADR-0045, amendment). Each entry goes through
`master_gain.glob_refusal` at load, which refuses a glob that would whitelist everything. Absent
it reads as `()`: the merge gate of today. Present and empty, or a wrong shape, fails at load.

**`docs`**: optional. A mapping of `generate`, the one-line command that rewrites every generated
doc artifact, and `paths`, the non-empty list of globs it owns. With it the system adds one check,
`docs`, last in `checks` (and in `delivery_checks` when that is declared): it runs `generate`,
fails when that leaves anything changed or untracked under the owned paths, and puts them back as
HEAD has them on every exit, pass or fail. The name is reserved: a declared check called `docs`
fails at load. Every path is validated, because the check deletes what it matches: not absolute,
no `..` or `.` segment, not starting with `:`, a non-empty literal prefix (the text before the
first `*?[`), and no overlap, in either direction, with `docs/adr`, `.agentic` or `.git`. With
`parallel_checks: true`, no declared command may name an owned path's literal prefix. The command
is built here and nowhere else (`docs_command`). Absent it reads as `None`: every answer as before.

The name is the one part that is not read at the task's base. It says *who* the repository
is, not what held at a commit: read at the base, a task older than the key would resume
into a run with no name, and a rename mid-task would split one task's samples across two
labels. It is also the only key the product cannot derive from anything else — a directory
name changes when someone clones under another name, and a remote changes on a fork —
which is why it is declared and not guessed.
"""

from __future__ import annotations

import importlib
import re
import shlex
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, TypeGuard

from agentic.core.process.domain import master_gain
from agentic.core.process.domain import performance as perf
from agentic.core.process.domain.design import Design
from agentic.core.process.domain.docs import Docs
from agentic.core.process.domain.presubmit import FlakeGate as FlakeGate
from agentic.core.process.domain.presubmit import RerunForm
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
    "delivery_checks",
    "forbidden",
    "design",
    "ci",
    "performance",
    "rerun_checks",
    "check_budgets",
    "merge_inert",
    "docs",
)
# The reserved name of the check a `docs` block adds to `checks`: no other module builds it.
DOCS_CHECK = "docs"
# Every sub-key `docs` may declare, and the roots no owned path may overlap: the written
# ADRs, the system's own files and git's.
DOCS_KEYS = ("generate", "paths")
DOCS_PROTECTED_ROOTS = ("docs/adr", ".agentic", ".git")
GLOB_CHARACTERS = "*?["
# The line the docs check prints before the files a regeneration changed.
DOCS_STALE = "docs stale: commit these"
# The token an affected-only command carries where the task's base sha goes (ADR-0046).
BASE_TOKEN = "{base}"
# What a key that is declared but empty is told, after its source and its key (ADR-0046,
# addendum): the one wording, so the docs and the template can be checked against it.
EMPTY_DECLARATION = "is declared empty; leave the key out to declare none"
# Every sub-key `design` may declare (ADR-0040): the same "unknown is a typo" rule,
# one level down.
DESIGN_KEYS = ("prototype", "entry", "sample_app", "compare")
# The name is a label value in Prometheus and a variable in the dashboard: a slug keeps
# one product from arriving under two spellings and keeps the panels' regexes honest.
# Every sub-key one `performance` scenario declares, and the two of each limit (GH-318).
PERFORMANCE_KEYS = ("seed", "measure", "volume", "volume_unit", "limits")
LIMIT_KEYS = ("limit", "unit")
VOLUME_UNITS = ("rows", "days", "months", "years")
# Every sub-key one `rerun_checks` entry declares, and the longest `test_id` it accepts (ADR-0066).
RERUN_KEYS = ("command", "test_id")
TEST_ID_MAX_LENGTH = 200
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
    found = _commands(_declared(data, source), source, "checks")
    block = docs(data, source)
    if block is None:
        return found
    if DOCS_CHECK in found:
        raise ValueError(
            f"{source}: a check named `{DOCS_CHECK}` is declared under `checks`, but with a "
            f"`docs` block `{DOCS_CHECK}` is reserved for the check the system builds from it"
        )
    return {**found, DOCS_CHECK: docs_command(block)}


def docs(data: object, source: object) -> Docs | None:
    """The optional `docs` block: the one-line `generate` command that rewrites every
    generated doc artifact and the `paths` it owns, validated at load.

    Absent is `None`, a product that declares no generated docs. Present, it is a mapping of
    exactly `generate` and `paths`; every owned path goes through `_path_refusal`, because the
    docs check removes and restores what it matches, and `parallel_checks` is refused when a
    declared command reads an owned path, which the restore could race with."""
    declared = _declared(data, source)
    if "docs" not in declared:
        return None
    found = _declared_nonempty(declared, source, "docs")
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `docs`, a mapping of {DOCS_KEYS}")
    if unknown := [key for key in found if key not in DOCS_KEYS]:
        raise ValueError(
            f"{source}: unknown key(s) {', '.join(sorted(map(str, unknown)))} under `docs`; "
            f"expected {DOCS_KEYS}"
        )
    generate = found.get("generate")
    if not isinstance(generate, str) or not generate.strip() or "\n" in generate.strip():
        raise ValueError(
            f"{source}: expected `generate` under `docs`, a non-blank one-line command"
        )
    entries = found.get("paths")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{source}: expected `paths` under `docs`, a non-empty list of globs")
    if not all(isinstance(entry, str) and entry.strip() for entry in entries):
        raise ValueError(f"{source}: expected `paths` under `docs` to hold non-blank strings")
    paths = tuple(entry.strip() for entry in entries)
    for path in paths:
        if reason := _path_refusal(path):
            raise ValueError(f"{source}: {path!r} under `docs` `paths` is refused: {reason}")
    if parallel_checks(data, source):
        _refuse_readers_of(paths, declared, source)
    return Docs(generate=generate.strip(), paths=paths)


def docs_command(block: Docs) -> str:
    """The shell command of the reserved `docs` check, the only place that builds it (POSIX
    `sh`, every argument quoted, the owned paths as `:(glob)` pathspecs).

    It runs `generate` in a subshell, reads `git status --porcelain` over the owned paths,
    and then puts those paths back as HEAD has them, on every exit: unstaged, tracked files
    restored one pathspec at a time (a pathspec that matches no tracked file yet is not an
    error), untracked files removed. It exits 1 when `generate` failed or anything under the
    owned paths changed or appeared, printing `DOCS_STALE` and the porcelain lines."""
    specs = " ".join(shlex.quote(f":(glob){path}") for path in block.paths)
    restores = "\n".join(
        f"git checkout -q -- {shlex.quote(f':(glob){path}')} 2>/dev/null" for path in block.paths
    )
    return f"""(
{block.generate}
)
generated=$?
stale=$(git status --porcelain --untracked-files=all -- {specs})
inspected=$?
git reset -q HEAD -- {specs} 2>/dev/null
{restores}
git clean -fdq -- {specs}
if [ "$generated" -ne 0 ]; then echo "docs generate exited $generated"; fi
if [ "$inspected" -ne 0 ]; then echo "docs: git status failed"; fi
if [ -n "$stale" ]; then echo {shlex.quote(DOCS_STALE)}; echo "$stale"; fi
if [ "$generated" -ne 0 ] || [ "$inspected" -ne 0 ] || [ -n "$stale" ]; then exit 1; fi
exit 0
"""


def _literal_prefix(path: str) -> str:
    """The text of an owned path before its first glob character."""
    for position, character in enumerate(path):
        if character in GLOB_CHARACTERS:
            return path[:position]
    return path


def _path_refusal(path: str) -> str | None:
    """Why an owned path may not be one, or None: the docs check deletes and restores what
    it matches, so an entry that could reach outside the docs, or the written ADRs, the
    system's files or git's, is refused. Judged on the literal prefix, both directions."""
    segments = path.split("/")
    if path.startswith("/"):
        return "it is absolute"
    if ".." in segments:
        return "it has a `..` segment"
    if "." in segments:
        return "it has a `.` segment; write the path without it"
    if path.startswith(":"):
        return "it starts with `:`, a pathspec magic of git's"
    prefix = _literal_prefix(path)
    if not prefix:
        return "it has an empty literal prefix: it would match the whole repository"
    for root in DOCS_PROTECTED_ROOTS:
        if prefix.startswith(root) or root.startswith(prefix):
            return f"its literal prefix {prefix!r} overlaps `{root}`, which is not generated"
    return None


def _refuse_readers_of(paths: tuple[str, ...], declared: dict[str, Any], source: object) -> None:
    """Under `parallel_checks`, no declared command may name an owned path: the docs check
    restores those paths while the others run. Reads the raw declaration, tolerating any
    shape, because the malformed ones are the business of their own loaders."""
    rerun = declared.get("rerun_checks")
    forms = {
        name: body.get("command")
        for name, body in (rerun.items() if isinstance(rerun, dict) else ())
        if isinstance(body, dict)
    }
    for key, group in (
        ("checks", declared.get("checks")),
        ("affected_checks", declared.get("affected_checks")),
        ("rerun_checks", forms),
    ):
        for name, command in group.items() if isinstance(group, dict) else ():
            if not isinstance(command, str):
                continue
            for path in paths:
                if (prefix := _literal_prefix(path)) in command:
                    raise ValueError(
                        f"{source}: {name!r} under `{key}` reads the owned path {path!r} "
                        f"(it contains {prefix!r}), and with `parallel_checks` the `docs` check "
                        "restores that path while it runs"
                    )


def _known_checks(data: object, source: object) -> dict[str, str]:
    """Every check the product has, the declared ones and the reserved `docs`: the mapping
    each name validator reads."""
    return checks(data, source)


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


def merge_inert(data: object, source: object) -> tuple[str, ...]:
    """The globs of the paths master may gain without a recorded check pass going stale, in
    file order. Absent is `()`. Present, it is a non-empty list of non-blank strings, none
    refused by `master_gain.glob_refusal`: a glob that quietly whitelisted everything would
    skip checks the product believes run, so every refusal fails at load with its reason."""
    declared = _declared(data, source)
    if "merge_inert" not in declared:
        return ()
    found = _declared_nonempty(declared, source, "merge_inert")
    if not isinstance(found, list) or not all(
        isinstance(glob, str) and glob.strip() for glob in found
    ):
        raise ValueError(f"{source}: expected `merge_inert` to be a list of non-blank glob strings")
    globs = tuple(glob.strip() for glob in found)
    for glob in globs:
        if reason := master_gain.glob_refusal(glob):
            raise ValueError(f"{source}: {glob!r} under `merge_inert` is refused: {reason}")
    return globs


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
    _declared_nonempty(declared, source, "affected_checks")
    found = _commands(declared, source, "affected_checks")
    known = _known_checks(data, source)
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
    order. Absent is `()`. Present and empty (`[]`, or a key with no value) is a
    half-written declaration and fails here, like `ci`, `forbidden` and `setup`: leave the
    key out to declare none. Anything that is not a list of non-blank strings fails too."""
    declared = _declared(data, source)
    if "affected_fallback" not in declared:
        return ()
    found = _declared_nonempty(declared, source, "affected_fallback")
    if not isinstance(found, list) or not all(
        isinstance(glob, str) and glob.strip() for glob in found
    ):
        raise ValueError(
            f"{source}: expected `affected_fallback` to be a list of non-blank glob strings"
        )
    return tuple(glob.strip() for glob in found)


def delivery_checks(data: object, source: object) -> tuple[str, ...]:
    """The names of the checks the delivery gate runs, the fast tier, in the file's order.

    Absent is `()`: no tiers, the gate of today. Present, it is a non-empty list of distinct
    non-blank names, every one a check declared under `checks`: a name that is not one would
    never run, and the product would believe its gate did. A list equal to all of `checks`
    is valid. Every shape that is not that fails at load, naming the key."""
    declared = _declared(data, source)
    if "delivery_checks" not in declared:
        return ()
    found = _declared_nonempty(declared, source, "delivery_checks")
    if not isinstance(found, list):
        raise ValueError(f"{source}: expected `delivery_checks`, a list of check names")
    names: list[str] = []
    for item in found:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{source}: expected non-blank check names under `delivery_checks`")
        names.append(item.strip())
    known = _known_checks(data, source)
    for position, name in enumerate(names):
        if name in names[:position]:
            raise ValueError(f"{source}: {name!r} is declared twice under `delivery_checks`")
        if name not in known:
            raise ValueError(
                f"{source}: {name!r} under `delivery_checks` is not a check declared under "
                f"`checks`; expected one of {', '.join(known)}"
            )
    if docs(data, source) is not None and DOCS_CHECK not in names:
        names.append(DOCS_CHECK)
    return tuple(names)


def rerun_checks(data: object, source: object) -> dict[str, RerunForm]:
    """Check name -> its failed-tests-only form, in the file's order (ADR-0066).

    Empty when the product declares none. Present, every entry is `{command, test_id}`, its
    check is declared under `checks`, the product declares `delivery_checks`, and `test_id`
    passes `_test_id_refusal`. Every shape that is not that fails at load, naming the key."""
    declared = _declared(data, source)
    if "rerun_checks" not in declared:
        return {}
    found = _declared_nonempty(declared, source, "rerun_checks")
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `rerun_checks`, a mapping of checks to forms")
    if not delivery_checks(data, source):
        raise ValueError(
            f"{source}: `rerun_checks` requires `delivery_checks`: a form skips tests, and "
            "only the full run on the integrated head that tier guarantees covers them"
        )
    known = _known_checks(data, source)
    forms: dict[str, RerunForm] = {}
    for name, body in found.items():
        if name not in known:
            raise ValueError(
                f"{source}: {name!r} under `rerun_checks` is not a check declared under "
                f"`checks`; expected one of {', '.join(known)}"
            )
        forms[name] = _rerun_form(name, body, source)
    return forms


def check_budgets(data: object, source: object) -> dict[str, float]:
    """Check name -> the seconds it may take on the `full_checks` run, in the file's order
    (GH-365).

    Empty when the product declares none. Present, a non-empty mapping whose every name is a
    check declared under `checks` and every figure a positive int or float (never a bool), and
    the product declares `delivery_checks`: the budget is compared only on the once-per-task
    full run that tiers create. Every shape that is not that fails at load, naming the key."""
    declared = _declared(data, source)
    if "check_budgets" not in declared:
        return {}
    found = _declared_nonempty(declared, source, "check_budgets")
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `check_budgets`, a mapping of checks to seconds")
    if not delivery_checks(data, source):
        raise ValueError(
            f"{source}: `check_budgets` requires `delivery_checks`: a budget is compared only "
            "on the once-per-task full run on the integrated head that tiers create"
        )
    known = _known_checks(data, source)
    budgets: dict[str, float] = {}
    for name, seconds in found.items():
        if name not in known:
            raise ValueError(
                f"{source}: {name!r} under `check_budgets` is not a check declared under "
                f"`checks`; expected one of {', '.join(known)}"
            )
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0:
            raise ValueError(
                f"{source}: {name!r} under `check_budgets` expects a positive number of seconds"
            )
        budgets[str(name)] = seconds
    return budgets


def _rerun_form(name: str, body: object, source: object) -> RerunForm:
    where = f"{source}: {name!r} under `rerun_checks`"
    if not isinstance(body, dict):
        raise ValueError(f"{where} is not a mapping of {RERUN_KEYS}")
    if unknown := [key for key in body if key not in RERUN_KEYS]:
        raise ValueError(
            f"{source}: unknown key(s) {', '.join(sorted(map(str, unknown)))} under "
            f"`rerun_checks`; expected {RERUN_KEYS}"
        )
    for key in RERUN_KEYS:
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where}: expected a non-empty `{key}`, which goes with the other")
    pattern = body["test_id"]
    if reason := _test_id_refusal(pattern):
        raise ValueError(f"{where}: `test_id` {reason}")
    return RerunForm(command=body["command"].strip(), test_id=pattern)


# The opcodes of `re._parser` that repeat what they hold, and the one that picks between
# alternatives: the only two shapes `_test_id_refusal` looks for.
_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")


class _Tree(Protocol):
    """The part of `re._parser.SubPattern` the scanner reads: its items, each an opcode and
    its argument. The standard library ships no stub for the private module, so this is the
    shape the walk below is typed against, not `Any`."""

    @property
    def data(self) -> Sequence[tuple[object, object]]: ...


class _Parsed(_Tree, Protocol):
    @property
    def state(self) -> _State: ...


class _State(Protocol):
    @property
    def groups(self) -> int: ...


def _test_id_refusal(pattern: str) -> str | None:
    """Why a product's `test_id` may not run inside the system, or None. Structural, over the
    parse tree of `re._parser` (the standard library's own, so what is checked is what runs):
    a pattern that does not compile; one without exactly one capturing group; one longer than
    `TEST_ID_MAX_LENGTH`; a quantified group whose body contains another quantifier (`(a+)+`)
    or an alternation (`(a|ab)+`), the two shapes that backtrack catastrophically. Plain
    quantifiers (`\\S+`) and alternation outside a quantified group stay legal."""
    if len(pattern) > TEST_ID_MAX_LENGTH:
        return (
            f"is {len(pattern)} characters long; expected at most {TEST_ID_MAX_LENGTH} characters"
        )
    parser = importlib.import_module("re._parser")
    try:
        re.compile(pattern)
        parsed: _Parsed = parser.parse(pattern)
    except (re.error, RecursionError, OverflowError) as exc:
        return f"does not compile: {exc}"
    if parsed.state.groups != 2:
        return (
            f"has {parsed.state.groups - 1} capturing groups; expected exactly one capturing group"
        )
    return _catastrophic(parsed)


def _catastrophic(tree: _Tree) -> str | None:
    for opcode, value in tree.data:
        name = str(opcode)
        children = _parts(value)
        if name in _REPEATS:
            body = _parts(value)[-1]
            if _has(body, _REPEATS):
                return "has a quantified group that contains a quantifier; write it without one"
            if _has(body, ("BRANCH",)):
                return "has an alternation inside a quantified group; write it without one"
        for child in children:
            if (found := _catastrophic(child)) is not None:
                return found
    return None


def _has(tree: _Tree, names: tuple[str, ...]) -> bool:
    for opcode, value in tree.data:
        if str(opcode) in names:
            return True
        if any(_has(part, names) for part in _parts(value)):
            return True
    return False


def _is_tree(value: object) -> TypeGuard[_Tree]:
    """Whether one parsed item is itself a sub-pattern: it holds `data`, its items."""
    return hasattr(value, "data")


def _parts(value: object) -> list[_Tree]:
    """Every sub-pattern one parsed item holds, however the opcode nests it."""
    if _is_tree(value):
        return [value]
    if isinstance(value, tuple | list):
        return [part for item in value for part in _parts(item)]
    return []


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


def performance(data: object, source: object) -> dict[str, perf.Scenario]:
    """Scenario name -> its declaration, in the file's order (GH-318). Empty when the key is
    absent. Every refusal is a `ValueError` naming the source, the scenario and the key, so
    a broken declaration fails at load and not in the middle of a measurement."""
    declared = _declared(data, source)
    if "performance" not in declared:
        return {}
    found = _declared_nonempty(declared, source, "performance")
    if not isinstance(found, dict):
        raise ValueError(f"{source}: expected `performance`, a mapping of scenarios")
    scenarios: dict[str, perf.Scenario] = {}
    for name, body in found.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{source}: expected a non-empty scenario name under `performance`")
        scenarios[name] = _scenario(name, body, source)
    return scenarios


def _scenario(name: str, body: object, source: object) -> perf.Scenario:
    where = f"{source}: scenario {name!r} under `performance`"
    if not isinstance(body, dict):
        raise ValueError(f"{where} is not a mapping")
    if unknown := [key for key in body if key not in PERFORMANCE_KEYS]:
        raise ValueError(
            f"{where} has unknown key(s) {', '.join(sorted(map(str, unknown)))}; "
            f"expected {PERFORMANCE_KEYS}"
        )
    if missing := [key for key in PERFORMANCE_KEYS if key not in body]:
        raise ValueError(f"{where} lacks `{missing[0]}`")
    commands: dict[str, str] = {}
    for key in ("seed", "measure"):
        command = body[key]
        if not isinstance(command, str):
            raise ValueError(f"{where}: expected a command for `{key}`, a one-line string")
        if reason := perf.command_refusal(command):
            raise ValueError(f"{where}: `{key}` is refused: {reason}")
        commands[key] = command.strip()
    volume = _positive(body["volume"])
    if volume is None:
        raise ValueError(f"{where}: expected `volume` to be a positive number")
    unit = body["volume_unit"]
    if unit not in VOLUME_UNITS:
        raise ValueError(f"{where}: expected `volume_unit` to be one of {', '.join(VOLUME_UNITS)}")
    return perf.Scenario(
        name=name,
        seed=commands["seed"],
        measure=commands["measure"],
        volume=volume,
        volume_unit=unit,
        limits=_limits(body["limits"], where),
    )


def _limits(found: object, where: str) -> dict[str, perf.Limit]:
    if not isinstance(found, dict) or not found:
        raise ValueError(f"{where}: expected `limits`, a non-empty mapping of metrics")
    limits: dict[str, perf.Limit] = {}
    for metric, entry in found.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != set(LIMIT_KEYS)
            or (number := _positive(entry["limit"])) is None
            or (unit := _unit(entry["unit"])) is None
        ):
            raise ValueError(
                f"{where}: expected {metric!r} under `limits` to be "
                "{limit: a positive number, unit: ms|queries|kb}"
            )
        suffix = perf.UNIT_SUFFIX[unit]
        if not isinstance(metric, str) or not metric.endswith(suffix) or metric == suffix:
            raise ValueError(
                f"{where}: the metric {metric!r} under `limits` must end with {suffix}, "
                f"the suffix of its unit {unit}"
            )
        limits[metric] = perf.Limit(number, unit)
    return limits


def _unit(value: object) -> perf.Unit | None:
    """The limit unit a stack declared, or None for anything that is not one of the three."""
    if value == "ms":
        return "ms"
    if value == "queries":
        return "queries"
    if value == "kb":
        return "kb"
    return None


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:  # an int too big for a float
        return None
    return number if 0 < number < float("inf") else None


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


def _declared_nonempty(declared: dict[str, Any], source: object, key: str) -> object:
    """`declared[key]`, refusing a value that is there but empty (`None`, `{}`, `[]`)."""
    value = declared[key]
    if value is None or (isinstance(value, dict | list) and not value):
        raise ValueError(f"{source}: `{key}` {EMPTY_DECLARATION}")
    return value


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
