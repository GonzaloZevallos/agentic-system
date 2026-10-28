"""`rules.fast_forward_blocked`: whether the product checkout can fast-forward to the upstream."""

from agentic.core.process.domain import rules


def test_a_head_that_is_not_an_ancestor_of_the_tip_is_diverged():
    block = rules.fast_forward_blocked(False, frozenset({"a"}), frozenset({"a"}))
    assert block == rules.FastForwardBlock("diverged", ())


def test_a_dirty_path_the_upstream_changes_would_be_overwritten():
    block = rules.fast_forward_blocked(
        True, frozenset({"x", ".agentic/tasks/GH-1.md"}), frozenset({".agentic/tasks/GH-1.md", "y"})
    )
    assert block == rules.FastForwardBlock("would_overwrite", (".agentic/tasks/GH-1.md",))


def test_the_paths_are_sorted_and_capped_at_five():
    names = frozenset(f"p{n}" for n in (7, 3, 9, 1, 8, 2, 5))
    block = rules.fast_forward_blocked(True, names, names)
    assert block == rules.FastForwardBlock("would_overwrite", ("p1", "p2", "p3", "p5", "p7"))


def test_an_ancestor_with_no_overlap_can_fast_forward():
    assert rules.fast_forward_blocked(True, frozenset({"a"}), frozenset({"b"})) is None
    assert rules.fast_forward_blocked(True, frozenset(), frozenset()) is None
