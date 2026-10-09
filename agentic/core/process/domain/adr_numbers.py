"""Renumeración de ADR (GH-229, ADR-0048): the pure rules of moving the ADRs a branch added
off the numbers upstream master took first.

Parallel runs choose "the next" ADR number from their own base, so two of them can pick
the same one and both pass on their own. `agentic merge` reads the paths of the ADR
directory at the merge-base, at the pull request head and at the upstream master M, asks
`collision` whether a number the branch added was taken since, and applies
`renumber_plan` plus `rewrite_line`. Nothing here reads git or a file: the paths and the
lines come in, the new paths and the new lines go out. This module and `rules` (which
decides *when*) are the only places an ADR number is parsed or the next one is computed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

ADR_DIR = "docs/adr"

# Directly under the directory: a nested `docs/adr/sub/0001-x.md` is not an ADR.
_ADR_FILE = re.compile(rf"^{re.escape(ADR_DIR)}/(\d{{4}})-([^/]+)\.md$")

_TESTS_SEGMENT = "tests"

# A frontmatter line that carries the file's own number.
_NUMBER_KEY = re.compile(r"^\s*(?:number|id|adr|numero|número)\s*:", re.IGNORECASE)


def adr_number(path: str) -> int | None:
    """The number of an ADR path, `None` for any path that is not `docs/adr/NNNN-<slug>.md`."""
    match = _ADR_FILE.match(path)
    return int(match.group(1)) if match else None


def adr_slug(path: str) -> str | None:
    """The slug of an ADR path (what follows `NNNN-`, without `.md`), `None` otherwise."""
    match = _ADR_FILE.match(path)
    return match.group(2) if match else None


def _numbers(paths: Iterable[str]) -> set[int]:
    return {n for n in map(adr_number, paths) if n is not None}


def _order(path: str) -> tuple[int, str]:
    return (adr_number(path) or 0, path)


def own_adrs(mb_paths: Collection[str], head_paths: Collection[str]) -> tuple[str, ...]:
    """The ADR paths the branch added itself: present at head, absent from the merge-base
    tree. An ADR master merged into the branch is in the merge-base, so it is never one of
    these. In number order."""
    held = set(mb_paths)
    return tuple(
        sorted(
            (p for p in head_paths if adr_number(p) is not None and p not in held),
            key=_order,
        )
    )


@dataclass(frozen=True)
class Collision:
    """What `collision` found. `colliding`: the branch's own ADRs whose number master took.
    `duplicate`: two files at head share the number of an own ADR, which a renumber does not
    settle. Falsy when neither."""

    colliding: tuple[str, ...] = ()
    duplicate: bool = False

    def __bool__(self) -> bool:
        return bool(self.colliding) or self.duplicate


def collision(
    own: Sequence[str],
    head_paths: Collection[str],
    mb_paths: Collection[str],
    master_paths: Collection[str],
) -> Collision:
    """The own ADRs whose number is held at master but was not held at the merge-base, or a
    `duplicate` when an own ADR shares its number with another file at head (the branch
    clashes with itself, and no renumber would be the author's choice). A duplicate wins
    over a collision. A branch with no own ADR has neither: a duplicate that was already
    there, in files the branch did not add, is not this branch's to be refused for."""
    at_head: dict[int, int] = {}
    for path in head_paths:
        number = adr_number(path)
        if number is not None:
            at_head[number] = at_head.get(number, 0) + 1
    if any(at_head.get(number, 0) > 1 for number in _numbers(own)):
        return Collision(duplicate=True)
    taken = _numbers(master_paths) - _numbers(mb_paths)
    return Collision(colliding=tuple(p for p in own if adr_number(p) in taken))


def renumber_plan(
    own_colliding: Sequence[str],
    own_all: Sequence[str],
    master_paths: Collection[str],
) -> Mapping[str, str]:
    """Old path -> new path for each colliding ADR. The first new number is one past the
    largest of the numbers at master and the numbers of the branch's other own ADRs (those
    that stay); the rest follow upward in the branch's own number order. Slugs are kept and
    numbers stay four digits."""
    staying = [p for p in own_all if p not in set(own_colliding)]
    floor = max(_numbers(master_paths) | _numbers(staying), default=0)
    plan: dict[str, str] = {}
    for offset, old in enumerate(sorted(own_colliding, key=_order), start=1):
        plan[old] = f"{ADR_DIR}/{floor + offset:04d}-{adr_slug(old)}.md"
    return plan


def _under_tests(path: str) -> bool:
    return _TESTS_SEGMENT in path.split("/")[:-1]


def _old_path(path: str, plan: Mapping[str, str]) -> str | None:
    """The key of `plan` that `path` is, whether named by its old or its new path."""
    if path in plan:
        return path
    return next((old for old, new in plan.items() if new == path), None)


def _names_slug(line: str, slug: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(slug)}(?![A-Za-z0-9])", line) is not None


def _swap(number: int) -> Callable[[re.Match[str]], str]:
    """The replacement that writes `number` in the form (padded or not) it replaces."""

    def replacement(match: re.Match[str]) -> str:
        return f"{number:04d}" if len(match.group(0)) == 4 else str(number)

    return replacement


def rewrite_line(
    path: str,
    line: str,
    plan: Mapping[str, str],
    *,
    added_whole: bool,
    is_renamed_file: bool,
) -> str:
    """`line`, added by the branch in `path`, with the old numbers of `plan` replaced. Three
    shapes, all on word boundaries (`ADR-00480` and `10048` never match):

    1. in the renamed file itself (`is_renamed_file`, `path` its old or new path): its own
       number in a heading and in a frontmatter number key;
    2. `NNNN-<exact slug>.md` anywhere on the line;
    3. the bare token `ADR-NNNN`, only when the line also names that ADR's slug, or the
       file was added whole.

    A path with a `tests/` segment comes back unchanged, whatever the shape. What no shape
    reaches is left as it is: `unrewritten` reports it."""
    if not plan or _under_tests(path):
        return line
    own_old = _old_path(path, plan) if is_renamed_file else None
    original = line
    for old, new in plan.items():
        old_n, new_n = adr_number(old), adr_number(new)
        slug = adr_slug(old)
        if old_n is None or new_n is None or slug is None:
            continue
        old_s, new_s = f"{old_n:04d}", f"{new_n:04d}"
        line = re.sub(rf"(?<![\w-]){old_s}-{re.escape(slug)}\.md(?!\w)", f"{new_s}-{slug}.md", line)
        if added_whole or _names_slug(original, slug):
            line = re.sub(rf"(?<!\w)ADR-{old_s}(?!\w)", f"ADR-{new_s}", line)
        if old == own_old and (line.lstrip().startswith("#") or _NUMBER_KEY.match(line)):
            # A heading carries the padded number only; a number key may be unpadded too.
            forms = f"{old_s}|{old_n}" if _NUMBER_KEY.match(line) else old_s
            line = re.sub(rf"(?<![\w-])(?:{forms})(?![\w-])", _swap(new_n), line)
    return line


def unrewritten(lines: Iterable[tuple[str, int, str]], plan: Mapping[str, str]) -> list[str]:
    """`path:line` for each `(path, line number, text after rewriting)` that still names an
    old number of `plan` as a whole token, in the order given: what a human has to read
    because no shape could safely change it (a bare token with no slug, anything under
    `tests/`, a reference to master's ADR of that number)."""
    numbers = [n for n in (adr_number(old) for old in plan) if n is not None]
    if not numbers:
        return []
    pattern = re.compile("|".join(rf"(?<!\w){n:04d}(?!\w)" for n in numbers))
    return [f"{path}:{number}" for path, number, text in lines if pattern.search(text)]
