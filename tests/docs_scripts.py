"""Loads a pure module of `scripts/docs/` by file path, the way `tests/test_docs.py` does,
so a test can use it without putting `scripts/docs` on `sys.path`."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts" / "docs"


def load(name: str, module_name: str) -> ModuleType:
    """The module `scripts/docs/<name>.py`, registered in `sys.modules` as `module_name`."""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


imports = load("imports", "docs_imports")
