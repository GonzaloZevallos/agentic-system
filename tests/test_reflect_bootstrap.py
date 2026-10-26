"""A product with no ledger starts reflecting by itself (ADR-0056 addendum): the first pass
reaches back over the `reflect_max_tasks_per_run` most recent documents, `auto_merged` ones
included, `--since` with no ledger starts the ledger at `since`, an environmental failure
stops the batch without costing an attempt, and a bug propagates. The poller launches, the
ledger is written, and the lesson reaches the role's prompt once the playbook PR is merged.
gh and the runner are fakes; git, the files and the lock are real.

Seams: `reflect_run.pending`, `reflect_run.reflect`, `poll.reflect_lines`."""

from __future__ import annotations

import pytest

from agentic.core.process.adapters.filesystem.prompts import FilesystemPrompts
from agentic.core.process.application import poll, reflect_run
from agentic.core.process.application import reflect as reflect_module
from agentic.core.process.application.ports import IssueState
from agentic.core.process.application.preflight import PLACEHOLDERS
from agentic.core.process.application.run_role import load_prompt
from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import playbook_candidates as pc
from agentic.core.process.domain.roles import DEVELOPER, prompt_hash
from tests.core.process.application.test_reflect import reflection
from tests.fakes import FakeLauncher
from tests.test_reflect_cli import CLOSED, LESSON, add_delta, git, product

pytestmark = pytest.mark.anyio

SINCE = "2026-10-01"


def summarise(documents, playbooks, applied, gained):
    return "summary", []


async def run(cfg, ports, since=None):
    return await reflect_run.reflect(
        cfg, ports, now="2026-11-01T00:00:00.000+00:00", summarise=summarise, since=since
    )


def ids(documents) -> list[str]:
    return [d["id"] for d in documents]


def ledger_of(root) -> pc.Ledger:
    return pc.parse_ledger(git(root, "show", f"{reflect_run.BRANCH}:{pc.LEDGER_FILE}"))


def test_with_no_ledger_an_auto_merged_task_is_pending(tmp_path):
    _root, cfg, ports, _ = product(tmp_path, 1)
    ports.documents.record_closure(
        "GH-1", {"closed_at": CLOSED.format(day=1), "delivery_outcome": "auto_merged"}
    )
    assert ids(reflect_run.pending(cfg, ports)) == ["GH-1"]


def test_with_no_ledger_a_closed_escalation_with_a_decision_is_pending(tmp_path):
    _root, cfg, ports, tracker = product(tmp_path, 0)
    ports.documents.save(
        {
            "id": "GH-7",
            "title": "t",
            "description": "d",
            "type": "feature",
            "status": "waiting_on_human",
            "runs": [
                {"role": "developer", "cost_usd": 0.1, "ended_at": "2026-10-05T09:00:00.000+00:00"}
            ],
            "sections": {"Human": "### Decision 1\n\nUse snake_case in the docs."},
        }
    )
    tracker.states[7] = IssueState("OPEN", "", (), None)
    assert reflect_run.pending(cfg, ports) == []
    tracker.states[7] = IssueState("CLOSED", "COMPLETED", (), None)
    assert ids(reflect_run.pending(cfg, ports)) == ["GH-7"]


def test_with_no_ledger_and_more_documents_than_the_cap_pending_is_the_n_most_recent(tmp_path):
    _root, cfg, ports, _ = product(tmp_path, 8)
    assert cfg.reflect_max_tasks_per_run == 5
    assert ids(reflect_run.pending(cfg, ports)) == [f"GH-{n}" for n in range(4, 9)]


async def test_since_with_no_ledger_starts_the_ledger_at_since_and_the_rest_follows(tmp_path):
    root, cfg, ports, _ = product(tmp_path, 8)
    outcome = await run(cfg, ports, since=SINCE)
    assert outcome.reflected == 5 and outcome.left == 3
    assert ledger_of(root).started_at == SINCE
    assert ids(reflect_run.pending(cfg, ports)) == ["GH-6", "GH-7", "GH-8"]


async def test_an_environmental_failure_stops_the_batch_and_costs_no_attempt(tmp_path, capsys):
    root, cfg, ports, _ = product(tmp_path, 3)
    ports.runner.queues["reflector"] = [
        reflection(add_delta("GH-1"), cost=0.3),
        OSError("disk full"),
        reflection(add_delta("GH-3"), cost=0.3),
    ]
    outcome = await run(cfg, ports, since=SINCE)
    assert outcome.reflected == 1 and outcome.left == 2  # only what finished counts
    assert "GH-2 stopped by OSError; its cost was not recorded" in capsys.readouterr().err
    assert list(ledger_of(root).tasks) == ["GH-1@review"]
    assert ids(reflect_run.pending(cfg, ports)) == ["GH-2", "GH-3"]
    assert len(ports.runner.calls) == 2  # the third document was never asked


async def test_a_bug_propagates_and_writes_no_ledger(tmp_path, monkeypatch):
    root, cfg, ports, tracker = product(tmp_path, 3)
    real = reflect_run.reflect_task
    calls: list[str] = []

    async def broken(*args, **kwargs):
        calls.append(str(args[2]["id"]))
        if len(calls) == 2:
            raise KeyError("a bug")
        return await real(*args, **kwargs)

    monkeypatch.setattr(reflect_run, "reflect_task", broken)
    with pytest.raises(KeyError):
        await run(cfg, ports, since=SINCE)
    assert pc.LEDGER_FILE not in git(root, "ls-tree", "-r", "--name-only", reflect_run.BRANCH)
    assert tracker.pull_requests == []


async def test_the_poller_starts_a_product_with_no_ledger_and_the_lesson_reaches_the_prompt(
    tmp_path,
):
    root, cfg, ports, tracker = product(tmp_path, 8)
    ports.runner.queues["reflector"] = [
        reflection(add_delta(f"GH-{n}"), cost=0.3) for n in range(4, 9)
    ]
    launcher = FakeLauncher()
    lines = poll.reflect_lines(cfg, ports, launcher, "agentic")
    assert lines == ["agentic: poll: reflect 5 task(s) -> pid 4242"]  # 1: the poller launches
    assert launcher.spawned == [["agentic", "reflect", "--all", "--product-dir", str(root)]]

    await run(cfg, ports)
    assert ledger_of(root).started_at == CLOSED.format(day=4)  # 2: the floor, never now
    assert reflect_run.pending(cfg, ports) == []  # 3: the rest of the pass is done

    # 4: the person merges the playbook PR; the prompt the role is rendered with then
    # carries the lesson, and its hash moves.
    def prompt():
        return load_prompt(DEVELOPER, cfg, ports, ports.flags.load(), **PLACEHOLDERS)

    empty = prompt()
    assert empty.playbook == ""
    git(root, "merge", "-q", "--ff-only", reflect_run.BRANCH)
    merged = prompt()
    section = pb.section(FilesystemPrompts(root).playbook(DEVELOPER))
    assert LESSON in section
    assert merged.text == empty.text + "\n\n" + section
    assert merged.hash != empty.hash
    assert merged.playbook == prompt_hash(section)


async def test_a_bug_inside_the_reflection_is_not_a_failed_attempt(tmp_path, monkeypatch):
    """The net of `reflect_task` is the role's own failure: a bug in the code that folds
    the model's deltas stops the run and writes no ledger entry or attempt."""
    root, cfg, ports, tracker = product(tmp_path, 2)

    def broken(*args, **kwargs):
        raise KeyError("a bug")

    monkeypatch.setattr(reflect_module, "_fold", broken)
    with pytest.raises(KeyError):
        await run(cfg, ports, since=SINCE)
    assert pc.LEDGER_FILE not in git(root, "ls-tree", "-r", "--name-only", reflect_run.BRANCH)
    assert tracker.pull_requests == []
