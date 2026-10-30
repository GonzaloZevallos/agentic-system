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
