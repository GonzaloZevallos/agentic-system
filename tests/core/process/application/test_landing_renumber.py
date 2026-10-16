"""The `renumber` landing step (GH-229, ADR-0049) over the memory ports, master being a tree
a test moves: `renumber_adr` through the `land` verb, then the merge verb on the new head.

Task A is "landed" by master taking its ADR (`advance_master`); task B is the branch under
test, whose own commits are `branch_commits`. The worktree is a real directory (`tmp_path`)
and `track_commits` makes the commit the step makes visible to the reads after it."""

from __future__ import annotations

from dataclasses import replace

from agentic.core.process.application import landing, merge
from agentic.core.process.application.ports import Upstream
from agentic.core.process.domain import adr_numbers, rules
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import URL, _HeldLocks, _pr
from tests.fakes import MemoryTracker, memory_ports

MINE = "docs/adr/0049-mine.md"
THEIRS = "docs/adr/0049-theirs.md"
NOTES = "docs/notes.md"
CITES = "see ADR-0049 for why"
BRANCH = rules.task_branch("GH-5")
LIVE = frozenset({"renumber"})


def _world(tmp_path, *, synced: bool = False, extra=()):
    """Task 5 approved at its head, whose own commit adds `MINE` and cites it in `NOTES`
    (and in `extra`, `(path, line)` pairs), while master holds task A's `THEIRS`."""
    ports = memory_ports(tracker=MemoryTracker())
    workspace = ports.workspace
    workspace.ROOT, workspace.track_commits, workspace.full_shas = tmp_path, True, True
    base = workspace.master_sha
    if not synced:
        workspace.advance_master(THEIRS)
    head = workspace.add_branch_commit(
        adds=(MINE,),
        lines=((MINE, "# ADR-0049: mine"), (NOTES, CITES), *extra),
        master=base,
    )
    workspace.files[(head, MINE)] = "# ADR-0049: mine\n"
    workspace.files[(head, NOTES)] = CITES + "\n"
    for path, line in extra:
        workspace.files[(head, path)] = line + "\n"
    if synced:  # master took 0049 after the branch added its own; the branch merged it
        after = workspace.advance_master(THEIRS)
        head = workspace.add_branch_commit(master=after)
    url = URL.format(5)
    ports.tracker.prs[url] = replace(_pr(), head_oid=head)
    unit = {"id": "only", "status": "approved", "reviewed_head": head}
    ports.documents.save(
        {
            "id": "GH-5",
            "status": "ready_for_review",
            "pull_request": url,
            "subtasks": {"only": unit},
        }
    )
    workspace.remote_tips[BRANCH] = head
    workspace.diff_paths = (MINE, NOTES)
    return ports, head


def _land(tmp_path, ports):
    workspace = ports.workspace
    return landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", workspace.master_sha, None),
        frozenset(),
        lambda line: None,
        clock=lambda: "2026-10-14T00:00:00+00:00",
        live=LIVE,
    )


def _pushed(ports) -> str:
    """What the remote and GitHub report once the push went through."""
    head = ports.workspace.branch_commits[-1].sha
    ports.workspace.remote_tips[BRANCH] = head
    ports.tracker.prs[URL.format(5)] = replace(_pr(), head_oid=head)
    return head


def _unit(ports) -> dict:
    return ports.documents.load("GH-5")["subtasks"]["only"]


def _tree(ports) -> list[str]:
    """Every ADR file name on master and on the branch head, as git would list them."""
    workspace = ports.workspace
    head = workspace.branch_commits[-1].sha
    names = {*workspace.tree_names(workspace.master_sha, "docs/adr")}
    return sorted(names | {*workspace.tree_names(head, "docs/adr")})


def test_the_step_commits_only_the_renaming_paths_and_pushes_with_a_lease_on_the_head(tmp_path):
    ports, head = _world(tmp_path)
    (tmp_path / "GH-5" / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "GH-5" / MINE).write_text("# ADR-0049: mine\n", encoding="utf-8")

    result = _land(tmp_path, ports)

    assert result.kind == "dispatched" and result.step == "renumber"
    [(_, relatives, _message)] = ports.workspace.path_commits
    assert relatives == (MINE, "docs/adr/0050-mine.md", NOTES)
    assert ports.workspace.pushes == [(BRANCH, head)]
    assert not (tmp_path / "GH-5" / MINE).exists()
    assert (tmp_path / "GH-5" / "docs/adr/0050-mine.md").read_text() == "# ADR-0050: mine\n"
    assert (tmp_path / "GH-5" / NOTES).read_text() == "see ADR-0050 for why\n"
    document = ports.documents.load("GH-5")
    [record] = document["adr_renumbered"]
    new = ports.workspace.branch_commits[-1].sha
    assert record == {
        "from": "0049",
        "to": "0050",
        "head_before": head,
        "head_after": new,
        "carried": True,
    }
    assert document["landing"][-1]["resolved_at"]


def test_a_docs_only_rewrite_carries_the_review_to_the_new_head(tmp_path):
    ports, head = _world(tmp_path)
    _land(tmp_path, ports)
    new = ports.workspace.branch_commits[-1].sha
    assert _unit(ports)["reviewed_head"] == new
    assert _unit(ports)["reviewed_head_carried_from"] == head


def test_a_rewrite_of_code_is_not_carried_and_the_next_round_re_reviews(tmp_path):
    code = ("agentic/x.py", 'MSG = "ADR-0049"')
    ports, head = _world(tmp_path, extra=(code,))

    _land(tmp_path, ports)

    new = ports.workspace.branch_commits[-1].sha
    assert ports.workspace.pushes == [(BRANCH, head)]
    assert _unit(ports)["reviewed_head"] == head  # the verdict stays on the head it was given on
    assert "reviewed_head_carried_from" not in _unit(ports)
    assert ports.documents.load("GH-5")["adr_renumbered"][0]["carried"] is False
    _pushed(ports)
    result = landing.land(
        "GH-5",
        _cfg(tmp_path),
        ports,
        _HeldLocks(),
        Upstream("origin/master", ports.workspace.master_sha, None),
        frozenset(),
        lambda line: None,
        clock=lambda: "2026-10-14T00:00:00+00:00",
        live=frozenset({"re_review"}),
    )
    assert result.step == "re_review"
    assert new != head


def test_a_lost_lease_pushes_nothing_and_records_nothing(tmp_path):
    ports, _ = _world(tmp_path)
    ports.workspace.remote_tips[BRANCH] = "somebody-else"

    _land(tmp_path, ports)

    assert ports.workspace.pushes == []
    document = ports.documents.load("GH-5")
    assert "adr_renumbered" not in document
    assert "resolved_at" not in document["landing"][-1]
    assert _unit(ports)["reviewed_head"] == ports.workspace.branch_commits[0].sha


def test_two_tasks_with_the_same_base_land_with_unique_numbers_and_resolving_references(
    tmp_path,
):
    ports, _ = _world(tmp_path)
    assert adr_numbers.duplicate_numbers(["0049-theirs.md", "0049-mine.md"]) == ["0049"]

    _land(tmp_path, ports)
    new = _pushed(ports)
    ports.workspace.contains_answer = "yes"
    ports.workspace.upstream_answer = Upstream("origin/master", ports.workspace.master_sha, None)
    merged = merge.merge_task(
        "GH-5",
        5,
        _cfg(tmp_path),
        ports,
        Upstream("origin/master", ports.workspace.master_sha, None),
        frozenset(),
        lambda line: None,
    )

    assert merged.kind == "merged", merged
    assert ports.tracker.merge_calls == [(URL.format(5), new)]
    names = _tree(ports)
    assert adr_numbers.duplicate_numbers(names) == []
    assert names == ["0049-theirs.md", "0050-mine.md"]
    assert ports.workspace.show(new, NOTES) == "see ADR-0050 for why\n"
    assert ports.workspace.show(new, "docs/adr/0050-mine.md") == "# ADR-0050: mine\n"


def test_after_a_sync_masters_own_0049_stays_and_only_the_branchs_is_renumbered(tmp_path):
    ports, _ = _world(tmp_path, synced=True)

    _land(tmp_path, ports)

    [(_, relatives, _message)] = ports.workspace.path_commits
    assert THEIRS not in relatives and "docs/adr/0050-theirs.md" not in relatives
    new = _pushed(ports)
    assert ports.workspace.show(new, "docs/adr/0050-mine.md") == "# ADR-0050: mine\n"
    assert adr_numbers.duplicate_numbers(_tree(ports)) == []
    assert "0049-theirs.md" in _tree(ports)


def test_a_master_that_takes_the_next_number_on_every_tick_ends_in_diagnose_after_three_pushes(
    tmp_path,
):
    ports, _ = _world(tmp_path)
    taken = 49
    for _ in range(landing_cap := 3):
        assert _land(tmp_path, ports).step == "renumber"
        _pushed(ports)
        taken += 1
        ports.workspace.advance_master(f"docs/adr/{taken:04d}-busy.md")

    result = _land(tmp_path, ports)

    assert (result.kind, result.step) == ("skipped", "diagnose")
    assert len(ports.workspace.pushes) == landing_cap
    assert len(ports.workspace.path_commits) == landing_cap
    assert [e["step"] for e in ports.documents.load("GH-5")["landing"]] == ["renumber"] * 3
