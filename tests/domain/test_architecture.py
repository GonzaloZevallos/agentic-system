"""The dependency rule of ADR-0007, checked on the imports: the domain and the
application never see the graph engine, the harness, tracing, the disk or the adapters."""

import ast
from pathlib import Path

import agentic

PACKAGE = Path(agentic.__file__).parent
OUTSIDE = ("langgraph", "langchain_core", "claude_agent_sdk", "opentelemetry", "subprocess")
INSIDE = ("agentic.adapters", "agentic.composition", "agentic.cli")


def imports_of(path: Path) -> set[str]:
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
    return names


def forbidden(layer: str) -> dict[str, set[str]]:
    hits = {}
    for path in (PACKAGE / layer).rglob("*.py"):
        bad = {
            name for name in imports_of(path) if name.startswith(OUTSIDE) or name.startswith(INSIDE)
        }
        if bad:
            hits[str(path.relative_to(PACKAGE))] = bad
    return hits


def test_domain_depends_on_nothing_of_the_project_or_the_infrastructure():
    assert forbidden("domain") == {}
    for path in (PACKAGE / "domain").rglob("*.py"):
        assert not any(n.startswith("agentic.application") for n in imports_of(path)), path


def test_application_depends_only_on_the_domain():
    assert forbidden("application") == {}
