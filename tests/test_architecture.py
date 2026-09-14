"""The dependency rules, checked on the imports (ADR-0007, ADR-0010).

Between modules: `shared` imports nothing of the project; `core.process` imports only
`shared`; `core.knowledge` and `core.measurement` import `shared` and `core.process`,
never each other, and never another module's adapters. Within a module: the domain
sees nothing of the project outside other domains; the application sees domains and
applications; only adapters see adapters and the frameworks. Only the root
(`composition`, `cli`) sees everything.
"""

import ast
from pathlib import Path

import pytest

import agentic

PACKAGE = Path(agentic.__file__).parent
OUTSIDE = ("langgraph", "langchain_core", "claude_agent_sdk", "opentelemetry", "subprocess")
LAYERS = ("domain", "application", "adapters")
ALLOWED = {
    "shared": set(),
    "process": {"shared"},
    "knowledge": {"shared", "process"},
    "measurement": {"shared", "process"},
}


def imports_of(path: Path) -> set[str]:
    """Every module a file names. `from X import a, b` counts as `X`, `X.a` and `X.b`:
    `a` may be a submodule, and a package name alone says nothing about its layer."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:  # relative: resolve against the file's package
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                prefix = ".".join(base.relative_to(PACKAGE.parent).parts)
                module = f"{prefix}.{module}" if module else prefix
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


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
    for name in imports_of(path):
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
        elif layer and other_layer and LAYERS.index(other_layer) > LAYERS.index(layer):
            bad.add(name)
        elif layer != "adapters" and not layer and other_layer == "adapters":
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
