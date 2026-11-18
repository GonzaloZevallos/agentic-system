"""Seam tests for the pure lexical similarity module over lessons.

Lesson fixtures are copied verbatim from .agentic/playbooks/candidates.yaml and
cited by role slug, category and seen_in task, because candidates carry no id.
"""

import ast
import itertools
import random
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pytest

from agentic.core.process.domain import playbook_similarity
from agentic.core.process.domain.playbook_similarity import (
    CLUSTER_MIN,
    STOPWORDS,
    cluster,
    jaccard,
    top_k,
    words,
)


@dataclass(frozen=True)
class Item:
    id: str
    lesson: str


# developer, standard-other, seen_in GH-585
GH585 = Item(
    "GH-585",
    "When you change a flow's outcomes or behaviour, search docs/architecture for"
    " hand-written descriptions of it and update them in the same change;"
    " generated docs alone do not count.",
)
# developer, standard-other, seen_in GH-593
GH593 = Item(
    "GH-593",
    "When your diff changes a flow, a module dependency or a documented behaviour,"
    " update the hand-written docs under docs/architecture and any comment that"
    " states the old behaviour, in the same change.",
)
# developer, standard-other, seen_in GH-500
GH500 = Item(
    "GH-500",
    "When you change a port's contract or storage model, update the matching"
    " docs/architecture page in the same change and confirm the doc diff lists it"
    " before you deliver.",
)


def score(a: Item, b: Item) -> Fraction:
    return jaccard(words(a.lesson), words(b.lesson))


def test_words_casefolds_splits_and_drops_stopwords() -> None:
    assert words("When you Change the Flow's docs/architecture!") == frozenset(
        {"change", "flow", "docs", "architecture"}
    )


def test_gh585_and_gh593_score_above_threshold_and_group() -> None:
    value = score(GH585, GH593)
    assert value == Fraction(9, 24)
    assert value >= CLUSTER_MIN
    assert cluster([GH585, GH593]) == (("GH-585", "GH-593"),)
    assert GH593 in top_k(GH585, [GH593, GH500])
    assert GH585 in top_k(GH593, [GH585, GH500])


def test_unrelated_port_lesson_stays_apart() -> None:
    assert score(GH585, GH500) < CLUSTER_MIN
    assert cluster([GH585, GH500]) == (("GH-500",), ("GH-585",))


def test_negation_changes_the_score_and_stays_top_one() -> None:
    flipped = Item("GH-585-flipped", GH585.lesson.replace("do not count", "count"))
    assert flipped.lesson != GH585.lesson
    assert score(GH585, flipped) < 1
    others = [GH593, GH500, Item("X-1", "Run the linter before you commit anything.")]
    assert top_k(GH585, [flipped, *others], k=1) == (flipped,)
    assert top_k(flipped, [GH585, *others], k=1) == (GH585,)


# Synthetic chain: A~B and B~C are close, A~C is not.
CHAIN_A = Item("A", "alpha beta gamma delta")
CHAIN_B = Item("B", "alpha beta gamma epsilon zeta")
CHAIN_C = Item("C", "gamma epsilon zeta eta theta")


def test_chain_is_not_single_linkage() -> None:
    ab, bc, ac = (score(CHAIN_A, CHAIN_B), score(CHAIN_B, CHAIN_C), score(CHAIN_A, CHAIN_C))
    assert ab > bc >= CLUSTER_MIN > ac
    assert cluster([CHAIN_A, CHAIN_B, CHAIN_C]) == (("A", "B"), ("C",))


FIVE = [
    Item("e", "alpha beta gamma delta"),
    Item("d", "alpha beta gamma epsilon zeta"),
    Item("c", "gamma epsilon zeta eta theta"),
    Item("b", "iota kappa lambda"),
    Item("a", "iota kappa lambda mu"),
]


def test_cluster_is_independent_of_input_order() -> None:
    expected = cluster(FIVE)
    assert expected == (("a", "b"), ("c",), ("d", "e"))
    perms = list(itertools.permutations(FIVE))
    assert len(perms) == 120
    assert all(cluster(p) == expected for p in perms)


def test_top_k_is_independent_of_input_order() -> None:
    candidate = FIVE[0]
    expected = top_k(candidate, FIVE, k=3)
    rng = random.Random(7)
    for _ in range(20):
        shuffled = FIVE[:]
        rng.shuffle(shuffled)
        assert top_k(candidate, shuffled, k=3) == expected


def test_top_k_ties_break_by_id() -> None:
    cand = Item("z", "alpha beta")
    twins = [Item("t2", "alpha beta"), Item("t1", "alpha beta"), Item("t3", "gamma")]
    assert [i.id for i in top_k(cand, twins)] == ["t1", "t2", "t3"]


# Corpus snapshot: developer lessons about docs/architecture.
CORPUS = [
    # developer, standard-other, GH-478
    Item(
        "GH-478",
        "When you change a port or a contract between modules, update the hand-written"
        " page under docs/architecture that describes it in the same change.",
    ),
    # developer, standard-other, GH-531
    Item(
        "GH-531",
        "When a change alters behaviour that the architecture docs describe, update"
        " those docs in the same change. Grep docs/architecture for the behaviour you"
        " changed before you deliver.",
    ),
    # developer, standard-other, GH-552
    Item(
        "GH-552",
        "When your change alters a flow, a persisted record or a gate decision, update"
        " the matching docs/architecture page in the same change and check that its"
        " diff is not empty before you deliver.",
    ),
    GH585,
    GH593,
    # developer, standard-other, GH-610
    Item(
        "GH-610",
        "When your change alters a flow's behaviour or wiring, update the hand-written"
        " flow page in docs/architecture in the same change. Regenerated pages alone"
        " do not count.",
    ),
    # developer, spec-scope, GH-768
    Item(
        "GH-768",
        "When a change alters a contract between modules or a key flow, update the"
        " matching docs/architecture pages in the same change and confirm with git"
        " diff --stat that they appear before you deliver.",
    ),
    # developer, spec-scope, GH-787
    Item(
        "GH-787",
        "When your change moves a flow or behaviour that docs/architecture describes,"
        " update that flow page in the same change, and grep the docs for the old"
        " wording before you deliver.",
    ),
]


EXPECTED_PARTITION = (
    ("GH-478",),
    ("GH-531", "GH-787"),
    ("GH-552", "GH-768"),
    ("GH-585", "GH-593", "GH-610"),
)


def test_corpus_snapshot_partition() -> None:
    # Pins today's behaviour so a tokenizer or threshold change shows up. It does
    # not claim these lessons belong together; CLUSTER_MIN and STOPWORDS must not
    # be tuned to force a grouping.
    assert cluster(CORPUS) == EXPECTED_PARTITION


def test_every_cluster_pair_meets_the_minimum() -> None:
    by_id = {i.id: words(i.lesson) for i in CORPUS}
    for group in cluster(CORPUS):
        for x, y in itertools.combinations(group, 2):
            inter = len(by_id[x] & by_id[y])
            union = len(by_id[x] | by_id[y])
            assert inter * CLUSTER_MIN.denominator >= CLUSTER_MIN.numerator * union


def test_cluster_minimum_is_a_parameter() -> None:
    assert cluster([CHAIN_A, CHAIN_C], Fraction(0)) == (("A", "C"),)
    assert cluster([CHAIN_A, CHAIN_C], Fraction(1)) == (("A",), ("C",))


def test_empty_sets_score_zero() -> None:
    assert jaccard(frozenset(), frozenset()) == 0
    assert jaccard(frozenset(), frozenset({"a"})) == 0
    assert isinstance(jaccard(frozenset({"a"}), frozenset({"a", "b"})), Fraction)


def test_top_k_excludes_self_and_does_not_pad() -> None:
    result = top_k(GH585, [GH585, GH593], k=10)
    assert result == (GH593,)


def test_top_k_rejects_bad_k_and_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        top_k(GH585, [GH593], k=0)
    with pytest.raises(ValueError):
        top_k(GH585, [GH593, GH593])


def test_cluster_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        cluster([GH585, Item("GH-585", "other")])


def test_cluster_empty_input() -> None:
    assert cluster([]) == ()


def test_module_imports_only_the_standard_library() -> None:
    source = Path(playbook_similarity.__file__).read_text()
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            roots.add(node.module.split(".")[0])
    assert roots <= set(sys.stdlib_module_names)


def test_stopwords_keep_negations_visible() -> None:
    assert STOPWORDS.isdisjoint({"not", "no", "never", "nor", "cannot"})
