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
from ..domain.presubmit import CheckResult, WitnessResult
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
        *,
        witness: str | None = None,
    ):
        self.ports = ports
        self.path = path
        self.before = before
        self.checks = checks
        self.check_timeout_s = check_timeout_s
        # The checkout witness the product declared, or None: a product that declares
        # none gets the presubmit of today, byte for byte (glossary: Testigo del
        # checkout). Keyword-only with this default so every existing construction
        # site compiles untouched.
        self.witness = witness
        self.refusals = 0
        # The witness's own budget, separate from `refusals`/`REFUSALS`: a misdeclared
        # witness must never spend the budget the checks need.
        self.witness_refusals = 0
        # What the last look saw: the head and the checks, None until a check ran.
        self.head: str | None = None
        self.results: list[CheckResult] | None = None
        # What the witness said the last time it ran, for `markdown()`; None when no
        # witness is declared, or none has run yet.
        self.witness_result: WitnessResult | None = None
        # Every invocation that actually ran the checks, in order: what
        # `agentic.presubmit.checks` and `.duration` read, one set of samples per
        # invocation with only the last one `final` (a Developer run may deliver,
        # get refused, and try again more than once).
        self.invocations: list[list[CheckResult]] = []

    @property
    def timeout_s(self) -> float:
        base = self.check_timeout_s * max(len(self.checks), 1)
        return base + (self.check_timeout_s if self.witness else 0)

    def __call__(self) -> str | None:
        workspace = self.ports.workspace
        head = workspace.head(self.path)
        moved, clean = head != self.before, workspace.is_clean(self.path)
        # Nothing to check before there is a commit on a clean tree: a delivery without
        # one fails the backstop anyway, and the record must hold what the head holds.
        # The witness sits inside this same branch, first, so no command is ever
        # spawned on a call that refuses for a missing commit or an unclean tree.
        witness_reason = None
        if moved and clean:
            if self.witness is not None:
                witness_reason = self._run_witness(self.witness)
            self.head, self.results = head, [self._run(n, c) for n, c in self.checks.items()]
            self.invocations.append(self.results)
        # A witness disagreement leads, on its own capped budget: `refusals`/`REFUSALS`
        # stay for the checks alone, so a bad witness declaration cannot spend the
        # budget the working gate needs. The checks below still ran either way.
        if witness_reason is not None and self.witness_refusals < presubmit.WITNESS_REFUSALS:
            self.witness_refusals += 1
            return presubmit.witness_refused(witness_reason)
        reason = presubmit.refusal(moved, clean, self.results or [])
        if reason is None or self.refusals >= presubmit.REFUSALS:
            return None
        self.refusals += 1
        return presubmit.refused(reason, self.refusals)

    def markdown(self) -> str | None:
        """The checks as the Developer's section opens with them; None if none ran. The
        witness's own line, when one ran, is in this same block whether it agreed or
        not -- including on the delivery the gate let through after its refusals (or
        the checks') were spent."""
        if self.results is None or self.head is None:
            return None
        return presubmit.checks_markdown(self.head, self.results, self.witness_result)

    def _run_witness(self, command: str) -> str | None:
        checkout = self.path.resolve()
        result = self.ports.commands.run(self.path, command, self.check_timeout_s)
        reason = presubmit.witness_refusal(
            command, result.output, result.exit_code, result.timed_out, checkout
        )
        self.witness_result = {
            "command": command,
            "output": presubmit.said(result.output, result.timed_out, self.check_timeout_s),
            "passed": reason is None,
        }
        return reason

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
