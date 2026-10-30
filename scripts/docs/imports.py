"""The import reader the docs generator and the architecture test share (GH-430).

Pure: a file and a package root in, a set of module names out. Nothing here knows what a
layer, a cluster or a diagram is; each caller decides what the names mean.
"""

import ast
from pathlib import Path


def imports_of(path: Path, root: Path) -> set[str]:
    """Every module a file names. Every import in the file counts: module-level, lazy
    (inside a function) and `TYPE_CHECKING` alike. Relative imports resolve against the
    file's package, taken relative to `root` (the directory that holds the top-level
    package, so `root / "agentic"` is the package). `from X import a, b` counts as `X`,
    `X.a` and `X.b`: `a` may be a submodule, and a package name alone says nothing about
    its layer."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:  # relative: resolve against the file's package
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                prefix = ".".join(base.relative_to(root).parts)
                module = f"{prefix}.{module}" if module else prefix
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names
