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
from collections.abc import Callable

from ..domain import run_check
from ..domain.run_check import CheckRun
from .presubmit import CheckoutPresubmit


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
        clock: Callable[[], float] = time.time,
    ) -> None:
        assert gate.reuse is not None, "run_check needs a presubmit that carries a Reuse"
        self.gate = gate
        self.clock = clock

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.gate.checks)

    @property
    def timeout_s(self) -> float:
        """The longest one call may take: every declared check could be asked for, and the
        witness runs before it (ADR-0065's `MCP_TOOL_TIMEOUT` assumption), plus the one
        rerun the gate's own allowance gives each declared check (ADR-0066), so the harness
        never kills a call that is inside one, plus the wait for a share (ADR-0070)."""
        checks = len(self.gate.checks)
        return self.gate.check_timeout_s * (checks + 1 + checks) + self.gate.wait_max_s

    def __call__(self, name: str) -> str:
        return run_check.render(self.run(name))

    def run(self, name: str) -> CheckRun:
        gate = self.gate
        command = gate.checks.get(name)
        if command is None:
            return CheckRun(name, refused=run_check.unknown_check(name, self.names))
        workspace = gate.ports.workspace
        committed = workspace.head(gate.path) != gate.before
        clean = workspace.is_clean(gate.path)
        # What this tree may read and store is decided by the gate's own rule, here and
        # never again below; on a dirty or uncommitted tree it clears whatever an earlier
        # call left, so nothing is read or written for a tree that is not the head's.
        gate._prepare_reuse(committed and clean)
        # The witness and the check run under a share of the machine's cores, like the gate's
        # (ADR-0070): without one a `{workers}` in the command would reach the shell as text.
        # Its place in the queue is its own, apart from the delivery gate's.
        with gate._share(fresh=True):
            # The witness runs before the check, in this same call: its agreement is what
            # lets the pass be stored. A disagreement does not stop the check, as at the gate.
            witness_reason = gate._run_witness(gate.witness) if gate.witness is not None else None
            row = gate._run(name, command)
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
            witness=None
            if gate.witness is None
            else ("agreed" if witness_reason is None else f"disagreed: {witness_reason}"),
        )
