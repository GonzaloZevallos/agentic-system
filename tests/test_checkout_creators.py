"""The modules that create a task's checkouts are a closed set (GH-824, ADR-0052 addendum).

The worktree sweep removes the checkouts of a CLOSED task under the task's own run lock, so
whoever creates `GH-N*`, `merge-GH-N` or `slice-GH-N` checkouts must hold that lock. This
ratchets the call sites: a module outside `ALLOWED` that calls one of the six creator names
fails, and so does a listed module that no longer calls one. It does not prove the lock; the
ADR's trace table does, by hand, and a new caller is where that trace is repeated."""

from __future__ import annotations

import ast
from pathlib import Path

import agentic

PACKAGE = Path(agentic.__file__).parent
APPLICATION = PACKAGE / "core" / "process" / "application"
CLI = PACKAGE / "cli.py"
PORTS = APPLICATION / "ports.py"
CHECKOUTS = APPLICATION / "checkouts.py"

BASE_NAMES = ("task_checkout", "subtask_checkout", "detached_checkout")
NAMES = frozenset(BASE_NAMES) | {f"{name}_in_thread" for name in BASE_NAMES}

# The modules that call or import a creator today, each traced to a RunLock holder in the
# ADR-0052 addendum (or listed there as an exception).
ALLOWED = frozenset(
    {
        "checkouts",
        "copy",
        "define",
        "deliver",
        "design",
        "develop",
        "diagnose",
        "integrate",
        "landing",
        "landing_adr",
        "landing_slice",
        "landing_sync",
        "merge",
        "owner_rules",
        "perf",
        "plan",
        "reflect_run",
        "review",
        "shadow_review",
        "validate_prd",
        "verifiers",
    }
)


def _creators(tree: ast.AST) -> set[str]:
    """The creator names a module calls (a bare name or an attribute on any receiver) or
    imports under any `asname`."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if isinstance(func, ast.Attribute):
                name = func.attr
            if name in NAMES:
                found.add(name)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names if alias.name in NAMES)
    return found


def _modules() -> dict[str, Path]:
    paths = {path.stem: path for path in APPLICATION.glob("*.py")}
    paths["cli"] = CLI
    return paths


def callers(modules: dict[str, Path]) -> set[str]:
    return {
        name
        for name, path in modules.items()
        if _creators(ast.parse(path.read_text(encoding="utf-8")))
    }


def verdict(found: set[str], allowed: frozenset[str]) -> list[str]:
    """What is wrong between the callers found and the frozen set; empty when they agree."""
    problems = [
        f"{name} calls a checkout creator but is not in ALLOWED: trace the new caller to the "
        "RunLock that covers it and list it in the ADR-0052 addendum, then add it here"
        for name in sorted(found - allowed)
    ]
    problems += [
        f"{name} is in ALLOWED but no longer calls a checkout creator: drop it here and from "
        "the ADR-0052 addendum"
        for name in sorted(allowed - found)
    ]
    return problems


def test_the_checkout_creators_are_the_listed_modules() -> None:
    assert verdict(callers(_modules()), ALLOWED) == []


def test_a_new_caller_and_a_stale_entry_are_refused(tmp_path: Path) -> None:
    sources = {
        "attribute": "def f(ports):\n    ports.workspace.task_checkout('GH-1')\n",
        "bare": "def f(state, cfg, ports):\n    return subtask_checkout(state, 'a', cfg, ports)\n",
        "imported": "from .checkouts import detached_checkout_in_thread as made\n",
        "innocent": "def f():\n    return 1\n",
    }
    modules = {}
    for name, source in sources.items():
        modules[name] = tmp_path / f"{name}.py"
        modules[name].write_text(source, encoding="utf-8")
    found = callers(modules)
    assert found == {"attribute", "bare", "imported"}
    problems = verdict(found, frozenset({"innocent"}))
    assert len(problems) == 4
    assert all("RunLock" in p or "no longer calls" in p for p in problems)
    assert sum("ADR-0052" in p for p in problems) == 4


def test_each_creator_name_still_exists_on_the_workspace_or_in_checkouts() -> None:
    protocol = ast.parse(PORTS.read_text(encoding="utf-8"))
    workspace = next(
        node
        for node in ast.walk(protocol)
        if isinstance(node, ast.ClassDef) and node.name == "Workspace"
    )
    defined = {
        node.name
        for node in workspace.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    defined |= {
        node.name
        for node in ast.parse(CHECKOUTS.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert sorted(NAMES - defined) == []
