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
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

REFUSALS = 2
TAIL_LINES = 30
# What `merge_checks` keeps of one failing command's output (GH-212): the last
# `TAIL_LINES` lines, then the last `MERGE_TAIL_CHARS` characters of those. The comment the
# poller posts about it never grows past `MERGE_COMMENT_MAX` (GitHub's own limit is 65536).
MERGE_TAIL_CHARS = 2000
MERGE_COMMENT_MAX = 60_000
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


# --- The evidence of a merge-checks run (GH-212, ADR-0045) -------------------------------

# Where a run that did not pass stopped, the closed set `merge_checks.failed_step` takes.
FailedStep = Literal["worktree", "conflict", "setup", "no_checks", "witness", "checks"]

_EARLY_STEPS = ("worktree", "conflict", "setup")
_FIXED_SUMMARY = {
    "worktree": "Failed: the worktree could not be built",
    "conflict": "Failed: the head does not merge with master",
    "no_checks": "Failed: no checks are declared",
}
_WITNESS_ENTRY = "witness"
_WITNESS_SUMMARY = "checkout witness disagreed"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
_MENTION = re.compile(r"([@#])(?=\w)")
_ZERO_WIDTH_SPACE = "​"


def merge_checks_record(
    *,
    head: str,
    master: str,
    duration_s: float,
    declared: Mapping[str, str],
    results: Sequence[CheckResult] | None,
    witness: WitnessResult | None,
    failed_step: FailedStep | None = None,
    setup_failure: tuple[str, str] | None = None,
) -> dict[str, object]:
    """The whole `merge_checks` mapping of one run (ADR-0045): the four keys it always
    had, `checks` (one entry per declared check, in declared order, the failing ones with
    their `tail`) and `failed_step` (only when the run did not pass). `passed` is the
    expression the merge verb always used, moved here: no declared check proves nothing, a
    missing result is a failure, and a witness that disagrees makes the checks prove
    nothing either; a worktree, a merge or a setup step that failed (`failed_step` says
    which) never passed. `setup_failure` is the failing setup step's name and what it
    said. The tails are the only text taken from a command: sanitized and capped here,
    displayed and never parsed."""
    passed = (
        failed_step not in _EARLY_STEPS
        and bool(declared)
        and results is not None
        and all(result["passed"] for result in results)
        and (witness is None or witness["passed"])
    )
    by_name = {result["name"]: result for result in results or []}
    entries: list[dict[str, object]] = []
    for name in declared:
        found = by_name.get(name)
        if found is None:
            entries.append({"name": name, "passed": False})
        elif found["passed"]:
            entries.append({"name": name, "passed": True})
        else:
            entries.append({"name": name, "passed": False, "tail": _tail_of(found["output"])})
    if witness is not None and not witness["passed"]:
        entries.append(
            {"name": _WITNESS_ENTRY, "passed": False, "tail": _tail_of(witness["output"])}
        )
    if setup_failure is not None:
        step_name, output = setup_failure
        entries.append({"name": step_name, "passed": False, "tail": _tail_of(output)})
    record: dict[str, object] = {
        "head": head,
        "master": master,
        "passed": passed,
        "duration_s": duration_s,
        "checks": entries,
    }
    if not passed:
        record["failed_step"] = failed_step or _derived_step(declared, results)
    return record


def _derived_step(declared: Mapping[str, str], results: Sequence[CheckResult] | None) -> FailedStep:
    if not declared:
        return "no_checks"
    if results is None or not all(result["passed"] for result in results):
        return "checks"
    return "witness"


def _tail_of(output: str) -> list[str]:
    """One command's output as the record keeps it, a list of lines (a multi-line scalar
    would break `record_closure`'s one-line surgery): no escape sequence, no control
    character but the newline, tabs as spaces, then the last `TAIL_LINES` lines and the
    last `MERGE_TAIL_CHARS` characters of those, in that order."""
    clean = _CONTROL.sub("", _ANSI.sub("", output).replace("\t", "    "))
    return tail(clean)[-MERGE_TAIL_CHARS:].splitlines()


def _failures(record: Mapping[str, object]) -> list[tuple[str, list[str]]]:
    """The failing entries of a recorded `checks`, name and tail, in the order recorded;
    whatever is not shaped like one is skipped, and a tail that is not a list of strings
    reads as empty (a document is edited by hand)."""
    checks = record.get("checks")
    found: list[tuple[str, list[str]]] = []
    for entry in checks if isinstance(checks, list) else []:
        if not isinstance(entry, dict) or entry.get("passed") is not False:
            continue
        name, entry_tail = entry.get("name"), entry.get("tail")
        if isinstance(name, str) and name:
            lines = entry_tail if isinstance(entry_tail, list) else []
            found.append((name, [line for line in lines if isinstance(line, str)]))
    return found


def failed_checks_summary(record: Mapping[str, object], head: str | None) -> str:
    """One line naming what failed in `record`, or "" when it says nothing about `head`
    (another head, a run that passed, a record written before GH-212). Never reads a tail."""
    if head is None or record.get("head") != head or record.get("passed") is not False:
        return ""
    step = record.get("failed_step")
    if isinstance(step, str) and step in _FIXED_SUMMARY:
        return _FIXED_SUMMARY[step]
    names = [" ".join(name.split()) for name, _ in _failures(record)]
    if not names:
        return ""
    if step == "setup":
        return f"Failed at setup: {names[0]}"
    shown = [_WITNESS_SUMMARY if name == _WITNESS_ENTRY else name for name in names]
    return f"Failed: {', '.join(shown)}"


def failed_checks_markdown(
    record: Mapping[str, object], head: str | None, *, neutralise: bool = True
) -> str:
    """`failed_checks_summary`, then each failing entry's name and tail, every tail in a
    fence one backtick longer than the longest run inside it. `neutralise` (the comment
    wants it, a log does not) puts U+200B after an `@` or `#` followed by a word
    character, in the tails only. Over `MERGE_COMMENT_MAX` the tails go, last entry
    first; the names stay."""
    summary = failed_checks_summary(record, head)
    if not summary:
        return ""
    blocks = [
        (name, _fenced("\n".join(lines), neutralise)) for name, lines in _failures(record) if lines
    ]

    def text(kept: int) -> str:
        parts = [summary]
        for i, (name, fenced) in enumerate(blocks):
            suffix = f"last lines:\n\n{fenced}" if i < kept else "last lines omitted"
            parts.append(f"`{name}`, {suffix}")
        return "\n\n".join(parts)

    kept = len(blocks)
    while kept > 0 and len(text(kept)) > MERGE_COMMENT_MAX:
        kept -= 1
    return text(kept)


def _fenced(tail_text: str, neutralise: bool) -> str:
    longest = max((len(run) for run in re.findall(r"`+", tail_text)), default=0)
    fence = "`" * max(3, longest + 1)
    body = _MENTION.sub(rf"\1{_ZERO_WIDTH_SPACE}", tail_text) if neutralise else tail_text
    return f"{fence}text\n{body}\n{fence}"
