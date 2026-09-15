"""`ProductChecks` over git: `.agentic/checks.yaml` as a given commit holds it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ...domain import checks


class GitChecks:
    def __init__(self, product_dir: Path):
        self.product_dir = product_dir

    def declared(self, revision: str) -> dict[str, str]:
        """The checks at `revision`, or the default when the file is not there."""
        spec = f"{revision}:{checks.OVERLAY}"
        if self._git("cat-file", "-e", spec).returncode != 0:
            if self._git("rev-parse", "--verify", "-q", f"{revision}^{{commit}}").returncode:
                raise ValueError(f"no commit {revision!r} in {self.product_dir}")
            text, source = checks.DEFAULT.read_text(encoding="utf-8"), checks.DEFAULT
        else:
            text, source = self._git("show", spec).stdout, spec
        return checks.declared(yaml.safe_load(text), source)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.product_dir, capture_output=True, text=True, check=False
        )
