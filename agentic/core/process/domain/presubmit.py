"""The presubmit (glossary: Puerta de entrega): what a Developer's delivery must be
before it counts, checked by the system when the model hands it back, not after.

A delivery is committed code on a clean tree whose product checks pass (`core.knowledge`
declares them). The presubmit refuses at most `REFUSALS` times per Developer run, its retry
included, plus one more when the product declares `failed_ids` and the failure is moving
(`refusal_allowed`): the reason goes
back to the model inside its turn, which is cheaper than a role failure or a review
that returns it. Past that it lets the delivery through, and what the checks said is in
the Developer's section, written by the system, for the reviewer to read. The commit and
the clean tree are still checked after the run, as a role failure (`develop`)."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

Mode = Literal["affected-only", "full"]

REFUSALS = 2
# The most refusals one run can reach: the one `REFUSALS` extra, earned only when the failure
# is moving (`refusal_allowed`).
MOVING_REFUSALS = REFUSALS + 1
TAIL_LINES = 30
# The stack file always forces the full form (ADR-0046): a change to what the checks are
# can never be measured by the selection that file declares.
STACK_FILE = ".agentic/stack.yaml"
BASE_TOKEN = "{base}"
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
    # Which form ran, set by the system under an `AffectedPlan` and never by the model;
    # absent when the product declares no affected form.
    mode: NotRequired[Mode]
    # True on a row the system took from a stored pass instead of running the check
    # (ADR-0065). Set by the system on read, never stored and never by the model; absent
    # on a row that ran.
    reused: NotRequired[bool]
    # True on a row produced by the one rerun of a red check (ADR-0066). Set by the system,
    # never by the model; never stored in the check-result store; absent on a first run.
    rerun: NotRequired[bool]
    # True on a row whose command hit `check_timeout_s`: a fact, so the rerun rule never
    # reads prose to decide that a timed-out check is not rerun.
    timed_out: NotRequired[bool]


FlakeVerdict = Literal["pass", "flake", "fail"]
# Which gate ran a check: the Developer's delivery gate, the merge gate (and the landing's
# slice gate), or `full_checks` (ADR-0066).
FlakeGate = Literal["delivery", "merge", "full_checks"]
# The last 64 KB of a check's output is all `failed_ids` reads (pytest's short summary and
# Playwright's failed list sit at the end), and 50 ids is all it returns: both bound the
# cost of a product-declared pattern.
FAILED_IDS_TAIL_BYTES = 65536
FAILED_IDS_MAX = 50


@dataclass(frozen=True)
class RerunForm:
    """A product's "failed tests only" form of one check (`rerun_checks`, ADR-0066): the
    command that reruns just the failed tests, and the regex whose single group names a
    failed test in the check's output. Validated when the stack is parsed."""

    command: str
    test_id: str


def flake_verdict(first: CheckResult, rerun: CheckResult | None) -> FlakeVerdict:
    """The one decision on a check at a gate (ADR-0066, invariant): `pass` when it was green
    the first time, `flake` when it was red and the rerun was green, `fail` otherwise. A
    timed-out first row is never rerun, so a rerun handed alongside it cannot save it."""
    if first["passed"]:
        return "pass"
    if rerun is not None and rerun["passed"] and not first.get("timed_out", False):
        return "flake"
    return "fail"


def refusal_allowed(
    refusals: int,
    previous_ids: Sequence[str] | None,
    current_ids: Sequence[str] | None,
) -> bool:
    """Whether the gate may refuse once more after `refusals` refusals. Below `REFUSALS`
    always. At exactly `REFUSALS` only when the product declares `failed_ids` (both lists
    present: the failed ids of the last two refusals) and they differ as sets, so the Developer
    is fixing something and the failure is moving. Never beyond `MOVING_REFUSALS`. A product
    without `failed_ids` passes `None` and gets `REFUSALS` exactly."""
    if refusals < REFUSALS:
        return True
    if refusals >= MOVING_REFUSALS or previous_ids is None or current_ids is None:
        return False
    return set(previous_ids) != set(current_ids)


def failed_ids(output: str, pattern: str) -> tuple[str, ...]:
    """The test ids a failing check's output names: group 1 of `pattern`, over at most the
    last `FAILED_IDS_TAIL_BYTES` bytes, deduplicated in order, at most `FAILED_IDS_MAX`.
    The only function that runs a product-declared pattern. A pattern that does not compile
    names nothing, which is a full rerun: the safe side."""
    tail = output[-FAILED_IDS_TAIL_BYTES:].encode("utf-8", "replace")[-FAILED_IDS_TAIL_BYTES:]
    text = tail.decode("utf-8", "ignore")
    try:
        matches = re.finditer(pattern, text)
        ids: list[str] = []
        for match in matches:
            found = match.group(1)
            if found and found not in ids:
                ids.append(found)
                if len(ids) == FAILED_IDS_MAX:
                    break
    except (re.error, IndexError):
        return ()
    return tuple(ids)


def rerun_command(
    full_command: str, first: CheckResult, form: RerunForm | None, gate: FlakeGate
) -> str | None:
    """The command that reruns a red check, or None when it is not rerun at all: a green
    row, a `timed_out` one and a rerun row never are. The product's form runs only at the
    delivery gate, only when it exists and `failed_ids` found at least one id in the
    failure; every other case reruns the full command (ADR-0066)."""
    if first["passed"] or first.get("timed_out", False) or first.get("rerun", False):
        return None
    if gate == "delivery" and form is not None and failed_ids(first["output"], form.test_id):
        return form.command
    return full_command


def outcomes(rows: Sequence[CheckResult]) -> dict[str, FlakeVerdict]:
    """The verdict of each check in `rows`, in first-seen order: a check whose last row is
    its rerun is judged by `flake_verdict(the row before it, the rerun)`; every other check
    by its last row alone. The only reader that turns rows into `pass | flake | fail`, so
    the Markdown, the record and the signal cannot disagree (ADR-0066)."""
    grouped: dict[str, list[CheckResult]] = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(row)
    verdicts: dict[str, FlakeVerdict] = {}
    for name, group in grouped.items():
        last = group[-1]
        if last.get("rerun", False) and len(group) > 1:
            verdicts[name] = flake_verdict(group[-2], last)
        else:
            verdicts[name] = flake_verdict(last, None)
    return verdicts


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


def refusal(
    moved: bool,
    clean: bool,
    results: Sequence[CheckResult],
    regressions: Sequence[str] = (),
) -> str | None:
    """Why this delivery is not one yet, or None. A branch where nothing was committed,
    or with changes left, is refused before any check is read. `regressions` are the
    criteria documents the head pushes past their line cap (GH-334); they refuse on the same
    budget as the checks and share their reason."""
    if not moved:
        return NO_COMMIT
    if not clean:
        return UNCOMMITTED
    failed = [r for r in results if not r["passed"]]
    parts: list[str] = []
    if failed:
        parts.append("the product's checks fail on your head; fix them, commit, and deliver again.")
        for r in failed:
            parts.append(_failed_text(r))
    if regressions:
        parts.append(_regressions_text(regressions))
    return "\n\n".join(parts) or None


def _failed_text(r: CheckResult) -> str:
    body = f"\n\n```text\n{tail(r['output'])}\n```"
    if r.get("timed_out", False):
        # A rerun would spend the same time again: the Developer looks at the check first.
        return (
            f"`{r['name']}` timed out: `{r['command']}`; look at why it is slow or hangs, "
            f"do not just rerun it.{body}"
        )
    return f"`{r['name']}` failed: `{r['command']}`{body}"


def _regressions_text(regressions: Sequence[str]) -> str:
    return (
        "your head makes a criteria document longer past its line cap; the merge refuses it, "
        "so cut it or split it, commit, and deliver again:\n"
        + "\n".join(f"- {message}" for message in regressions)
    )


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
    """Seconds spent per check name, summed over its rows; a row without one counts zero.
    A reused row never ran, so it adds nothing and a check with only reused rows has no
    entry (ADR-0065): its stored duration is another run's, not a sample of this one. A
    rerun row (ADR-0066) is a second run of the same check, not a sample of the first: it
    adds nothing either."""
    totals: dict[str, float] = {}
    for row in rows:
        if row.get("reused") or row.get("rerun"):
            continue
        totals[row["name"]] = totals.get(row["name"], 0.0) + row.get("duration_s", 0.0)
    return totals


def refused(reason: str, count: int) -> str:
    """The reason as the model reads it, with how many refusals are left."""
    limit = REFUSALS if count <= REFUSALS else MOVING_REFUSALS
    return f"The presubmit refused this delivery ({count} of {limit}): {reason}"


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
    regressions: Sequence[str] = (),
) -> str:
    """The checks as the Developer's section opens with them: written by the system, so
    the reviewer reads what ran, not what the Developer says ran (ADR-0016, addendum).
    Every row shows, a superseded one too.

    `witness`, when a checkout witness ran, gets its own line in this same block,
    printing the declared command verbatim beside the checks' own commands, agreed or
    not (glossary: Testigo del checkout) -- never a table row, so it is never read as a
    fourth check: it carries no `agentic.presubmit.checks` label of its own.

    A mode column and one line on what `affected-only` means appear only when some row
    carries a `mode`; `base` names what that selection was measured against.

    `regressions`, when the last look found some, are listed after the checks: a delivery
    the gate let through after its refusals still shows what it leaves over the cap."""
    moded = any("mode" in r for r in results)
    if moded:
        header = "| Check | Command | Mode | Result | Seconds |\n|---|---|---|---|---|\n"
    else:
        header = "| Check | Command | Result | Seconds |\n|---|---|---|---|\n"
    verdicts = outcomes(results)
    seconds = check_durations(results)
    rows = []
    for r in results:
        outcome = "passed" if r["passed"] else "**failed**"
        if r.get("rerun"):
            # The one rerun of a red check (ADR-0066): green is a flake, never plain passed.
            outcome = "flake" if verdicts[r["name"]] == "flake" else "**failed**"
            outcome += " (rerun)"
        if r.get("reused"):
            outcome += " (reused)"
        cells = [f"`{r['name']}`", f"`{r['command']}`"]
        if moded:
            cells.append(r.get("mode", "-"))
        shown = f"{seconds[r['name']]:.1f}" if r["name"] in seconds else "-"
        rows.append("| " + " | ".join([*cells, outcome, shown]) + " |")
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
    if regressions:
        parts.append(_regressions_text(regressions))
    flaked = [name for name, verdict in verdicts.items() if verdict == "flake"]
    if flaked:
        names = ", ".join(f"`{name}`" for name in flaked)
        parts.append(
            f"Flake: {names} failed and then passed on the system's one rerun; the delivery "
            "passes and the flake is recorded (ADR-0066)."
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
    row_master: str | None = None,
) -> dict[str, object]:
    """The whole `merge_checks` mapping of one run (ADR-0045): the four keys it always
    had, `checks` (one entry per declared check, in declared order, the failing ones with
    their `tail`) and `failed_step` (only when the run did not pass). `passed` is the
    expression the merge verb always used, moved here: no declared check proves nothing, a
    missing result is a failure, and a witness that disagrees makes the checks prove
    nothing either; a worktree, a merge or a setup step that failed (`failed_step` says
    which) never passed. `setup_failure` is the failing setup step's name and what it
    said. The tails are the only text taken from a command: sanitized and capped here,
    displayed and never parsed. `row_master`, when given, is the master the checks ran on:
    each entry then carries it as `master`; without it the entries are as they always were."""
    # A check decides by its last row: a flake's first row is red and its rerun green.
    decided = final_results(results or [])
    passed = (
        failed_step not in _EARLY_STEPS
        and bool(declared)
        and results is not None
        and all(result["passed"] for result in decided)
        and (witness is None or witness["passed"])
    )
    by_name = {result["name"]: result for result in decided}
    verdicts = outcomes(results or [])
    # The only place a record's per-check seconds come from; absent where there is no sample.
    seconds = check_durations(results or [])
    entries: list[dict[str, object]] = []
    for name in declared:
        found = by_name.get(name)
        if found is None:
            entries.append({"name": name, "passed": False})
        elif found["passed"]:
            entry: dict[str, object] = {"name": name, "passed": True}
            if found.get("reused"):
                entry["reused"] = True
            if verdicts[name] == "flake":
                entry["flake"] = True
            entries.append(entry)
        else:
            failed: dict[str, object] = {
                "name": name,
                "passed": False,
                "tail": _tail_of(found["output"]),
            }
            if found.get("rerun"):
                failed["rerun"] = True
            entries.append(failed)
        if found is not None and name in seconds:
            entries[-1]["duration_s"] = round(seconds[name], 1)
    if witness is not None and not witness["passed"]:
        entries.append(
            {"name": _WITNESS_ENTRY, "passed": False, "tail": _tail_of(witness["output"])}
        )
    if setup_failure is not None:
        step_name, output = setup_failure
        entries.append({"name": step_name, "passed": False, "tail": _tail_of(output)})
    if row_master is not None:
        # Every entry, a reused one and a failing one too, names the master it ran on.
        entries = [{**entry, "master": row_master} for entry in entries]
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
    if results is None or not all(result["passed"] for result in final_results(results)):
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


def failed_names(record: Mapping[str, object]) -> list[str]:
    """The names of the failing entries of a recorded `checks`, in recorded order: what a
    landing rerun runs again (GH-239)."""
    return [name for name, _ in _failures(record)]


def _entries(record: Mapping[str, object]) -> list[dict[str, object]]:
    checks = record.get("checks")
    return (
        [entry for entry in checks if isinstance(entry, dict)] if isinstance(checks, list) else []
    )


def rerun_record(previous: Mapping[str, object], rerun: Mapping[str, object]) -> dict[str, object]:
    """The `merge_checks` mapping after only the failed checks ran again (GH-239): the
    rerun's entries replace the previous ones of the same name, the rest stay as recorded,
    and `passed` is every entry passing. A rerun that stopped early (worktree, conflict,
    setup) proved nothing about the checks: its record stands as it is. A kept entry keeps
    the `master` it ran on; the record's own `master` is the rerun's."""
    if rerun.get("failed_step") in _EARLY_STEPS:
        return dict(rerun)
    again = {entry.get("name"): entry for entry in _entries(rerun)}
    earlier = previous.get("master")
    kept = [again.pop(entry.get("name"), _ran_on(entry, earlier)) for entry in _entries(previous)]
    entries = [*kept, *again.values()]
    passed = bool(entries) and all(entry.get("passed") is True for entry in entries)
    record: dict[str, object] = {**rerun, "checks": entries, "passed": passed}
    if passed:
        record.pop("failed_step", None)
    else:
        record["failed_step"] = rerun.get("failed_step") or previous.get("failed_step") or "checks"
    return record


def _ran_on(entry: dict[str, object], recorded: object) -> dict[str, object]:
    """A kept entry as it ran: its own `master` when it has one. An entry written before
    each row named its master ran on the record's `master`; the rerun replaces that key, so
    the kept entry takes it now, or it would read as having run on the newer master."""
    if "master" in entry or not isinstance(recorded, str):
        return entry
    return {**entry, "master": recorded}


def row_masters(record: Mapping[str, object]) -> tuple[str, ...]:
    """The distinct masters the entries of a `merge_checks` mapping ran on, in first-seen
    order. An entry without a `master` takes the record's (a document written before each
    row named its own); a `master` that is not a string is skipped."""
    recorded = record.get("master")
    seen: dict[str, None] = {}
    for entry in _entries(record):
        master = entry.get("master", recorded)
        if isinstance(master, str) and master:
            seen.setdefault(master)
    return tuple(seen)


def tier_section(fast: Sequence[str], rest: Sequence[str]) -> str:
    """The section `develop` appends to a Developer's prompt for a product that declares
    tiers (GH-358): the delivery gate runs only `fast`, the rest -- and `fast` without
    affected forms -- runs once on the integrated head. Written by the system, never by the
    shared template, so a product without tiers renders today's text. `rest` may be empty."""
    fast_names = ", ".join(fast)
    if rest:
        later = (
            f"`{', '.join(rest)}`, and `{fast_names}` without affected forms, run once on the "
            "integrated head"
        )
    else:
        later = f"`{fast_names}` without affected forms run once on the integrated head"
    return (
        "### Delivery checks\n\n"
        f"This product runs two tiers. The delivery gate runs only `{fast_names}`. "
        f"The full suite runs once per task: {later}, before the pull request opens. "
        "You must not run the full suite yourself. The full suite reuses your pass only when "
        "the integrated tree is yours. This supersedes the sentence above saying "
        "that every check runs at delivery."
    )
