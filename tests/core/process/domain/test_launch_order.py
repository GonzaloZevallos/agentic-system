"""GH-836: Orden de lanzamiento. `dependents` counts the open issues that wait, transitively, on
each blocker; `launch_key` puts the issue that unblocks most first, then the lowest number."""

from agentic.core.process.domain import launch_order


def test_a_chain_counts_every_transitive_dependent():
    # B waits on A, C waits on B.
    counts = launch_order.dependents({2: [1], 3: [2]})
    assert counts[1] == 2
    assert counts[2] == 1


def test_a_diamond_counts_each_dependent_once():
    # B and C wait on A, D waits on both B and C.
    counts = launch_order.dependents({2: [1], 3: [1], 4: [2, 3]})
    assert counts[1] == 3
    assert counts[2] == 1
    assert counts[3] == 1


def test_a_cycle_terminates_and_never_counts_an_issue_as_its_own_dependent():
    counts = launch_order.dependents({1: [2], 2: [1]})
    assert counts == {1: 1, 2: 1}
    assert launch_order.dependents({5: [5]}) == {}


def test_an_issue_nobody_waits_on_has_no_entry():
    assert launch_order.dependents({2: [1]}).get(2, 0) == 0
    assert launch_order.dependents({}) == {}


def test_a_repeated_edge_counts_once():
    assert launch_order.dependents({2: [1, 1]}) == {1: 1}


def test_launch_key_puts_most_dependents_first_then_the_lowest_issue():
    counts = {9: 2, 4: 2, 7: 1}
    order = sorted([3, 4, 7, 9], key=lambda n: launch_order.launch_key(n, counts))
    assert order == [4, 9, 7, 3]
