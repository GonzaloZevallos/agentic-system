"""What the Developer's `run_check` tool decides and says (ADR-0065, GH-359).

The tool runs a check the product already declares, inside the Developer's own checkout, and
reports what the system measured. This module is the pure half: whether a pass may be stored
and, when it may not, the one reason that says why; and the words the model reads. Nothing
here reads a clock, a file or a port."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import presubmit

# The tool's description, which reaches the model with the tool definitions and never with
# the system prompt: a product that declares no tiers renders the Developer prompt it always
# did (ADR-0064).
DESCRIPTION = (
    "Run one of the product's declared checks in your checkout, through the system. Run by "
    "hand, through this tool, only the checks for what you touched; the delivery gate runs "
    "the rest and reuses your passes on the same tree. The exit status, the output and the "
    "time come from the system, never from you."
)


@dataclass(frozen=True)
class CheckRun:
    """One call of the tool as the system measured it. `refused` is set when nothing ran
    (an unknown name); `unrecorded` is the reason a pass was not stored, or None when it
    was (or when it was reused, which is already stored)."""

    name: str
    passed: bool = False
    reused: bool = False
    output: str = ""
    duration_s: float = 0.0
    refused: str | None = None
    unrecorded: str | None = None
    witness: str | None = None


def unknown_check(name: str, declared: Sequence[str]) -> str:
    return f"unknown check {name!r}: the declared checks are {', '.join(declared) or 'none'}"


def unrecorded_reason(
    *,
    passed: bool,
    reused: bool,
    committed: bool,
    clean: bool,
    fingerprinted: bool,
    setup_recorded: bool,
    drifted: bool,
    witness_agreed: bool,
    store: bool,
) -> str | None:
    """Why a pass is not stored, or None when it is (invariant 3 of ADR-0065): a pass on a
    clean tree with a commit, a fingerprint, a valid setup-tree record, no drift and a
    witness that agreed in that same call or is not declared. A reused row is already in
    the store and is never written again."""
    if reused:
        return None
    if not passed:
        return "the check did not pass"
    if not committed:
        return "there is no commit yet"
    if not clean:
        return "the tree is not clean"
    if not fingerprinted:
        return "this system version has no fingerprint"
    if not setup_recorded:
        return "the checkout has no valid setup record"
    if drifted:
        return "the environment drifted since the setup"
    if not witness_agreed:
        return "the checkout witness did not agree"
    if not store:
        return "there is no store"
    return None


def render(run: CheckRun) -> str:
    """The reply the model reads: every figure is the system's."""
    if run.refused is not None:
        return run.refused
    lines = [
        f"check: {run.name}",
        f"passed: {'yes' if run.passed else 'no'}",
        f"reused: {'yes' if run.reused else 'no'}",
    ]
    if run.witness is not None:
        lines.append(f"witness: {run.witness}")
    if run.unrecorded is not None:
        lines.append(f"not recorded: {run.unrecorded}")
    lines.append(f"duration: {run.duration_s:.1f} s")
    lines.append(f"output:\n{presubmit.tail(run.output)}")
    return "\n".join(lines)
