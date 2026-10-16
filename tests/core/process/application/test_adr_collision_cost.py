"""What the ADR-collision probe reads, counted at the `Ports` seam (GH-262, after GH-229).

Every cost claim in the docstrings of `application.adr_collision` and
`poll._adr_collision` is a counter asserted here: per tick, for each task that is
`ready_for_review` with an `OPEN` pull request, the head is made local (`fetch_commit`) and
diffed (`changed_paths`) once; when the diff touches `docs/adr/` that is one `added_adrs`
more; a branch that added an ADR reads `tree_names(master)` once, and `tree_names(head)` a
second time only when the whole set of its added ADRs has a candidate collision.

The equivalence test enumerates its universe: five own ADR paths (one colliding with
master's number 0004, two sharing 0005, one with master's own basename 0007-c.md, one with
no collision), every subset S of them added by the branch and every subset T of S still in
the tree at head: 2**5 subsets of added, and for each of them its own subsets, 243 pairs.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import chain, combinations

from agentic.core.process.application import adr_collision, poll
from agentic.core.process.application.ports import AddedAdr
from agentic.core.process.domain import adr_numbers
from agentic.core.process.domain.adr_numbers import Ambiguous, OwnAdr, OwnLine, Renaming
from tests.core.process.application.test_poll_merge import HEAD, URL, _pr, _ready
from tests.fakes import BranchCommit, MemoryTracker, memory_ports

MINE = "docs/adr/0049-mine.md"
THEIRS = "docs/adr/0049-theirs.md"


def _tick(ports, *, status: str = "ready_for_review", state: str = "OPEN", head: str = HEAD):
    """`assess_merge` for GH-5 as the poller asks it, the head being `head`."""
    ports.tracker.prs[URL.format(5)] = replace(_pr(), state=state, head_oid=head)
    raw = ports.documents.load("GH-5")
    assert raw is not None
    return poll.assess_merge(
        ports, "GH-5", {**raw, "status": status}, 5, frozenset(), ports.workspace.master_sha
    )


def _ports(*, diff: tuple[str, ...]):
    ports = memory_ports(tracker=MemoryTracker())
    ports.workspace.upstream_answer = None  # unused: `assess_merge` is given the master
    _ready(ports, 5, checks=False)
    ports.workspace.diff_paths = diff
    return ports


def test_a_task_that_is_not_ready_reads_no_git():
    ports = _ports(diff=(MINE,))
    _tick(ports, status="developing")
    assert ports.workspace.fetched_commits == [] and ports.workspace.diff_reads == []
    assert ports.workspace.added_adrs_calls == []


def test_a_pull_request_that_is_not_open_reads_no_git():
    ports = _ports(diff=(MINE,))
    _tick(ports, state="MERGED")
    assert ports.workspace.fetched_commits == [] and ports.workspace.diff_reads == []
    assert ports.workspace.added_adrs_calls == []


def test_a_ready_open_task_makes_its_head_local_and_diffs_it_once():
    ports = _ports(diff=("agentic/x.py",))
    _tick(ports)
    assert ports.workspace.fetched_commits == [HEAD]
    assert len(ports.workspace.diff_reads) == 1
    assert ports.workspace.added_adrs_calls == []
    assert ports.workspace.tree_names_calls == []


def test_a_probed_tick_reads_the_task_base_once_more_than_one_that_is_not_probed():
    """`task_base` is `git config --get branch.<b>.agenticBase`. `assess_merge` reads it once
    for the stack's CI whatever the task; the probe's diff is the one more, per probed tick."""
    idle = _ports(diff=("agentic/x.py",))
    _tick(idle, status="developing")
    probed = _ports(diff=("agentic/x.py",))
    _tick(probed)
    assert len(probed.workspace.base_of_calls) == len(idle.workspace.base_of_calls) + 1


def test_a_diff_that_touches_no_adr_path_reads_no_adr():
    ports = _ports(diff=("docs/product/x.md", "docs/adrs.md"))
    ports.workspace.add_branch_commit(adds=(MINE,))
    _tick(ports)
    assert ports.workspace.added_adrs_calls == []


def test_an_unapproved_ready_task_is_still_probed_because_the_collision_is_ahead_of_it():
    ports = _ports(diff=(MINE,))
    ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    ports.workspace.files[(head, MINE)] = "# ADR-0049: mine\n"
    ports.documents.save(
        {**(ports.documents.load("GH-5") or {}), "subtasks": {"only": {"status": "developing"}}}
    )
    assessment = _tick(ports, head=head)
    assert assessment is not None
    assert assessment.decision.reason == "adr_number_taken"


def test_a_branch_that_added_no_adr_costs_one_added_adrs_and_no_tree():
    ports = _ports(diff=("docs/adr/0001-a.md",))  # an edit of master's ADR, not an addition
    head = ports.workspace.add_branch_commit(lines=(("docs/adr/0001-a.md", "x"),))
    _tick(ports, head=head)
    assert len(ports.workspace.added_adrs_calls) == 1
    assert ports.workspace.tree_names_calls == []


def test_added_adrs_with_no_collision_cost_exactly_one_tree_read():
    ports = _ports(diff=(MINE,))
    ports.workspace.advance_master("docs/adr/0048-x.md")
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    assessment = _tick(ports, head=head)
    assert assessment is not None and assessment.facts.adr_collision is None
    assert len(ports.workspace.added_adrs_calls) == 1
    assert ports.workspace.tree_names_calls == [(ports.workspace.master_sha, "docs/adr")]


def test_a_candidate_collision_costs_two_tree_reads_and_the_renaming_is_found():
    ports = _ports(diff=(MINE,))
    base = ports.workspace.master_sha
    ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(adds=(MINE,), master=base)
    ports.workspace.files[(head, MINE)] = "# ADR-0049: mine\n"
    assessment = _tick(ports, head=head)
    assert assessment is not None and isinstance(assessment.facts.adr_collision, Renaming)
    reads = ports.workspace.tree_names_calls
    assert reads[:2] == [(ports.workspace.master_sha, "docs/adr"), (head, "docs/adr")]


def test_two_own_adrs_one_removed_at_head_cost_two_tree_reads_and_are_no_collision():
    """The superset is a duplicate number (a candidate), the head's tree drops one of them,
    and nothing in master collides: the second read is what clears it."""
    ports = _ports(diff=(MINE,))
    other = "docs/adr/0049-other.md"
    first = ports.workspace.add_branch_commit(adds=(MINE, other))
    head = "c" * 40
    ports.workspace.branch_commits.append(
        BranchCommit(head, (), (), ports.workspace.master_sha, removes=(other,))
    )
    assert first != head
    assert adr_collision.adr_collision(ports, ports.workspace.master_sha, head) is None
    assert [ref for ref, _ in ports.workspace.tree_names_calls] == [
        ports.workspace.master_sha,
        head,
    ]


# The universe of the equivalence and the stacked-shape tests.
MASTER_NAMES = ("0003-a.md", "0004-b.md", "0007-c.md")
OWN_PATHS = (
    "docs/adr/0004-x.md",
    "docs/adr/0005-y.md",
    "docs/adr/0005-z.md",
    "docs/adr/0007-c.md",
    "docs/adr/0009-w.md",
)


def _subsets(items):
    return chain.from_iterable(combinations(items, n) for n in range(len(items) + 1))


class _Stub:
    """A workspace that answers from a table and counts `tree_names`: master's names, the
    head's (`at_head`), the added files."""

    def __init__(self, added: tuple[str, ...], at_head: tuple[str, ...]) -> None:
        self.added = added
        self.at_head = tuple(p.rsplit("/", 1)[-1] for p in at_head)
        self.tree_calls: list[str] = []

    def added_adrs(self, master: str, head: str) -> tuple[AddedAdr, ...]:
        return tuple(AddedAdr(path, "c" * 40) for path in self.added)

    def tree_names(self, ref: str, directory: str) -> tuple[str, ...]:
        self.tree_calls.append(ref)
        return MASTER_NAMES if ref == "master" else self.at_head if ref == "head" else ()

    def added_lines(self, master: str, head: str, numbers):
        return ()

    def show(self, ref: str, path: str) -> str | None:
        return "# ADR\n"


def _old_order(workspace, master: str, head: str):
    """GH-229's order, as it was: the head's tree first, then `collisions` on what is left."""
    added = workspace.added_adrs(master, head)
    if not added:
        return None
    at_head = set(workspace.tree_names(head, "docs/adr"))
    own = [OwnAdr(a.path, a.commit) for a in added if a.path.rsplit("/", 1)[-1] in at_head]
    if not own:
        return None
    moves = adr_numbers.collisions(workspace.tree_names(master, "docs/adr"), own)
    if isinstance(moves, Ambiguous):
        return moves
    if not moves:
        return None
    lines = [
        OwnLine(f.path, f.commit, f.text)
        for f in workspace.added_lines(master, head, adr_numbers.old_numbers(moves))
    ]
    trees = {c: workspace.tree_names(c, "docs/adr") for c in sorted({x.commit for x in lines})}
    texts = {}
    for path in sorted({x.path for x in lines} | set(moves)):
        text = workspace.show(head, path)
        if text is not None:
            texts[path] = (workspace.show(master, path), text)
    return adr_numbers.renumber(moves, lines, trees, texts)


def test_the_new_order_returns_what_the_old_one_did_for_every_added_and_head_subset():
    seen = {"None": 0, "Renaming": 0, "Ambiguous": 0}
    pairs = 0
    for added in _subsets(OWN_PATHS):
        for at_head in _subsets(added):
            new_ws, old_ws = _Stub(added, at_head), _Stub(added, at_head)
            new = adr_collision.adr_collision(
                replace(memory_ports(), workspace=new_ws), "master", "head"
            )
            assert new == _old_order(old_ws, "master", "head"), (added, at_head)
            superset = adr_numbers.collisions(MASTER_NAMES, [OwnAdr(p, "c" * 40) for p in added])
            reads = len(new_ws.tree_calls)
            if not added:
                assert reads == 0
            elif superset == {}:
                assert reads == 1 and new is None, (added, at_head)
            else:
                assert reads >= 2 and new_ws.tree_calls[:2] == ["master", "head"], (added, at_head)
            seen[type(new).__name__ if new is not None else "None"] += 1
            pairs += 1
    assert pairs == 243
    assert all(seen.values())  # every kind of result is in the universe


def test_the_stacked_false_collision_shape_is_pinned_as_it_is_today():
    """GH-262 stacked on GH-229: master holds `0049-other.md` and `0050-gh229.md`, the
    branch's own commit added `0049-gh229.md`, and its head tree still holds it. Pinned, not
    fixed: the decision order is unchanged."""
    ports = memory_ports(tracker=MemoryTracker())
    ports.workspace.advance_master("docs/adr/0049-other.md", "docs/adr/0050-gh229.md")
    mine = "docs/adr/0049-gh229.md"
    head = ports.workspace.add_branch_commit(adds=(mine,))
    ports.workspace.files[(head, mine)] = "# ADR-0049: gh229\n"
    result = adr_collision.adr_collision(ports, ports.workspace.master_sha, head)
    assert isinstance(result, Renaming)
    assert result.moves == ((mine, "docs/adr/0051-gh229.md"),)
