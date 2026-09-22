"""The presubmit (glossary: Puerta de entrega): what a Developer's delivery must be
before it counts, checked by the system when the model hands it back, not after.

A delivery is committed code on a clean tree whose product checks pass (`core.knowledge`
declares them). The presubmit refuses at most `REFUSALS` times per Developer run, its retry
included: the reason goes
back to the model inside its turn, which is cheaper than a role failure or a review
that returns it. Past that it lets the delivery through, and what the checks said is in
the Developer's section, written by the system, for the reviewer to read. The commit and
the clean tree are still checked after the run, as a role failure (`develop`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict

REFUSALS = 2
TAIL_LINES = 30
# The checkout witness's own budget (glossary: Testigo del checkout), separate from
# `REFUSALS`: a misdeclared witness must never spend the budget the checks need.
WITNESS_REFUSALS = 1

NO_COMMIT = (
    "no commit on the branch: a delivery is committed code, commit your work on the "
    "current branch and then deliver"
)
UNCOMMITTED = (
    "uncommitted changes in the working tree: the branch must hold exactly what you "
    "tested, commit them (or discard them) and then deliver"
)

# Never a bare "witness mismatch": no prompt mentions a witness (`prompts.fingerprint`
# does not move), so the reason has to explain itself the moment it fires -- the
# command, what to do, and, once earned, what it printed and where the checkout is.
WITNESS_TIMED_OUT = (
    "the checkout witness `{command}` timed out: the system cannot tell where the code "
    "the checks just measured lives, so their result cannot be trusted here. Point its "
    "toolchain at this checkout, never up to a parent environment, and deliver again."
)
WITNESS_FAILED = (
    "the checkout witness `{command}` exited {exit_code}: the system cannot tell where "
    "the code the checks just measured lives, so their result cannot be trusted here. "
    "Point its toolchain at this checkout, never up to a parent environment, and "
    "deliver again."
)
WITNESS_OUTSIDE = (
    "the checkout witness `{command}` printed `{printed}`, which is not this checkout "
    "(`{checkout}`): the code the checks just measured does not live where this "
    "delivery does. Point its toolchain at this checkout, never up to a parent "
    "environment, and deliver again."
)


class CheckResult(TypedDict):
    name: str
    command: str
    passed: bool
    # The command's output, whole; the presubmit and the document keep its last lines.
    output: str
    # How long the command took, for `agentic.presubmit.duration`. Absent on a result
    # built before this field existed (none are, today: nothing persists a `CheckResult`).
    duration_s: NotRequired[float]


class WitnessResult(TypedDict):
    """What the checkout witness said the last time it ran, for `checks_markdown`: the
    declared command, verbatim, and what a human reads (`said`-annotated, never the raw
    output the verdict itself was decided from)."""

    command: str
    output: str
    passed: bool


def refusal(moved: bool, clean: bool, results: Sequence[CheckResult]) -> str | None:
    """Why this delivery is not one yet, or None. A branch where nothing was committed,
    or with changes left, is refused before any check is read."""
    if not moved:
        return NO_COMMIT
    if not clean:
        return UNCOMMITTED
    failed = [r for r in results if not r["passed"]]
    if not failed:
        return None
    parts = ["the product's checks fail on your head; fix them, commit, and deliver again."]
    for r in failed:
        parts.append(f"`{r['name']}` failed: `{r['command']}`\n\n```text\n{tail(r['output'])}\n```")
    return "\n\n".join(parts)


def refused(reason: str, count: int) -> str:
    """The reason as the model reads it, with how many refusals are left."""
    return f"The presubmit refused this delivery ({count} of {REFUSALS}): {reason}"


def witness_refusal(
    command: str, output: str, exit_code: int, timed_out: bool, checkout: Path
) -> str | None:
    """Why the checkout witness disagrees with this checkout, or None (glossary:
    Testigo del checkout). Decided strictly in this order, never by reading text before
    it is earned: timed out, then a non-zero exit, only then the last non-empty line of
    the RAW `output`, stripped, which must be an absolute path that falls under
    `checkout` -- the last line and not any line, because `ShellCommands` merges
    stderr into stdout on purpose and a witness printing one path in and one out would
    otherwise pass. `checkout` arrives already resolved: the two `.resolve()` calls
    this needs belong to `CheckoutPresubmit`, the layer with a filesystem; this is pure
    text and `is_relative_to` algebra."""
    if timed_out:
        return WITNESS_TIMED_OUT.format(command=command)
    if exit_code != 0:
        return WITNESS_FAILED.format(command=command, exit_code=exit_code)
    printed = _last_nonempty_line(output)
    candidate = Path(printed) if printed else None
    if candidate is not None and candidate.is_absolute() and candidate.is_relative_to(checkout):
        return None
    return WITNESS_OUTSIDE.format(
        command=command, printed=printed or "(nothing)", checkout=checkout
    )


def witness_refused(reason: str) -> str:
    """The witness's reason as the model reads it, on its own budget
    (`WITNESS_REFUSALS`), never `REFUSALS`, the checks' own."""
    return (
        f"The presubmit refused this delivery (checkout witness, {WITNESS_REFUSALS} of "
        f"{WITNESS_REFUSALS}): {reason}"
    )


def _last_nonempty_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def said(output: str, timed_out: bool, timeout_s: float) -> str:
    """What a product's command said, with the note that makes an empty output legible:
    a killed process often drains nothing, so without this the human reads a blank block
    and no reason for it."""
    return f"{output}\n(timed out after {timeout_s:.0f} s)" if timed_out else output


def tail(output: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(output.rstrip("\n").splitlines()[-lines:])


def checks_markdown(
    head: str, results: Sequence[CheckResult], witness: WitnessResult | None = None
) -> str:
    """The checks as the Developer's section opens with them: written by the system, so
    the reviewer reads what ran, not what the Developer says ran (ADR-0016, addendum).

    `witness`, when a checkout witness ran, gets its own line in this same block,
    printing the declared command verbatim beside the checks' own commands, agreed or
    not (glossary: Testigo del checkout) -- never a table row, so it is never read as a
    fourth check: it carries no `agentic.presubmit.checks` label of its own."""
    rows = [
        f"| `{r['name']}` | `{r['command']}` | {'passed' if r['passed'] else '**failed**'} |"
        for r in results
    ]
    parts = [
        f"### Checks\n\nRun by the system on `{head}` when the Developer delivered.",
        "| Check | Command | Result |\n|---|---|---|\n" + "\n".join(rows),
    ]
    if witness is not None:
        verdict = "agreed" if witness["passed"] else "**disagreed**"
        parts.append(
            f"Checkout witness (not a check): `{witness['command']}`, {verdict}. "
            f"It printed:\n\n```text\n{tail(witness['output'])}\n```"
        )
    for r in results:
        if not r["passed"]:
            parts.append(f"`{r['name']}`, last lines:\n\n```text\n{tail(r['output'])}\n```")
    return "\n\n".join(parts)


def describe_checks(checks: Mapping[str, str]) -> str:
    """The declared checks as a prompt lists them."""
    return "\n".join(f"- `{name}`: `{command}`" for name, command in checks.items())
