"""Lexical similarity and clustering of playbook lessons.

Pure and standard-library only. Callers pass one role's lessons at a time; the
module has no notion of role.

``top_k`` is the near-duplicate finder. ``cluster`` groups conservatively
(complete linkage), so a lesson missing from a cluster is not proof that it is
unique.

Scores are exact: a score is an ``(inter, union)`` pair of integers, compared by
cross-multiplication, and no float is used anywhere.

Cost: word sets are computed once per item; pairwise scores are O(n^2) set
operations and the merge loop is O(n^3) integer comparisons in the worst case
(O(n^2) memory). It runs offline, in reflect only.
"""

import re
from collections.abc import Iterable
from fractions import Fraction
from typing import Protocol

STOPWORDS: frozenset[str] = frozenset(
    {
        "when", "you", "your", "a", "an", "the", "and", "or", "of", "in", "to",
        "it", "its", "them", "for", "do", "s", "that", "any", "under", "with",
        "on", "is", "be", "as", "by", "this", "before",
    }
)  # fmt: skip
"""Topic-free function words. Negations (not, no, never, nor, cannot) stay."""

CLUSTER_MIN = Fraction(35, 100)

_NON_WORD = re.compile(r"[^a-z0-9]+")

_Score = tuple[int, int]  # (intersection size, union size)


class Lesson(Protocol):
    """Anything with an id and a lesson wording, such as ``playbook.Item``."""

    @property
    def id(self) -> str: ...

    @property
    def lesson(self) -> str: ...


def words(text: str) -> frozenset[str]:
    """The word set of a lesson: casefolded, split on non-alphanumerics, no stopwords."""
    tokens = _NON_WORD.split(text.casefold())
    return frozenset(t for t in tokens if t and t not in STOPWORDS)


def _score(a: frozenset[str], b: frozenset[str]) -> _Score:
    return len(a & b), len(a | b)


def _greater(x: _Score, y: _Score) -> bool:
    """Whether score x is strictly above y; an empty union scores 0."""
    return x[0] * max(y[1], 1) > y[0] * max(x[1], 1)


def _at_least(x: _Score, minimum: Fraction) -> bool:
    """Whether score x reaches ``minimum``; an empty union scores 0."""
    return x[0] * minimum.denominator >= minimum.numerator * max(x[1], 1)


def jaccard(a: frozenset[str], b: frozenset[str]) -> Fraction:
    """Exact Jaccard score; two empty sets score 0."""
    inter, union = _score(a, b)
    return Fraction(inter, union) if union else Fraction(0)


def _reject_duplicates(ids: list[str]) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError("lesson ids must be unique")


def top_k(candidate: Lesson, lessons: Iterable[Lesson], k: int = 10) -> tuple[Lesson, ...]:
    """The up to ``k`` lessons most similar to ``candidate``, best first, ties by id.

    The candidate's own id is excluded; zero scores are kept and nothing is padded.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    pool = list(lessons)
    _reject_duplicates([lesson.id for lesson in pool])
    base = words(candidate.lesson)
    scored = [
        (_score(base, words(lesson.lesson)), lesson) for lesson in pool if lesson.id != candidate.id
    ]
    ordered: list[tuple[_Score, Lesson]] = []
    for score, lesson in sorted(scored, key=lambda pair: pair[1].id):
        position = len(ordered)
        while position > 0 and _greater(score, ordered[position - 1][0]):
            position -= 1
        ordered.insert(position, (score, lesson))
    return tuple(lesson for _, lesson in ordered[:k])


def cluster(
    items: Iterable[Lesson], minimum: Fraction = CLUSTER_MIN
) -> tuple[tuple[str, ...], ...]:
    """Complete-linkage agglomerative clusters of ids, independent of input order.

    Every pair of ids inside a cluster scores at or above ``minimum``. Ids are sorted
    inside each cluster, clusters are ordered by their first id, singletons included.
    """
    ordered = sorted(items, key=lambda lesson: lesson.id)
    ids = [lesson.id for lesson in ordered]
    _reject_duplicates(ids)
    sets = [words(lesson.lesson) for lesson in ordered]
    pair = {
        (i, j): _score(sets[i], sets[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))
    }
    groups: list[list[int]] = [[i] for i in range(len(ids))]  # sorted by first member
    while True:
        best: tuple[_Score, int, int] | None = None
        for x in range(len(groups)):
            for y in range(x + 1, len(groups)):
                link = _linkage(groups[x], groups[y], pair)
                if not _at_least(link, minimum):
                    continue
                if best is None or _greater(link, best[0]):
                    best = (link, x, y)
        if best is None:
            break
        _, x, y = best
        groups[x] = sorted(groups[x] + groups[y])
        del groups[y]
        groups.sort(key=lambda g: g[0])
    return tuple(tuple(ids[i] for i in g) for g in groups)


def _linkage(g: list[int], h: list[int], pair: dict[tuple[int, int], _Score]) -> _Score:
    """Complete linkage: the lowest pairwise score between two clusters."""
    scores = [pair[(i, j)] if i < j else pair[(j, i)] for i in g for j in h]
    low = scores[0]
    for s in scores[1:]:
        if _greater(low, s):
            low = s
    return low
