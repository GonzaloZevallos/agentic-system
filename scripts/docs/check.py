"""The `docs` step of CI (GH-400, ADR-0076): the same command the delivery gate runs.

`uv run python scripts/docs/check.py [--root DIR]` reads `.agentic/stack.yaml` of `DIR` (by
default the repository this file lives in), builds the command of the reserved `docs` check
with `stack.docs_command` and runs it with `sh -c` from `DIR`. That command regenerates the
owned docs, exits 1 naming what changed (`DOCS_STALE`) and puts the tree back as HEAD has it.
The command is built only by the stack module: no owned path is written here.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from agentic.core.knowledge.domain import stack

REPO = Path(__file__).resolve().parents[2]
HINT = "run `uv run python scripts/docs/generate.py` and commit"


def command_for(root: Path) -> str:
    """The shell command of the `docs` block declared by `root`'s stack."""
    source = root / ".agentic" / "stack.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    block = stack.docs(data, source)
    if block is None:
        raise ValueError(f"{source}: declares no `docs` block")
    return stack.docs_command(block)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py", description="Fail when the generated docs are stale."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO,
        help="the repository to check (default: the one this script is in)",
    )
    root = parser.parse_args(argv).root.resolve()
    try:
        command = command_for(root)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"check.py: {error}", file=sys.stderr)
        return 1
    sys.stdout.flush()
    code = subprocess.run(["sh", "-c", command], cwd=root, check=False).returncode
    if code != 0:
        print(HINT, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
