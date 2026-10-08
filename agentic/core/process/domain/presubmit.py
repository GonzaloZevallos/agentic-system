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

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

Mode = Literal["affected-only", "full"]

REFUSALS = 2
TAIL_LINES = 30
# The stack file always forces the full form (ADR-0046): a change to what the checks are
# can never be measured by the selection that file declares.
STACK_FILE = ".agentic/stack.yaml"
BASE_TOKEN = "{base}"
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
    # Which form ran, set by the system under an `AffectedPlan` and never by the model;
    # absent when the product declares no affected form.
    mode: NotRequired[Mode]


@dataclass(frozen=True)
class AffectedPlan:
    """The affected-only forms a product declares, with the base they are measured
    against. `base` arrives computed (`task_base`); this domain never derives it."""

    base: str
    commands: Mapping[str, str]
    fallback: tuple[str, ...]


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


def affected_command(command: str, base: str) -> str:
    """The affected form with the base in it. `replace`, never `format`: a command is
    shell text and may carry braces of its own."""
    return command.replace(BASE_TOKEN, base)


def affected_prefix(command: str) -> str:
    """The literal text before the base token, for the reviewer's `Bash(prefix:*)` rule."""
    return command.split(BASE_TOKEN, 1)[0].rstrip()


def check_modes(
    names: Sequence[str], plan: AffectedPlan, changed_paths: Sequence[str] | None
) -> dict[str, Mode]:
    """Which form each check runs. Everything runs full when the changed paths are unknown
    (`None`, the lookup failed) or empty, or when any path matches a fallback glob or the
    stack file; a check with no affected form always runs full. Globs match the whole
    POSIX path, so `*` crosses `/`: that errs toward running full."""
    everything_full = not changed_paths or any(
        fnmatchcase(path, glob) for path in changed_paths for glob in (*plan.fallback, STACK_FILE)
    )
    return {
        name: "affected-only" if name in plan.commands and not everything_full else "full"
        for name in names
    }


def final_results(rows: Sequence[CheckResult]) -> list[CheckResult]:
    """The last row per check name, in first-seen order: a check that ran twice (affected
    then full) is decided by its later row. The only reader that decides passed or failed."""
    last: dict[str, CheckResult] = {}
    for row in rows:
        last[row["name"]] = row
    return list(last.values())


def check_durations(rows: Sequence[CheckResult]) -> dict[str, float]:
    """Seconds spent per check name, summed over its rows; a row without one counts zero."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["name"]] = totals.get(row["name"], 0.0) + row.get("duration_s", 0.0)
    return totals


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
    otherwise pass. `checkout` arrives already resolved: the `.resolve()` this needs
    for a checkout reached through a symlink belongs to `CheckoutPresubmit`, the layer
    with a filesystem. What stays here is pure text algebra: `posixpath.normpath`
    collapses `.`/`.` segments lexically (no filesystem, no existence check), so a
    path that only textually starts with the checkout but escapes it once normalised
    (`<checkout>/../sibling`) never reads as one still inside it -- `is_relative_to`
    alone, being a plain prefix comparison of path parts, would otherwise accept it."""
    if timed_out:
        return WITNESS_TIMED_OUT.format(command=command)
    if exit_code != 0:
        return WITNESS_FAILED.format(command=command, exit_code=exit_code)
    printed = _last_nonempty_line(output)
    candidate = Path(posixpath.normpath(printed)) if printed and printed.startswith("/") else None
    if candidate is not None and candidate.is_relative_to(checkout):
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
    head: str,
    results: Sequence[CheckResult],
    witness: WitnessResult | None = None,
    *,
    base: str | None = None,
) -> str:
    """The checks as the Developer's section opens with them: written by the system, so
    the reviewer reads what ran, not what the Developer says ran (ADR-0016, addendum).
    Every row shows, a superseded one too.

    `witness`, when a checkout witness ran, gets its own line in this same block,
    printing the declared command verbatim beside the checks' own commands, agreed or
    not (glossary: Testigo del checkout) -- never a table row, so it is never read as a
    fourth check: it carries no `agentic.presubmit.checks` label of its own.

    A mode column and one line on what `affected-only` means appear only when some row
    carries a `mode`; `base` names what that selection was measured against."""
    moded = any("mode" in r for r in results)
    if moded:
        header = "| Check | Command | Mode | Result |\n|---|---|---|---|\n"
    else:
        header = "| Check | Command | Result |\n|---|---|---|\n"
    rows = []
    for r in results:
        outcome = "passed" if r["passed"] else "**failed**"
        cells = [f"`{r['name']}`", f"`{r['command']}`"]
        if moded:
            cells.append(r.get("mode", "-"))
        rows.append("| " + " | ".join([*cells, outcome]) + " |")
    parts = [
        f"### Checks\n\nRun by the system on `{head}` when the Developer delivered.",
        header + "\n".join(rows),
    ]
    if moded:
        against = f"`{base}`" if base else "the task base"
        parts.append(
            f"`affected-only` rows ran the tests selected against {against} (the whole "
            "branch, `task_base..head`, not the increment under review); the full suite "
            "runs at merge (ADR-0045)."
        )
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


def describe_checks(checks: Mapping[str, str], *, affected: AffectedPlan | None = None) -> str:
    """The declared checks as a prompt lists them. With an `affected` plan, each check
    that has an affected form also lists it, the base already in it, and the text says
    such a row is not evidence for the whole suite."""
    if affected is None:
        return "\n".join(f"- `{name}`: `{command}`" for name, command in checks.items())
    lines: list[str] = []
    for name, command in checks.items():
        lines.append(f"- `{name}`: `{command}`")
        if name in affected.commands:
            form = affected_command(affected.commands[name], affected.base)
            lines.append(f"  - affected-only form: `{form}`")
    lines.append(
        f"An `affected-only` row selected against `{affected.base}` (the whole branch, "
        "`task_base..head`, not the increment under review) is not evidence for the whole "
        "suite; the full suite runs at merge (ADR-0045)."
    )
    return "\n".join(lines)
