"""`ProductStack` over git: `.agentic/stack.yaml` as a given commit holds it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ....process.domain.design import Design
from ....process.domain.performance import Scenario
from ....process.domain.presubmit import RerunForm
from ....process.domain.suppressions import Forbidden
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

    def witness(self, revision: str) -> str | None:
        data, source = self._declaration(revision)
        return stack.witness(data, source)

    def parallel_checks(self, revision: str) -> bool:
        data, source = self._declaration(revision)
        return stack.parallel_checks(data, source)

    def ci(self, revision: str) -> tuple[str, ...]:
        data, source = self._declaration(revision)
        return stack.ci(data, source)

    def affected_checks(self, revision: str) -> dict[str, str]:
        data, source = self._declaration(revision)
        return stack.affected_checks(data, source)

    def affected_fallback(self, revision: str) -> tuple[str, ...]:
        data, source = self._declaration(revision)
        return stack.affected_fallback(data, source)

    def delivery_checks(self, revision: str) -> tuple[str, ...]:
        data, source = self._declaration(revision)
        return stack.delivery_checks(data, source)

    def rerun_checks(self, revision: str) -> dict[str, RerunForm]:
        data, source = self._declaration(revision)
        return stack.rerun_checks(data, source)

    def forbidden(self, revision: str) -> tuple[Forbidden, ...]:
        data, source = self._declaration(revision)
        return stack.forbidden(data, source)

    def product(self, revision: str = "HEAD") -> str:
        """The product's name, HEAD by default and not the task's base: the name says who
        the repository is, and a task older than the key would otherwise resume with no
        name at all (`domain/stack.py`)."""
        data, source = self._declaration(revision)
        return stack.product(data, source)

    def design(self, revision: str) -> Design | None:
        data, source = self._declaration(revision)
        return stack.design(data, source)

    def performance(self, revision: str) -> dict[str, Scenario]:
        data, source = self._declaration(revision)
        return stack.performance(data, source)

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
