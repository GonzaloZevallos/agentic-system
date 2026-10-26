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
from collections.abc import Callable, Sequence

from agentic.shared.stage0 import CHECKOUT

from ..domain import check_results, run_check
from ..domain.run_check import CheckRun
from .ports import GitError, Ports
from .presubmit import CheckoutPresubmit


def stage0_version(ports: Ports) -> str:
    """The full sha of the system this run executes from, `+dirty` when its tracked files
    carry uncommitted changes or when git cannot say (ADR-0065: such a version has no
    fingerprint, so it neither reads nor stores)."""
    try:
        if ports.workspace.repository_at(CHECKOUT) is None:
            return f"unknown{check_results.DIRTY}"
        sha = ports.workspace.full_head(CHECKOUT)
        return sha if ports.workspace.is_committed(CHECKOUT) else f"{sha}{check_results.DIRTY}"
    except (GitError, OSError):
        return f"unknown{check_results.DIRTY}"


class RunCheck:
    """One per Developer run, over a `CheckoutPresubmit` that holds every declared check
    (not only the delivery gate's fast tier). `stage0` is the system's own version: a
    `+dirty` one has no fingerprint, so it neither reads nor stores (`check_results`).
    `clock` gives the `now` the store is read and written at; the domain never reads it."""

    def __init__(
        self,
        gate: CheckoutPresubmit,
        *,
        product: str,
        stage0: str,
        setup: Sequence[str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.gate = gate
        self.product = product
        self.stage0 = stage0
        self.setup = tuple(setup)
        self.clock = clock

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.gate.checks)

    @property
    def timeout_s(self) -> float:
        """The longest one call may take: every declared check could be asked for, and the
        witness runs before it (ADR-0065's `MCP_TOOL_TIMEOUT` assumption)."""
        return self.gate.check_timeout_s * (len(self.gate.checks) + 1)

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
        # The witness runs before the check, in this same call: its agreement is what lets
        # the pass be stored. A disagreement does not stop the check, as at the gate.
        witness_reason = gate._run_witness(gate.witness) if gate.witness is not None else None
        row = gate._run(name, command)
        passed, reused = row["passed"], bool(row.get("reused", False))
        key = self._key(name, command) if committed and clean else None
        tree = workspace.tree(gate.path) if key is not None else None
        setup_tree = workspace.setup_tree(gate.path) if key is not None else None
        store = gate.ports.check_results
        reason = run_check.unrecorded_reason(
            passed=passed,
            reused=reused,
            committed=committed,
            clean=clean,
            fingerprinted=self._fingerprint() is not None,
            setup_recorded=setup_tree is not None,
            drifted=self._drifted(setup_tree, tree),
            witness_agreed=witness_reason is None,
            store=store is not None,
        )
        if reason is None and not reused and store is not None and key is not None:
            store.put(key, row, self.clock())
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

    def _fingerprint(self) -> str | None:
        return check_results.fingerprint(self.setup, self.stage0, self.gate.check_timeout_s)

    def _key(self, name: str, command: str) -> str | None:
        """The store key of `name` on the checkout's tree, or None without a fingerprint."""
        fingerprint = self._fingerprint()
        if fingerprint is None:
            return None
        tree = self.gate.ports.workspace.tree(self.gate.path)
        return check_results.check_key(self.product, name, command, tree, fingerprint)

    def _drifted(self, setup_tree: str | None, tree: str | None) -> bool:
        """A manifest, lock or tool-version file moved between the setup's tree and this
        one. Unreadable is drift: a pass is only stored on what could be compared."""
        if setup_tree is None or tree is None:
            return False  # `setup_recorded` already says no
        try:
            paths = self.gate.ports.workspace.changed_between_trees(setup_tree, tree)
        except (GitError, OSError):
            return True
        return check_results.env_drifted(paths, self.setup)
