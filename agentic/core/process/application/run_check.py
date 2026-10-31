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
from collections.abc import Callable, Mapping

from ..domain import run_check
from ..domain.presubmit import CheckResult, tail
from ..domain.run_check import CheckRun, Outcome, Verification, VerificationCall, resolve_wall
from .presubmit import CheckoutPresubmit


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
    ) -> None:
        assert gate.reuse is not None, "run_check needs a presubmit that carries a Reuse"
        self.gate = gate
        # The product's declared verifications (ADR-0065 addendum): run on demand like a check
        # but under their own wall, and never stored. Their walls travel in `gate.timeouts`.
        self.verifications: Mapping[str, Verification] = verifications or {}
        self.clock = clock
        # The machine's load and core count around a verification, for the evidence line only
        # (ADR-0065 addendum): a reader that cannot answer, or none, is a load "not read".
        self._load = load
        self._cores = cores
        # One entry per call on a verification, a refused name included, in order: what the
        # system writes into the Developer's section, never what the model says it ran.
        self.calls: list[VerificationCall] = []

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.gate.checks)

    @property
    def verification_names(self) -> tuple[str, ...]:
        return tuple(self.verifications)

    @property
    def timeout_s(self) -> float:
        """The longest one call may take: every declared check could be asked for, and the
        witness runs before it (ADR-0065's `MCP_TOOL_TIMEOUT` assumption), plus the one
        rerun the gate's own allowance gives each declared check (ADR-0066), so the harness
        never kills a call that is inside one, plus the wait for a share (ADR-0070). A product
        that declared a wall gets the larger of that and `call_timeout_s` (ADR-0065 addendum):
        the longest wall twice, for the `not_ready` retry, the wait twice and the witness. One
        that declared none gets the figure it always had."""
        gate = self.gate
        checks = len(gate.checks)
        legacy = gate.check_timeout_s * (checks + 1 + checks) + gate.wait_max_s
        if not gate.timeouts:
            return legacy
        witness_s = gate.check_timeout_s if gate.witness else 0.0
        walls = list(gate.timeouts.values())
        return max(legacy, run_check.call_timeout_s(walls, gate.wait_max_s, witness_s))

    def __call__(self, name: str) -> str:
        return run_check.render(self.run(name))

    def run(self, name: str) -> CheckRun:
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
        committed = workspace.head(gate.path) != gate.before
        clean = workspace.is_clean(gate.path)
        # What this tree may read and store is decided by the gate's own rule, here and
        # never again below; on a dirty or uncommitted tree it clears whatever an earlier
        # call left, so nothing is read or written for a tree that is not the head's.
        # A verification is never read from or written to the store (ADR-0065 addendum).
        gate._prepare_reuse(committed and clean and verification is None)
        # The witness and the check run under a share of the machine's cores, like the gate's
        # (ADR-0070): without one a `{workers}` in the command would reach the shell as text.
        # Its place in the queue is its own, apart from the delivery gate's.
        with gate._share(fresh=True):
            before = self._read_load() if verification is not None else None
            # The witness runs before the check, in this same call: its agreement is what
            # lets the pass be stored. A disagreement does not stop the check, as at the gate.
            witness_reason = gate._run_witness(gate.witness) if gate.witness is not None else None
            row = gate._run(name, command)
            after = self._read_load() if verification is not None else None
        if verification is not None:
            return self._verified(name, row, witness_reason, (before, after))
        passed, reused = row["passed"], bool(row.get("reused", False))
        key = gate._key(name, command)
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
        )
        if reason is None and not reused and store is not None and key is not None:
            try:
                store.put(key, row, self.clock())
            except OSError:
                # As at the gate: a store that cannot be written is only a miss later.
                reason = "the store could not be written"
        return CheckRun(
            name,
            passed=passed,
            reused=reused,
            output=row["output"],
            duration_s=row.get("duration_s", 0.0),
            unrecorded=reason,
            witness=self._witness_line(witness_reason),
        )

    def _read_load(self) -> float | None:
        return self._load() if self._load is not None else None

    def evidence(self) -> str | None:
        """The block of the verification calls for the Developer's section; None when there
        were none, so a delivery that ran no verification writes the section it always did."""
        return run_check.evidence_markdown(self.calls)

    def _verified(
        self,
        name: str,
        row: CheckResult,
        witness_reason: str | None,
        load: tuple[float | None, float | None],
    ) -> CheckRun:
        """What the system measured of a verification: its row, and never a stored pass. The
        call is recorded here, with the load read around the run."""
        self.calls.append(
            VerificationCall(
                name,
                Outcome.PASSED if row["passed"] else Outcome.FAILED,
                row.get("duration_s", 0.0),
                load[0],
                load[1],
                self._cores,
                tail(row["output"]),
            )
        )
        return CheckRun(
            name,
            passed=row["passed"],
            output=row["output"],
            duration_s=row.get("duration_s", 0.0),
            unrecorded=run_check.UNRECORDED_VERIFICATION,
            witness=self._witness_line(witness_reason),
        )

    def _witness_line(self, reason: str | None) -> str | None:
        if self.gate.witness is None:
            return None
        return "agreed" if reason is None else f"disagreed: {reason}"
