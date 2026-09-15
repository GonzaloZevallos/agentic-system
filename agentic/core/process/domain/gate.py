"""The delivery gate (glossary: Puerta de entrega): what a Developer's delivery must be
before it counts, checked by the system when the model hands it back, not after.

A delivery is committed code on a clean tree whose product checks pass (`core.knowledge`
declares them). The gate refuses at most `REFUSALS` times per Developer run, its retry
included: the reason goes
back to the model inside its turn, which is cheaper than a role failure or a review
that returns it. Past that it lets the delivery through, and what the checks said is in
the Developer's section, written by the system, for the reviewer to read. The commit and
the clean tree are still checked after the run, as a role failure (`develop`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict

REFUSALS = 2
TAIL_LINES = 30

NO_COMMIT = (
    "no commit on the branch: a delivery is committed code, commit your work on the "
    "current branch and then deliver"
)
UNCOMMITTED = (
    "uncommitted changes in the working tree: the branch must hold exactly what you "
    "tested, commit them (or discard them) and then deliver"
)


class CheckResult(TypedDict):
    name: str
    command: str
    passed: bool
    # The command's output, whole; the gate and the document keep its last lines.
    output: str


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
    return f"The delivery gate refused this delivery ({count} of {REFUSALS}): {reason}"


def tail(output: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(output.rstrip("\n").splitlines()[-lines:])


def checks_markdown(head: str, results: Sequence[CheckResult]) -> str:
    """The checks as the Developer's section opens with them: written by the system, so
    the reviewer reads what ran, not what the Developer says ran (ADR-0016, addendum)."""
    rows = [
        f"| `{r['name']}` | `{r['command']}` | {'passed' if r['passed'] else '**failed**'} |"
        for r in results
    ]
    parts = [
        f"### Checks\n\nRun by the system on `{head}` when the Developer delivered.",
        "| Check | Command | Result |\n|---|---|---|\n" + "\n".join(rows),
    ]
    for r in results:
        if not r["passed"]:
            parts.append(f"`{r['name']}`, last lines:\n\n```text\n{tail(r['output'])}\n```")
    return "\n\n".join(parts)


def describe_checks(checks: Mapping[str, str]) -> str:
    """The declared checks as a prompt lists them."""
    return "\n".join(f"- `{name}`: `{command}`" for name, command in checks.items())
