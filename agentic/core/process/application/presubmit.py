"""The presubmit of one Developer run, over the ports (glossary: Puerta de entrega).

The harness asks it each time the model hands back its delivery (`RoleRunner`,
`presubmit`). It looks at the checkout: a commit, a clean tree, and the product's checks as
the task's base declares them, run in that checkout. It keeps what the checks said the
last time, so the use case can write it into the document: the system's record, not the
Developer's word."""

from __future__ import annotations

import time
from pathlib import Path

from ..domain import presubmit
from ..domain.presubmit import CheckResult
from ..domain.task import TaskState
from .checkouts import task_base
from .ports import Ports


def declared_checks(state: TaskState, ports: Ports) -> dict[str, str]:
    """The checks the product's stack declares for this task: the ones at the commit its
    branch started from, never the branch's own (issue #22, 7)."""
    return ports.stack.checks(task_base(state, ports))


class CheckoutPresubmit:
    """The `Presubmit` over one Developer's checkout. One per Developer run: the
    refusals it has left carry over to the retry."""

    def __init__(
        self,
        ports: Ports,
        path: Path,
        before: str,
        checks: dict[str, str],
        check_timeout_s: float,
    ):
        self.ports = ports
        self.path = path
        self.before = before
        self.checks = checks
        self.check_timeout_s = check_timeout_s
        self.refusals = 0
        # What the last look saw: the head and the checks, None until a check ran.
        self.head: str | None = None
        self.results: list[CheckResult] | None = None
        # Every invocation that actually ran the checks, in order: what
        # `agentic.presubmit.checks` and `.duration` read, one set of samples per
        # invocation with only the last one `final` (a Developer run may deliver,
        # get refused, and try again more than once).
        self.invocations: list[list[CheckResult]] = []

    @property
    def timeout_s(self) -> float:
        return self.check_timeout_s * max(len(self.checks), 1)

    def __call__(self) -> str | None:
        workspace = self.ports.workspace
        head = workspace.head(self.path)
        moved, clean = head != self.before, workspace.is_clean(self.path)
        # Nothing to check before there is a commit on a clean tree: a delivery without
        # one fails the backstop anyway, and the record must hold what the head holds.
        if moved and clean:
            self.head, self.results = head, [self._run(n, c) for n, c in self.checks.items()]
            self.invocations.append(self.results)
        reason = presubmit.refusal(moved, clean, self.results or [])
        if reason is None or self.refusals >= presubmit.REFUSALS:
            return None
        self.refusals += 1
        return presubmit.refused(reason, self.refusals)

    def markdown(self) -> str | None:
        """The checks as the Developer's section opens with them; None if none ran."""
        if self.results is None or self.head is None:
            return None
        return presubmit.checks_markdown(self.head, self.results)

    def _run(self, name: str, command: str) -> CheckResult:
        started = time.monotonic()
        result = self.ports.commands.run(self.path, command, self.check_timeout_s)
        duration_s = time.monotonic() - started
        output = presubmit.said(result.output, result.timed_out, self.check_timeout_s)
        return {
            "name": name,
            "command": command,
            "passed": result.passed,
            "output": output,
            "duration_s": duration_s,
        }
