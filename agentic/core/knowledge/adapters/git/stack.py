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
        data, source = self._declaration(revision)
        return stack.checks(data, source)

    def setup(self, revision: str) -> dict[str, str]:
        data, source = self._declaration(revision)
        return stack.setup(data, source)

    def _declaration(self, revision: str) -> tuple[object, object]:
        """The file as `revision` holds it. Absent, the run stops: there is no default to
        fall back on, and a product that declares nothing is told what to write (#36)."""
        spec = f"{revision}:{stack.OVERLAY}"
        if self._git("cat-file", "-e", spec).returncode == 0:
            return yaml.safe_load(self._git("show", spec).stdout), spec
        if self._git("rev-parse", "--verify", "-q", f"{revision}^{{commit}}").returncode:
            raise ValueError(f"no commit {revision!r} in {self.product_dir}")
        raise stack.not_declared(spec)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.product_dir, capture_output=True, text=True, check=False
        )
