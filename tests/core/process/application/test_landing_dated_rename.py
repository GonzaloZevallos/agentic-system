"""The dated path of the `renumber` step (GH-611, the owner's rule) over the memory ports: a
product that names ADRs by date renames the branch's numbered ADR to `<yyyymmdd>-<slug>.md`,
rewrites what cites it, regenerates the docs and pushes once, asking nobody. GH-592's shapes:
a head that holds `0079-x.md`, and one that holds `0085-x.md` after an applied decision."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from agentic.core.process.application import landing
from agentic.core.process.application.ports import CommandResult, Upstream
from agentic.core.process.domain import rules
from agentic.core.process.domain.docs import Docs
from tests.core.process.application.test_poll import _cfg
from tests.core.process.application.test_poll_merge import URL, _HeldLocks, _pr
from tests.fakes import MemoryCommands, MemoryStack, MemoryTracker, memory_ports

THEIRS = "docs/adr/0079-theirs.md"
NOTES = "docs/notes.md"
BRANCH = rules.task_branch("GH-5")
DOCS = Docs(generate="make docs", paths=("docs/gen-out/**",))
FRONT = "---\ndate: 2026-10-30\n---\n# ADR-{n}: mine\n"


def _world(tmp_path, *, number="0079", commands=None, cite="see ADR-0079 for why", extra=()):
    ports = memory_ports(
        tracker=MemoryTracker(),
        stack=MemoryStack(adr_naming="dated", docs=DOCS),
        commands=commands or MemoryCommands(),
    )
    workspace = ports.workspace
    workspace.ROOT, workspace.track_commits, workspace.full_shas = tmp_path, True, True
    base = workspace.master_sha
    workspace.advance_master(THEIRS)
    mine = f"docs/adr/{number}-mine.md"
    cite = cite.replace("0079", number)
    head = workspace.add_branch_commit(
        adds=(mine,), lines=((mine, f"# ADR-{number}: mine"), (NOTES, cite), *extra), master=base
    )
    workspace.files[(head, mine)] = FRONT.format(n=number)
    workspace.files[(head, NOTES)] = cite + "\n"
    workspace.files[(workspace.master_sha, THEIRS)] = "# ADR-0079: theirs\n"
    url = URL.format(5)
    ports.tracker.prs[url] = replace(_pr(), head_oid=head)
    unit = {"id": "only", "status": "approved", "reviewed_head": head}
    state = {"id": "GH-5", "status": "ready_for_review", "pull_request": url}
    ports.documents.save({**state, "subtasks": {"only": unit}})
    workspace.remote_tips[BRANCH] = head
    workspace.diff_paths = (mine, NOTES)
    (tmp_path / "GH-5" / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "GH-5" / mine).write_text(FRONT.format(n=number), encoding="utf-8")
    return ports, head, mine


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
        clock=lambda: "2026-11-12T00:00:00+00:00",
        live=frozenset({"renumber"}),
        turn="head",
    )


def test_a_head_holding_0079_is_renamed_to_the_dated_form_and_pushed_once(tmp_path):
    ports, head, mine = _world(tmp_path)

    result = _land(tmp_path, ports)

    assert result.kind == "dispatched" and result.step == "renumber"
    [(_, relatives, _message)] = ports.workspace.path_commits
    assert mine in relatives and "docs/adr/20261030-mine.md" in relatives
    assert "docs/gen-out/**" in relatives
    assert ports.workspace.pushes == [(BRANCH, head)]
    assert not (tmp_path / "GH-5" / mine).exists()
    assert (tmp_path / "GH-5" / NOTES).read_text() == "see ADR-20261030-mine for why\n"
    document = ports.documents.load("GH-5")
    [record] = document["adr_renumbered"]
    assert (record["mode"], record["from"], record["to"]) == ("dated", "0079-mine", "20261030-mine")
    entry = document["landing"][-1]
    assert (entry["step"], entry["mode"], entry["outcome"]) == ("renumber", "dated", "renamed")
    assert not any(e["step"] == "escalate_adr" for e in document["landing"])


def test_a_head_holding_0085_after_an_applied_decision_is_renamed_without_a_question(tmp_path):
    ports, head, _mine = _world(tmp_path, number="0085")
    document = ports.documents.load("GH-5")
    ports.documents.save(
        {
            **document,
            "landing": [
                {
                    "head": "o" * 40,
                    "step": "escalate_adr",
                    "refusal": "adr_number_taken",
                    "at": "2026-11-12T09:00:00+00:00",
                    "resolved_at": "2026-11-12T09:00:05+00:00",
                    "released_at": "2026-11-12T09:30:00+00:00",
                    "moves": [["docs/adr/0079-mine.md", "docs/adr/0085-mine.md"]],
                },
                {
                    "head": "o" * 40,
                    "step": "apply_adr_decision",
                    "refusal": "adr_number_taken",
                    "at": "2026-11-12T10:00:00+00:00",
                    "resolved_at": "2026-11-12T10:30:00+00:00",
                    "outcome": "applied",
                    "decision_at": "2026-11-12T09:30:00+00:00",
                },
                {
                    "head": head,
                    "step": "sync_master",
                    "refusal": "master_moved",
                    "at": "2026-11-12T11:00:00+00:00",
                    "resolved_at": "2026-11-12T11:01:00+00:00",
                },
            ],
        }
    )

    result = _land(tmp_path, ports)

    assert result.step == "renumber"
    assert ports.workspace.pushes == [(BRANCH, head)]
    steps = [e["step"] for e in ports.documents.load("GH-5")["landing"]]
    assert steps[-1] == "renumber" and "escalate_adr" not in steps[3:]


def test_a_references_outside_docs_costs_a_re_review(tmp_path):
    ports, head, _mine = _world(tmp_path, extra=(("CONTEXT.md", "decided in ADR-0079"),))
    ports.workspace.files[(head, "CONTEXT.md")] = "decided in ADR-0079\n"
    ports.workspace.diff_paths = (*ports.workspace.diff_paths, "CONTEXT.md")
    (tmp_path / "GH-5" / "CONTEXT.md").write_text("decided in ADR-0079\n", encoding="utf-8")

    _land(tmp_path, ports)

    [record] = ports.documents.load("GH-5")["adr_renumbered"]
    assert record["carried"] is False


def test_a_failed_generate_pushes_nothing_and_records_a_retry(tmp_path):
    commands = MemoryCommands(**{"make docs": [CommandResult(2, "boom")]})
    ports, _head, _mine = _world(tmp_path, commands=commands)

    _land(tmp_path, ports)

    assert ports.workspace.pushes == []
    entry = ports.documents.load("GH-5")["landing"][-1]
    assert (entry["step"], entry["outcome"], entry["cause"]) == (
        "renumber",
        "retry",
        "generate_failed",
    )


def test_a_slug_master_already_holds_is_declined_and_pushes_nothing(tmp_path):
    ports, _head, _mine = _world(tmp_path)
    ports.workspace.advance_master("docs/adr/20260101-mine.md")

    _land(tmp_path, ports)

    assert ports.workspace.pushes == []
    entry = ports.documents.load("GH-5")["landing"][-1]
    assert (entry["outcome"], entry["cause"]) == ("declined", "slug_taken")


def test_the_first_commit_date_names_an_adr_without_a_date_line(tmp_path):
    ports, _head, mine = _world(tmp_path)
    head = ports.workspace.branch_commits[-1].sha
    ports.workspace.files[(head, mine)] = "# ADR-0079: mine\n"
    ports.workspace.first_dates[mine] = date(2026, 11, 3)

    _land(tmp_path, ports)

    [(_, relatives, _message)] = ports.workspace.path_commits
    assert "docs/adr/20261103-mine.md" in relatives
