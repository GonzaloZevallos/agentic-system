"""Every `pause_log` entry the system writes carries the instant it was made (GH-467).

`rules.logged`, `rules.resumed`, `rules.resumed_entry`, `landing.unescalated` and
`landing.migration` take `at` as a required keyword, and `application/` is only partly
under mypy, so this test is what catches a call site that forgot it: it walks `agentic/`
and names every call of those five without the keyword.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "agentic"
# The function -> the modules a call to it can be spelled through (`rules.logged`) or, inside
# the defining module itself, as a bare name.
STAMPED = {
    "logged": ("rules",),
    "resumed": ("rules",),
    "resumed_entry": ("rules",),
    "unescalated": ("landing", "landing_rules"),
    "migration": ("landing", "landing_rules"),
}
OWNER_FILES = {"rules": "rules.py", "landing": "landing.py"}


def unstamped_calls(source: str, filename: str) -> list[str]:
    """`file:line: name` for every call of a stamped function that has no `at=` keyword."""
    found: list[str] = []
    bare_owner = OWNER_FILES.get(Path(filename).stem) == Path(filename).name
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            name, via = func.attr, func.value.id
            if name not in STAMPED or via not in STAMPED[name]:
                continue
        elif isinstance(func, ast.Name) and bare_owner and func.id in STAMPED:
            name = func.id
        else:
            continue
        if not any(keyword.arg == "at" for keyword in node.keywords):
            found.append(f"{filename}:{node.lineno}: {name}")
    return found


def test_every_pause_entry_call_in_the_product_passes_its_instant():
    missing = [
        call
        for path in sorted(ROOT.rglob("*.py"))
        for call in unstamped_calls(path.read_text(), f"{path.relative_to(ROOT.parent)}")
    ]
    assert missing == []


def test_the_walk_rejects_a_call_without_the_instant():
    source = (
        "from agentic.core.process.domain import rules\n"
        "patch = rules.logged({})\n"
        "entry = rules.resumed_entry()\n"
        "ok = rules.logged({}, at=now)\n"
    )
    assert unstamped_calls(source, "x.py") == ["x.py:2: logged", "x.py:3: resumed_entry"]


def test_the_walk_reads_a_bare_call_only_inside_the_module_that_defines_it():
    assert unstamped_calls("x = logged({})\n", "rules.py") == ["rules.py:1: logged"]
    assert unstamped_calls("x = logged({})\n", "elsewhere.py") == []
