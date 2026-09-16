"""`ProductStack` over git: `.agentic/stack.yaml` as a given commit holds it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ...domain import stack


class GitStack:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def checks(self, revision: str) -> dict[str, str]:
        """The checks at `revision`, or the default when the file is not there."""
        text, source = self._declaration(revision)
        return stack.checks(yaml.safe_load(text), source)

    def _declaration(self, revision: str) -> tuple[str, object]:
        spec = f"{revision}:{stack.OVERLAY}"
        if self._git("cat-file", "-e", spec).returncode == 0:
            return self._git("show", spec).stdout, spec
        if self._git("rev-parse", "--verify", "-q", f"{revision}^{{commit}}").returncode:
            raise ValueError(f"no commit {revision!r} in {self.product_dir}")
        return stack.DEFAULT.read_text(encoding="utf-8"), stack.DEFAULT

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.product_dir, capture_output=True, text=True, check=False
        )
