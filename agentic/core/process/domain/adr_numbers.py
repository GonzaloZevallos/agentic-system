"""The ADR numbering rule (GH-229, ADR-0049). Pure: names and texts in, a decision out.

An ADR's number is decided here and nowhere else: which of a branch's own ADRs collide
with the ones master already holds (`collisions`), what they become and what has to be
rewritten for the references to follow (`renumber`), and whether a directory listing
repeats a number (`duplicate_numbers`). Reading git, writing files and pushing are the
application's; this module sees only names and texts.
"""

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
# GH-579: a bare reference to an ADR directory entry, `docs/adr/NNNN` or `adr/NNNN`, whatever
# follows the number (a slug that is not the moved name included).
_BARE_PATH = re.compile(r"(?<![\w-])(?:docs/)?adr/(?P<number>\d{4})(?!\d)")
_HEADING = re.compile(r"#{1,6}\s")

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
    line as `renaming` leaves it, which is the line a pass that edits the spots finds.

    `bound` (GH-576), in the same shape and order: every line (a spot excluded) where rule (a)
    or (d) rewrote a name or a link label, and the first Markdown heading of each moved ADR.
    A pass may move these lines inside their file (a sorted index) but never change their
    text; `outside_bounds` is what checks it."""

    renaming: Renaming
    spots: tuple[tuple[str, int, str], ...]
    bound: tuple[tuple[str, int, str], ...] = ()


@dataclass(frozen=True)
class _Grammar:
    """The inputs of one rewrite, read once: what `renumber` and `renumber_decided` share."""

    carried: tuple[tuple[str, str], ...]
    numbers: dict[str, str]
    names: dict[str, str]
    pattern: re.Pattern[str]
    own_by_path: dict[str, dict[str, list[str]]]
    trees: Mapping[str, Sequence[str]]
    link_labels: bool = False
    bases: Mapping[str, str | None] = field(default_factory=dict)


@dataclass
class _Scan:
    """One text's rewrite and what in it is undecided, each as `(line index, line)`, in the
    order `renumber` reports them: ranges, then citations of master's file, then the rest."""

    text: str
    ranges: list[tuple[int, str]]
    cites: list[tuple[int, str]]
    unclassified: list[tuple[int, str]]
    rewrote: set[int] = field(default_factory=set)  # lines (a) or (d) rewrote, `hold` only


def _grammar(
    moves: Mapping[str, str],
    lines: Sequence[OwnLine],
    trees: Mapping[str, Sequence[str]],
    texts: Mapping[str, tuple[str | None, str]],
    *,
    link_labels: bool = False,
    bases: Mapping[str, str | None] | None = None,
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
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # Rule (d): a Markdown link `[NNNN](<dir/>NNNN-name.md)`, first so it wins at its `[`.
    link = (
        r"\[(?P<label>\d{4})\]\((?P<ldir>[^)\s]*/)?(?<![\w-])(?P<lname>"
        + alternation
        + r")(?![\w-])\)|"
        if link_labels
        else ""
    )
    pattern = re.compile(
        link
        + r"(?P<prefix>docs/adr/|adr/)?(?<![\w-])(?P<name>"
        + alternation
        + r")(?![\w-])"
        + r"|(?<!\w)ADRs?[ -](?P<items>"
        + _ITEM
        + "(?:"
        + _SEPARATOR
        + _ITEM
        + ")*)"
    )
    return _Grammar(
        carried, numbers, names, pattern, own_by_path, trees, link_labels, bases or {}
    )


def _scan(
    grammar: _Grammar, path: str, at_master: str | None, at_head: str, *, hold: bool
) -> _Scan:
    """Rewrite `at_head` in one simultaneous substitution and collect what is undecided.
    With `hold`, a line holding a range, and an item list that cites master's file, stay as
    they were: the lines whoever edits the spots finds untouched.

    GH-579: in a path the reads marked as a candidate (`grammar.bases`), a line no own commit
    added and master does not hold (at master or at the merge base) is classified on the
    grammar's ADR forms only: an `ADR-NNNN` / `ADR NNNN` / `ADRs` list or a bare
    `docs/adr/NNNN` of a moved number makes it an `unclassified_token`. Never rewritten, and a
    bare four-digit token there is never one."""
    numbers, names = grammar.numbers, grammar.names
    master_lines = set((at_master or "").splitlines())
    candidate = path in grammar.bases
    foreign_skip = master_lines | set((grammar.bases.get(path) or "").splitlines())
    foreign: list[tuple[int, str]] = []
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

    rewrote: set[int] = set()

    def substitute(found: re.Match[str]) -> str:
        groups = found.groupdict()
        linked = groups.get("lname") is not None
        named = linked or found.group("name") is not None
        index, line = _row_at(rows, found.start()) if hold or not named else (0, "")
        if hold and index in range_lines:
            return found.group(0)
        if linked:
            label, target = groups["label"], groups["lname"]
            kept = (groups["ldir"] or "") + names[target]
            if number_of(target) != label:  # the label is not the file's: only the target follows
                covered.append(found.span("lname"))
                rewrote.add(index)
                return f"[{label}]({kept})"
            covered.append(found.span())
            rewrote.add(index)
            return f"[{numbers[label]}]({kept})"
        if named:
            covered.append(found.span())
            rewrote.add(index)
            return (found.group("prefix") or "") + names[found.group("name")]
        if line not in own:
            cited_there = _DIGITS.findall(found.group("items"))
            if candidate and line not in foreign_skip and any(n in numbers for n in cited_there):
                foreign.append((index, line))
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
    if candidate:
        for index, (start, line) in enumerate(rows, 1):
            if line in own or line in foreign_skip:
                continue
            for bare in _BARE_PATH.finditer(line):
                spot = start + bare.start()
                if bare.group("number") in numbers and not any(a <= spot < b for a, b in covered):
                    foreign.append((index, line))
                    break
    for index, (start, line) in enumerate(rows, 1):
        if line not in own:
            continue
        for token in _TOKEN.finditer(line):
            spot = start + token.start()
            if token.group(0) in numbers and not any(a <= spot < b for a, b in covered):
                unclassified.append((index, line))
                break
    unclassified = sorted(set(unclassified) | set(foreign))
    return _Scan(rewritten, ranges, cites, unclassified, rewrote if hold else set())


def renumber(
    moves: Mapping[str, str],
    lines: Sequence[OwnLine],
    trees: Mapping[str, Sequence[str]],
    texts: Mapping[str, tuple[str | None, str]],
    *,
    bases: Mapping[str, str | None] | None = None,
) -> Renaming | Ambiguous:
    """Apply `moves` (old path -> new path) to `texts` (path -> its text at master, `None`
    if absent, and at the branch head), in one simultaneous substitution per text.

    The closed grammar: (a) the full old file name, with or without a `docs/adr/` or
    `adr/` prefix, anywhere; (b) `ADR-NNNN` and `ADR NNNN`; (c) a list after one `ADR` or
    `ADRs` head joined by `,`, `and`, `y` or `/`. (b) and (c) are rewritten only on lines
    the branch's own commits added. `trees` maps a commit to the ADR names in its tree.
    A renumbered number left on an own line outside the grammar makes it `Ambiguous`, and so
    does one that is the end of a range (`ADR-0054…ADR-0060`): moving one end silently would
    change what the range names. Every `Ambiguous` carries `moves`.

    `bases` (GH-579) maps the paths the reads marked as candidates to their text at the merge
    base of master and the branch (`None` if absent): on those paths a line no own commit added
    and neither master nor the merge base holds, carrying an ADR form of a moved number, is an
    `unclassified_token` too (a merge resolution's reference). Without it nothing changes."""
    grammar = _grammar(moves, lines, trees, texts, bases=bases)
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
    *,
    bases: Mapping[str, str | None] | None = None,
) -> DecidedRenaming | Ambiguous:
    """`renumber` that does not stop at the first thing it cannot decide (GH-544): the same
    grammar and the same single simultaneous substitution, but every own line left as an
    `unclassified_token`, a `range` or a `cites_master_after_sync` is kept as it was and
    listed in `spots`, sorted by path and line, so an owner's decision can be applied to just
    those lines. A moved number twice is still `Ambiguous("duplicate_own_number")`: no spot
    is there to edit. `bases` is `renumber`'s (GH-579)."""
    grammar = _grammar(moves, lines, trees, texts, link_labels=True, bases=bases)
    if isinstance(grammar, Ambiguous):
        return grammar
    written: dict[str, str] = {}
    spots: list[tuple[str, int, str]] = []
    bound: list[tuple[str, int, str]] = []
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
        fixed = set(scan.rewrote)
        if path in moves:
            title = next((i for i, row in enumerate(rows, 1) if _HEADING.match(row)), None)
            if title is not None:
                fixed.add(title)
        bound.extend((target, index, rows[index - 1]) for index in sorted(fixed - undecided))
    return DecidedRenaming(
        Renaming(grammar.carried, written), tuple(sorted(spots)), tuple(sorted(bound))
    )


def outside_bounds(
    before: Mapping[str, str], after: Mapping[str, str], found: DecidedRenaming
) -> tuple[tuple[str, int], ...]:
    """The `(path, line)` pairs where a pass went beyond what `found` lets it change, sorted;
    empty means within bounds (GH-576). `before` and `after` are the texts of the paths the
    pass changed, at the mechanical commit and at the pass's head. Compared as multisets of
    lines per path: a removed line must be the text of a spot of that path, no more lines are
    added than removed, a `bound` line is still there as often as it was (it may have moved),
    and every other line is untouched. An added or changed line is named by its 1-based line
    in `after`, a removed one by its line in `before`; a changed path with neither a spot nor
    a `bound` line is `(path, 0)`."""
    spots: dict[str, Counter[str]] = defaultdict(Counter)
    for path, _, text in found.spots:
        spots[path][text] += 1
    bound: dict[str, Counter[str]] = defaultdict(Counter)
    for path, _, text in found.bound:
        bound[path][text] += 1
    outside: set[tuple[str, int]] = set()
    for path, new_text in after.items():
        old_text = before.get(path, "")
        if old_text == new_text:
            continue
        if path not in spots and path not in bound:
            outside.add((path, 0))
            continue
        old, new = old_text.splitlines(), new_text.splitlines()
        removed, added = Counter(old) - Counter(new), Counter(new) - Counter(old)
        allowed = Counter({text: min(count, spots[path][text]) for text, count in removed.items()})
        # A bound line that is no longer there as often as it was counts as removed.
        for text, count in bound[path].items():
            if Counter(new)[text] < count:
                removed[text] = max(removed[text], count - Counter(new)[text])
                allowed[text] = 0
        removed_at = _positions(old, removed)
        outside.update(
            (path, number)
            for text, count in removed.items()
            for number in removed_at[text][: count - allowed[text]]
        )
        free = _unpaired(
            _positions(new, added),
            [number for text, count in allowed.items() for number in removed_at[text][:count]],
        )
        outside.update((path, number) for number in free)
    return tuple(sorted(outside))


def _positions(lines: Sequence[str], wanted: Mapping[str, int]) -> dict[str, list[int]]:
    """For each text in `wanted`, the 1-based numbers of the lines holding it, ascending."""
    found: dict[str, list[int]] = defaultdict(list)
    for number, line in enumerate(lines, 1):
        if line in wanted:
            found[line].append(number)
    return found


def _unpaired(added: Mapping[str, list[int]], replaced: Sequence[int]) -> list[int]:
    """The added lines left once each replaced line (a spot edited) took the added line
    nearest to it, the earlier one on a tie: an edit keeps its place, so that is the one
    the spot became."""
    left = sorted(number for numbers in added.values() for number in numbers)
    for at in sorted(replaced):
        if left:
            left.remove(min(left, key=lambda number: (abs(number - at), number)))
    return left
