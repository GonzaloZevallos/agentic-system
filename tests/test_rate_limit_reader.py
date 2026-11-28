"""GH-1157: `read_rate_limit_document` is the only place that launches `gh api rate_limit`.

Whatever launches that read through `run_gh` must pass `latch=None` and `access=None`, and the
reader does; a second launcher would be one more place that can mark a latch from the read that
decides a rate-limit wait. The check walks every module of the package for a tuple literal
`("api", "rate_limit")` and names the ones outside the reader."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "agentic"
READER = ("agentic/core/process/adapters/github/run.py", "read_rate_limit_document")
# The scores backfill's quota read goes through an injected `GhRead` (`backfill_gh_read` in the
# composition), never through the poller's latch: its latch is a throwaway nobody reads. It is
# a known second site, named here so a third one cannot arrive unseen.
ALLOWED = {("agentic/core/measurement/adapters/github/issue_listing.py", "rate_limit")}


def _is_rate_limit_tuple(node: ast.AST) -> bool:
    if not isinstance(node, ast.Tuple):
        return False
    values = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
    return any(a == "api" and b == "rate_limit" for a, b in zip(values, values[1:], strict=False))


def launchers(source: str, path: str) -> list[tuple[str, str]]:
    """Each `(path, enclosing function)` holding a `("api", "rate_limit")` tuple; the function
    is `<module>` outside any."""
    found: list[tuple[str, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if _is_rate_limit_tuple(node):
            found.append((path, function))
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else function
            )
            visit(child, name)

    visit(ast.parse(source), "<module>")
    return found


def test_only_read_rate_limit_document_launches_the_rate_limit_read():
    found: list[tuple[str, str]] = []
    for file in sorted(PACKAGE.rglob("*.py")):
        relative = file.relative_to(PACKAGE.parent).as_posix()
        found += launchers(file.read_text(), relative)
    assert READER in found, "the reader no longer holds the literal: update this test"
    assert set(found) - {READER} == ALLOWED
    assert found.count(READER) == 1


def test_the_check_rejects_a_second_launcher():
    source = (
        "def other(run):\n"
        "    return run_gh(run, ('api', 'rate_limit'), latch=None)\n"
        "ARGS = ('gh', 'api', 'rate_limit')\n"
        "OK = ('api', 'repos/x')\n"
    )
    assert launchers(source, "agentic/x.py") == [
        ("agentic/x.py", "other"),
        ("agentic/x.py", "<module>"),
    ]
