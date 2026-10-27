"""The presubmit of one Developer run, over the ports (glossary: Puerta de entrega).

The harness asks it each time the model hands back its delivery (`RoleRunner`,
`presubmit`). It looks at the checkout: a commit, a clean tree, and the product's checks as
the task's base declares them, run in that checkout. It keeps what the checks said the
last time, so the use case can write it into the document: the system's record, not the
Developer's word."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentic.shared.stage0 import CHECKOUT

from ..domain import check_results as reuse_rules
from ..domain import presubmit
from ..domain.check_schedule import WAIT_MAX_S, CheckPriority
from ..domain.presubmit import (
    AffectedPlan,
    CheckResult,
    FlakeGate,
    Mode,
    RerunForm,
    WitnessResult,
)
from ..domain.task import FlakeRecord, GateRecord, TaskState
from .checkouts import task_base
from .ports import CheckRequest, GitError, Grant, Ports
from .preflight import criteria_regressed, product_name

# The delivery hook's own guard budget (`hooks.GUARD_TIMEOUT_S`, pinned equal by a test): a
# share is held that much past the checks' timeout, so the ledger never drops it before the
# hook gives up.
HOLD_MARGIN_S = 30.0


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


@dataclass(frozen=True)
class Reuse:
    """What a presubmit needs to key a stored pass (ADR-0065) besides the checkout's own
    tree: the product, the setup commands the checkout was prepared with, in the order the
    base declares them, and the version of the system doing the checking. A version that
    ends `+dirty` names no code, so it neither reads nor writes."""

    product: str
    setup: tuple[str, ...]
    stage0_version: str


def reuse_of(ports: Ports, base: str) -> Reuse | None:
    """The `Reuse` for a gate over a checkout of `base`: None for ports with no store, which
    is the gate of before. The stage0 version is the full sha of the checkout the system
    runs from, `+dirty` when it has uncommitted changes or git cannot name it."""
    if ports.check_results is None:
        return None
    return Reuse(
        product_name(ports),
        tuple(ports.stack.setup(base).values()),
        _stage0_version(ports),
    )


def _stage0_version(ports: Ports) -> str:
    try:
        sha = ports.workspace.full_head(CHECKOUT)
        committed = ports.workspace.is_committed(CHECKOUT)
    except (GitError, OSError):
        return reuse_rules.DIRTY
    return sha if committed else f"{sha}{reuse_rules.DIRTY}"


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
        priority: CheckPriority,
        wait_max_s: float = WAIT_MAX_S,
        witness: str | None = None,
        parallel: bool = False,
        affected: AffectedPlan | None = None,
        criteria_base: str | None = None,
        reuse: Reuse | None = None,
        rerun_forms: Mapping[str, RerunForm] | None = None,
        flake_gate: FlakeGate = "delivery",
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
        # How close to done this gate is (GH-361): required, so no construction site can
        # forget to say, and with no default so none can fall back to a wrong one.
        self.priority = priority
        # The most the gate waits for a share over its whole life, `check_wait_max_s`.
        self._wait_max_s = wait_max_s
        # What the scheduler gave the last time the gate ran: `None` until one did. The
        # first `enqueued_at` and the token stay with the instance, so a gate refused and
        # retried keeps one place in the queue (ADR-0070); `waited_s` is the total wait.
        self.grant: Grant | None = None
        self.waited_s = 0.0
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
        # What keys a stored pass (ADR-0065), or None: every check runs, as before.
        # Keyword-only with this default so every construction site compiles untouched.
        self.reuse = reuse
        # The product's failed-tests-only forms (ADR-0066), by check, and which gate this
        # presubmit is: the form is used only at the delivery gate. None and "delivery" are
        # the gate of before, plus the one full rerun of a red check. Keyword-only with
        # these defaults so every construction site compiles untouched.
        self.rerun_forms: Mapping[str, RerunForm] = rerun_forms or {}
        self.flake_gate: FlakeGate = flake_gate
        # Set once per call on a clean, moved tree (`_prepare_reuse`): the tree, the
        # environment fingerprint, and whether this checkout may store what it runs.
        self._tree: str | None = None
        self._fingerprint: str | None = None
        self._may_write = False
        # The checks that failed in their full form on this instance: they never run
        # their affected form again, even after they later pass in full. A new Developer
        # run builds a new instance and starts on affected-only again.
        self.stuck_full: set[str] = set()
        self.refusals = 0
        # The failed ids each refusal named, in order (None when the product declares no
        # `rerun_checks`): `presubmit.refusal_allowed` reads the last two.
        self._refused_ids: list[tuple[str, ...] | None] = []
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
        # The wait and the run of each of those invocations, apart (GH-361): one record per
        # entry of `invocations`, in the same order.
        self.gates: list[GateRecord] = []
        # The declared command behind each command a row ran, `{workers}` still in it: what
        # a stored pass is keyed by, so the share never splits the store.
        self._declared: dict[tuple[str, str], str] = {}

    @property
    def timeout_s(self) -> float:
        witness = self.check_timeout_s if self.witness else 0
        if self.parallel:
            # Every check runs at once, each under its own `check_timeout_s`: the
            # slowest one bounds the call, and the witness runs before them, alone. With
            # an affected plan that one worker may run its affected form and then the
            # full one, one after the other.
            # The worker's last row may be red and rerun once more (ADR-0066).
            runs = (2 if self.affected is not None else 1) + 1
            return self.check_timeout_s * runs + witness
        # Serial: each declared check may be rerun once, one more run per check.
        reruns = self.check_timeout_s * len(self.checks)
        if self.affected is not None:
            runs = len(self.checks) + len(self.affected.commands)
            return self.check_timeout_s * max(runs, 1) + reruns + witness
        base = self.check_timeout_s * max(len(self.checks), 1)
        return base + reruns + (self.check_timeout_s if self.witness else 0)

    @property
    def wait_max_s(self) -> float:
        """The configured wait for a share. Not part of `timeout_s`: the checks' clock
        starts after the grant, and the delivery hook adds this on its own."""
        return self._wait_max_s

    @property
    def gate(self) -> GateRecord | None:
        """The record of the last invocation that ran the checks, or None when none did."""
        return self.gates[-1] if self.gates else None

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
            witness_reason = self._run_holding_a_share(head)
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
        if reason is None:
            return None
        # A third refusal asks whether the failure moved between the last two refusals.
        previous, current = ([None, None, *self._refused_ids])[-2:]
        if not presubmit.refusal_allowed(self.refusals, previous, current):
            return None
        self.refusals += 1
        self._refused_ids.append(self._failed_ids())
        return presubmit.refused(reason, self.refusals)

    def _run_holding_a_share(self, head: str) -> str | None:
        """The witness and the checks, under a share of the machine's cores. `__call__` is
        its only caller; the acquire itself is `_share`, the one place that asks the
        scheduler (ADR-0069). The time spent waiting for the share is over before any
        command starts, so it is never part of a check's timeout."""
        asked_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._share() as grant:
            granted = time.monotonic()
            self._prepare_reuse()
            witness_reason = None
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
            self.gates.append(
                {
                    "started_at": asked_at,
                    "wait_s": round(grant.wait_s, 3),
                    "run_s": round(time.monotonic() - granted, 3),
                    "share": grant.share,
                    "peers": grant.peers,
                    "priority": self.priority.value,
                    "overdue": grant.overdue,
                }
            )
            # A pass is stored only after the witness had its say in this same call: a
            # checkout the witness doubts proves nothing about the tree.
            if self.witness is None or witness_reason is None:
                self._store_passes(results)
            return witness_reason

    @contextmanager
    def _share(self, *, fresh: bool = False) -> Iterator[Grant]:
        """Hold a share of the machine's cores, given back whatever happens inside. The only
        method that calls the `CheckScheduler` port. A gate keeps its first `enqueued_at`
        and token across the calls it is refused and retried in, and the seconds it already
        waited count against its ceiling. `fresh` is for a one-off check the Developer asked
        for through `run_check`: a new place in the queue (an old `enqueued_at` would age it
        into `land`), its own full wait, and the gate's grant is back as it was afterwards."""
        previous = self.grant
        keep = previous is not None and not fresh
        request = CheckRequest(
            self.priority,
            wait_max_s=max(self._wait_max_s - (0.0 if fresh else self.waited_s), 0.0),
            expires_after_s=self.timeout_s + HOLD_MARGIN_S,
            enqueued_at=previous.enqueued_at if keep and previous else None,
            token=previous.token if keep and previous else None,
        )
        scheduler = self.ports.check_scheduler
        grant = scheduler.acquire(request)
        self.grant = grant
        if not fresh:
            self.waited_s += grant.wait_s
        try:
            yield grant
        finally:
            scheduler.release(grant)
            if fresh:
                self.grant = previous

    def _failed_ids(self) -> tuple[str, ...] | None:
        """The test ids the failing checks of this look name, or None when the product declares
        no failed-tests form (the gate then keeps its budget of before). Read from each failing
        check's first run, the one that saw the whole failure, never from its rerun."""
        if not self.rerun_forms:
            return None
        ids: list[str] = []
        for final in presubmit.final_results(self.results or []):
            form = self.rerun_forms.get(final["name"])
            if final["passed"] or form is None:
                continue
            first = [
                r for r in self.results or [] if r["name"] == final["name"] and not r.get("rerun")
            ]
            ids.extend(presubmit.failed_ids(first[-1]["output"], form.test_id))
        return tuple(ids)

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
            return [row for n, c in self.checks.items() for row in self._run_once(n, c)]
        # `map` keeps declared order. If a `_run` raises, leaving the `with` waits for
        # the other started checks before the error propagates, so no check outlives
        # the call; nothing is recorded for it, the same as serial.
        with ThreadPoolExecutor(max_workers=max(len(self.checks), 1)) as pool:
            per_check = pool.map(lambda item: self._run_once(*item), self.checks.items())
            return [row for rows in per_check for row in rows]

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
            # `{base}` first, then `{workers}`: the order the substitution is defined in.
            form = presubmit.affected_command(plan.commands[name], plan.base)
            first = self._run(name, form, "affected-only")
            rows.append(first)
            if first["passed"]:
                return rows
        full = self._run(name, command, "full")
        rows.append(full)
        if not full["passed"]:
            self.stuck_full.add(name)
        return self._with_rerun(name, command, rows)

    def _run_once(self, name: str, command: str) -> list[CheckResult]:
        """One check without a plan: its row, and its one rerun when it went red."""
        return self._with_rerun(name, command, [self._run(name, command)])

    def _with_rerun(self, name: str, command: str, rows: list[CheckResult]) -> list[CheckResult]:
        """`rows` and, right after them, the one rerun of the check's last row when the
        domain says it reruns (ADR-0066): a red row that did not time out. Never the stored
        pass: a rerun is a run, and the row says so."""
        last = rows[-1]
        form = self.rerun_forms.get(name)
        rerun_as = presubmit.rerun_command(command, last, form, self.flake_gate)
        if rerun_as is None:
            return rows
        again = self._execute(name, rerun_as, last.get("mode"))
        again["rerun"] = True
        return [*rows, again]

    def flakes(self) -> list[FlakeRecord]:
        """The flakes of the last call: one record per check the domain judges a `flake`,
        with the test ids its first failure named when the product declares a form."""
        rows = self.results or []
        head = self.head
        if head is None:
            return []
        records: list[FlakeRecord] = []
        for name, verdict in presubmit.outcomes(rows).items():
            if verdict != "flake":
                continue
            first = [r for r in rows if r["name"] == name and not r.get("rerun")][-1]
            form = self.rerun_forms.get(name)
            ids = presubmit.failed_ids(first["output"], form.test_id) if form else ()
            records.append(
                {"check": name, "head": head, "gate": self.flake_gate, "tests": list(ids)}
            )
        return records

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
        # The witness gets the grant's environment but its command is never substituted.
        result = self.ports.commands.run(
            self.path, command, self.check_timeout_s, env=self._grant_env()
        )
        reason = presubmit.witness_refusal(
            command, result.output, result.exit_code, result.timed_out, checkout
        )
        self.witness_result = {
            "command": command,
            "output": presubmit.said(result.output, result.timed_out, self.check_timeout_s),
            "passed": reason is None,
        }
        return reason

    def _grant_env(self) -> dict[str, str] | None:
        """The worker count of the share held, as the environment of every command; None
        without a grant, which is the call of before."""
        return None if self.grant is None else presubmit.workers_env(self.grant.share)

    def _sized(self, command: str) -> str:
        """`{workers}` in a declared command, sized to the share held; the command as it is
        without a grant or without the placeholder."""
        if self.grant is None:
            return command
        return presubmit.workers_command(command, self.grant.share)

    def _prepare_reuse(self, eligible: bool = True) -> None:
        """Decide, once per call, what this clean tree may read from and write to the store.
        Reading needs the tree and a fingerprint; writing also needs a record that setup
        ran on a tree whose environment inputs this one did not change. `eligible` is false
        for a tree that is dirty or has no commit: it only clears what an earlier call set."""
        self._tree = self._fingerprint = None
        self._may_write = False
        reuse = self.reuse
        if not eligible or reuse is None or self.ports.check_results is None:
            return
        fingerprint = reuse_rules.fingerprint(
            reuse.setup, reuse.stage0_version, self.check_timeout_s
        )
        if fingerprint is None:
            return
        workspace = self.ports.workspace
        try:
            tree = workspace.tree(self.path)
        except (GitError, OSError):
            return
        self._tree, self._fingerprint = tree, fingerprint
        try:
            recorded = workspace.setup_tree(self.path)
            self._may_write = recorded is not None and not reuse_rules.env_drifted(
                workspace.tree_changes(self.path, recorded, tree), reuse.setup
            )
        except (GitError, OSError):
            self._may_write = False

    def _key(self, name: str, command: str) -> str | None:
        reuse = self.reuse
        if reuse is None or self._tree is None or self._fingerprint is None:
            return None
        return reuse_rules.check_key(reuse.product, name, command, self._tree, self._fingerprint)

    def _stored(self, name: str, command: str, mode: Mode | None) -> CheckResult | None:
        """The pass stored for this exact check on this tree, as a row marked reused."""
        key = self._key(name, command)
        store = self.ports.check_results if key is not None else None
        if key is None or store is None:
            return None
        row = store.get(key, time.time())
        if row is None or row["passed"] is not True:
            return None
        row.pop("mode", None)
        if mode is not None:
            row["mode"] = mode
        row["reused"] = True
        return row

    def _store_passes(self, results: list[CheckResult]) -> None:
        store = self.ports.check_results if self._may_write else None
        if store is None:
            return
        for row in results:
            # Keyed by the command as declared, `{workers}` and all, the same as the lookup in
            # `_stored`: a pass at a share of 6 is the pass of the check at a share of 12.
            declared = self._declared.get((row["name"], row["command"]), row["command"])
            key = self._key(row["name"], declared)
            if key is None or not row["passed"] or row.get("reused") or row.get("rerun"):
                continue
            try:
                store.put(key, row, time.time())
            except OSError:
                # A store that cannot be written is only a miss the next time.
                return

    def _run(self, name: str, command: str, mode: Mode | None = None) -> CheckResult:
        if (stored := self._stored(name, command, mode)) is not None:
            return stored
        return self._execute(name, command, mode)

    def _execute(self, name: str, command: str, mode: Mode | None = None) -> CheckResult:
        started = time.monotonic()
        declared, command = command, self._sized(command)
        self._declared[(name, command)] = declared
        result = self.ports.commands.run(
            self.path, command, self.check_timeout_s, env=self._grant_env()
        )
        duration_s = time.monotonic() - started
        output = presubmit.said(result.output, result.timed_out, self.check_timeout_s)
        row: CheckResult = {
            "name": name,
            "command": command,
            "passed": result.passed,
            "output": output,
            "duration_s": duration_s,
        }
        if result.timed_out:
            row["timed_out"] = True
        if mode is not None:
            row["mode"] = mode
        return row
