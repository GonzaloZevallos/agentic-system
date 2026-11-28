"""The Developer's `run_check` tool over the ports (ADR-0065, GH-359).

A check the product declares, run on demand inside the Developer's own checkout, through the
same `CheckoutPresubmit._run` the gate uses: so its row (exit, output, duration) is the
system's measurement and its reuse of a stored pass is the gate's. The model supplies only
the name. What it adds is the one write the gate does not make: a pass the Developer ran by
hand is stored, under the same conditions `CheckoutPresubmit.__call__` stores one (a clean
tree with a commit, a fingerprint, a valid setup-tree record, no environment drift, a
witness that agreed in this same call or none declared), so the gate later reuses it instead
of running the check again."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import replace

from ..domain import presubmit, run_check
from ..domain.presubmit import CheckResult, GateForm, tail
from ..domain.run_check import (
    CheckRun,
    Outcome,
    Retry,
    Verification,
    VerificationCall,
    resolve_wall,
)
from .ports import GitError
from .presubmit import CheckoutPresubmit, stop_if_abandoned


def resolved_walls(
    verifications: Mapping[str, Verification],
    check_timeouts: Mapping[str, float],
    check_timeout_s: float,
    ceiling: float,
) -> dict[str, float]:
    """The wall of every verification and every check the product gave one, each through
    `resolve_wall` (ADR-0065 addendum, GH-451), the only place a wall is decided. Only what
    the product declared is in it: every other command keeps `check_timeout_s`. A figure over
    `ceiling` raises `WallOverCeiling` here too, so it is never clipped on the way to a run."""
    declared = {name: v.wall_s for name, v in verifications.items()} | dict(check_timeouts)
    return {name: resolve_wall(name, declared, check_timeout_s, ceiling) for name in declared}


class RunCheck:
    """One per Developer run, over a `CheckoutPresubmit` that holds every declared check
    (not only the delivery gate's fast tier) and the same `Reuse` the delivery gate has: the
    key, the fingerprint, the setup-tree record and the drift rule are the gate's own
    (`CheckoutPresubmit._prepare_reuse`), so what this tool stores is what the gate reads. A
    `+dirty` stage0 has no fingerprint, so it neither reads nor stores (`check_results`).
    `clock` gives the `now` a pass is written at; the domain never reads it."""

    def __init__(
        self,
        gate: CheckoutPresubmit,
        *,
        verifications: Mapping[str, Verification] | None = None,
        clock: Callable[[], float] = time.time,
        load: Callable[[], float | None] | None = None,
        cores: int | None = None,
        delivery: Collection[str] = (),
        affected: Mapping[str, str] | None = None,
        base: str = "",
        red: Callable[[], frozenset[str]] = frozenset,
    ) -> None:
        assert gate.reuse is not None, "run_check needs a presubmit that carries a Reuse"
        self.gate = gate
        # What decides a refusal (`run_check.suite_refusal`, ADR-0065 addendum 2026-11-25): the
        # checks the delivery gate runs, their affected forms at `base`, and the checks the gate
        # or the full tier failed on, read at every call. The defaults refuse nothing.
        self._delivery = tuple(delivery)
        self._affected: Mapping[str, str] = affected or {}
        self._base = base
        self._red = red
        # The product's declared verifications (ADR-0065 addendum): run on demand like a check
        # but under their own wall, and never stored. Their walls travel in `gate.walls`.
        self.verifications: Mapping[str, Verification] = verifications or {}
        self.clock = clock
        # The machine's load and core count around a verification, for the evidence line only
        # (ADR-0065 addendum): a reader that cannot answer, or none, is a load "not read".
        self._load = load
        self._cores = cores
        # One entry per call on a verification, a refused name included, in order: what the
        # system writes into the Developer's section, never what the model says it ran.
        self.calls: list[VerificationCall] = []
        # The form each call on a declared check ran, in call order, with `via` "tool": read by
        # the develop node, which records them on the run before the gate's (GH-1096).
        self.forms: list[GateForm] = []

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.gate.checks)

    @property
    def verification_names(self) -> tuple[str, ...]:
        return tuple(self.verifications)

    @property
    def timeout_s(self) -> float:
        """The longest one call may take: every declared check could be asked for, and the
        witness runs before it (ADR-0065's `MCP_TOOL_TIMEOUT` assumption), plus the
        runs a call may spend on each declared check (`call_runs`: the full form and its
        `not_ready` retry, and the affected form before them when the check has one), so the
        harness never kills a call that is inside them, plus the wait for a share (ADR-0070). A
        product that declared a wall gets the larger of that and `call_timeout_s` (ADR-0065
        addendum): the longest wall that many times, the wait twice and the witness. One that
        declared none, and no plan, gets the figure it always had."""
        gate = self.gate
        plan = gate.affected
        # Per declared check, how many runs one call may spend on it: `call_runs`, and two for
        # every check when there is no plan (the figure the tool always had).
        runs = [
            run_check.call_runs(plan is not None and name in plan.commands) for name in gate.checks
        ]
        legacy = gate.check_timeout_s * (1 + sum(runs)) + gate.wait_max_s
        if not gate.walls:
            return legacy
        witness_s = gate.check_timeout_s if gate.witness else 0.0
        walls = list(gate.walls.values())
        longest = run_check.call_timeout_s(walls, gate.wait_max_s, witness_s, max(runs, default=2))
        return max(legacy, longest)

    def __call__(self, name: str, *, abandoned: Callable[[], bool] | None = None) -> str:
        return run_check.render(self.run(name, abandoned=abandoned))

    def run(self, name: str, *, abandoned: Callable[[], bool] | None = None) -> CheckRun:
        """`abandoned` is the flag of the attempt that asked (GH-1169), or None. Once it is set
        the call raises `CheckAbandoned` at the next look and writes nothing to the gate, the
        store or `calls`; a check already launched runs to its own wall, which the flag cannot
        shorten."""
        stop_if_abandoned(abandoned)
        gate = self.gate
        command = gate.checks.get(name)
        verification = None if command is not None else self.verifications.get(name)
        if verification is not None:
            command = verification.command
        if command is None:
            known = [*self.names, *self.verifications]
            self.calls.append(
                VerificationCall(name, Outcome.NOT_DECLARED, 0.0, None, None, None, "")
            )
            return CheckRun(name, refused=run_check.unknown_check(name, known))
        workspace = gate.ports.workspace
        # The head now, on every call: `before` only says whether anything was ever committed.
        head = workspace.head(gate.path)
        committed = head != gate.before
        pending, clean, tree_read = (None, False, True)
        if verification is None:
            pending, clean, tree_read = self._look(gate)
        # Before the share, the witness and every store access: a refused call takes none of
        # them and leaves no `VerificationCall`, like an undeclared name.
        if (
            refused := run_check.suite_refusal(
                name,
                delivery=self._delivery,
                affected=self._affected,
                verifications=self.verifications,
                red=self._red(),
                base=self._base,
                stores=committed and clean,
            )
        ) is not None:
            return CheckRun(name, refused=refused)
        # What this tree may read and store is decided by the gate's own rule, here and
        # never again below; on a dirty or uncommitted tree it clears whatever an earlier
        # call left, so nothing is read or written for a tree that is not the head's.
        # A verification is never read from or written to the store (ADR-0065 addendum).
        stop_if_abandoned(abandoned)
        gate._prepare_reuse(committed and clean and verification is None)
        # The witness and the check run under a share of the machine's cores, like the gate's
        # (ADR-0070): without one a `{workers}` in the command would reach the shell as text.
        # Its place in the queue is its own, apart from the delivery gate's.
        with gate._share(fresh=True, abandoned=abandoned):
            stop_if_abandoned(abandoned)
            # A command with a `not_ready_exit` has its load read by the gate's classification
            # (in the row); one read of ours would only repeat it.
            measured = verification is not None and not self._classified(name)
            before = self._read_load() if measured else None
            # The witness runs before the check, in this same call: its agreement is what
            # lets the pass be stored. A disagreement does not stop the check, as at the gate.
            witness_reason = (
                gate._run_witness(gate.witness, abandoned) if gate.witness is not None else None
            )
            workers = gate.whole_share()
            planned: presubmit.PlannedForm | None = None
            rows: list[CheckResult] = []
            if verification is not None:
                row = gate._run(name, command, workers, None, abandoned)
            else:
                planned, rows = self._planned_rows(name, command, head, pending, workers, abandoned)
                row = presubmit.final_results(rows)[-1]
            after = self._read_load() if measured else None
            outcome: Outcome | None = None
            retry: Retry | None = None
            if self._classified(name):
                settled, outcome, retry = self._settle(name, command, row, workers, abandoned)
                if settled is not row:
                    rows, row = [*rows, settled], settled
        stop_if_abandoned(abandoned)  # no call is recorded, no pass stored, for nobody awaiting it
        if verification is not None:
            return self._verified(name, row, witness_reason, outcome, (before, after), retry)
        assert planned is not None
        form = gate.form_of(planned, rows)
        if form["mode"] == "full" and row["passed"]:
            # Only the tool releases a check from `stuck_full`, and only on a passing full row.
            gate.stuck_full.discard(name)
        passed, reused = row["passed"], bool(row.get("reused", False))
        # The deciding row is stored under its own declared command: an affected pass is the
        # pass of the affected form, never of the full one.
        key = gate._key(name, gate._declared_of(row))
        store = gate.ports.check_results
        fingerprinted = gate._fingerprint is not None
        # Past a fingerprint the gate's `_may_write` is the record and the drift rule in one:
        # which of the two refused is only read back for the reason the tool reports.
        recorded = not fingerprinted or workspace.setup_tree(gate.path) is not None
        reason = run_check.unrecorded_reason(
            passed=passed,
            reused=reused,
            committed=committed,
            clean=clean,
            fingerprinted=fingerprinted,
            setup_recorded=recorded,
            drifted=fingerprinted and recorded and not gate._may_write,
            witness_agreed=witness_reason is None,
            store=store is not None,
            tree_read=tree_read,
        )
        if reason is None and not reused and store is not None and key is not None:
            try:
                store.put(key, row, self.clock())
            except OSError:
                # As at the gate: a store that cannot be written is only a miss later.
                reason = "the store could not be written"
        self.forms.append(form)
        return self._measured(
            CheckRun(
                name,
                passed=passed,
                reused=reused,
                output=row["output"],
                duration_s=row.get("duration_s", 0.0),
                unrecorded=reason,
                witness=self._witness_line(witness_reason),
                form=form,
            ),
            row,
            outcome,
            retry,
        )

    def _look(self, gate: CheckoutPresubmit) -> tuple[tuple[str, ...] | None, bool, bool]:
        """What the tree has pending, whether it is clean, and whether it could be read, once
        per call. With the `PendingPaths` port wired a tree is clean when it has nothing
        pending and a lookup that fails is pending `None`, not clean, not read. Without it the
        tree is `is_clean`, with `()` pending when it is and `None` when it is not."""
        port = gate.ports.pending_paths
        if port is None:
            clean = gate.ports.workspace.is_clean(gate.path)
            return ((), True, True) if clean else (None, False, True)
        try:
            pending = port.pending_paths(gate.path)
        except (GitError, OSError):
            return None, False, False
        return pending, not pending, True

    def _planned_rows(
        self,
        name: str,
        command: str,
        head: str,
        pending: tuple[str, ...] | None,
        workers: int | None,
        abandoned: Callable[[], bool] | None = None,
    ) -> tuple[presubmit.PlannedForm, list[CheckResult]]:
        """What a declared check was planned to run and the rows it ran. Under a plan, the
        gate's own `_planned_rows` (the affected form, then the full one on a red); without
        one, the full command once. The changed paths come only from the gate's `_paths`."""
        gate = self.gate
        plan = gate.affected
        if plan is None:
            return presubmit.UNPLANNED, [gate._run(name, command, workers, None, abandoned)]
        planned = presubmit.plan_form(
            name,
            gate._paths(plan, head, pending),
            plan.fallback,
            plan.commands,
            frozenset(gate.stuck_full),
        )
        return planned, gate._planned_rows(name, command, planned, plan, workers, abandoned)

    def _classified(self, name: str) -> bool:
        ready = self.gate.readiness
        return ready is not None and name in ready.exits

    def _read_load(self) -> float | None:
        """The load for the evidence line: the reader the caller gave, else the readiness
        reader the gate classifies with (the `LoadReader` port, the only `getloadavg`)."""
        if self._load is not None:
            return self._load()
        ready = self.gate.readiness
        return ready.load.one_minute() if ready is not None else None

    def evidence(self) -> str | None:
        """The block of the verification calls for the Developer's section; None when there
        were none, so a delivery that ran no verification writes the section it always did."""
        return run_check.evidence_markdown(self.calls)

    def _settle(
        self,
        name: str,
        command: str,
        first: CheckResult,
        workers: int | None,
        abandoned: Callable[[], bool] | None = None,
    ) -> tuple[CheckResult, Outcome, Retry | None]:
        """What a command with a declared `not_ready_exit` came to. A `not_ready` first row is
        run once more in this same call, judged by the gate's own `flake_verdict`; the pair is
        never recorded as a flake. A second `not_ready` is `unverified`: the machine could not
        say, which is neither a pass nor a red."""
        if not first.get("not_ready", False):
            return first, Outcome.PASSED if first["passed"] else Outcome.FAILED, None
        # The first run is kept as a `Retry`: the second run's figures replace it in the row,
        # and the reader must still see what justified the retry.
        spent = Retry(
            first.get("duration_s", 0.0), first.get("load_before"), first.get("load_after")
        )
        again = self.gate._execute(name, command, workers, None, abandoned)
        if presubmit.flake_verdict(first, again) == "flake":
            return again, Outcome.PASSED, spent
        failed = Outcome.UNVERIFIED if again.get("not_ready", False) else Outcome.FAILED
        return again, failed, spent

    def _verified(
        self,
        name: str,
        row: CheckResult,
        witness_reason: str | None,
        outcome: Outcome | None,
        load: tuple[float | None, float | None],
        retry: Retry | None,
    ) -> CheckRun:
        """What the system measured of a verification: its row, and never a stored pass. The
        call is recorded here, with the load the gate's classification read when the command
        has a `not_ready_exit` and the load read around the run otherwise."""
        outcome = outcome or (Outcome.PASSED if row["passed"] else Outcome.FAILED)
        ready = self.gate.readiness
        before = row["load_before"] if "load_before" in row else load[0]
        after = row.get("load_after", load[1]) if "load_before" in row else load[1]
        cores = self._cores if self._cores is not None else (ready.cores if ready else None)
        self.calls.append(
            VerificationCall(
                name,
                outcome,
                row.get("duration_s", 0.0),
                before,
                after,
                cores,
                tail(row["output"]),
                retry,
            )
        )
        return self._measured(
            CheckRun(
                name,
                passed=row["passed"],
                output=row["output"],
                duration_s=row.get("duration_s", 0.0),
                unrecorded=run_check.UNRECORDED_VERIFICATION,
                witness=self._witness_line(witness_reason),
            ),
            row,
            outcome,
            retry,
        )

    def _measured(
        self, run: CheckRun, row: CheckResult, outcome: Outcome | None, retry: Retry | None
    ) -> CheckRun:
        """`run` with the outcome and the load a call measured; the load only for a command the
        product declared a `not_ready_exit` for, so every other reply is what it was."""
        ready = self.gate.readiness
        run = replace(run, outcome=outcome, retry=retry)
        if ready is None or "load_before" not in row:
            return run
        return replace(
            run, load_before=row["load_before"], load_after=row.get("load_after"), cores=ready.cores
        )

    def _witness_line(self, reason: str | None) -> str | None:
        if self.gate.witness is None:
            return None
        return "agreed" if reason is None else f"disagreed: {reason}"
