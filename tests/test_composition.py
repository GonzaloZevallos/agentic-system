"""The checkpointer the CLI opens: a real SQLite file in the product (ADR-0003)."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
from dataclasses import replace
from pathlib import Path
from typing import get_args

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agentic import composition
from agentic.core.process.adapters.filesystem.free_space import StatvfsFreeSpace
from agentic.core.process.adapters.filesystem.write_gate import WRITE_TURN_FILE
from agentic.core.process.adapters.git import GitWorkspace
from agentic.core.process.adapters.github.gh_call_log import InProcessGhCallLog
from agentic.core.process.adapters.shell import EventStopFlag, NullStopFlag
from agentic.core.process.application import preflight
from agentic.core.process.application.ports import RunStopped
from agentic.core.process.application.preflight import RunsOnItsOwnCheckout
from agentic.core.process.domain import reaping
from agentic.core.process.domain.board import Etapa
from agentic.core.process.domain.ci import CiCheck, CiReport, red_checks
from agentic.core.process.domain.github_access import GithubAccess
from agentic.core.process.domain.landing import CI_LOG_JOBS
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


def _retry_of(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    return composition.default_ports(Configuration(product_dir=product), **kwargs).tracker._retry


def test_the_tracker_has_no_retry_policy_unless_the_caller_asks(tmp_path, monkeypatch):
    # The poller, `inbox`, `close` and every other verb build their ports like this.
    assert _retry_of(tmp_path, monkeypatch) is None
    assert composition.system_repo(GithubAccess(), InProcessGhCallLog()).tracker._retry is None


def test_run_and_resume_retry_with_the_long_cap_and_their_stop_flag(tmp_path, monkeypatch):
    from agentic.core.process.adapters.github.run import GH_RATE_WAIT_MAX_S

    stop = EventStopFlag()
    retry = _retry_of(tmp_path, monkeypatch, stop_flag=stop, gh_retry="run")
    assert retry.max_wait_s == GH_RATE_WAIT_MAX_S == 900
    assert retry.stop is stop


def test_the_merge_and_land_children_retry_with_the_one_minute_cap(tmp_path, monkeypatch):
    from agentic.core.process.adapters.github.run import GH_EXEMPT_WAIT_MAX_S

    retry = _retry_of(tmp_path, monkeypatch, gh_retry="exempt")
    assert retry.max_wait_s == GH_EXEMPT_WAIT_MAX_S == 60


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
    ports = composition._ports(cfg, NullStopFlag(), calls=InProcessGhCallLog())
    git = GitWorkspace(system, cfg.git_identity)
    with ports.scratch.open("GH-1", "r", "abcdef0123456789", "developer") as tmpdir:
        assert tmpdir.is_dir()
        assert git.repository_at(system) is not None
        assert git.repository_at(tmpdir) != git.repository_at(system)
        assert git.repository_at(tmpdir) != git.repository_at(preflight.CHECKOUT)


HEAD_SHA = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"


def _committed_product(tmp_path, monkeypatch, stack_yaml: str):
    """A product repo whose one commit holds `.agentic/stack.yaml` as given."""
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    product = a_repository(tmp_path / "product")
    (product / ".agentic").mkdir()
    (product / ".agentic/stack.yaml").write_text(stack_yaml)
    git = ["git", "-C", str(product), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "stack"], check=True)
    return product


def _report_with_an_aggregate_and_five_shards() -> CiReport:
    # Links that do not match `JOB_LINK`: `_read` answers `unreadable` and never launches `gh`.
    names = ["check", *(f"shard {n}" for n in range(1, 6))]
    checks = tuple(CiCheck(n, "COMPLETED", "FAILURE", f"https://example.test/{n}") for n in names)
    report = CiReport(HEAD_SHA, "master", None, None, checks)
    assert len(red_checks(report)) > CI_LOG_JOBS
    return report


def _jobs_read(product, monkeypatch, report: CiReport) -> list[str]:
    monkeypatch.setattr(composition, "ci_reader", lambda tracker: lambda reference: report)
    ports = composition.default_ports(Configuration(product_dir=product), NullStopFlag())
    assert ports.ci_logs is not None
    return [log.job for log in ports.ci_logs.failed_job_logs("ref", HEAD_SHA)]


def test_the_ci_logs_it_builds_read_the_shards_before_the_stacks_aggregate(tmp_path, monkeypatch):
    product = _committed_product(
        tmp_path, monkeypatch, "checks:\n  tests: 'true'\nci:\n  - check\n"
    )
    report = _report_with_an_aggregate_and_five_shards()
    assert _jobs_read(product, monkeypatch, report) == [f"shard {n}" for n in range(1, 5)]


def test_an_unreadable_stack_makes_the_ci_logs_select_as_if_nothing_were_declared(
    tmp_path, monkeypatch
):
    product = _committed_product(tmp_path, monkeypatch, "checks: [unclosed\n  ci: {\n")
    report = _report_with_an_aggregate_and_five_shards()
    jobs = _jobs_read(product, monkeypatch, report)
    assert jobs == [c.name for c in red_checks(report)[:CI_LOG_JOBS]]
    assert jobs[0] == "check"


class _FakeRun:
    """A `run` that raises `error` and counts its calls, the `gh api rate_limit` ones too."""

    def __init__(self, error):
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        raise self.error


def _failing_with_stderr(stderr, code=1):
    return subprocess.CalledProcessError(code, ["gh"], stderr=stderr)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_failing_with_stderr("gh: HTTP 502"), "TrackerTransient"),
        (subprocess.TimeoutExpired(["gh"], 60), "TrackerTimedOut"),
        (_failing_with_stderr("gh: API rate limit exceeded for user"), "TrackerRateLimited"),
        (_failing_with_stderr("gh: Forbidden (HTTP 403)"), "TrackerRefused"),
        (_failing_with_stderr("error connecting to api.github.com"), "TrackerUnreachable"),
    ],
)
def test_the_backfill_read_makes_one_call_and_names_why_it_failed(
    tmp_path, capsys, error, expected
):
    from agentic.core.process.application import ports

    run = _FakeRun(error)
    with pytest.raises(getattr(ports, expected)):
        composition.backfill_gh_read(tmp_path, run=run)(("api", "repos/{owner}/{repo}/issues"))
    assert run.calls == [["gh", "api", "repos/{owner}/{repo}/issues"]]
    assert capsys.readouterr().out == ""


def test_the_backfill_read_says_what_is_missing_in_its_own_words(tmp_path):
    with pytest.raises(ValueError, match="scores backfill needs the gh CLI"):
        composition.backfill_gh_read(tmp_path, run=_FakeRun(FileNotFoundError("gh")))(("api", "x"))


def _checkout_with_origin(path: Path, url: str | None) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    if url is not None:
        subprocess.run(["git", "remote", "add", "origin", url], cwd=path, check=True)
    return path


def test_the_backfill_listing_is_the_github_adapter_over_a_filesystem_cache(tmp_path, monkeypatch):
    from agentic.core.measurement.adapters.filesystem.read_cache import FilesystemReadCache
    from agentic.core.measurement.adapters.github.issue_listing import GhIssueListing

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    checkout = _checkout_with_origin(tmp_path / "co", "https://github.com/Acme/Widgets.git")
    listing, cache = composition.backfill_listing(checkout)
    assert isinstance(listing, GhIssueListing)
    assert isinstance(cache, FilesystemReadCache)


def test_the_cache_file_and_the_page_path_come_from_one_key_built_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    checkout = _checkout_with_origin(tmp_path / "co", "git@github.com:Acme/Widgets.git")
    seen = []
    real = composition.repo_key
    monkeypatch.setattr(composition, "repo_key", lambda url: (seen.append(url), real(url))[1])
    monkeypatch.setattr(composition, "backfill_gh_read", lambda product_dir: lambda args: "[]")
    listing, cache = composition.backfill_listing(checkout)
    assert seen == ["git@github.com:Acme/Widgets.git"]
    listing.page(1)
    cache.save({})
    assert (tmp_path / "xdg" / "agentic" / "scores-reads" / "acme__widgets.json").is_file()


def test_the_listing_path_names_the_same_repository_as_the_cache_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    checkout = _checkout_with_origin(tmp_path / "co", "https://github.com/Acme/Widgets")
    asked = []

    def gh(args):
        asked.append(args)
        return "[]"

    monkeypatch.setattr(composition, "backfill_gh_read", lambda product_dir: gh)
    listing, cache = composition.backfill_listing(checkout)
    listing.page(1)
    cache.save({})
    assert asked[0][1].startswith("repos/acme/widgets/issues?")
    assert [p.name for p in (tmp_path / "xdg" / "agentic" / "scores-reads").iterdir()] == [
        "acme__widgets.json"
    ]


@pytest.mark.parametrize("url", [None, "https://gitlab.com/acme/widgets.git"])
def test_without_a_github_origin_there_is_no_cache_and_the_placeholders_stay(
    tmp_path, monkeypatch, url
):
    from agentic.core.measurement.application.ports import NoCache

    checkout = _checkout_with_origin(tmp_path / "co", url)
    asked = []

    def gh(args):
        asked.append(args)
        return "[]"

    monkeypatch.setattr(composition, "backfill_gh_read", lambda product_dir: gh)
    listing, cache = composition.backfill_listing(checkout)
    listing.page(1)
    assert isinstance(cache, NoCache)
    assert asked[0][1].startswith("repos/{owner}/{repo}/issues?")


def test_the_cache_root_honours_xdg_cache_home_and_falls_back_to_the_home_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert composition.backfill_cache_root() == tmp_path / "xdg" / "agentic" / "scores-reads"
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert (
        composition.backfill_cache_root()
        == tmp_path / "home" / ".cache" / "agentic" / "scores-reads"
    )
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert (
        composition.backfill_cache_root()
        == tmp_path / "home" / ".cache" / "agentic" / "scores-reads"
    )


def test_the_backfill_clock_reads_the_current_utc_time():
    from datetime import UTC, datetime

    before = datetime.now(UTC)
    moment = composition.backfill_clock()()
    assert before <= moment <= datetime.now(UTC)
    assert moment.tzinfo is not None


def _gated_ports(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    # The board's memory file is named after the product, read from a stack the fixture lacks.
    monkeypatch.setattr(composition.GitStack, "product", lambda self, revision="HEAD": "demo")
    product = a_repository(tmp_path / "product")
    cfg = Configuration(
        product_dir=product, github_dir=tmp_path / "github", board_project="owner/7"
    )
    return cfg, composition.default_ports(cfg, **kwargs)


def test_the_poller_tracker_and_its_board_share_one_gate_on_the_configured_file(
    tmp_path, monkeypatch
):
    cfg, ports = _gated_ports(tmp_path, monkeypatch)
    access = ports.tracker._access
    assert access is not None and access.gate is not None
    assert ports.board.access is access
    assert ports.format_fixes._tracker._access is access
    assert access.gate._path == tmp_path / "github" / WRITE_TURN_FILE
    assert ports.format_filing._system.tracker._access is access


def test_the_gate_cap_is_one_minute_under_exempt_and_the_long_cap_otherwise(tmp_path, monkeypatch):
    for gh_retry, cap in ((None, 900), ("run", 900), ("exempt", 60)):
        _, ports = _gated_ports(tmp_path / str(gh_retry), monkeypatch, gh_retry=gh_retry)
        assert ports.tracker._access.gate.cap_s == cap
        assert ports.board.access.gate.cap_s == cap


def test_the_stop_flag_of_the_run_reaches_the_gate(tmp_path, monkeypatch):
    stop = EventStopFlag()
    _, ports = _gated_ports(tmp_path, monkeypatch, stop_flag=stop, gh_retry="run")
    assert ports.tracker._access.gate._stop is stop


def test_the_write_access_names_the_file_under_the_configured_github_dir(tmp_path):
    cfg = Configuration(product_dir=tmp_path, github_dir=tmp_path / "gh")
    gate = composition.write_access(cfg, None, NullStopFlag()).gate
    assert gate._path == tmp_path / "gh" / WRITE_TURN_FILE
    assert gate.cap_s == 900


def test_the_system_repo_and_the_stage0_window_trackers_carry_the_access_given(tmp_path):
    access = GithubAccess()
    calls = InProcessGhCallLog()
    assert composition.system_repo(access, calls).tracker._access is access
    window = composition.stage0_master_window(tmp_path, "u", tmp_path, lambda s: (), access, calls)
    assert window.ci._tracker._access is access


# -- GH-990: every gh adapter the composition builds counts into the one log it is given --------

_HEAD = "0258d6f10b0c8f84d6e3a819f0173809f18d7a12"
_PULL_REQUEST = "https://github.com/o/r/pull/7"


def _identity_answer() -> dict:
    options = [{"id": f"O{i}", "name": n} for i, n in enumerate(get_args(Etapa))]
    nodes = [
        {"id": "F_etapa", "name": "Etapa", "dataType": "SINGLE_SELECT", "options": options},
        {"id": "F_motivo", "name": "Motivo", "dataType": "TEXT"},
        {"id": "F_costo", "name": "Costo USD", "dataType": "NUMBER"},
        {"id": "F_inicio", "name": "Inicio", "dataType": "DATE"},
        {"id": "F_fin", "name": "Fin", "dataType": "DATE"},
    ]
    project = {"id": "PVT_1", "fields": {"nodes": nodes}}
    return {"data": {"repositoryOwner": {"projectV2": project}}}


def _report_answer() -> dict:
    red = {
        "__typename": "CheckRun",
        "name": "check",
        "status": "COMPLETED",
        "conclusion": "FAILURE",
        "detailsUrl": "https://github.com/o/r/actions/runs/5/job/11",
    }
    contexts = {"pageInfo": {"hasNextPage": False}, "nodes": [red]}
    commit = {"oid": _HEAD, "statusCheckRollup": {"contexts": contexts}}
    pull_request = {
        "baseRefName": "master",
        "createdAt": "2026-01-01T00:00:00Z",
        "commits": {"nodes": [{"commit": commit}]},
    }
    return {"data": {"repository": {"pullRequest": pull_request}}}


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A `gh` first on PATH that always exits 0, appends one line per launch to a file and
    prints the minimal JSON the read at hand parses. Returns the function that counts the
    launches since it was last called."""
    answers = {
        "issue_state": {
            "state": "OPEN",
            "stateReason": None,
            "blockedBy": {"nodes": [], "totalCount": 0},
            "closedByPullRequestsReferences": [],
            "labels": [],
        },
        "identity": _identity_answer(),
        "report": _report_answer(),
    }
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launches = tmp_path / "launches.txt"
    script = bin_dir / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""
            import json, sys
            args = sys.argv[1:]
            with open({str(launches)!r}, "a") as out:
                out.write(" ".join(args).replace("\\n", " ") + "\\n")
            answers = json.loads({json.dumps(answers)!r})
            if args[:2] == ["issue", "list"]:
                print("[]")
            elif args[:2] == ["issue", "view"]:
                print(json.dumps(answers["issue_state"]))
            elif args[:2] == ["repo", "view"]:
                print(json.dumps({{"nameWithOwner": "o/r"}}))
            elif args[:2] == ["api", "graphql"] and "--input" in args:
                print(json.dumps(answers["identity"]))
            elif args[:2] == ["api", "graphql"]:
                print(json.dumps(answers["report"]))
            elif args[:2] == ["run", "view"]:
                print("E   boom")
            """
        )
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    seen = [0]

    def launched() -> int:
        lines = launches.read_text().splitlines() if launches.exists() else []
        fresh, seen[0] = len(lines) - seen[0], len(lines)
        return fresh

    return launched


def _board_cfg(product: Path) -> Configuration:
    return Configuration.from_sources(
        {"product_dir": str(product)}, {"AGENTIC_BOARD_PROJECT": "owner/3"}
    )


def test_each_read_of_each_adapter_lands_on_the_log_it_was_built_with(
    tmp_path, monkeypatch, fake_gh
):
    # The board's memory file is named after the product, read from a stack the fixture lacks.
    monkeypatch.setattr(composition.GitStack, "product", lambda self, revision="HEAD": "demo")
    product = a_repository(tmp_path / "product")
    log = InProcessGhCallLog()
    ports = composition._ports(_board_cfg(product), NullStopFlag(), calls=log)

    ports.tracker.issue_state(7)
    assert dict(log.drain()) == {
        "issue_list": 0, "issue_view": 1, "pr_list": 0, "pr_view": 0, "api": 0, "other": 0
    }  # fmt: skip
    assert fake_gh() == 1

    ports.inbox.open_issues()
    assert dict(log.drain()) == {
        "issue_list": 1, "issue_view": 0, "pr_list": 0, "pr_view": 0, "api": 0, "other": 0
    }  # fmt: skip
    assert fake_gh() == 1

    logs = ports.ci_logs.failed_job_logs(_PULL_REQUEST, _HEAD)
    assert [entry.cause for entry in logs] == ["read"]
    assert dict(log.drain()) == {
        "issue_list": 0, "issue_view": 0, "pr_list": 0, "pr_view": 0, "api": 1, "other": 1
    }  # fmt: skip
    assert fake_gh() == 2

    assert ports.board is not None
    assert ports.board.project_id() == "PVT_1"
    assert dict(log.drain()) == {
        "issue_list": 0, "issue_view": 0, "pr_list": 0, "pr_view": 0, "api": 1, "other": 0
    }  # fmt: skip
    assert fake_gh() == 1

    # The slug read is the one launch: the issue is of another repository than `o/r`.
    assert ports.format_fixes.closing("https://github.com/x/y/issues/9") is None
    assert dict(log.drain()) == {
        "issue_list": 0, "issue_view": 0, "pr_list": 0, "pr_view": 0, "api": 0, "other": 1
    }  # fmt: skip
    assert fake_gh() == 1


def test_a_second_ports_with_its_own_log_leaves_the_first_log_untouched(
    tmp_path, monkeypatch, fake_gh
):
    monkeypatch.setattr(composition.GitStack, "product", lambda self, revision="HEAD": "demo")
    product = a_repository(tmp_path / "product")
    first, second = InProcessGhCallLog(), InProcessGhCallLog()
    cfg = _board_cfg(product)
    composition._ports(cfg, NullStopFlag(), calls=first)
    other = composition._ports(cfg, NullStopFlag(), calls=second)

    other.tracker.issue_state(7)
    assert dict(first.drain()) == {
        "issue_list": 0, "issue_view": 0, "pr_list": 0, "pr_view": 0, "api": 0, "other": 0
    }  # fmt: skip
    assert dict(second.drain()) == {
        "issue_list": 0, "issue_view": 1, "pr_list": 0, "pr_view": 0, "api": 0, "other": 0
    }  # fmt: skip
    assert fake_gh() == 1


def test_two_default_ports_without_calls_share_the_process_call_log(tmp_path, monkeypatch, fake_gh):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    cfg = Configuration(product_dir=a_repository(tmp_path / "product"))
    one = composition.default_ports(cfg)
    two = composition.default_ports(cfg)

    one.inbox.open_issues()
    two.tracker.issue_state(7)
    assert dict(composition.process_call_log().drain()) == {
        "issue_list": 1, "issue_view": 1, "pr_list": 0, "pr_view": 0, "api": 0, "other": 0
    }  # fmt: skip
    assert fake_gh() == 2


def test_the_activity_read_is_not_wired_while_the_notifier_address_is_unset(tmp_path, monkeypatch):
    # GH-1058: the Activity read exists only with the notifier (AGENTIC_NOTIFY_EMAIL_TO).
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    monkeypatch.delenv("AGENTIC_NOTIFY_EMAIL_TO", raising=False)
    product = a_repository(tmp_path / "product")
    ports = composition.default_ports(Configuration(product_dir=product))
    assert ports.activity is None and ports.notifier is None


def test_the_offline_ports_never_carry_an_activity_read(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "CHECKOUT", a_repository(tmp_path / "system"))
    monkeypatch.setenv("AGENTIC_NOTIFY_EMAIL_TO", "someone@example.com")
    product = a_repository(tmp_path / "product")
    ports = composition.offline_ports(Configuration(product_dir=product))
    assert ports.activity is None and ports.notifier is None and ports.notified is None
