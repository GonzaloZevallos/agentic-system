"""The dependency rules, checked on the imports (ADR-0007, ADR-0010).

Between modules: `shared` imports nothing of the project; `core.process` imports only
`shared`; `core.knowledge` and `core.measurement` import `shared` and `core.process`,
never each other, and never another module's adapters. Within a module: the domain
sees nothing of the project outside other domains; the application sees domains and
applications; only adapters see adapters and the frameworks. Only the root
(`composition`, `cli`) sees everything.
"""

import ast
import re
from pathlib import Path

import pytest

import agentic
from tests.docs_scripts import imports

PACKAGE = Path(agentic.__file__).parent
OUTSIDE = ("langgraph", "langchain_core", "claude_agent_sdk", "opentelemetry", "subprocess")
LAYERS = ("domain", "application", "adapters")
ALLOWED = {
    "shared": set(),
    "process": {"shared"},
    "knowledge": {"shared", "process"},
    "measurement": {"shared", "process"},
}
# What each layer may import, by layer. `None` is a file outside the three (a module's
# `__init__.py`): it may see the domain and the application, never the adapters.
ALLOWED_LAYERS = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    None: {"domain", "application"},
    "adapters": set(LAYERS),
}


def place(module: str) -> tuple[str, str | None] | None:
    """(module, layer) of a project import. `None` for anything outside `agentic` and
    for the bare `agentic.core` package, whose members the aliases already name."""
    parts = module.split(".")
    if parts[0] != "agentic":
        return None
    if parts[1:2] == ["shared"]:
        return "shared", None
    if parts[1:2] == ["core"]:
        if len(parts) == 2:
            return None
        layer = parts[3] if len(parts) > 3 and parts[3] in LAYERS else None
        return parts[2], layer
    return "root", None


def module_name(path: Path) -> str:
    parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def violations(path: Path) -> set[str]:
    here = place(module_name(path))
    if here is None or here[0] == "root":
        return set()
    module, layer = here
    bad = set()
    for name in imports.imports_of(path, PACKAGE.parent):
        if layer != "adapters" and name.startswith(OUTSIDE):
            bad.add(name)
        there = place(name)
        if there is None:
            continue
        other, other_layer = there
        if other == "root":
            bad.add(name)
        elif other != module and other not in ALLOWED[module]:
            bad.add(name)
        elif other != module and other_layer == "adapters":
            bad.add(name)
        elif other_layer is not None and other_layer not in ALLOWED_LAYERS[layer]:
            bad.add(name)
    return bad


def test_every_module_respects_the_dependency_rule():
    hits = {str(p.relative_to(PACKAGE)): violations(p) for p in PACKAGE.rglob("*.py")}
    assert {k: v for k, v in hits.items() if v} == {}


def test_the_rule_knows_where_things_are():
    assert place("agentic.shared.configuration") == ("shared", None)
    assert place("agentic.core.process.domain.task") == ("process", "domain")
    assert place("agentic.core.knowledge.adapters.filesystem.flags") == ("knowledge", "adapters")
    assert place("agentic.core.measurement") == ("measurement", None)
    assert place("agentic.core") is None
    assert place("agentic.composition") == ("root", None)
    assert place("pydantic") is None
    assert module_name(PACKAGE / "core" / "process" / "__init__.py") == "agentic.core.process"


@pytest.mark.parametrize(
    ("where", "source", "caught"),
    [
        # A package import names the submodule through the alias: the layer is seen.
        ("core/knowledge/domain/x.py", "from agentic.core.process import adapters", True),
        ("core/knowledge/domain/x.py", "from .. import adapters", True),
        ("core/process/domain/x.py", "from .. import application", True),
        # The frameworks are for adapters only, also outside the three layers.
        ("core/process/x.py", "from opentelemetry import trace", True),
        ("core/process/__init__.py", "import langgraph", True),
        ("core/process/adapters/x.py", "import langgraph", False),
        # Supporting modules may reach the core's domain and application, not its adapters.
        ("core/measurement/domain/x.py", "from agentic.core.process.domain import task", False),
        ("core/measurement/adapters/x.py", "from agentic.core.process import adapters", True),
        # The GitHub listing adapter takes its `gh` call as a parameter (GH-802): the process
        # adapters stay out, the process application's exception classes are allowed.
        (
            "core/measurement/adapters/github/x.py",
            "from agentic.core.process.adapters.github import run",
            True,
        ),
        (
            "core/measurement/adapters/github/x.py",
            "from ....process.adapters.github import run",
            True,
        ),
        (
            "core/measurement/adapters/github/x.py",
            "from agentic.core.process.application.ports import TrackerTransient",
            False,
        ),
        ("core/process/domain/x.py", "from agentic.core.knowledge.domain import flags", True),
        ("core/process/adapters/x.py", "from agentic.core import knowledge", True),
        ("shared/x.py", "from agentic.core.process.domain import task", True),
    ],
)
def test_the_rule_catches_what_it_promises(tmp_path, monkeypatch, where, source, caught):
    fake = tmp_path / "agentic"
    fake.mkdir()
    monkeypatch.setattr("tests.test_architecture.PACKAGE", fake)
    path = fake / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source + "\n")
    assert bool(violations(path)) is caught, violations(path)


_STARTS_A_PROCESS = re.compile(r"subprocess\.(run|Popen)\b|\bPopen\(")


def _starts_own_process(adapter: Path) -> list[str]:
    return [
        path.name
        for path in sorted(adapter.glob("*.py"))
        if path.name != "run.py" and _STARTS_A_PROCESS.search(path.read_text())
    ]


def test_nothing_in_the_github_adapter_starts_a_process_outside_run_py():
    """GH-266: every `gh` call goes through `run.run_gh`, so none can lack a time limit."""
    adapter = Path(agentic.__file__).parent / "core/process/adapters/github"
    assert _starts_own_process(adapter) == []


def test_the_process_guard_rejects_a_module_that_starts_its_own(tmp_path):
    (tmp_path / "run.py").write_text("subprocess.run(['gh'])\n")  # the one allowed place
    (tmp_path / "late.py").write_text("import subprocess\nsubprocess.run(['gh'])\n")
    (tmp_path / "popen.py").write_text("from subprocess import Popen\nPopen(['gh'])\n")
    (tmp_path / "fine.py").write_text("Run = Callable[..., subprocess.CompletedProcess[str]]\n")
    assert _starts_own_process(tmp_path) == ["late.py", "popen.py"]


def _run_gh_calls_without_latch(root: Path) -> list[str]:
    """GH-996: every call to `run_gh` names its `latch` (a `RateLimitLatch` or `None`), so a
    rate limit that ends a call cannot go unmarked by forgetting the argument."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "run_gh" and all(keyword.arg != "latch" for keyword in node.keywords):
                found.append(f"{path.name}:{node.lineno}")
    return found


def test_every_run_gh_call_passes_a_latch():
    assert _run_gh_calls_without_latch(PACKAGE) == []


def test_the_latch_guard_rejects_a_call_that_omits_it(tmp_path):
    (tmp_path / "bad.py").write_text("run_gh(run, args, cwd=cwd)\nmod.run_gh(run, args)\n")
    (tmp_path / "good.py").write_text("run_gh(run, args, latch=None)\n")
    assert _run_gh_calls_without_latch(tmp_path) == ["bad.py:1", "bad.py:2"]


# Invariant 7 (GH-274, ADR-0051): `psycopg` is imported by the Langfuse adapter alone, and the
# root never imports that adapter at module level, so no other command loads the driver.
CONFINED = ("psycopg",)
ADAPTER_MODULES = ("langfuse.dashboards", "langfuse.widget_data")
DRIVER_HOME = Path("core") / "measurement" / "adapters" / "langfuse"


def _imports_driver(path: Path) -> bool:
    return any(name.split(".")[0] in CONFINED for name in imports.imports_of(path, PACKAGE.parent))


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.update(f"{'.' * node.level}{node.module or ''}.{a.name}" for a in node.names)
    return found


def _outside_the_home(package: Path) -> list[str]:
    home = package / DRIVER_HOME
    return sorted(
        str(p.relative_to(package))
        for p in package.rglob("*.py")
        if home not in p.parents and _imports_driver(p)
    )


def test_only_the_langfuse_adapter_imports_the_database_driver():
    assert _outside_the_home(PACKAGE) == []
    assert _imports_driver(PACKAGE / DRIVER_HOME / "dashboards.py")


def test_the_driver_guard_rejects_a_domain_or_application_module_that_imports_it(tmp_path):
    for layer in ("domain", "application"):
        bad = tmp_path / "core" / "measurement" / layer / "x.py"
        bad.parent.mkdir(parents=True)
        bad.write_text("import psycopg\n")
    ok = tmp_path / DRIVER_HOME / "dashboards.py"
    ok.parent.mkdir(parents=True)
    ok.write_text("from psycopg.types.json import Jsonb\n")
    assert _outside_the_home(tmp_path) == [
        "core/measurement/application/x.py",
        "core/measurement/domain/x.py",
    ]


def test_the_application_layer_is_under_the_layer_rule(tmp_path, monkeypatch):
    fake = tmp_path / "agentic"
    monkeypatch.setattr("tests.test_architecture.PACKAGE", fake)
    path = fake / "core/measurement/application/x.py"
    path.parent.mkdir(parents=True)
    path.write_text("from agentic.core.measurement.adapters.langfuse import dashboards\n")
    assert violations(path)


def _eager_dashboard_adapter(path: Path) -> bool:
    return any(adapter in name for name in _top_level_imports(path) for adapter in ADAPTER_MODULES)


def test_the_root_does_not_import_the_dashboard_adapters_at_module_level():
    assert not _eager_dashboard_adapter(PACKAGE / "cli.py")
    assert not _eager_dashboard_adapter(PACKAGE / "composition.py")


def test_the_lazy_import_guard_rejects_a_module_level_import(tmp_path):
    eager = tmp_path / "cli.py"
    eager.write_text("from .core.measurement.adapters.langfuse.dashboards import X\n")
    assert _eager_dashboard_adapter(eager)
    lazy = tmp_path / "composition.py"
    lazy.write_text("def f():\n    from .core.measurement.adapters.langfuse.dashboards import X\n")
    assert not _eager_dashboard_adapter(lazy)


# Invariant 2 (GH-722): the application of a carve-out is reached through `plan_carve_out` alone.
CARVE_APPLICATION = "agentic.core.process.application.carve_out"
CARVE_HOME = Path("core") / "process" / "application" / "plan_carve_out.py"


def _imports_carve_application(path: Path, root: Path) -> bool:
    return CARVE_APPLICATION in imports.imports_of(path, root)


def _outside_the_carve_home(package: Path) -> list[str]:
    return sorted(
        str(p.relative_to(package))
        for p in package.rglob("*.py")
        if p != package / CARVE_HOME and _imports_carve_application(p, package.parent)
    )


def test_only_plan_carve_out_imports_the_carve_out_application():
    assert _outside_the_carve_home(PACKAGE) == []
    assert _imports_carve_application(PACKAGE / CARVE_HOME, PACKAGE.parent)


def test_the_carve_out_guard_rejects_any_other_module_that_imports_it(tmp_path):
    package = tmp_path / "agentic"
    home = package / CARVE_HOME
    home.parent.mkdir(parents=True)
    home.write_text("from . import carve_out\n")
    for name, source in (
        ("plan.py", "from .carve_out import apply_carve\n"),
        ("late.py", "from agentic.core.process.application import carve_out\n"),
        ("fine.py", "from agentic.core.process.domain import carve_out\n"),
    ):
        (home.parent / name).write_text(source)
    assert _outside_the_carve_home(package) == [
        "core/process/application/late.py",
        "core/process/application/plan.py",
    ]


# Invariant 1 (GH-839): the hold label's name is written once, as `HOLD_LABEL` in `hold.py`.
HOLD_HOME = Path("core") / "process" / "domain" / "hold.py"
HOLD_LITERAL = "agentic-hold"


def _writes_the_hold_literal(source: str) -> bool:
    """Whether `source` holds the label as a string constant of its own; a docstring or a
    comment that only mentions it in prose is not an exact constant."""
    return any(
        isinstance(node, ast.Constant) and node.value == HOLD_LITERAL
        for node in ast.walk(ast.parse(source))
    )


def _outside_the_hold_home(package: Path) -> list[str]:
    return sorted(
        str(p.relative_to(package))
        for p in package.rglob("*.py")
        if p != package / HOLD_HOME and _writes_the_hold_literal(p.read_text(encoding="utf-8"))
    )


def test_the_hold_label_literal_lives_only_in_the_hold_module():
    assert _outside_the_hold_home(PACKAGE) == []
    assert _writes_the_hold_literal((PACKAGE / HOLD_HOME).read_text(encoding="utf-8"))


def test_the_hold_literal_guard_rejects_a_module_that_writes_the_label(tmp_path):
    package = tmp_path / "agentic"
    home = package / HOLD_HOME
    home.parent.mkdir(parents=True)
    home.write_text(f'HOLD_LABEL = "{HOLD_LITERAL}"\n')
    (home.parent / "copy.py").write_text(f'LABELS = frozenset({{"{HOLD_LITERAL}"}})\n')
    (home.parent / "prose.py").write_text(f'"""The {HOLD_LITERAL} label, in prose."""\nX = 1\n')
    (home.parent / "fine.py").write_text("from .hold import HOLD_LABEL\n")
    assert _outside_the_hold_home(package) == ["core/process/domain/copy.py"]


# GH-858: only `stacked_start` writes `stack_parent`; the other two homes read it.
STACK_PARENT_HOMES = frozenset(
    {
        Path("core/process/domain/document.py"),
        Path("core/process/domain/stacking.py"),
        Path("core/process/application/stacked_start.py"),
    }
)
STACK_WRITER = Path("core/process/application/stacked_start.py")
# `stacking` also builds the value, as a parser of what a document says and as a placeholder: it
# reads a record, it never writes one.
STACK_BUILDERS = frozenset({STACK_WRITER, Path("core/process/domain/stacking.py")})


def _callee_name(func: ast.expr) -> str | None:
    """The name a call goes by, bare (`StackParent(...)`) or qualified (`task.StackParent(...)`)."""
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _stack_parent_leaks(package: Path) -> list[str]:
    """The files outside their home that hold the quoted `stack_parent` key, or call
    `StackParent(` outside the writer."""
    found: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        quoted = relative not in STACK_PARENT_HOMES and any(
            (isinstance(n, ast.Constant) and n.value == "stack_parent")
            or (isinstance(n, ast.keyword) and n.arg == "stack_parent")
            for n in ast.walk(tree)
        )
        built = relative not in STACK_BUILDERS and any(
            isinstance(n, ast.Call) and _callee_name(n.func) == "StackParent"
            for n in ast.walk(tree)
        )
        if quoted or built:
            found.append(str(relative))
    return found


def test_only_stacked_start_writes_the_stack_parent_record():
    assert _stack_parent_leaks(PACKAGE) == []


def test_the_stack_parent_guard_rejects_a_module_that_writes_or_builds_it(tmp_path):
    package = tmp_path / "agentic"
    for relative, source in (
        (
            "core/process/application/stacked_start.py",
            'X = {"stack_parent": 1}\nStackParent(a=1)\n',
        ),
        ("core/process/application/other.py", 'patch = {"stack_parent": None}\n'),
        ("core/process/application/builds.py", "r = StackParent(issue=1)\n"),
        ("core/process/application/qualified.py", "r = task.StackParent(issue=1)\n"),
        ("core/process/application/keyword.py", "patch = dict(stack_parent=None)\n"),
        ("core/process/application/prose.py", '"""The stack_parent key, in prose."""\n'),
    ):
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    assert _stack_parent_leaks(package) == [
        "core/process/application/builds.py",
        "core/process/application/keyword.py",
        "core/process/application/other.py",
        "core/process/application/qualified.py",
    ]


# GH-990: every `gh` launch of this process counts into the one `GhCallLog`, so a call site that
# leaves the log out is a launch nobody sees. Four scans over `agentic/**`, each with a self-test.
GH_HOME = "core/process/adapters/github/run.py"
# (repo-relative path, enclosing `Class.method` or function): a `run_gh(`/`stream_gh(` call that
# passes no `calls=` on purpose, with the reason.
UNCOUNTED_CALLS: dict[tuple[str, str], str] = {
    (
        "agentic/core/process/adapters/github/label_timeline.py",
        "GhLabelTimeline.label_added_at",
    ): "only `agentic close` and the system merge build it, never a node's ports",
    (
        "agentic/composition.py",
        "backfill_gh_read",
    ): "it has its own GithubAccess and runs as a separate process role",
}
GH_LAUNCHERS = ("run_gh", "stream_gh")
GH_ADAPTERS = ("GhTracker", "GhInbox", "GhBoard", "GhCiLogs", "GhFormatFixReads", "GhRepoSlug")


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _enclosing_calls(tree: ast.AST) -> list[tuple[str, ast.Call]]:
    """Every call with the dotted name of the class and function around it ("" at module level)."""
    found: list[tuple[str, ast.Call]] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, ast.Call):
            found.append((".".join(scope), node))
        inner = (
            (*scope, node.name)
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            else scope
        )
        for child in ast.iter_child_nodes(node):
            walk(child, inner)

    walk(tree, ())
    return found


def _calls_value(node: ast.Call) -> ast.expr | None:
    return next((kw.value for kw in node.keywords if kw.arg == "calls"), None)


def _without_calls(
    root: Path, names: tuple[str, ...], allowed: dict[tuple[str, str], str]
) -> list[str]:
    """The calls of `names` under `root` that pass no `calls=` or pass the constant `None`, and
    the `allowed` entries that no longer enclose such a call (a stale entry is a violation)."""
    problems: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in _python_files(root):
        relative = f"{root.name}/{path.relative_to(root).as_posix()}"
        for scope, node in _enclosing_calls(ast.parse(path.read_text(encoding="utf-8"))):
            if _called_name(node) not in names:
                continue
            # A call in a function nested inside an allowed one (`backfill_gh_read.read`) is
            # inside it.
            enclosure = next(
                (
                    entry
                    for entry in allowed
                    if entry[0] == relative
                    and (scope == entry[1] or scope.startswith(f"{entry[1]}."))
                ),
                None,
            )
            if enclosure is not None:
                used.add(enclosure)
                continue
            value = _calls_value(node)
            if value is None or (isinstance(value, ast.Constant) and value.value is None):
                problems.append(f"{relative}:{node.lineno} {_called_name(node)}")
    problems += [f"stale allow-list entry {entry}" for entry in sorted(set(allowed) - used)]
    return problems


def _gh_literals(root: Path, home: str) -> list[str]:
    """The list or tuple literals under `root` whose first element is the string "gh", but for
    the file `home`, the one place the executable is named."""
    found: list[str] = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative == home:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.List | ast.Tuple)
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "gh"
            ):
                found.append(f"{relative}:{node.lineno}")
    return found


def _log_constructions(root: Path, home: tuple[str, str]) -> list[str]:
    found: list[str] = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        for scope, node in _enclosing_calls(ast.parse(path.read_text(encoding="utf-8"))):
            if _called_name(node) == "InProcessGhCallLog" and (relative, scope) != home:
                found.append(f"{relative}:{node.lineno}")
    return found


def test_no_gh_argument_list_is_written_outside_run_py():
    assert _gh_literals(PACKAGE, GH_HOME) == []


def test_the_gh_literal_scan_rejects_a_list_or_tuple_that_starts_with_gh(tmp_path):
    (tmp_path / "run.py").write_text("ARGV = ['gh', 'api']\n")
    (tmp_path / "list.py").write_text("ARGV = ['gh', 'api']\n")
    (tmp_path / "tuple.py").write_text("ARGV = ('gh', *rest)\n")
    (tmp_path / "fine.py").write_text("ARGV = ['git', 'gh']\nNAME = 'gh'\n")
    assert _gh_literals(tmp_path, "run.py") == ["list.py:1", "tuple.py:1"]


def test_every_gh_launch_passes_the_call_log_but_the_ones_named_here():
    assert _without_calls(PACKAGE, GH_LAUNCHERS, UNCOUNTED_CALLS) == []


def test_the_launch_scan_rejects_a_missing_argument_a_literal_none_and_a_stale_entry(tmp_path):
    root = tmp_path / "agentic"
    root.mkdir()
    (root / "bad.py").write_text(
        "def read(run):\n    run_gh(run, ())\n    stream_gh((), calls=None)\n"
        "    stream_gh((), calls=log)\n"
    )
    stale = {("agentic/gone.py", "nobody"): "no longer calls"}
    assert _without_calls(root, GH_LAUNCHERS, stale) == [
        "agentic/bad.py:2 run_gh",
        "agentic/bad.py:3 stream_gh",
        "stale allow-list entry ('agentic/gone.py', 'nobody')",
    ]


def test_the_launch_scan_lets_an_allowed_enclosure_through(tmp_path):
    root = tmp_path / "agentic"
    root.mkdir()
    (root / "own.py").write_text("class A:\n    def m(self):\n        run_gh(1)\n")
    assert _without_calls(root, GH_LAUNCHERS, {("agentic/own.py", "A.m"): "reason"}) == []


def test_every_gh_adapter_is_built_with_the_call_log():
    assert _without_calls(PACKAGE, GH_ADAPTERS, {}) == []


def test_the_adapter_scan_rejects_a_construction_without_the_log(tmp_path):
    root = tmp_path / "agentic"
    root.mkdir()
    (root / "wire.py").write_text(
        "a = GhTracker(p)\nb = GhInbox(p, calls=None)\nc = GhBoard(p, calls=log)\n"
    )
    assert _without_calls(root, GH_ADAPTERS, {}) == [
        "agentic/wire.py:1 GhTracker",
        "agentic/wire.py:2 GhInbox",
    ]


def test_the_process_log_is_built_only_by_process_call_log():
    assert _log_constructions(PACKAGE, ("composition.py", "process_call_log")) == []


def test_the_log_construction_scan_rejects_a_second_place_that_builds_it(tmp_path):
    (tmp_path / "composition.py").write_text(
        "def process_call_log():\n    return InProcessGhCallLog()\n"
        "def other():\n    return InProcessGhCallLog()\n"
    )
    (tmp_path / "elsewhere.py").write_text("log = InProcessGhCallLog()\n")
    assert _log_constructions(tmp_path, ("composition.py", "process_call_log")) == [
        "composition.py:4",
        "elsewhere.py:1",
    ]
