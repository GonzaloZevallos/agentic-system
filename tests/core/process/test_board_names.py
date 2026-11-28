"""No adapter or test repeats an Etapa option name or the Motivo/Bloqueo field names as a
string literal (GH-1166): they come from `get_args(Etapa)` and `domain/board_schema.py`. An
ast scan, so a name in a docstring does not count and a name in code always does."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from agentic.core.process.domain.board import Etapa
from agentic.core.process.domain.board_schema import (
    KNOWN_ETAPAS,
    MOTIVO_FIELD,
    PREVIOUS_MOTIVO_FIELD,
)

ROOT = Path(__file__).resolve().parents[3]
SCANNED = (ROOT / "agentic" / "core" / "process" / "adapters", ROOT / "tests")
NAMES = KNOWN_ETAPAS | {MOTIVO_FIELD, PREVIOUS_MOTIVO_FIELD}
# GhBoard's own tests are frozen (invariant 6: the file is not modified) and spell the Motivo
# field the way the Project does, which invariant 1 forbids: the two cannot both hold. The
# scan skips that one file, found by its name and its directory.
FROZEN_NAME, FROZEN_DIR = "test_board.py", "github"


def docstrings(tree: ast.AST) -> set[int]:
    """The `id` of each docstring constant: the first expression of a module, class or
    function body."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def flagged(path: Path) -> list[tuple[int, str]]:
    """The (line, value) of each string constant outside a docstring that is one of `NAMES`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = docstrings(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value in NAMES
    ]


def test_no_adapter_or_test_repeats_a_board_name() -> None:
    found = {
        f"{path.relative_to(ROOT)}:{line}: {value!r}"
        for root in SCANNED
        for path in root.rglob("*.py")
        if (path.name, path.parent.name) != (FROZEN_NAME, FROZEN_DIR)
        for line, value in flagged(path)
    }
    assert not found, sorted(found)


def test_the_scan_flags_code_and_spares_docstrings(tmp_path: Path) -> None:
    name = get_args(Etapa)[-1]
    source = tmp_path / "sample.py"
    source.write_text(
        f'"""{name}"""\n\n\ndef f() -> str:\n    """{name}"""\n    return {name!r}\n',
        encoding="utf-8",
    )
    assert flagged(source) == [(6, name)]
