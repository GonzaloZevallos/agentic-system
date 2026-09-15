"""The delivery gate of one Developer run, over the ports (glossary: Puerta de entrega).

The harness asks it each time the model hands back its delivery (`RoleRunner`,
`gate`). It looks at the checkout: a commit, a clean tree, and the product's checks as
the task's base declares them, run in that checkout. It keeps what the checks said the
last time, so the use case can write it into the document: the system's record, not the
Developer's word."""

from __future__ import annotations

from pathlib import Path

from ..domain import gate, rules
from ..domain.gate import CheckResult
from ..domain.task import TaskState
from .ports import Ports


def declared_checks(state: TaskState, ports: Ports) -> dict[str, str]:
    """The checks that hold for this task: the ones at the commit its branch started
    from, never the branch's own (issue #22, 7). The product's HEAD when the branch
    carries no base, which only a branch the system did not create lacks."""
    base = ports.workspace.base_of(rules.task_branch(state["id"]))
    return ports.checks.declared(base or "HEAD")


class CheckoutGate:
    """The `DeliveryGate` over one Developer's checkout. One per Developer run: the
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
        reason = gate.refusal(moved, clean, self.results or [])
        if reason is None or self.refusals >= gate.REFUSALS:
            return None
        self.refusals += 1
        return gate.refused(reason, self.refusals)

    def markdown(self) -> str | None:
        """The checks as the Developer's section opens with them; None if none ran."""
        if self.results is None or self.head is None:
            return None
        return gate.checks_markdown(self.head, self.results)

    def _run(self, name: str, command: str) -> CheckResult:
        result = self.ports.commands.run(self.path, command, self.check_timeout_s)
        output = result.output
        if result.timed_out:
            output = f"{output}\n(timed out after {self.check_timeout_s:.0f} s)"
        return {"name": name, "command": command, "passed": result.passed, "output": output}
