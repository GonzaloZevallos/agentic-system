"""The `docs` step of CI (GH-400, ADR-0076): the same command the delivery gate runs.

`uv run python scripts/docs/check.py [--root DIR]` reads `.agentic/stack.yaml` of `DIR` (by
default the repository this file lives in), builds the command of the reserved `docs` check
with `stack.docs_command` and runs it with `sh -c` from `DIR`. That command regenerates the
owned docs and puts the tree back as HEAD has it. In `commit` mode it exits 1 naming what
changed (`DOCS_STALE`); in `validate` mode it exits non-zero only when generation itself failed.
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
from agentic.core.process.domain.docs import Docs

REPO = Path(__file__).resolve().parents[2]
HINT = "run `uv run python scripts/docs/generate.py` and commit"
VALIDATE_HINT = "generation failed; fix the generator, there is nothing to commit"
DESCRIPTION = "Fail when the generated docs are stale."
VALIDATE_DESCRIPTION = "Fail when generating the docs fails."


def block_for(root: Path) -> Docs:
    """The `docs` block declared by `root`'s stack."""
    source = root / ".agentic" / "stack.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    block = stack.docs(data, source)
    if block is None:
        raise ValueError(f"{source}: declares no `docs` block")
    return block


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py",
        description=f"{DESCRIPTION} With `docs.mode: validate`: {VALIDATE_DESCRIPTION}",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO,
        help="the repository to check (default: the one this script is in)",
    )
    root = parser.parse_args(argv).root.resolve()
    try:
        block = block_for(root)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"check.py: {error}", file=sys.stderr)
        return 1
    sys.stdout.flush()
    command = stack.docs_command(block)
    code = subprocess.run(["sh", "-c", command], cwd=root, check=False).returncode
    if code != 0:
        print(VALIDATE_HINT if block.mode == "validate" else HINT, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
