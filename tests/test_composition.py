"""The checkpointer the CLI opens: a real SQLite file in the product (ADR-0003)."""

import asyncio
import sqlite3
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agentic import composition
from agentic.core.process.adapters.filesystem.free_space import StatvfsFreeSpace
from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.shell import EventStopFlag, NullStopFlag
from agentic.core.process.application import preflight
from agentic.core.process.application.ports import RunStopped
from agentic.core.process.application.preflight import RunsOnItsOwnCheckout
from agentic.core.process.domain import reaping
from agentic.core.process.domain.task import TaskState
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryCommands

pytestmark = pytest.mark.anyio


async def test_a_pause_survives_the_process_in_the_products_state_file(tmp_path):
    # `TaskState` is plain JSON, so the saver keeps it whole; a second connection, as a
    # later `agentic resume` would open, finds the pause and resumes it.
    def pause(state: TaskState) -> TaskState:
        answer = interrupt({"reason": state.get("escalated")})
        return {"escalated": None, "sections": {"Human": answer["decision"]}}

    graph = StateGraph(TaskState)
    graph.add_node("pause", pause)
    graph.add_edge(START, "pause")
    graph.add_edge("pause", END)
    cfg = Configuration(product_dir=tmp_path)
    config = {"configurable": {"thread_id": "GH-1"}}
    state = {"id": "GH-1", "title": "t", "description": "d", "escalated": "cap", "cost_usd": 1.5}

    async with composition.checkpointer(cfg, "GH-1") as saver:
        await graph.compile(checkpointer=saver).ainvoke(state, config)
    own_file = tmp_path / ".agentic/state/GH-1.sqlite"
    assert own_file.is_file()
    async with composition.checkpointer(cfg, "GH-1") as saver:
        app = graph.compile(checkpointer=saver)
        assert [i.value for i in (await app.aget_state(config)).interrupts] == [{"reason": "cap"}]
        final = await app.ainvoke(Command(resume={"decision": "go"}), config)
    assert final["escalated"] is None and final["sections"] == {"Human": "go"}
    assert final["cost_usd"] == 1.5


async def _db_path(saver) -> str:
    """The file SQLite itself reports for `saver`'s own connection: never recomputed
    with `state_file_for`, which would prove nothing about where the connection
    actually lives (invariant: every isolation test reads the store path off the open
    connection)."""
    cursor = await saver.conn.execute("PRAGMA database_list")
    (_, _, file) = (await cursor.fetchall())[0]
    return file


async def _aput(saver, thread_id: str) -> None:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    await saver.aput(config, empty_checkpoint(), {"source": "update", "step": 1, "parents": {}}, {})


async def _seed_legacy(path: Path, thread_id: str) -> None:
    """A real checkpoint for `thread_id`, written with a real `AsyncSqliteSaver` on
    `path` (invariant: a migration claim is proven over real SQLite, never the
    `InMemorySaver` double)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await _aput(saver, thread_id)


async def test_two_tasks_report_different_database_files(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    async with composition.checkpointer(cfg, "GH-1") as saver1:
        path1 = await _db_path(saver1)
    async with composition.checkpointer(cfg, "GH-2") as saver2:
        path2 = await _db_path(saver2)
    assert path1 != path2
    assert Path(path1).resolve() == (tmp_path / ".agentic/state/GH-1.sqlite").resolve()
    assert Path(path2).resolve() == (tmp_path / ".agentic/state/GH-2.sqlite").resolve()


async def test_concurrent_aputs_for_two_tasks_both_complete(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    async with (
        composition.checkpointer(cfg, "GH-1") as saver1,
        composition.checkpointer(cfg, "GH-2") as saver2,
    ):
        await asyncio.gather(_aput(saver1, "GH-1"), _aput(saver2, "GH-2"))


async def test_a_lock_on_one_tasks_file_never_blocks_the_other(tmp_path):
    """Negative control: a raw connection holds `BEGIN IMMEDIATE` on GH-1's own file.
    The lock is released only once this test's own two actions are both done --
    never on a timeout racing either of them, so a routing regression cannot pass by
    outlasting a bounded wait.

    GH-2's `aput` lands on a different file and completes while the lock is still
    held: the input that must succeed. An `aput` aimed straight at GH-1's own file,
    the lock still held, fails with `database is locked`: the input that must fail,
    proving the lock is real and that GH-2 succeeding actually means something."""
    cfg = Configuration(product_dir=tmp_path)
    async with composition.checkpointer(cfg, "GH-1") as saver1:
        path1 = await _db_path(saver1)

    holding = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        conn = sqlite3.connect(path1, isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS _lock_probe (x int)")
        holding.set()
        release.wait()
        conn.execute("ROLLBACK")
        conn.close()

    thread = threading.Thread(target=hold_lock)
    thread.start()
    try:
        holding.wait()
        # Must succeed: a different file, never contended by GH-1's lock.
        async with composition.checkpointer(cfg, "GH-2") as saver2:
            await _aput(saver2, "GH-2")
        # Must fail: GH-1's own file, still locked by the thread above.
        async with AsyncSqliteSaver.from_conn_string(path1) as locked_saver:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                await _aput(locked_saver, "GH-1")
    finally:
        release.set()
        thread.join()


async def test_a_task_already_in_the_legacy_file_keeps_using_it(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    legacy = tmp_path / ".agentic/state.sqlite"
    await _seed_legacy(legacy, "GH-1")

    async with composition.checkpointer(cfg, "GH-1") as saver1:
        path1 = await _db_path(saver1)
    assert Path(path1).resolve() == legacy.resolve()

    async with composition.checkpointer(cfg, "GH-2") as saver2:
        path2 = await _db_path(saver2)
    assert Path(path2).resolve() == (tmp_path / ".agentic/state/GH-2.sqlite").resolve()


async def test_with_no_legacy_file_asking_for_a_checkpointer_creates_none(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    async with composition.checkpointer(cfg, "GH-1"):
        pass
    assert not (tmp_path / ".agentic/state.sqlite").exists()


async def test_a_legacy_file_with_no_checkpoints_table_resolves_to_the_tasks_own_file(tmp_path):
    cfg = Configuration(product_dir=tmp_path)
    legacy = tmp_path / ".agentic/state.sqlite"
    legacy.parent.mkdir(parents=True)
    sqlite3.connect(legacy).close()  # exists, but never had a `checkpoints` table
    async with composition.checkpointer(cfg, "GH-1") as saver:
        path = await _db_path(saver)
    assert Path(path).resolve() == (tmp_path / ".agentic/state/GH-1.sqlite").resolve()


async def test_a_product_dir_with_hash_and_percent_still_resolves_the_legacy_uri(tmp_path):
    """Invariant: the routing lookup's URI is built with `Path.as_uri()` on the
    resolved path, never an f-string -- both `#` and `%` would otherwise corrupt a
    hand-built `sqlite:///...` string."""
    product_dir = tmp_path / "c#p%20q"
    product_dir.mkdir()
    cfg = Configuration(product_dir=product_dir)
    legacy = product_dir / ".agentic/state.sqlite"
    await _seed_legacy(legacy, "GH-1")

    async with composition.checkpointer(cfg, "GH-1") as saver1:
        path1 = await _db_path(saver1)
    assert Path(path1).resolve() == legacy.resolve()

    async with composition.checkpointer(cfg, "GH-2") as saver2:
        path2 = await _db_path(saver2)
    assert Path(path2).resolve() == (product_dir / ".agentic/state/GH-2.sqlite").resolve()


def a_repository(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_the_ports_of_a_product_in_the_running_repository_are_refused(tmp_path, monkeypatch):
    """The guard cannot live in the CLI alone: `langgraph dev`, Studio and the LangGraph
    API import the wired graph and reach `default_ports` without ever passing there
    (issue #44). This is the door they all share."""
    repo = a_repository(tmp_path / "system")
    monkeypatch.setattr(preflight, "CHECKOUT", repo)
    with pytest.raises(RunsOnItsOwnCheckout):
        composition.default_ports(Configuration(product_dir=repo))


def test_the_ports_of_a_product_of_its_own_are_built(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    assert composition.default_ports(Configuration(product_dir=product)).documents.product_dir


def test_with_no_log_path_the_runner_opens_no_file(tmp_path, monkeypatch):
    # The `langgraph dev` door: `configurable` never carries `log_file`, so `cfg.log_file`
    # is `None` and the runner is built exactly as before this task (issue #33).
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    ports = composition.default_ports(Configuration(product_dir=product))
    assert ports.runner.stream is None
    assert list((product / ".agentic").rglob("*.log")) == []


def test_with_a_log_path_the_runner_gets_an_open_append_only_handle(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    log_file = product / ".agentic/logs/GH-1-20260921T000000Z.log"
    log_file.parent.mkdir(parents=True)
    ports = composition.default_ports(Configuration(product_dir=product, log_file=log_file))
    try:
        assert ports.runner.stream is not None
        assert ports.runner.stream.mode == "a"
        ports.runner.stream.write("hello\n")
        ports.runner.stream.flush()
        assert log_file.read_text() == "hello\n"
    finally:
        ports.runner.stream.close()


def test_ports_built_without_a_flag_get_one_that_never_stops_a_command(tmp_path, monkeypatch):
    """Only `run`/`resume` pass a markable flag: the ports of every other verb hold one that
    `set()` cannot mark, so `ShellCommands.run` can never raise `RunStopped` for them."""
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    made: list[NullStopFlag] = []

    class Spy(NullStopFlag):
        def __init__(self) -> None:
            made.append(self)

    monkeypatch.setattr(composition, "NullStopFlag", Spy)
    ports = composition.default_ports(Configuration(product_dir=product))
    (flag,) = made
    flag.set()
    assert not flag.is_set()
    assert ports.commands.run(product, "echo hi", timeout_s=10).passed


def test_a_markable_flag_passed_to_the_composition_reaches_the_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    stop = EventStopFlag()
    ports = composition.default_ports(Configuration(product_dir=product), stop)
    stop.set()
    with pytest.raises(RunStopped):
        ports.commands.run(product, "echo hi", timeout_s=10)


def test_a_markable_flag_passed_to_the_composition_reaches_the_perf_runner_and_lock(
    tmp_path, monkeypatch
):
    """GH-385: the run's flag is what stops a measurement; this fails if either perf adapter is
    built on a flag of its own."""
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    cfg = Configuration(product_dir=product, perf_lock_path=tmp_path / "perf.lock")
    flag = EventStopFlag()
    ports = composition.default_ports(cfg, flag)
    assert ports.perf_runner is not None and ports.perf_lock is not None
    flag.set()
    with pytest.raises(RunStopped):
        ports.perf_runner.run(product, f"touch {product}/started", {}, 10)
    with pytest.raises(RunStopped):
        ports.perf_lock.acquire(0)
    assert not (product / "started").exists()
    assert not cfg.perf_lock_path.exists()


def test_the_perf_verbs_builders_pass_a_flag_that_never_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    cfg = Configuration(product_dir=product, perf_lock_path=tmp_path / "perf.lock")
    ports = composition.default_ports(cfg, NullStopFlag())
    runner, lock = composition.perf_runner(cfg, ports), composition.perf_lock(cfg)
    assert runner.run(product, "true", {}, 10).exit_code == 0
    assert lock.acquire(0) is True
    lock.release()


def test_the_composition_builds_the_scratch_and_free_space_adapters(tmp_path, monkeypatch):
    # GH-391: both ports are wired for later subtasks to use; the scratch is on the
    # root outside the product (GH-397), in this product's partition.
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    cfg = Configuration(product_dir=product)
    ports = composition.default_ports(cfg, NullStopFlag())
    assert ports.scratch is not None and ports.free_space is not None
    key = reaping.product_key(str(product.resolve()))
    with ports.scratch.open("GH-1", "c", "0123456789abcdef", "tests") as directory:
        assert directory.parent == cfg.scratch_dir.resolve() / key / "GH-1"
        assert not directory.is_relative_to(product.resolve())
    assert ports.scratch.ensure_kept("GH-1") == product.resolve() / ".agentic/kept/GH-1"
    assert isinstance(ports.free_space, StatvfsFreeSpace)


def test_the_checks_of_a_task_bound_to_it_file_their_scratch_under_its_root_key(
    tmp_path, monkeypatch
):
    # GH-391, invariant 1: `<task>` is the root task key, `_untasked` only with no task.
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    cfg = Configuration(product_dir=product)
    ports = composition.default_ports(cfg, NullStopFlag())
    print_tmpdir = "python3 -c 'import tempfile; print(tempfile.gettempdir())'"
    scratch_root = cfg.scratch_dir.resolve() / reaping.product_key(str(product.resolve()))

    unbound = Path(ports.commands.run(product, print_tmpdir, timeout_s=30).output.strip())
    bound_ports = composition.bind_task(ports, "GH-7")
    bound = Path(bound_ports.commands.run(product, print_tmpdir, timeout_s=30).output.strip())

    assert unbound.parent == scratch_root / "_untasked"
    assert bound.parent == scratch_root / "GH-7"
    assert not bound.exists()


def test_ports_whose_commands_are_not_the_shells_are_bound_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    ports = replace(
        composition.default_ports(Configuration(product_dir=product), NullStopFlag()),
        commands=MemoryCommands(),
    )
    assert composition.bind_task(ports, "GH-7") is ports


def test_a_scratch_root_inside_the_products_repository_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    root = product / "cache" / "scratch"
    root.mkdir(parents=True)
    with pytest.raises(preflight.ScratchInARepository) as refusal:
        composition.default_ports(Configuration(product_dir=product, scratch_dir=root))
    # Absolute, and says how to fix it.
    assert str(root) in str(refusal.value)
    assert "AGENTIC_SCRATCH_DIR" in str(refusal.value)
    assert "XDG_CACHE_HOME" in str(refusal.value)


def test_a_scratch_root_inside_the_running_repository_is_refused(tmp_path, monkeypatch):
    system = a_repository(tmp_path / "system")
    monkeypatch.setattr(preflight, "CHECKOUT", system)
    product = a_repository(tmp_path / "product")
    with pytest.raises(preflight.ScratchInARepository):
        composition.default_ports(Configuration(product_dir=product, scratch_dir=system / "s"))


def test_a_scratch_root_not_yet_created_is_judged_from_its_nearest_existing_ancestor(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    (product / "cache").mkdir()
    root = product / "cache" / "not" / "yet" / "scratch"
    assert not (product / "cache" / "not").exists()
    with pytest.raises(preflight.ScratchInARepository):
        composition.default_ports(Configuration(product_dir=product, scratch_dir=root))


def test_a_scratch_root_in_a_third_repository_is_accepted(tmp_path, monkeypatch):
    """The comparison is by repository: only the product's and stage0's are forbidden."""
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    other = a_repository(tmp_path / "other")
    ports = composition.default_ports(Configuration(product_dir=product, scratch_dir=other / "s"))
    assert ports.scratch is not None


def test_the_default_scratch_root_raises_neither_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    assert composition.default_ports(Configuration(product_dir=product)).scratch


def test_the_tmpdir_of_an_attempt_on_the_systems_own_repository_is_outside_it(
    tmp_path, monkeypatch
):
    """Criterion 1 of GH-397. `_ports` and not `default_ports`: the product here IS the
    repository `CHECKOUT` names, which `default_ports` refuses by design (ADR-0017); the
    production run of this system as a product goes through the same `_ports`, and the
    directory `scratch.open` yields is what the runner exports as `TMPDIR`."""
    system = a_repository(tmp_path / "system")
    monkeypatch.setattr(preflight, "CHECKOUT", system)
    monkeypatch.setattr(composition, "refuse_its_own_checkout", lambda cfg, ports: None)
    cfg = Configuration(product_dir=system)
    ports = composition._ports(cfg, NullStopFlag())
    git = GitWorkspace(system, cfg.git_identity)
    with ports.scratch.open("GH-1", "r", "abcdef0123456789", "developer") as tmpdir:
        assert tmpdir.is_dir()
        assert git.repository_at(system) is not None
        assert git.repository_at(tmpdir) != git.repository_at(system)
        assert git.repository_at(tmpdir) != git.repository_at(preflight.CHECKOUT)
