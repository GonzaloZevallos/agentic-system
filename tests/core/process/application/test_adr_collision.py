"""`adr_collision` and the merge verb's use of it (GH-229, ADR-0049) over the memory ports: a
mutable master, the branch's own commits, and what each phase of the git reads was asked."""

from __future__ import annotations

from dataclasses import replace

from agentic.core.process.application import adr_collision, merge
from agentic.core.process.application.ports import GitError, Upstream
from agentic.core.process.domain import landing as landing_rules
from agentic.core.process.domain.adr_numbers import Ambiguous, Renaming
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import URL, _pr, _ready
from tests.core.process.domain.test_landing import _facts, _spent
from tests.fakes import MemoryTracker, memory_ports

MINE = "docs/adr/0049-mine.md"
THEIRS = "docs/adr/0049-theirs.md"
CITES = "see ADR-0049 for why"


def _ports():
    ports = memory_ports(tracker=MemoryTracker())
    return ports


def _text(ports, head: str, path: str, text: str) -> None:
    ports.workspace.files[(head, path)] = text


def test_a_branch_that_added_no_adr_reads_nothing_more_and_asks_for_no_lines():
    ports = _ports()
    head = ports.workspace.add_branch_commit(lines=(("docs/a.md", "ADR-0049"),))
    assert adr_collision.adr_collision(ports, ports.workspace.master_sha, head) is None
    assert ports.workspace.added_lines_calls == []
    assert ports.workspace.tree_names_calls == []


def test_an_own_adr_whose_number_master_does_not_hold_is_no_collision_and_asks_no_lines():
    ports = _ports()
    ports.workspace.advance_master("docs/adr/0048-x.md")
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    assert adr_collision.adr_collision(ports, ports.workspace.master_sha, head) is None
    assert ports.workspace.added_lines_calls == []


def test_an_own_adr_the_branch_later_removed_is_no_longer_its_own():
    ports = _ports()
    ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    real = ports.workspace.tree_names

    class Renumbered:
        """The head's tree, as git has it after a renumber commit: the old name is gone."""

        added_adrs = ports.workspace.added_adrs

        @staticmethod
        def tree_names(ref: str, directory: str) -> tuple[str, ...]:
            return tuple(n for n in real(ref, directory) if not (ref == head and "mine" in n))

    after = replace(ports, workspace=Renumbered())
    assert adr_collision.adr_collision(after, ports.workspace.master_sha, head) is None


def test_a_collision_gives_the_renaming_with_the_references_rewritten():
    ports = _ports()
    base = ports.workspace.master_sha
    ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(
        adds=(MINE,), lines=(("docs/notes.md", CITES),), master=base
    )
    _text(ports, head, MINE, "# ADR-0049: mine\n")
    _text(ports, head, "docs/notes.md", f"{CITES}\n")
    result = adr_collision.adr_collision(ports, ports.workspace.master_sha, head)
    assert isinstance(result, Renaming)
    assert result.moves == ((MINE, "docs/adr/0050-mine.md"),)
    assert result.texts["docs/notes.md"] == "see ADR-0050 for why\n"
    assert ports.workspace.added_lines_calls == [(ports.workspace.master_sha, head, ("0049",))]


def test_after_a_sync_masters_own_0049_stays_and_only_the_branchs_moves():
    ports = _ports()
    first = ports.workspace.add_branch_commit(adds=(MINE,), lines=(("docs/notes.md", CITES),))
    ports.workspace.advance_master(THEIRS)  # the sync: master's file is reachable from M
    head = ports.workspace.add_branch_commit(lines=(("docs/other.md", "unrelated"),))
    _text(ports, head, MINE, "# ADR-0049: mine\n")
    _text(ports, head, "docs/notes.md", f"{CITES}\n")
    result = adr_collision.adr_collision(ports, ports.workspace.master_sha, head)
    assert isinstance(result, Renaming)
    assert result.moves == ((MINE, "docs/adr/0050-mine.md"),)
    assert THEIRS not in result.texts and "docs/adr/0050-theirs.md" not in result.texts
    assert first != head


def test_a_citation_added_after_masters_file_arrived_is_ambiguous():
    ports = _ports()
    ports.workspace.add_branch_commit(adds=(MINE,))
    after = ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(lines=(("docs/notes.md", CITES),), master=after)
    _text(ports, head, MINE, "# ADR-0049: mine\n")
    _text(ports, head, "docs/notes.md", f"{CITES}\n")
    result = adr_collision.adr_collision(ports, ports.workspace.master_sha, head)
    assert isinstance(result, Ambiguous)
    assert result.reason == "cites_master_after_sync"


def _delivered(ports, head: str):
    ports.workspace.upstream_answer = Upstream("origin/master", ports.workspace.master_sha, None)
    _ready(ports, 5, checks=False)
    ports.tracker.prs[URL.format(5)] = replace(_pr(), head_oid=head)


def test_the_verb_refuses_the_collision_names_the_moves_and_runs_no_check(tmp_path):
    ports = _ports()
    ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    _text(ports, head, MINE, "# ADR-0049: mine\n")
    _delivered(ports, head)
    ports.workspace.diff_paths = (MINE,)
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", ports.workspace.master_sha, None),
        frozenset(),
        lambda line: None,
    )
    assert result == merge.MergeResult("refused", "adr_number_taken")
    assert ports.workspace.detached == []
    assert ports.commands.ran == []
    task_id, body = ports.tracker.comments[0]
    assert task_id == "GH-5"
    assert body.startswith(f"Auto-merge refused: {head} adr_number_taken")
    assert f"- {MINE} -> docs/adr/0050-mine.md" in body
    assert ports.workspace.added_adrs_calls == [(ports.workspace.master_sha, head)]


def test_the_verb_reads_no_adr_for_a_diff_that_touches_none(tmp_path):
    ports = _ports()
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    _delivered(ports, head)
    ports.workspace.diff_paths = ("agentic/x.py",)
    merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", ports.workspace.master_sha, None),
        frozenset(),
        lambda line: None,
    )
    assert ports.workspace.added_adrs_calls == []


def test_touches_adrs_is_true_only_under_docs_adr():
    assert adr_collision.touches_adrs(("a.py", MINE))
    assert not adr_collision.touches_adrs(("docs/adrs.md", "docs/product/x.md"))
    assert not adr_collision.touches_adrs(None)


def test_a_git_failure_reading_the_collision_stops_the_verb_instead_of_escaping(tmp_path):
    ports = _ports()
    ports.workspace.advance_master(THEIRS)
    head = ports.workspace.add_branch_commit(adds=(MINE,))
    _delivered(ports, head)
    ports.workspace.diff_paths = (MINE,)

    def broken(master: str, head: str):
        raise GitError("bad object")

    ports.workspace.added_adrs = broken  # type: ignore[method-assign]
    result = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", ports.workspace.master_sha, None),
        frozenset(),
        lambda line: None,
    )
    assert result.kind == "stopped"
    assert (result.reason or "").startswith("adr_unreadable")
    assert ports.commands.ran == []


GH384_OWN = [f"docs/adr/{n:04d}-gh384-{n}.md" for n in range(54, 61)]
GH384_MASTER = "docs/adr/0054-local-checks-and-ci-run-one-list-enforced-by-a-test.md"
GH384_MOVES = ((GH384_OWN[0], "docs/adr/0061-gh384-54.md"),)
GH384_LINES = (
    "| 0054 | configuration per business |",
    "numbers 0054–0060",
    "ADR-0054…ADR-0060",
    "ADRs 0054, 0055",
)


def _gh384(lines: tuple[str, ...]):
    """GH-384's shape built the way landing builds it: the branch's own commits add ADRs
    0054-0060, master gained its own 0054 meanwhile, and `adr_collision` reads that history."""
    ports = _ports()
    base = ports.workspace.master_sha
    ports.workspace.advance_master(GH384_MASTER)
    head = ports.workspace.add_branch_commit(
        adds=tuple(GH384_OWN),
        lines=tuple(("docs/notes.md", line) for line in lines),
        master=base,
    )
    for path in GH384_OWN:
        _text(ports, head, path, f"# {path}\n")
    _text(ports, head, "docs/notes.md", "\n".join(lines) + "\n")
    return adr_collision.adr_collision(ports, ports.workspace.master_sha, head), head


def _gh384_step(found, head: str, *, escalated: bool = False) -> landing_rules.LandingStep:
    return landing_rules.landing_step(
        "adr_number_taken",
        _facts(pr_head=head, adr_collision=found),
        _spent(),
        (),
        adr_escalated=escalated,
    )


def test_gh384_shape_escalates_with_the_moves_and_is_never_a_restart():
    found, head = _gh384(GH384_LINES)
    assert isinstance(found, Ambiguous)
    assert found.moves == GH384_MOVES

    step = _gh384_step(found, head)
    assert step.kind == "escalate_adr"
    assert step.kind not in ("restart", "diagnose", "land_slice")
    assert _gh384_step(found, head, escalated=True).kind == "none"


def test_gh384_shape_with_only_the_listed_citations_renumbers_just_the_colliding_adr():
    found, head = _gh384(("ADRs 0054, 0055", "ADR-0054"))
    assert isinstance(found, Renaming)
    assert found.moves == GH384_MOVES
    assert _gh384_step(found, head).kind == "renumber"
