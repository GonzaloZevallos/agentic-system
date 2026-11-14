"""What the poller keeps only in memory, and why losing it at a re-exec is safe (GH-736).

Seams: the inventory table below and an `ast` scan over `poll.py` and the per-round modules.
One row per entry; `kind` says why a new process may start without it:

- `on_disk`: the entry is persisted, a new process reads it back;
- `rebuilt_each_round`: rebuilt by every round, or on import, from what is on disk;
- `harmless_loss`: losing it repeats one notice or one idempotent send, nothing more.

A memory-only entry that holds a bound is not a row: it is a failing case. A new local of the
`_poll` loop, a new `run_once` argument, a new function-local cache or a new module-level
mutable in a scanned module fails here until it gets its row."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.board_sent import FilesystemBoardSent
from agentic.core.process.adapters.filesystem.run_registry import FilesystemRunRegistry
from agentic.core.process.application import poll
from agentic.core.process.application.ports import SentEntry, SentMemory
from agentic.core.process.domain import exempt_queue

PROCESS = Path(poll.__file__).resolve().parents[1]
SCANNED = [
    PROCESS / "application/poll.py",
    PROCESS / "domain/exempt_queue.py",
    PROCESS / "domain/board.py",
    PROCESS / "application/landing_lane.py",
    PROCESS / "domain/landing_order.py",
    PROCESS / "adapters/filesystem/board_sent.py",
]
KINDS = {"on_disk", "rebuilt_each_round", "harmless_loss"}

INVENTORY: list[tuple[str, str, str]] = [
    # the locals of `_poll`
    ("_poll.lock", "on_disk", "FilesystemRunLock record under .agentic/runs, taken again at start"),
    ("_poll.registry", "on_disk", "FilesystemRunRegistry under run_registry_dir"),
    ("_poll.real_launcher", "harmless_loss", "no state: SpawnLauncher(cfg) is built at start"),
    ("_poll.executable", "harmless_loss", "no state: rebuilt from argv0 at start"),
    ("_poll.proc", "harmless_loss", "no state: the /proc reader and reaper, rebuilt at start"),
    ("_poll.spawn_as", "harmless_loss", "no state: realpath of the executable, rebuilt at start"),
    (
        "_poll.inherited",
        "harmless_loss",
        "pids of children inherited across an exec: losing them leaves a zombie to the init",
    ),
    ("_poll.told", "harmless_loss", "losing it repeats one reexec_failed line"),
    ("_poll.hold", "harmless_loss", "a fresh process holds the lock again through _locked"),
    ("_poll.beat", "harmless_loss", "a closure over the heartbeat key, rebuilt at start"),
    ("_poll.said", "harmless_loss", "losing it repeats one fast-forward notice"),
    ("_poll.upstream", "rebuilt_each_round", "read again by every round"),
    ("_poll.notice", "rebuilt_each_round", "the lines of one round, printed in it"),
    ("_poll.line", "rebuilt_each_round", "the loop variable over one round's lines"),
    ("_poll.exc", "rebuilt_each_round", "the exception of one round, printed in it"),
    # the arguments of `run_once`
    ("run_once.cfg", "rebuilt_each_round", "Configuration, read from env and argv at start"),
    ("run_once.ports", "rebuilt_each_round", "default_ports(cfg), built at start"),
    ("run_once.registry", "on_disk", "the run registry directory"),
    ("run_once.launcher", "harmless_loss", "no state: launches children, remembers none"),
    ("run_once.now", "rebuilt_each_round", "the clock, read at the top of each round"),
    ("run_once.argv0", "harmless_loss", "the poller's executable, rebuilt at start"),
    ("run_once.register_timeout_s", "rebuilt_each_round", "a constant default"),
    ("run_once.sleep", "rebuilt_each_round", "a function default"),
    ("run_once.lock", "on_disk", "the run lock records under .agentic/runs"),
    ("run_once.current_boot", "rebuilt_each_round", "read from the kernel each round"),
    ("run_once.eval_set", "rebuilt_each_round", "read from the tracker each round"),
    ("run_once.live_steps", "rebuilt_each_round", "a constant default"),
    ("run_once.system_version", "rebuilt_each_round", "read from git each round"),
    ("run_once.dry_run", "rebuilt_each_round", "a flag of this round"),
    # the function-local caches of poll.py
    ("poll.read_version", "rebuilt_each_round", "functools.cache built inside one round"),
    ("poll.tray", "rebuilt_each_round", "functools.cache built inside one round"),
    ("poll.fresh", "rebuilt_each_round", "functools.cache built inside one round"),
    ("poll.upstream", "rebuilt_each_round", "functools.cache built inside one round"),
    ("poll.sweep_upstream", "rebuilt_each_round", "functools.cache built inside one round"),
    # module-level tables: constants rebuilt by the import of the new process
    ("poll.LANE_TEXT", "rebuilt_each_round", "a constant table, built on import, never mutated"),
    ("poll._SHAPE_TEXT", "rebuilt_each_round", "a constant table, built on import, never mutated"),
    ("exempt_queue._KIND_RANK", "rebuilt_each_round", "a constant table, built on import"),
    ("board._STATUSES", "rebuilt_each_round", "a constant table, built on import"),
    # the per-round modules
    ("exempt_queue.waits", "on_disk", "published by the run registry: publish_exempt_wait"),
    ("exempt_queue.attempts", "on_disk", "the run registry's exempt attempt records"),
    ("board.sent_memory", "on_disk", "FilesystemBoardSent, one JSON file per product"),
    ("landing_lane.records", "on_disk", "lane_admitted and lane_merges on the task documents"),
    ("landing_order.head_budget", "on_disk", "head_charged_s, head_seen_at on the task document"),
]
ROWS = {name: kind for name, kind, _ in INVENTORY}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _poll_function() -> ast.AsyncFunctionDef:
    tree = _tree(PROCESS.parent.parent / "cli.py")
    return next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_poll")


def _is_cache_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = ast.unparse(node.func)
    return name in {"functools.cache", "cache", "functools.lru_cache", "lru_cache"}


MUTABLE_CONSTRUCTORS = {"dict", "list", "set", "defaultdict", "OrderedDict", "deque", "Counter"}


def _is_mutable_value(value: ast.AST | None) -> bool:
    if isinstance(value, ast.Dict | ast.List | ast.Set | ast.ListComp | ast.DictComp | ast.SetComp):
        return True
    if isinstance(value, ast.Call):
        name = ast.unparse(value.func)
        return name.split(".")[-1] in MUTABLE_CONSTRUCTORS or name.startswith("collections.")
    return False


def _module_mutables(path: Path) -> list[str]:
    """Names assigned at module level to a mutable literal or constructor, and functions
    decorated with a cache, as `<module>.<name>`."""
    found: list[str] = []
    for node in _tree(path).body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and _is_mutable_value(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [f"{path.stem}.{t.id}" for t in targets if isinstance(t, ast.Name)]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                _is_cache_call(d) or ast.unparse(d) in {"cache", "functools.cache"}
                for d in node.decorator_list
            ):
                found.append(f"{path.stem}.{node.name}")
    return found


def _cache_in(value: ast.AST | None) -> bool:
    """Whether `value` holds a `functools.cache(...)` call anywhere (`y or functools.cache(f)`)."""
    return value is not None and any(_is_cache_call(n) for n in ast.walk(value))


def _caches_of(tree: ast.AST, module: str) -> list[str]:
    """`<module>.<name>` for every name bound to a cache object anywhere in `tree`: a plain or
    annotated assignment whose value holds a `functools.cache(...)` call, and a function,
    nested or not, decorated with a cache."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and _cache_in(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [f"{module}.{t.id}" for t in targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(
                _cache_in(d) or ast.unparse(d) in {"cache", "functools.cache"}
                for d in node.decorator_list
            ):
                found.append(f"{module}.{node.name}")
    return found


def _local_caches(path: Path) -> list[str]:
    return _caches_of(_tree(path), path.stem)


def _locals_of(function: ast.AST) -> set[str]:
    """Every name a function binds: assignments, loop targets and `except ... as` names."""
    names: set[str] = set()
    for n in ast.walk(function):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
    return {f"_poll.{name}" for name in names}


def _poll_locals() -> set[str]:
    return _locals_of(_poll_function())


def test_every_row_has_a_known_kind_and_a_reason() -> None:
    assert len(ROWS) == len(INVENTORY)
    assert all(kind in KINDS and why for _, kind, why in INVENTORY)


def test_the_known_rows() -> None:
    assert ROWS["_poll.said"] == "harmless_loss"
    assert ROWS["board.sent_memory"] == "on_disk"
    assert ROWS["exempt_queue.waits"] == "on_disk"


def test_every_poll_local_and_run_once_argument_has_a_row() -> None:
    arguments = {f"run_once.{p}" for p in inspect.signature(poll.run_once).parameters}
    assert (_poll_locals() | arguments) - ROWS.keys() == set()
    assert arguments <= ROWS.keys()


def test_every_row_names_something_that_exists() -> None:
    """A row for a local or argument that is gone is stale."""
    existing = (
        _poll_locals()
        | {f"run_once.{p}" for p in inspect.signature(poll.run_once).parameters}
        | {n for path in SCANNED for n in [*_module_mutables(path), *_local_caches(path)]}
    )
    prefixes = ("_poll.", "run_once.", "poll.", "exempt_queue._", "board._")
    stale = {name for name in ROWS if name.startswith(prefixes) and name not in existing}
    assert stale == set()


def test_every_function_local_cache_and_module_mutable_has_a_row() -> None:
    found = [n for path in SCANNED for n in [*_module_mutables(path), *_local_caches(path)]]
    assert found, "the scan found nothing: it is broken"
    assert [n for n in found if n not in ROWS] == []


@pytest.mark.parametrize(
    "source",
    [
        "CACHE = {}\n",
        "ITEMS: list[int] = []\n",
        "SEEN = set()\n",
        "import collections\nQ = collections.deque()\n",
        "import functools\n@functools.cache\ndef f(): ...\n",
        "import functools\n@functools.lru_cache(maxsize=8)\ndef f(): ...\n",
    ],
)
def test_the_scan_rejects_a_mutable_module_state_that_is_not_a_row(source, tmp_path) -> None:
    module = tmp_path / "newmod.py"
    module.write_text(source)
    found = _module_mutables(module)
    assert found and all(n not in ROWS for n in found)


def test_the_scan_accepts_a_constant_module(tmp_path) -> None:
    module = tmp_path / "constmod.py"
    module.write_text('X = frozenset({"a"})\nY = ("a", "b")\nZ = 3\n')
    assert _module_mutables(module) == []


def test_the_exempt_waits_are_in_the_registry(tmp_path) -> None:
    """`on_disk`: a wait published by one registry object is read by a fresh one."""
    now = datetime(2026, 11, 14, tzinfo=UTC)
    candidate = exempt_queue.ExemptCandidate("merge", "p", "key", 7, first_seen=now)
    root = tmp_path / "tasks"
    root.mkdir()
    FilesystemRunRegistry(tmp_path / "runs", boot_id=lambda: "b1").publish_exempt_wait(
        root, "p", candidate, now
    )
    waits, _ = FilesystemRunRegistry(tmp_path / "runs", boot_id=lambda: "b1").exempt_waits(now)
    assert [(w.product, w.since) for w in waits] == [("p", now)]


def test_the_board_sent_memory_is_on_disk(tmp_path) -> None:
    memory = SentMemory(project_id="P", issues={3: SentEntry("fp", frozenset({"etapa"}))})
    FilesystemBoardSent(tmp_path / "sent.json").save(memory)
    loaded = FilesystemBoardSent(tmp_path / "sent.json").load()
    assert loaded.project_id == "P" and loaded.issues[3].fingerprint == "fp"


@pytest.mark.parametrize(
    "source",
    [
        "import functools\ndef f(g):\n    x = functools.cache(g)\n",
        "import functools\ndef f():\n    @functools.cache\n    def inner(): ...\n",
        "import functools\ndef f(g, y=None):\n    x = y or functools.cache(g)\n",
        "import functools\ndef f(g):\n    x: object = functools.lru_cache(g)\n",
    ],
)
def test_the_scan_rejects_a_function_local_cache_that_is_not_a_row(source) -> None:
    found = _caches_of(ast.parse(source), "newmod")
    assert found and all(n not in ROWS for n in found)


def test_the_scan_of_poll_py_sees_the_or_form_of_a_cache() -> None:
    assert "poll.upstream" in _local_caches(SCANNED[0])


def test_a_new_poll_local_without_a_row_is_reported() -> None:
    source = (
        "async def _poll():\n"
        "    brand_new = 1\n"
        "    for item in []:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except OSError as problem:\n"
        "        pass\n"
    )
    found = _locals_of(ast.parse(source))
    assert found == {"_poll.brand_new", "_poll.item", "_poll.problem"}
    assert {n for n in found if n not in ROWS} == found
