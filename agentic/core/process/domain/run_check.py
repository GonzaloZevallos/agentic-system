"""What the Developer's `run_check` tool decides and says (ADR-0065, GH-359).

The tool runs a check the product already declares, inside the Developer's own checkout, and
reports what the system measured. This module is the pure half: whether a pass may be stored
and, when it may not, the one reason that says why; and the words the model reads. Nothing
here reads a clock, a file or a port."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

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

# Why a verification's pass is not stored: it is not a check the gate declares, so there is no
# result of it the gate could ever reuse (ADR-0065 addendum, GH-451).
UNRECORDED_VERIFICATION = "a verification is not a declared check"


class Outcome(StrEnum):
    """What a call of the tool on a declared verification came to, the closed set the system's
    evidence line names (ADR-0065, addendum GH-451). `not_ready` and `unverified` are not
    failures of the code: they never reach `flakes` and never count as a return."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_READY = "not_ready"
    UNVERIFIED = "unverified"
    NOT_DECLARED = "not_declared"


@dataclass(frozen=True)
class Verification:
    """One declared verification: the command the product runs and the wall it may take, in
    seconds. Both come from `stack.yaml`, never from a role of the run."""

    command: str
    wall_s: float


class WallOverCeiling(ValueError):
    """A declared wall above the system's ceiling. A `ValueError`, so the CLI refuses with it;
    it is never clipped to the ceiling."""


def resolve_wall(
    name: str, declared: Mapping[str, float], check_timeout_s: float, ceiling: float
) -> float:
    """The wall of one check or verification, the only place a wall of seconds is decided: the
    figure the product declared for `name`, or `check_timeout_s` when it declared none. A figure
    above `ceiling` (`Configuration.verification_wall_max_s`) raises `WallOverCeiling`, naming
    both; it is rejected and never recorded as the ceiling."""
    wall = declared.get(name, check_timeout_s)
    if wall > ceiling:
        raise WallOverCeiling(
            f"{name!r} declares a wall of {wall:g} s, above the ceiling of {ceiling:g} s "
            "(`verification_wall_max_s`, set by the owner in `.env`): lower the figure in "
            "`.agentic/stack.yaml` or raise the ceiling"
        )
    return wall


def call_timeout_s(walls: Sequence[float], wait_max_s: float, witness_s: float) -> float:
    """How long one `run_check` call may take as a whole: the largest wall twice (a
    `not_ready` run is retried once), the readiness wait twice, and the witness."""
    return max(walls, default=0.0) * 2 + 2 * wait_max_s + witness_s


def saturated(before: float | None, after: float | None, cores: int) -> bool:
    """Whether the machine was too loaded for a `not_ready` to be the code's: the larger of the
    two load readings is at least the core count. A reading that could not be taken is None and
    counts as not saturated, so a machine that cannot be read never excuses a red."""
    readings = [load for load in (before, after) if load is not None]
    return max(readings, default=0.0) >= cores


def description(verifications: Sequence[str]) -> str:
    """The tool's description: `DESCRIPTION` as it always was when the product declares no
    verification, and with their names after it when it does."""
    if not verifications:
        return DESCRIPTION
    return (
        f"{DESCRIPTION} It also runs the product's declared verifications, which take longer "
        f"than a Bash step may: {', '.join(verifications)}."
    )


@dataclass(frozen=True)
class Retry:
    """The first run of a call the system ran again because it was `not_ready` (ADR-0065
    addendum, GH-451): the figures of the run whose saturated load justified the retry, which
    the second run's own figures would otherwise replace."""

    duration_s: float
    load_before: float | None
    load_after: float | None


def retry_text(retry: Retry) -> str:
    """One phrase for a `Retry`, for the reply and for the system's line."""
    shown = [f"{v:.2f}" if v is not None else "?" for v in (retry.load_before, retry.load_after)]
    return (
        f"retried once after a not_ready (first run: {retry.duration_s:.1f} s, "
        f"load {shown[0]} before, {shown[1]} after)"
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
    # Set for a verification and for a check whose product declared a `not_ready_exit`
    # (GH-451); None for every other check, whose reply is what it always was.
    outcome: Outcome | None = None
    load_before: float | None = None
    load_after: float | None = None
    cores: int | None = None
    retry: Retry | None = None


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


def _load(figure: float | None) -> str:
    return "unknown" if figure is None else f"{figure:.1f}"


def render(run: CheckRun) -> str:
    """The reply the model reads: every figure is the system's."""
    if run.refused is not None:
        return run.refused
    lines = [f"check: {run.name}"]
    if run.outcome is not None:
        lines.append(f"outcome: {run.outcome}")
    # A machine too loaded to say is never reported as a red (invariant of GH-451).
    if run.outcome not in (Outcome.NOT_READY, Outcome.UNVERIFIED):
        lines.append(f"passed: {'yes' if run.passed else 'no'}")
    lines.append(f"reused: {'yes' if run.reused else 'no'}")
    if run.cores is not None:
        lines.append(
            f"load before: {_load(run.load_before)}, after: {_load(run.load_after)}, "
            f"cores: {run.cores}"
        )
    if run.witness is not None:
        lines.append(f"witness: {run.witness}")
    if run.unrecorded is not None:
        lines.append(f"not recorded: {run.unrecorded}")
    if run.retry is not None:
        lines.append(f"retried: {retry_text(run.retry)}")
    lines.append(f"duration: {run.duration_s:.1f} s")
    lines.append(f"output:\n{presubmit.tail(run.output)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class VerificationCall:
    """One call of the tool on a verification, as the system measured it: what the reviewer
    reads about it in the Developer's section (ADR-0065 addendum, GH-451). `load_before` and
    `load_after` are None when the load could not be read, and `cores` when the core count is
    not known; a name that is not declared ran nothing, so its duration is 0 and its tail empty."""

    name: str
    outcome: Outcome
    duration_s: float
    load_before: float | None
    load_after: float | None
    cores: int | None
    tail: str
    retry: Retry | None = None


def evidence_line(call: VerificationCall) -> str:
    """The system's line for one call: name, outcome, duration, the load around the run with
    the cores it is read against, and the tail of the log. The Developer's `summary` is not in
    it and cannot change it."""
    if call.load_before is None and call.load_after is None:
        load = "load not read"
    else:
        shown = [f"{v:.2f}" if v is not None else "?" for v in (call.load_before, call.load_after)]
        load = f"load {shown[0]} before, {shown[1]} after"
        if call.cores is not None:
            load += f", {call.cores} cores"
    line = f"- `{call.name}`: outcome `{call.outcome.value}`, {call.duration_s:.1f} s, {load}."
    if call.retry is not None:
        line += f" It was {retry_text(call.retry)}; the figures above are the second run's."
    if not call.tail:
        return line
    return f"{line} It printed:\n\n```text\n{call.tail}\n```"


def evidence_markdown(calls: Sequence[VerificationCall]) -> str | None:
    """The block the Developer's section carries for the verifications it ran, one entry per
    call in order; None when there were none, so a section without them is today's."""
    if not calls:
        return None
    return "\n\n".join(
        [
            "### Verifications\n\nRun by the system when the Developer called `run_check` on "
            "them: the reviewer reads these lines, never the summary.",
            *(evidence_line(call) for call in calls),
        ]
    )
