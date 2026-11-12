"""The dated ADR name (GH-611, the owner's rule): on a product that names its ADRs by date, a
branch's numbered ADR becomes `docs/adr/<yyyymmdd>-<slug>.md` and the references follow.
Pure: names and texts in, a `DatedRenaming` or a `Declined` out.

It is the sibling of `adr_numbers` and reuses its scanner (the line reader, the range and
token patterns), not its NNNN to NNNN grammar: a dated name is never a number, so none of
this module's output is read back by `adr_numbers.number_of`. The rewrite is one
simultaneous substitution per text: the full old name (anywhere), a link whose label is the
number, a bare `docs/adr/NNNN` and a single `ADR-NNNN` / `ADR NNNN` (the last two only on a
line master does not hold). A list or range holding a moved number, and a bare number no
form covers, are not guessed: `Declined("unrewritable")`."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from . import adr_numbers
from .adr_numbers import (
    _BARE_PATH,
    _DIGITS,
    _ITEM,
    _RANGE,
    _SEPARATOR,
    _TOKEN,
    ADR_DIR,
    OwnAdr,
    OwnLine,
    _row_at,
    _rows,
)

DeclineCause = Literal["unrewritable", "slug_taken"]

_FRONT_DATE = re.compile(r"""^date:\s*(['"]?)(\d{4})-(\d{2})-(\d{2})\1\s*$""", re.MULTILINE)
_DATED = re.compile(r"^\d{8}-(?P<slug>.+)\.md$")
_NUMBERED = re.compile(r"^\d{4}-(?P<slug>.+)\.md$")


@dataclass(frozen=True)
class Undated:
    """The collision value of a product that names ADRs by date: the branch's own ADRs that
    still carry a number and that master does not hold under that name, whether or not the
    number collides."""

    own: tuple[OwnAdr, ...]


@dataclass(frozen=True)
class DatedRenaming:
    """`moves`: `(old_path, new_path)` pairs, sorted. `texts`: the full new text of every path
    written, a renamed file under its new path."""

    moves: tuple[tuple[str, str], ...]
    texts: Mapping[str, str]


@dataclass(frozen=True)
class Declined:
    """The rename cannot be made without guessing (`unrewritable`, `detail` names the path and
    the line) or its slug is already an ADR's (`slug_taken`)."""

    cause: DeclineCause
    detail: str


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _front_matter_date(text: str) -> date | None:
    """The `date:` line of the front matter at the top of `text`, when it is a real day."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i, row in enumerate(lines[1:], 1) if row.strip() == "---"), None)
    if end is None:
        return None
    found = _FRONT_DATE.search("\n".join(lines[1:end]))
    if found is None:
        return None
    try:
        return date(int(found.group(2)), int(found.group(3)), int(found.group(4)))
    except ValueError:
        return None


def dated_name(old_path: str, text: str, first_date: date | None) -> str:
    """`docs/adr/<yyyymmdd>-<slug>.md` for the numbered ADR at `old_path` holding `text`: the
    slug is its file name without the `NNNN-`, the date the front matter's when that is a
    real day, else `first_date` (UTC). With neither there is no name: `ValueError`."""
    when = _front_matter_date(text) or first_date
    if when is None:
        raise ValueError(f"{old_path} has no date: line in its front matter and no first date")
    directory, _, name = old_path.rpartition("/")
    renamed = f"{when:%Y%m%d}-{name[5:]}"
    return f"{directory}/{renamed}" if directory else renamed


def _slug(name: str) -> str | None:
    found = _DATED.match(name) or _NUMBERED.match(name)
    return found.group("slug") if found else None


def _slug_clash(
    moved: Mapping[str, str], master_names: Collection[str], kept_names: Collection[str]
) -> Declined | None:
    taken = {m.group("slug") for n in master_names if (m := _DATED.match(n))}
    taken |= {s for n in kept_names if (s := _slug(n))}
    claimed: dict[str, str] = {}
    for old in sorted(moved):
        slug = _slug(_basename(moved[old]))
        if slug is None:
            continue
        if slug in taken or slug in claimed:
            other = claimed.get(slug)
            where = f" and {other}" if other else ""
            return Declined("slug_taken", f"{slug!r} is already an ADR's slug: {old}{where}")
        claimed[slug] = old
    return None


@dataclass
class _Undecided:
    path: str
    line: int
    text: str

    def declined(self) -> Declined:
        return Declined("unrewritable", f"{self.path}:{self.line}: {self.text}")


def rename(
    own: Sequence[OwnAdr],
    first_dates: Mapping[str, date | None],
    texts: Mapping[str, tuple[str | None, str]],
    *,
    master_names: Collection[str],
    kept_names: Collection[str] = (),
    lines: Sequence[OwnLine] = (),
    trees: Mapping[str, Sequence[str]] | None = None,
) -> DatedRenaming | Declined:
    """The dated name of every `own` ADR (`first_dates`: path -> the UTC date of the commit
    that first added it, read only when its front matter has no real `date:`) and `texts`
    (path -> its text at master, `None` if absent, and at the head) rewritten to follow.

    `master_names` and `kept_names` are the ADR file names of master and of the branch's own
    ADRs that stay: a slug they hold makes `Declined("slug_taken")`. A line is the branch's
    when master's text of that path does not hold it. `lines` and `trees` are `adr_numbers`':
    a bare `ADR-NNNN` on a line whose commit already had a file of that number besides the
    moved ones (or, with no record of the commit, when master holds that number) is master's
    and is not guessed. Two own ADRs may share a number; a bare citation of it is then not
    guessed either."""
    moves: dict[str, str] = {}
    for adr in sorted(own, key=lambda a: a.path):
        text = texts.get(adr.path, (None, ""))[1]
        moves[adr.path] = dated_name(adr.path, text, first_dates.get(adr.path))
    if (clash := _slug_clash(moves, master_names, kept_names)) is not None:
        return clash
    moved_names = {_basename(old): _basename(new) for old, new in moves.items()}
    stems: dict[str, list[str]] = defaultdict(list)  # old number -> new stems
    own_number: dict[str, str] = {}  # moved path -> its old number
    for old, new in moves.items():
        number = adr_numbers.number_of(_basename(old))
        if number is None:
            raise ValueError(f"{old} is not a numbered ADR")
        stems[number].append(_basename(new).removesuffix(".md"))
        own_number[old] = number
    held_by_master = {n for name in master_names if (n := adr_numbers.number_of(name))}
    own_by_path: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for entry in lines:
        own_by_path[entry.path][entry.text].append(entry.commit)
    names = "|".join(re.escape(n) for n in sorted(moved_names, key=len, reverse=True))
    pattern = re.compile(
        r"\[(?P<label>\d{4})\]\((?P<ldir>[^)\s]*/)?(?<![\w-])(?P<lname>" + names + r")(?![\w-])\)"
        r"|(?P<prefix>docs/adr/|adr/)?(?<![\w-])(?P<name>" + names + r")(?![\w-])"
        r"|(?P<bprefix>(?:docs/)?adr/)(?P<bnum>\d{4})(?![\w-])"
        r"|(?<!\w)ADRs?[ -](?P<items>" + _ITEM + "(?:" + _SEPARATOR + _ITEM + ")*)"
    )
    written: dict[str, str] = {}
    for path, (at_master, at_head) in sorted(texts.items()):
        master_lines = set((at_master or "").splitlines())
        rows = _rows(at_head)
        undecided: list[_Undecided] = []
        covered: list[tuple[int, int]] = []

        def stem_for(path: str, line: str, number: str) -> str | None:
            """The one stem `number` stands for on `line`, `None` when that is a guess."""
            if own_number.get(path) == number:
                return stems[number][moves_by_old[path]]
            if len(stems[number]) != 1:
                return None
            commits = own_by_path.get(path, {}).get(line)
            if commits:
                if any(
                    adr_numbers.number_of(n) == number and n not in moved_names
                    for commit in commits
                    for n in (trees or {}).get(commit, ())
                ):
                    return None
            elif number in held_by_master:
                return None
            return stems[number][0]

        moves_by_old = {
            old: stems[own_number[old]].index(_basename(new).removesuffix(".md"))
            for old, new in moves.items()
        }

        def substitute(found: re.Match[str]) -> str:
            groups = found.groupdict()
            index, line = _row_at(rows, found.start())
            mine = line not in master_lines
            if groups["lname"] is not None:
                label, target = groups["label"], groups["lname"]
                kept = (groups["ldir"] or "") + moved_names[target]
                if adr_numbers.number_of(target) != label:  # only the target follows
                    covered.append(found.span("lname"))
                    return f"[{label}]({kept})"
                covered.append(found.span())
                return f"[ADR-{moved_names[target].removesuffix('.md')}]({kept})"
            if groups["name"] is not None:
                covered.append(found.span())
                return (groups["prefix"] or "") + moved_names[groups["name"]]
            if not mine:
                return found.group(0)
            if groups["bnum"] is not None:
                number = groups["bnum"]
                if number not in stems:
                    return found.group(0)
                stem = stem_for(path, line, number)
                if stem is None:
                    undecided.append(_Undecided(path, index, line))
                    return found.group(0)
                covered.append(found.span())
                return groups["bprefix"] + stem
            cited = _DIGITS.findall(groups["items"])
            moved = [n for n in cited if n in stems]
            if not moved:
                return found.group(0)
            covered.append(found.span())
            stem = stem_for(path, line, moved[0]) if len(cited) == 1 else None
            if stem is None:
                undecided.append(_Undecided(path, index, line))
                return found.group(0)
            return f"ADR-{stem}"

        rewritten = pattern.sub(substitute, at_head)
        for index, (start, line) in enumerate(rows, 1):
            if line in master_lines:
                continue
            if any(
                ends.group("first") in stems or ends.group("last") in stems
                for ends in _RANGE.finditer(line)
            ):
                undecided.append(_Undecided(path, index, line))
                continue
            for token in _TOKEN.finditer(line):
                spot = start + token.start()
                if token.group(0) in stems and not any(a <= spot < b for a, b in covered):
                    undecided.append(_Undecided(path, index, line))
                    break
            else:
                for bare in _BARE_PATH.finditer(line):
                    spot = start + bare.start()
                    if bare.group("number") in stems and not any(a <= spot < b for a, b in covered):
                        undecided.append(_Undecided(path, index, line))
                        break
        if undecided:
            return min(undecided, key=lambda found: (found.line, found.text)).declined()
        if path in moves or rewritten != at_head:
            written[moves.get(path, path)] = rewritten
    return DatedRenaming(tuple(sorted(moves.items())), written)


__all__ = [
    "ADR_DIR",
    "DatedRenaming",
    "Declined",
    "Undated",
    "dated_name",
    "rename",
]
