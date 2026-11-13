"""`agentic poll --dry-run --once` (GH-622, part 1b): one round from reads only.

Seams: `cli._poll` (the dry round end to end, over the real filesystem lock, registry, scratch
and documents under `tmp_path`, and doubles that raise on any write) and `cli.main`'s parser."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic import cli
from agentic.core.process.adapters.filesystem.documents import FilesystemTaskDocuments
from agentic.core.process.adapters.filesystem.lock import FilesystemRunLock
from agentic.core.process.adapters.filesystem.run_registry import FilesystemRunRegistry
from agentic.core.process.adapters.filesystem.scratch import FilesystemScratch
from agentic.core.process.application import dry_run, poll
from agentic.core.process.application.ports import IssueState, TrackerCallFailed
from agentic.core.process.domain import rules
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryInbox, MemoryTracker, MemoryWorkspace, memory_ports

BANNER = "dry-run: plan from reads only; effects of this round's own writes are not simulated"
SKIPPED = [f"dry-run: skipped {name}" for name in ("close_issues", "scratch", "ci_refs", "board")]
MERGED = {
    "delivery_outcome": "auto_merged",
    "merge_commit": "7a22ebfd" + "0" * 32,
    "closed_at": "2026-10-25T14:44:55Z",
    "pull_request": "https://github.com/o/r/pull/377",
}
OPEN = IssueState("OPEN", "", None, None)


class Guarded:
    """A double that fails the test on a member the dry round must never reach, and records the
    members it did: `calls`."""

    def __init__(self, inner: object, port: str) -> None:
        self._inner = inner
        self._forbidden = (
            dry_run.NONE_WRITES.get(port, frozenset())
            | frozenset(dry_run.WRITES_VALUED.get(port, {}))
            | dry_run.UNREACHED.get(port, frozenset())
        )
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        if name in self._forbidden:
            raise AssertionError(f"a write reached the adapter: {name}")
        self.calls.append(name)
        return getattr(self._inner, name)


class World:
    def __init__(self, tmp_path: Path, spend_pacing: bool = False) -> None:
        self.root = tmp_path / "product"
        self.root.mkdir()
        self.cfg = Configuration(product_dir=self.root, spend_pacing=spend_pacing)
        self.lock = FilesystemRunLock(tmp_path / "runs")
        self.registry = FilesystemRunRegistry(tmp_path / "registry")
        self.tracker = MemoryTracker()
        self.inbox = MemoryInbox()
        self.workspace = Guarded(MemoryWorkspace(), "Workspace")
        self.documents = FilesystemTaskDocuments(self.root)
        self.scratch = FilesystemScratch(tmp_path / "scratch", "product", self.root)
        self.tmp = tmp_path

    def issue(self, number: int, body: str = "a plain description") -> None:
        self.inbox.issues[str(number)] = {"id": f"GH-{number}", "title": "t", "description": body}
        self.tracker.states[number] = OPEN
        self.tracker._queue.append(number)

    def ports(self):
        return memory_ports(
            tracker=Guarded(self.tracker, "Tracker"),
            inbox=Guarded(self.inbox, "Inbox"),
            workspace=self.workspace,
            documents=self.documents,
            scratch=self.scratch,
        )

    def tree(self) -> dict[str, int]:
        everything = [self.tmp, *sorted(self.tmp.rglob("*"))]
        return {str(p): p.stat().st_mtime_ns for p in everything}

    def seed(self) -> None:
        """What a live poller leaves on disk: a dead lock, a launch attempt, a spend file."""
        gone = subprocess.Popen(["true"])
        gone.wait()
        runs = self.tmp / "runs"
        runs.mkdir(exist_ok=True)
        record = {
            "pid": gone.pid,
            "host": FilesystemRunLock(runs)._host(),
            "started_at": "2026-11-01T00:00:00+00:00",
            "command": "agentic run --issue 9",
        }
        (runs / "GH-9.lock").write_text(json.dumps(record))
        self.documents.save({"id": "GH-9", "title": "t", "description": "d"})
        now = datetime.now(UTC)
        self.registry.record_launch("product", 6, now - timedelta(minutes=1))
        spent = [rules.SpendRun(now - timedelta(hours=2), now - timedelta(hours=1), 3.0)]
        self.registry.publish_spend(self.root / "tasks", "product", spent, now)


async def dry(world: World, capsys: pytest.CaptureFixture[str]) -> tuple[list[str], int]:
    """The printed lines of one dry round and its exit code."""
    code = 0
    try:
        await cli._poll(world.cfg, world.ports(), 60.0, world.lock, world.registry, dry_run=True)
    except SystemExit as exit_:
        code = int(exit_.code or 0)
    return capsys.readouterr().out.splitlines(), code


@pytest.mark.anyio
@pytest.mark.parametrize("pacing", [False, True])
async def test_a_dry_round_changes_nothing_on_disk_and_says_what_it_would_do(
    tmp_path, capsys, pacing
):
    world = World(tmp_path, spend_pacing=pacing)
    world.issue(5)
    world.issue(6)
    world.seed()
    before = world.tree()

    lines, code = await dry(world, capsys)

    assert code == 0
    assert world.tree() == before
    assert lines[0] == BANNER
    assert lines[1 : 1 + len(SKIPPED)] == SKIPPED
    assert "would launch issue 5" in lines
    assert not any(line.startswith("dry-run: unclassified") for line in lines)
    assert "upstream" in world.workspace.calls  # the declared exception: the fetch is a read
    assert world.tracker.closed_issues == []


@pytest.mark.anyio
async def test_a_blocker_the_live_sweep_would_close_stays_open_and_its_dependent_waits(
    tmp_path, capsys
):
    """GH-348, the documented limit: the sweep that closes #7 this round is skipped."""
    world = World(tmp_path)
    world.documents.save({"id": "GH-7", "title": "t", "description": "d", **MERGED})
    world.issue(7)
    world.issue(8, "Blocked by: #7")

    lines, code = await dry(world, capsys)

    assert code == 0
    assert not any(line.startswith("would tracker.close_issue") for line in lines)
    assert not any(line.startswith("would launch") for line in lines)
    assert world.tracker.closed_issues == []
    assert "dry-run: skipped close_issues" in lines


def _touching_an_unclassified_name(monkeypatch, swallow: type[BaseException]) -> None:
    real = poll.gather_resumes

    def gather(ports, *args, **kwargs):
        try:
            ports.commands.nonsense()
        except swallow:
            pass
        return real(ports, *args, **kwargs)

    monkeypatch.setattr(poll, "gather_resumes", gather)


@pytest.mark.anyio
@pytest.mark.parametrize("swallow", [ValueError, Exception])
async def test_a_refused_call_exits_1_whichever_handler_stands_around_it(
    tmp_path, capsys, monkeypatch, swallow
):
    """A narrow handler lets the refusal through; a broad one swallows it, and the record of it
    survives."""
    _touching_an_unclassified_name(monkeypatch, swallow)
    world = World(tmp_path)
    world.issue(5)
    before = world.tree()

    lines, code = await dry(world, capsys)

    assert code == 1
    assert lines[-1] == "dry-run: unclassified commands.nonsense"
    assert world.tree() == before


@pytest.mark.anyio
async def test_a_stalled_tracker_ends_the_round_early_with_exit_1(tmp_path, capsys, monkeypatch):
    def failing(*args, **kwargs):
        raise TrackerCallFailed("gh issue list timed out")

    monkeypatch.setattr(poll, "run_once", failing)
    world = World(tmp_path)

    lines, code = await dry(world, capsys)

    assert code == 1
    assert lines == [BANNER, "agentic: poll: round ended early: gh issue list timed out"]


@pytest.mark.anyio
async def test_a_dry_round_takes_no_lock_spawns_nothing_and_never_sleeps(
    tmp_path, capsys, monkeypatch
):
    def no_spawn(self, argv):
        raise AssertionError("a dry round spawns nothing")

    monkeypatch.setattr(cli.SpawnLauncher, "spawn", no_spawn)
    world = World(tmp_path)
    world.issue(5)

    async def no_sleep(seconds: float) -> None:
        raise AssertionError("a dry round does not sleep")

    await cli._poll(
        world.cfg, world.ports(), 60.0, world.lock, world.registry, dry_run=True, sleep=no_sleep
    )

    assert not (tmp_path / "runs").exists()  # `_locked` would have written poll's lock here
    assert "would launch issue 5" in capsys.readouterr().out


def _main(monkeypatch, capsys, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "_replace_environ", lambda env: None)
    monkeypatch.setattr("sys.argv", ["agentic", *argv])
    with pytest.raises(SystemExit) as info:
        cli.main()
    captured = capsys.readouterr()
    return int(info.value.code or 0), captured.out, captured.err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--once"], "go together"),
        (["--dry-run"], "go together"),
        (["--dry-run", "--once", "--detach"], "exclusive"),
    ],
)
def test_poll_refuses_flags_that_do_not_go_together_with_exit_2(
    monkeypatch, capsys, tmp_path, argv, message
):
    code, _, err = _main(monkeypatch, capsys, "poll", "--product-dir", str(tmp_path), *argv)
    assert code == 2
    assert message in err
    assert not (tmp_path / ".agentic").exists()


def test_poll_help_names_the_limits_of_a_dry_round(monkeypatch, capsys):
    code, out, _ = _main(monkeypatch, capsys, "poll", "--help")
    flat = " ".join(out.split())
    assert code == 0
    for words in ("upstream fetch", "sweeps are skipped", "GH-348", "exempt stamp"):
        assert words in flat
