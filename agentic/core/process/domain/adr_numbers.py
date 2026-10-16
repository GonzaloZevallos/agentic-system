"""The ADR numbering rule (GH-229, ADR-0049). Pure: names and texts in, a decision out.

An ADR's number is decided here and nowhere else: which of a branch's own ADRs collide
with the ones master already holds (`collisions`), what they become and what has to be
rewritten for the references to follow (`renumber`), and whether a directory listing
repeats a number (`duplicate_numbers`). Reading git, writing files and pushing are the
application's; this module sees only names and texts.
"""

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

ADR_DIR = "docs/adr"
_NAME = re.compile(r"^(\d{4})-.+\.md$")

AmbiguityReason = Literal["duplicate_own_number", "cites_master_after_sync", "unclassified_token"]


def number_of(name: str) -> str | None:
    """The 4-digit prefix of an ADR file name, `None` for any other name."""
    found = _NAME.match(name)
    return found.group(1) if found else None


@dataclass(frozen=True)
class OwnAdr:
    """An ADR file a branch's own commit added, and the commit that added it."""

    path: str
    commit: str


@dataclass(frozen=True)
class OwnLine:
    """A line (no line break) a branch's own commit added to `path`."""

    path: str
    commit: str
    text: str


@dataclass(frozen=True)
class Renaming:
    """`moves`: `(old_path, new_path)` pairs. `texts`: the full new text of every path
    written, a renamed file under its new path."""

    moves: tuple[tuple[str, str], ...]
    texts: Mapping[str, str]


@dataclass(frozen=True)
class Ambiguous:
    """The rewrite cannot be decided mechanically; `detail` names the path and the line."""

    reason: AmbiguityReason
    detail: str


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def duplicate_numbers(names: Sequence[str]) -> list[str]:
    """The numbers two or more different ADR file names share, ascending."""
    by_number: dict[str, set[str]] = defaultdict(set)
    for name in names:
        number = number_of(name)
        if number is not None:
            by_number[number].add(name)
    return sorted(number for number, files in by_number.items() if len(files) > 1)


def collisions(master_names: Sequence[str], own: Sequence[OwnAdr]) -> Mapping[str, str] | Ambiguous:
    """`old_path -> new_path` for every own ADR whose number master holds under another
    file name; empty when none does. The new numbers start after the highest one among
    master's names and the branch's own non-colliding ADRs."""
    master = set(master_names)
    master_numbers = {n for n in map(number_of, master) if n is not None}
    adrs: dict[str, str] = {}  # path -> number
    for adr in own:
        number = number_of(_basename(adr.path))
        if number is not None:
            adrs[adr.path] = number
    by_number: dict[str, list[str]] = defaultdict(list)
    for path, number in adrs.items():
        by_number[number].append(path)
    for number, paths in sorted(by_number.items()):
        if len(paths) > 1:
            return Ambiguous(
                "duplicate_own_number", f"{number} is the number of {', '.join(sorted(paths))}"
            )
    colliding = sorted(
        (number, path)
        for path, number in adrs.items()
        if number in master_numbers and _basename(path) not in master
    )
    if not colliding:
        return {}
    taken = master_numbers | {n for p, n in adrs.items() if n not in {c[0] for c in colliding}}
    following = max(int(n) for n in taken) + 1
    moves: dict[str, str] = {}
    for offset, (_, path) in enumerate(colliding):
        directory, _, name = path.rpartition("/")
        renamed = f"{following + offset:04d}{name[4:]}"
        moves[path] = f"{directory}/{renamed}" if directory else renamed
    return moves


def old_numbers(moves: Mapping[str, str]) -> list[str]:
    """The numbers `moves` (old path -> new path) moves away from, ascending: what the
    adapter is asked for, as plain 4-digit strings."""
    found = (number_of(_basename(old)) for old in moves)
    return sorted({number for number in found if number is not None})


_SEPARATOR = r"(?:\s*,\s*(?:(?:and|y)\s+)?|\s+(?:and|y)\s+|\s*/\s*)"
_ITEM = r"\d{4}(?!\d)"
_TOKEN = re.compile(r"(?<!\d)\d{4}(?!\d)")
_DIGITS = re.compile(r"\d{4}")


def _rows(text: str) -> list[tuple[int, str]]:
    """Each line of `text` with the offset it starts at, without its line break."""
    rows: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        rows.append((offset, line.rstrip("\r\n")))
        offset += len(line)
    return rows


def _row_at(rows: Sequence[tuple[int, str]], offset: int) -> tuple[int, str]:
    """The 1-based number and the text of the line holding `offset`."""
    index = bisect_right([start for start, _ in rows], offset)
    return index, rows[index - 1][1]


def renumber(
    moves: Mapping[str, str],
    lines: Sequence[OwnLine],
    trees: Mapping[str, Sequence[str]],
    texts: Mapping[str, tuple[str | None, str]],
) -> Renaming | Ambiguous:
    """Apply `moves` (old path -> new path) to `texts` (path -> its text at master, `None`
    if absent, and at the branch head), in one simultaneous substitution per text.

    The closed grammar: (a) the full old file name, with or without a `docs/adr/` or
    `adr/` prefix, anywhere; (b) `ADR-NNNN` and `ADR NNNN`; (c) a list after one `ADR` or
    `ADRs` head joined by `,`, `and`, `y` or `/`. (b) and (c) are rewritten only on lines
    the branch's own commits added. `trees` maps a commit to the ADR names in its tree.
    A renumbered number left on an own line outside the grammar makes it `Ambiguous`."""
    numbers: dict[str, str] = {}
    names: dict[str, str] = {}
    for old, new in sorted(moves.items()):
        old_number, new_number = number_of(_basename(old)), number_of(_basename(new))
        if old_number is None or new_number is None:
            raise ValueError(f"{old} -> {new} is not a move between ADR names")
        if old_number in numbers:
            return Ambiguous("duplicate_own_number", f"{old_number} is moved twice: {old}")
        numbers[old_number] = new_number
        names[_basename(old)] = _basename(new)
        if old not in texts:
            raise ValueError(f"the moved path {old} has no text")
    own_by_path: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for line in lines:
        own_by_path[line.path][line.text].append(line.commit)

    pattern = re.compile(
        r"(?P<prefix>docs/adr/|adr/)?(?<![\w-])(?P<name>"
        + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        + r")(?![\w-])"
        + r"|(?<!\w)ADRs?[ -](?P<items>"
        + _ITEM
        + "(?:"
        + _SEPARATOR
        + _ITEM
        + ")*)"
    )

    def held(commit: str, number: str) -> bool:
        return any(number_of(n) == number and n not in names for n in trees.get(commit, ()))

    def rewrite(path: str, at_master: str | None, at_head: str) -> str | Ambiguous:
        master_lines = set((at_master or "").splitlines())
        own = {t: c for t, c in own_by_path.get(path, {}).items() if t not in master_lines}
        rows = _rows(at_head)
        covered: list[tuple[int, int]] = []
        problems: list[Ambiguous] = []

        def substitute(found: re.Match[str]) -> str:
            if found.group("name") is not None:
                covered.append(found.span())
                return (found.group("prefix") or "") + names[found.group("name")]
            index, line = _row_at(rows, found.start())
            if line not in own:
                return found.group(0)
            cited = [n for n in _DIGITS.findall(found.group("items")) if n in numbers]
            if any(held(commit, n) for n in cited for commit in own[line]):
                problems.append(Ambiguous("cites_master_after_sync", f"{path}:{index}: {line}"))
            covered.append(found.span())
            return _DIGITS.sub(lambda i: numbers.get(i.group(0), i.group(0)), found.group(0))

        rewritten = pattern.sub(substitute, at_head)
        if problems:
            return problems[0]
        for index, (start, line) in enumerate(rows, 1):
            if line not in own:
                continue
            for token in _TOKEN.finditer(line):
                spot = start + token.start()
                if token.group(0) in numbers and not any(a <= spot < b for a, b in covered):
                    return Ambiguous("unclassified_token", f"{path}:{index}: {line}")
        return rewritten

    written: dict[str, str] = {}
    for path, (at_master, at_head) in sorted(texts.items()):
        result = rewrite(path, at_master, at_head)
        if isinstance(result, Ambiguous):
            return result
        if path in moves or result != at_head:
            written[moves.get(path, path)] = result
    return Renaming(tuple(sorted(moves.items())), written)
