"""What the `reflect` verb does with a lesson for the system, and the poller's decision door
(ADR-0056, ADR-0002): a `scope: system` lesson becomes one issue in the system's repo with
`needs-triage` and never `agentic`, never a playbook line nor a file of that repo; the same
lesson twice opens one issue; a failure to open it does not fail the product's reflection
and is retried; an escalated task is eligible only once its issue is closed. gh and the
runner are fakes; git, the files and the lock are real.

Seams: `reflect_run.reflect (with two fake trackers)`, `reflect_run.pending`,
`poll.reflect_lines`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentic.core.process.application import poll, reflect_run
from agentic.core.process.application.ports import IssueState
from agentic.core.process.domain import playbook_candidates as pc
from agentic.shared.configuration import Configuration
from tests.core.process.application.test_reflect import reflection
from tests.fakes import FakeLauncher, MemoryInbox, MemoryTracker, memory_ports
from tests.test_reflect_cli import add_delta, git, product

pytestmark = pytest.mark.anyio

SINCE = "2026-10-01"
SYSTEM_LESSON = "The reflector prompt should say which source a lesson came from."
SHOUTED = "THE reflector prompt  should say which source a lesson came from."
OPEN = IssueState("OPEN", "", (), None)
CLOSED = IssueState("CLOSED", "COMPLETED", (), None)


def summarise(documents, playbooks, applied, gained):
    return "summary", []


def system_delta(task: str, lesson: str = SYSTEM_LESSON, **extra: Any) -> dict[str, Any]:
    return {**add_delta(task), "lesson": lesson, "scope": "system", "confidence": "high", **extra}


def system_repo(inbox: MemoryInbox | None = None):
    tracker = MemoryTracker("https://github.com/o/system/issues/1")
    return reflect_run.SystemRepo(tracker, inbox or MemoryInbox()), tracker


async def run(cfg, ports, system, task_id: str | None = None):
    return await reflect_run.reflect(
        cfg,
        ports,
        now="2026-11-01T00:00:00.000+00:00",
        summarise=summarise,
        since=SINCE,
        task_id=task_id,
        system=system,
    )


def at_branch(root: Path, relative: str) -> str:
    return git(root, "show", f"{reflect_run.BRANCH}:{relative}")


def files_of_branch(root: Path) -> list[str]:
    return git(root, "ls-tree", "-r", "--name-only", reflect_run.BRANCH).splitlines()


def has_playbook_file(root: Path) -> bool:
    return any(f.endswith("developer.yaml") for f in files_of_branch(root))


async def test_a_system_lesson_opens_one_needs_triage_issue_and_writes_no_playbook(tmp_path):
    root, cfg, ports, product_tracker = product(tmp_path, 1)
    ports.runner.queues["reflector"] = [reflection(system_delta("GH-1"), cost=0.1)]
    system, system_tracker = system_repo()
    await run(cfg, ports, system)
    [(title, body)] = system_tracker.opened_issues
    assert system_tracker.opened_labels == ["needs-triage"]
    assert SYSTEM_LESSON.lower() in body.lower() and "GH-1" in body
    assert "reflect" in title.lower()
    # nothing of the lesson reached a playbook, a candidate or the product's own issues
    assert not has_playbook_file(root)
    assert pc.CANDIDATES_FILE not in files_of_branch(root)
    assert product_tracker.opened_issues == []
    entry = pc.parse_ledger(at_branch(root, pc.LEDGER_FILE)).tasks["GH-1@review"]
    assert [d["scope"] for d in entry.applied] == ["system"]
    [(_, _, pr_body)] = product_tracker.pull_requests
    record = pc.parse_block(pr_body)
    assert record is not None and record.adds == ()


async def test_the_same_lesson_in_two_runs_opens_a_single_issue(tmp_path):
    root, cfg, ports, _ = product(tmp_path, 2)
    ports.runner.queues["reflector"] = [
        reflection(system_delta("GH-1"), cost=0.1),
        reflection(system_delta("GH-2", SHOUTED), cost=0.1),
    ]
    system, system_tracker = system_repo()
    await run(cfg, ports, system, task_id="GH-1")
    await run(cfg, ports, system, task_id="GH-2")
    assert len(system_tracker.opened_issues) == 1


async def test_a_lesson_already_in_an_open_issue_is_not_proposed_again(tmp_path):
    root, cfg, ports, _ = product(tmp_path, 1)
    ports.runner.queues["reflector"] = [reflection(system_delta("GH-1"), cost=0.1)]
    first, first_tracker = system_repo()
    await run(cfg, ports, first)
    [(_, body)] = first_tracker.opened_issues
    # a fresh clone of the ledger knows nothing: the open issue's marker is what remembers
    git(root, "branch", "-D", reflect_run.BRANCH)
    ports.runner.queues["reflector"] = [reflection(system_delta("GH-1"), cost=0.1)]
    second, second_tracker = system_repo(MemoryInbox(open_issues=[("https://x/issues/9", body)]))
    await run(cfg, ports, second)
    assert second_tracker.opened_issues == []


async def test_a_low_confidence_system_lesson_and_one_with_a_url_open_nothing(tmp_path):
    root, cfg, ports, _ = product(tmp_path, 2)
    ports.runner.queues["reflector"] = [
        reflection(system_delta("GH-1", confidence="low"), cost=0.1),
        reflection(system_delta("GH-2", "See https://example.com for the rule."), cost=0.1),
    ]
    system, system_tracker = system_repo()
    await run(cfg, ports, system)
    assert system_tracker.opened_issues == []
    assert not has_playbook_file(root)


async def test_a_failure_to_open_the_issue_keeps_the_reflection_and_is_retried(tmp_path, capsys):
    root, cfg, ports, _ = product(tmp_path, 2)
    ports.runner.queues["reflector"] = [
        reflection(system_delta("GH-1"), cost=0.1),
        reflection(cost=0.1),
    ]
    system, system_tracker = system_repo()
    system_tracker.open_issue_fails_on = {0}
    outcome = await run(cfg, ports, system, task_id="GH-1")
    assert outcome.reflected == 1 and system_tracker.opened_issues == []
    assert "could not open" in capsys.readouterr().err
    ledger = pc.parse_ledger(at_branch(root, pc.LEDGER_FILE))
    assert not ledger.tasks["GH-1@review"].failed
    assert pc.reflected_tasks(ledger) == 1
    # the next run, for another task, files the lesson that was left
    await run(cfg, ports, system, task_id="GH-2")
    [(_, body)] = system_tracker.opened_issues
    assert SYSTEM_LESSON.lower() in body.lower()
    again = await run(cfg, ports, system)
    assert again.reflected == 0 and len(system_tracker.opened_issues) == 1


async def test_a_system_lesson_that_is_not_an_add_is_refused_not_written(tmp_path):
    root, cfg, ports, _ = product(tmp_path, 1)
    merge = {**system_delta("GH-1"), "op": "merge", "item_id": "developer-1"}
    ports.runner.queues["reflector"] = [reflection(merge, cost=0.1)]
    system, system_tracker = system_repo()
    await run(cfg, ports, system)
    assert system_tracker.opened_issues == []
    assert not has_playbook_file(root)


async def test_without_a_system_repo_a_system_lesson_waits_and_writes_nothing(tmp_path):
    root, cfg, ports, _ = product(tmp_path, 1)
    ports.runner.queues["reflector"] = [reflection(system_delta("GH-1"), cost=0.1)]
    await reflect_run.reflect(
        cfg, ports, now="2026-11-01T00:00:00.000+00:00", summarise=summarise, since=SINCE
    )
    assert not has_playbook_file(root)
    entry = pc.parse_ledger(at_branch(root, pc.LEDGER_FILE)).tasks["GH-1@review"]
    assert entry.applied[0]["filed"] is False


def _escalated(ports, number: int, state: IssueState) -> None:
    ports.documents.save(
        {
            "id": f"GH-{number}",
            "title": "t",
            "description": "d",
            "type": "feature",
            "status": "waiting_on_human",
            "runs": [{"role": "developer", "ended_at": "2026-10-05T09:00:00.000+00:00"}],
            "sections": {"Human": "### Decision 1\n\nUse snake_case in the docs."},
        }
    )
    ports.tracker.states[number] = state


def _started(ports) -> None:
    ports.workspace.files[("HEAD", pc.LEDGER_FILE)] = pc.render_ledger(
        pc.Ledger(started_at="2026-10-01T00:00:00+00:00")
    )


def test_an_escalation_is_eligible_only_once_its_issue_is_closed(tmp_path):
    ports = memory_ports()
    _started(ports)
    _escalated(ports, 7, OPEN)
    cfg = Configuration(product_dir=tmp_path)
    assert reflect_run.pending(cfg, ports) == []
    ports.tracker.states[7] = CLOSED
    assert [d["id"] for d in reflect_run.pending(cfg, ports)] == ["GH-7"]
    ports.tracker.state_errors.add(7)  # gh fails: nothing to reflect on
    assert reflect_run.pending(cfg, ports) == []


def test_the_poller_launches_reflect_for_a_closed_escalation_and_not_for_an_open_one(tmp_path):
    ports = memory_ports()
    _started(ports)
    _escalated(ports, 7, OPEN)
    cfg = Configuration(product_dir=tmp_path)
    launcher = FakeLauncher()
    assert poll.reflect_lines(cfg, ports, launcher, "agentic") == []
    ports.tracker.states[7] = CLOSED
    lines = poll.reflect_lines(cfg, ports, launcher, "agentic")
    assert lines == ["agentic: poll: reflect 1 task(s) -> pid 4242"]
    assert launcher.spawned == [["agentic", "reflect", "--all", "--product-dir", str(tmp_path)]]
