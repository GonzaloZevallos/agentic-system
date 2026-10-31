"""`ProductStack` over git: `.agentic/stack.yaml` as a given commit holds it."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import yaml

from ....process.domain.design import Design
from ....process.domain.docs import Docs
from ....process.domain.performance import Scenario
from ....process.domain.presubmit import CiCover, RerunForm
from ....process.domain.run_check import Verification
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

    def ci_covers(self, revision: str) -> Mapping[str, CiCover]:
        data, source = self._declaration(revision)
        return stack.ci_covers(data, source)

    def check_budgets(self, revision: str) -> dict[str, float]:
        data, source = self._declaration(revision)
        return stack.check_budgets(data, source)

    def verifications(self, revision: str) -> dict[str, Verification]:
        data, source = self._declaration(revision)
        return stack.verifications(data, source)

    def check_timeouts(self, revision: str) -> dict[str, float]:
        data, source = self._declaration(revision)
        return stack.check_timeouts(data, source)

    def not_ready_exit(self, revision: str) -> dict[str, int]:
        data, source = self._declaration(revision)
        return stack.not_ready_exit(data, source)

    def merge_inert(self, revision: str) -> tuple[str, ...]:
        """The globs master may gain without a recorded pass going stale. A revision that
        cannot be read -- not fetched, no file, YAML that does not parse, not a mapping, a
        key the system does not know -- reads `()`, never raises and never falls back to
        another revision: what cannot be read is not covered. A key that is there but
        broken still raises `ValueError`, as it does for every other key."""
        try:
            data, source = self._declaration(revision)
        except (ValueError, yaml.YAMLError):
            return ()
        if not isinstance(data, dict) or any(key not in stack.KEYS for key in data):
            return ()
        return stack.merge_inert(data, source)

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

    def docs(self, revision: str) -> Docs | None:
        data, source = self._declaration(revision)
        return stack.docs(data, source)

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
