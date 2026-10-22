"""What the `reflect` verb remembers and does by code (ADR-0056): the ledger keyed by door
with the decisions it read, a transient failure retried up to `REFLECT_ATTEMPTS`, a
candidate promoted keeping its `seen_in` and recorded as an `add`, and an item that
stopped applying archived, found by id, never when its pull request never merged. gh and
the runner are fakes; git, the files and the lock are real.

Seam: `reflect_run.reflect (with Ports of test and a fake model)`, `reflect_run.gather`."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic.core.process.application import reflect_run
from agentic.core.process.application.ports import ListedPullRequest
from agentic.core.process.domain import playbook as pb
from agentic.core.process.domain import playbook_candidates as pc
from tests.core.process.application.test_reflect import reflection
from tests.fakes import ScriptedRunner, memory_ports
from tests.test_reflect_cli import add_delta, git, product

pytestmark = pytest.mark.anyio

SINCE = "2026-10-01"
LESSON = "Run every declared check before delivering, not only the single test file."


def summarise(documents, playbooks, applied, gained):
    return "summary", []


async def run(cfg, ports, since: str | None = SINCE, task_id: str | None = None):
    return await reflect_run.reflect(
        cfg,
        ports,
        now="2026-11-01T00:00:00.000+00:00",
        summarise=summarise,
        since=since,
        task_id=task_id,
    )


def at_branch(root: Path, relative: str) -> str:
    return git(root, "show", f"{reflect_run.BRANCH}:{relative}")


def ledger_of(root: Path) -> pc.Ledger:
    return pc.parse_ledger(at_branch(root, pc.LEDGER_FILE))


async def test_a_transient_failure_is_retried_three_times_and_then_stays_failed(tmp_path):
    root, cfg, ports, _tracker = product(tmp_path, 1)
    boom = RuntimeError("harness down")
    ports = memory_ports(
        ScriptedRunner(reflector=[boom] * 6),
        documents=ports.documents,
        prompts=ports.prompts,
        workspace=ports.workspace,
        tracker=ports.tracker,
    )
    await run(cfg, ports)
    first = ledger_of(root).tasks["GH-1@review"]
    assert first.attempts == 1 and not first.failed
    assert [d["id"] for d in reflect_run.pending(cfg, ports, since=SINCE)] == ["GH-1"]
    assert pc.reflected_tasks(ledger_of(root)) == 0

    await run(cfg, ports)
    assert ledger_of(root).tasks["GH-1@review"].attempts == 2
    await run(cfg, ports)
    last = ledger_of(root).tasks["GH-1@review"]
    assert last.attempts == pc.REFLECT_ATTEMPTS and "harness down" in last.failed
    assert pc.reflected_tasks(ledger_of(root)) == 1
    calls = len(ports.runner.calls)
    outcome = await run(cfg, ports)
    assert outcome.reflected == 0 and len(ports.runner.calls) == calls
    assert not reflect_run.pending(cfg, ports, since=SINCE)


async def test_the_ledger_keys_by_door_and_remembers_how_many_decisions_it_read(tmp_path):
    root, cfg, ports, _tracker = product(tmp_path, 1)
    doc = ports.documents.load("GH-1")
    assert doc is not None
    doc["sections"] = {
        **doc["sections"],
        "Human": "### Decision 1\n\nuse snake_case\n\n### Decision 2\n\nkeep it short",
    }
    ports.documents.save(doc)
    ports.documents.record_closure(
        "GH-1", {"closed_at": "2026-10-01T09:00:00.000+00:00", "delivery_outcome": "merged"}
    )
    await run(cfg, ports)
    assert ledger_of(root).tasks["GH-1@review"].decisions == 2


def _item_file() -> str:
    lesson = "A hand edited lesson about running every declared check."
    item = pb.Item("developer-1", lesson, "standard-other", "empty", ("GH-90",), "2026-09-01")
    return pb.render_file(pb.Playbook("developer", (item,)))


def _put_playbook(root: Path, message: str) -> str:
    directory = root / pc.PLAYBOOKS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "developer.yaml").write_text(_item_file(), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def _aging_documents(ports, count: int) -> None:
    for n in range(1, count + 1):
        doc = ports.documents.load(f"GH-{n}")
        assert doc is not None
        doc["change_history"] = []  # nothing backs the item
        doc["prompts"] = {"developer": "h1"}
        doc["runs"] = [
            {"role": "developer", "ended_at": f"2099-01-{n:02d}T00:00:00.000+00:00", "cost_usd": 0}
        ]
        ports.documents.save(doc)
        ports.documents.record_closure(
            f"GH-{n}",
            {"closed_at": f"2026-10-{n:02d}T09:00:00.000+00:00", "delivery_outcome": "merged"},
        )


@pytest.mark.parametrize("merged", [True, False])
async def test_an_item_that_stopped_applying_is_archived_only_after_its_pr_merged(tmp_path, merged):
    root, cfg, ports, tracker = product(tmp_path, 24)
    _aging_documents(ports, 24)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "documents with runs")
    ports.runner.queues["reflector"] = [reflection(cost=0.1) for _ in range(10)]
    await run(cfg, ports)
    _, _, body = tracker.pull_requests[-1]
    if merged:
        # The PR merged with a hand edited lesson: the block's text no longer matches.
        commit = _put_playbook(root, "merge the playbook PR")
        pull = ListedPullRequest(7, tracker.url, "MERGED", body, commit, reflect_run.BRANCH)
    else:
        # The item sits on the open PR's own branch and nowhere else.
        git(root, "checkout", "-q", reflect_run.BRANCH)
        _put_playbook(root, "item on the open branch")
        git(root, "checkout", "-q", "main")
        pull = ListedPullRequest(7, tracker.url, "OPEN", body, "", reflect_run.BRANCH)
    tracker.listed = [pull]
    await run(cfg, ports)
    developer = pb.parse(at_branch(root, pc.playbook_file("developer")), "developer")
    if merged:
        assert developer.items == ()
        [row] = pc.parse_archived(at_branch(root, pc.ARCHIVED_FILE))
        assert row.item.id == "developer-1" and row.reason == "stale"
    else:
        assert [i.id for i in developer.items] == ["developer-1"]
        assert developer.items[0].in_prompt_since == ""


async def test_a_candidate_promoted_keeps_its_seen_in_and_is_recorded_as_an_add(tmp_path):
    root, cfg, ports, tracker = product(tmp_path, 1)
    waiting = pb.Pending(
        "developer", "standard-other", LESSON, "low", ("GH-90",), ("return",), "2026-09-01"
    )
    directory = root / pc.PLAYBOOKS_DIR
    directory.mkdir(parents=True)
    (directory / "candidates.yaml").write_text(pc.render_candidates([waiting]), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a waiting candidate")
    ports.runner.queues["reflector"] = [reflection(add_delta("GH-1"), cost=0.1)]
    await run(cfg, ports)
    developer = pb.parse(at_branch(root, pc.playbook_file("developer")), "developer")
    [item] = developer.items
    assert item.seen_in == ("GH-90", "GH-1")
    assert pc.parse_candidates(at_branch(root, pc.CANDIDATES_FILE)) == []
    [(_, _, body)] = tracker.pull_requests
    record = pc.parse_block(body)
    assert record is not None
    assert [(a.slug, a.category) for a in record.adds] == [("developer", "standard-other")]


async def test_gather_refuses_a_ledger_it_cannot_read_instead_of_guessing(tmp_path):
    root, cfg, ports, _tracker = product(tmp_path, 1)
    directory = root / pc.PLAYBOOKS_DIR
    directory.mkdir(parents=True)
    (directory / "reflected.yaml").write_text("tasks: [not, a, mapping]", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "broken ledger")
    with pytest.raises(ValueError, match="reflected.yaml"):
        reflect_run.gather(cfg, ports)
