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

AmbiguityReason = Literal[
    "duplicate_own_number", "cites_master_after_sync", "unclassified_token", "range"
]


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
    """The rewrite cannot be decided mechanically; `detail` names the path and the line.
    `moves`: the `(old_path, new_path)` pairs `renumber` was given, sorted, so whoever
    escalates can say what the renumbering would have been; empty when no renaming was
    computed (`collisions` finding a duplicate own number)."""

    reason: AmbiguityReason
    detail: str
    moves: tuple[tuple[str, str], ...] = ()


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


def new_numbers(moves: Mapping[str, str]) -> list[str]:
    """The numbers `moves` (old path -> new path) moves to, in the order of the old paths'
    numbers: what a commit message and the record of a renumbering say."""
    ordered = sorted(moves.items(), key=lambda pair: pair[0])
    found = (number_of(_basename(new)) for _, new in ordered)
    return [number for number in found if number is not None]


_SEPARATOR = r"(?:\s*,\s*(?:(?:and|y)\s+)?|\s+(?:and|y)\s+|\s*/\s*)"
_ITEM = r"\d{4}(?!\d)"
_TOKEN = re.compile(r"(?<!\d)\d{4}(?!\d)")
_DIGITS = re.compile(r"\d{4}")

# What can sit between the two ends of a range of ADR numbers, as a regex alternation: an
# ellipsis (`…`, `...`, `..`), an en or em dash, a hyphen directly before a digit (so the `-`
# of `ADR-0054` and of `0054-slug.md` is never one: no number follows it) and the words
# `to`, `through` and `a`. A closed set: a range written any other way is not seen.
_RANGE_SEPARATOR = r"(?:…|\.\.\.|\.\.|–|—|-(?=\d)|\bto\b|\bthrough\b|\ba\b)"
# Both ends of a range, either with an `ADR-` or `ADR ` prefix, whitespace around the
# separator. Lookahead on the second end, so ends shared by two ranges are both found.
_RANGE = re.compile(
    r"(?<!\d)(?P<first>\d{4})(?!\d)(?=\s*"
    + _RANGE_SEPARATOR
    + r"\s*(?:ADR[- ])?(?P<last>\d{4})(?!\d))"
)


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


@dataclass(frozen=True)
class DecidedRenaming:
    """What `renumber_decided` made of a collision `renumber` calls `Ambiguous`: the
    `renaming` with every rewrite the grammar can decide, and the `spots`, every own line
    it cannot (`unclassified_token`, `range` or `cites_master_after_sync`) as `(path, line,
    text)`: the path as written (the new one for a moved file), the 1-based line, and the
    line as `renaming` leaves it, which is the line a pass that edits the spots finds."""

    renaming: Renaming
    spots: tuple[tuple[str, int, str], ...]


@dataclass(frozen=True)
class _Grammar:
    """The inputs of one rewrite, read once: what `renumber` and `renumber_decided` share."""

    carried: tuple[tuple[str, str], ...]
    numbers: dict[str, str]
    names: dict[str, str]
    pattern: re.Pattern[str]
    own_by_path: dict[str, dict[str, list[str]]]
    trees: Mapping[str, Sequence[str]]


@dataclass
class _Scan:
    """One text's rewrite and what in it is undecided, each as `(line index, line)`, in the
    order `renumber` reports them: ranges, then citations of master's file, then the rest."""

    text: str
    ranges: list[tuple[int, str]]
    cites: list[tuple[int, str]]
    unclassified: list[tuple[int, str]]


def _grammar(
    moves: Mapping[str, str],
    lines: Sequence[OwnLine],
    trees: Mapping[str, Sequence[str]],
    texts: Mapping[str, tuple[str | None, str]],
) -> _Grammar | Ambiguous:
    carried = tuple(sorted(moves.items()))
    numbers: dict[str, str] = {}
    names: dict[str, str] = {}
    for old, new in sorted(moves.items()):
        old_number, new_number = number_of(_basename(old)), number_of(_basename(new))
        if old_number is None or new_number is None:
            raise ValueError(f"{old} -> {new} is not a move between ADR names")
        if old_number in numbers:
            return Ambiguous("duplicate_own_number", f"{old_number} is moved twice: {old}", carried)
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
    return _Grammar(carried, numbers, names, pattern, own_by_path, trees)


def _scan(
    grammar: _Grammar, path: str, at_master: str | None, at_head: str, *, hold: bool
) -> _Scan:
    """Rewrite `at_head` in one simultaneous substitution and collect what is undecided.
    With `hold`, a line holding a range, and an item list that cites master's file, stay as
    they were: the lines whoever edits the spots finds untouched."""
    numbers, names = grammar.numbers, grammar.names
    master_lines = set((at_master or "").splitlines())
    own = {t: c for t, c in grammar.own_by_path.get(path, {}).items() if t not in master_lines}
    rows = _rows(at_head)
    covered: list[tuple[int, int]] = []
    ranges: list[tuple[int, str]] = []
    cites: list[tuple[int, str]] = []

    def held(commit: str, number: str) -> bool:
        return any(number_of(n) == number and n not in names for n in grammar.trees.get(commit, ()))

    for index, (_, line) in enumerate(rows, 1):
        if line not in own:
            continue
        for ends in _RANGE.finditer(line):
            if ends.group("first") in numbers or ends.group("last") in numbers:
                ranges.append((index, line))
                break
    range_lines = {index for index, _ in ranges}

    def substitute(found: re.Match[str]) -> str:
        named = found.group("name") is not None
        index, line = _row_at(rows, found.start()) if hold or not named else (0, "")
        if hold and index in range_lines:
            return found.group(0)
        if named:
            covered.append(found.span())
            return (found.group("prefix") or "") + names[found.group("name")]
        if line not in own:
            return found.group(0)
        cited = [n for n in _DIGITS.findall(found.group("items")) if n in numbers]
        covered.append(found.span())
        if any(held(commit, n) for n in cited for commit in own[line]):
            cites.append((index, line))
            if hold:
                return found.group(0)
        return _DIGITS.sub(lambda i: numbers.get(i.group(0), i.group(0)), found.group(0))

    rewritten = grammar.pattern.sub(substitute, at_head)
    unclassified: list[tuple[int, str]] = []
    for index, (start, line) in enumerate(rows, 1):
        if line not in own:
            continue
        for token in _TOKEN.finditer(line):
            spot = start + token.start()
            if token.group(0) in numbers and not any(a <= spot < b for a, b in covered):
                unclassified.append((index, line))
                break
    return _Scan(rewritten, ranges, cites, unclassified)


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
    A renumbered number left on an own line outside the grammar makes it `Ambiguous`, and so
    does one that is the end of a range (`ADR-0054…ADR-0060`): moving one end silently would
    change what the range names. Every `Ambiguous` carries `moves`."""
    grammar = _grammar(moves, lines, trees, texts)
    if isinstance(grammar, Ambiguous):
        return grammar
    written: dict[str, str] = {}
    for path, (at_master, at_head) in sorted(texts.items()):
        scan = _scan(grammar, path, at_master, at_head, hold=False)
        undecided: tuple[tuple[AmbiguityReason, list[tuple[int, str]]], ...] = (
            ("range", scan.ranges),
            ("cites_master_after_sync", scan.cites),
            ("unclassified_token", scan.unclassified),
        )
        for reason, found in undecided:
            if found:
                index, line = found[0]
                return Ambiguous(reason, f"{path}:{index}: {line}", grammar.carried)
        if path in moves or scan.text != at_head:
            written[moves.get(path, path)] = scan.text
    return Renaming(grammar.carried, written)


def renumber_decided(
    moves: Mapping[str, str],
    lines: Sequence[OwnLine],
    trees: Mapping[str, Sequence[str]],
    texts: Mapping[str, tuple[str | None, str]],
) -> DecidedRenaming | Ambiguous:
    """`renumber` that does not stop at the first thing it cannot decide (GH-544): the same
    grammar and the same single simultaneous substitution, but every own line left as an
    `unclassified_token`, a `range` or a `cites_master_after_sync` is kept as it was and
    listed in `spots`, sorted by path and line, so an owner's decision can be applied to just
    those lines. A moved number twice is still `Ambiguous("duplicate_own_number")`: no spot
    is there to edit."""
    grammar = _grammar(moves, lines, trees, texts)
    if isinstance(grammar, Ambiguous):
        return grammar
    written: dict[str, str] = {}
    spots: list[tuple[str, int, str]] = []
    for path, (at_master, at_head) in sorted(texts.items()):
        scan = _scan(grammar, path, at_master, at_head, hold=True)
        target = moves.get(path, path)
        if path in moves or scan.text != at_head:
            written[target] = scan.text
        rows = scan.text.splitlines()
        undecided = {
            index for found in (scan.ranges, scan.cites, scan.unclassified) for index, _ in found
        }
        spots.extend((target, index, rows[index - 1]) for index in sorted(undecided))
    return DecidedRenaming(Renaming(grammar.carried, written), tuple(sorted(spots)))
