"""The presubmit of one Developer run, over the ports (glossary: Puerta de entrega).

The harness asks it each time the model hands back its delivery (`RoleRunner`,
`presubmit`). It looks at the checkout: a commit, a clean tree, and the product's checks as
the task's base declares them, run in that checkout. It keeps what the checks said the
last time, so the use case can write it into the document: the system's record, not the
Developer's word."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..domain import presubmit
from ..domain.presubmit import AffectedPlan, CheckResult, Mode, WitnessResult
from ..domain.task import TaskState
from .checkouts import task_base
from .ports import GitError, Ports
from .preflight import criteria_regressed


def declared_checks(state: TaskState, ports: Ports) -> dict[str, str]:
    """The checks the product's stack declares for this task: the ones at the commit its
    branch started from, never the branch's own (issue #22, 7)."""
    return ports.stack.checks(task_base(state, ports))


def delivery_checks(state: TaskState, ports: Ports) -> dict[str, str]:
    """The checks a delivery gate runs (GH-358): `declared_checks` narrowed to the product's
    `delivery_checks` at the task's base, or all of them for a product that declares no
    tiers. The merge gate and the `full_checks` node never ask: they read `declared_checks`
    whole."""
    return fast_tier(
        declared_checks(state, ports), ports.stack.delivery_checks(task_base(state, ports))
    )


def fast_tier(declared: dict[str, str], fast: tuple[str, ...]) -> dict[str, str]:
    """`declared` kept to the names in `fast`, in the order `declared` has them; all of it
    when `fast` is empty, which is a product with no tiers. For a caller that already holds
    the declared checks and must not read the stack for them twice."""
    if not fast:
        return declared
    return {name: command for name, command in declared.items() if name in fast}


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
        parallel: bool = False,
        affected: AffectedPlan | None = None,
        criteria_base: str | None = None,
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
        # The product's `parallel_checks`: run the declared checks concurrently. False
        # (the default) is the serial gate of before, byte for byte; keyword-only so
        # every existing construction site compiles untouched.
        self.parallel = parallel
        # The product's affected-only forms, with the base they are measured against
        # (ADR-0046), or None: the gate of before, byte for byte. Keyword-only with this
        # default so every existing construction site, `merge.py`'s included, compiles
        # and behaves untouched.
        self.affected = affected
        # The commit the delivery is measured against for the criteria line caps (GH-334),
        # the task's base like the affected plan's, or None: no document is read and the
        # gate is the one of before. Keyword-only, so the merge gate's own construction
        # (which judges regressions in its decision, not here) is untouched.
        self.criteria_base = criteria_base
        # The checks that failed in their full form on this instance: they never run
        # their affected form again, even after they later pass in full. A new Developer
        # run builds a new instance and starts on affected-only again.
        self.stuck_full: set[str] = set()
        self.refusals = 0
        # The witness's own budget, separate from `refusals`/`REFUSALS`: a misdeclared
        # witness must never spend the budget the checks need.
        self.witness_refusals = 0
        # What the last look saw: the head and the checks, None until a check ran.
        self.head: str | None = None
        self.results: list[CheckResult] | None = None
        # The criteria documents the last look found pushed past their cap by this head.
        self.regressions: list[str] = []
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
        witness = self.check_timeout_s if self.witness else 0
        if self.parallel:
            # Every check runs at once, each under its own `check_timeout_s`: the
            # slowest one bounds the call, and the witness runs before them, alone. With
            # an affected plan that one worker may run its affected form and then the
            # full one, one after the other.
            runs = 2 if self.affected is not None else 1
            return self.check_timeout_s * runs + witness
        if self.affected is not None:
            runs = len(self.checks) + len(self.affected.commands)
            return self.check_timeout_s * max(runs, 1) + witness
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
            plan = self.affected
            if plan is None:
                results = self._run_unplanned()
            else:
                results = self._run_under_plan(plan, self._modes(plan, head))
            self.head, self.results = head, results
            self.regressions = self._criteria_regressions(head)
            self.invocations.append(self.results)
        # A witness disagreement leads, on its own capped budget: `refusals`/`REFUSALS`
        # stay for the checks alone, so a bad witness declaration cannot spend the
        # budget the working gate needs. The checks below still ran either way.
        if witness_reason is not None and self.witness_refusals < presubmit.WITNESS_REFUSALS:
            self.witness_refusals += 1
            return presubmit.witness_refused(witness_reason)
        # A check that ran twice is decided by its last row, never by the affected one
        # the full rerun superseded.
        reason = presubmit.refusal(
            moved, clean, presubmit.final_results(self.results or []), self.regressions
        )
        if reason is None or self.refusals >= presubmit.REFUSALS:
            return None
        self.refusals += 1
        return presubmit.refused(reason, self.refusals)

    def _modes(self, plan: AffectedPlan, head: str) -> dict[str, Mode]:
        """Which form each check runs under the plan. A changed-paths lookup that fails
        is `None` for the domain, which runs everything full."""
        try:
            paths: tuple[str, ...] | None = self.ports.workspace.changed_paths(plan.base, head)
        except (GitError, OSError):
            paths = None
        return presubmit.check_modes(list(self.checks), plan, paths)

    def _run_unplanned(self) -> list[CheckResult]:
        if not self.parallel:
            return [self._run(n, c) for n, c in self.checks.items()]
        # `map` keeps declared order. If a `_run` raises, leaving the `with` waits for
        # the other started checks before the error propagates, so no check outlives
        # the call; nothing is recorded for it, the same as serial.
        with ThreadPoolExecutor(max_workers=max(len(self.checks), 1)) as pool:
            return list(pool.map(lambda item: self._run(*item), self.checks.items()))

    def _run_under_plan(self, plan: AffectedPlan, modes: dict[str, Mode]) -> list[CheckResult]:
        if not self.parallel:
            return [
                row
                for name, command in self.checks.items()
                for row in self._run_planned(name, command, modes[name], plan)
            ]
        with ThreadPoolExecutor(max_workers=max(len(self.checks), 1)) as pool:
            per_check = pool.map(
                lambda item: self._run_planned(item[0], item[1], modes[item[0]], plan),
                self.checks.items(),
            )
            return [row for rows in per_check for row in rows]

    def _run_planned(
        self, name: str, command: str, mode: Mode, plan: AffectedPlan
    ) -> list[CheckResult]:
        """One check under a plan: its affected form, then the full one when the
        affected one did not pass; the full form alone when the mode says so or the
        check already failed in full on this instance."""
        rows: list[CheckResult] = []
        if mode == "affected-only" and name not in self.stuck_full:
            form = presubmit.affected_command(plan.commands[name], plan.base)
            first = self._run(name, form, "affected-only")
            rows.append(first)
            if first["passed"]:
                return rows
        full = self._run(name, command, "full")
        rows.append(full)
        if not full["passed"]:
            self.stuck_full.add(name)
        return rows

    def markdown(self) -> str | None:
        """The checks as the Developer's section opens with them; None if none ran. The
        witness's own line, when one ran, is in this same block whether it agreed or
        not -- including on the delivery the gate let through after its refusals (or
        the checks') were spent."""
        if self.results is None or self.head is None:
            return None
        base = self.affected.base if self.affected is not None else None
        return presubmit.checks_markdown(
            self.head, self.results, self.witness_result, base=base, regressions=self.regressions
        )

    def _criteria_regressions(self, head: str) -> list[str]:
        """What this head adds past the criteria caps, against the base this gate was given.
        An unreadable document reports nothing: the merge decision is the guarantee, and an
        early warning that cannot read must not block a delivery."""
        if self.criteria_base is None:
            return []
        try:
            return criteria_regressed(self.ports, self.criteria_base, head)
        except GitError:
            return []

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

    def _run(self, name: str, command: str, mode: Mode | None = None) -> CheckResult:
        started = time.monotonic()
        result = self.ports.commands.run(self.path, command, self.check_timeout_s)
        duration_s = time.monotonic() - started
        output = presubmit.said(result.output, result.timed_out, self.check_timeout_s)
        row: CheckResult = {
            "name": name,
            "command": command,
            "passed": result.passed,
            "output": output,
            "duration_s": duration_s,
        }
        if mode is not None:
            row["mode"] = mode
        return row
