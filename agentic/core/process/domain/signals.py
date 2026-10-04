"""Whether a role that executes may send a signal to a process (ADR-0011, eighth
addendum). A role that executes may kill what it started; it may never kill by name,
by pattern or by port, because that reaches processes other roles -- or the human --
started too (issue #151: `pkill -f uvicorn` took down the human's own local app twice).

`denial` is total and pure: it never reads the environment, the filesystem or `/proc`
itself, and it never raises. Every fact about a process it needs -- whose checkout a
pid's cwd resolves under, if any -- arrives already computed, through the injected
`owner`. That keeps the verdict unit-testable with a plain dict and makes the adapter's
own `/proc` read (`hooks._owner`) the only place that can ever disagree with reality."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SIMPLE_WRAPPERS = ("builtin", "command", "exec", "nohup", "time")

# xargs's own options: short ones that take a value (also glued to it, `-n1`, `-I{}`)
# and GNU long ones (space-separated or `=`-joined). Anything else that starts with
# `-` is read as a flag with no value and the scan goes on -- `-r`, `-0`, `-t`, `-p`,
# `-x` land here, and so does an option this module has never heard of.
_XARGS_VALUE = (
    "-I",
    "-n",
    "-L",
    "-P",
    "-d",
    "-E",
    "-s",
    "-a",
    "--max-args",
    "--max-lines",
    "--max-procs",
    "--delimiter",
    "--replace",
    "--eof",
    "--max-chars",
    "--arg-file",
)
_XARGS_GLUED = ("-I", "-n", "-L", "-P", "-d", "-E", "-s", "-a")
_ENV_VALUE = ("-u", "-C", "-S")
_SUDO_VALUE = ("-u", "-g")
_TIMEOUT_VALUE = ("-s", "--signal", "-k", "--kill-after")
_NICE_VALUE = ("-n",)

_RUNNERS: tuple[tuple[str, ...], ...] = (
    ("npx",),
    ("npm", "exec"),
    ("pnpm", "dlx"),
    ("yarn", "dlx"),
)
_PATTERN_PROGRAMS = ("pkill", "killall", "kill-port")

_SPEC_VALUE = ("-s", "-n", "--signal")
_SIGNAL_SHORT = re.compile(r"^-[0-9A-Za-z]+$")
_PID_NUMBER = re.compile(r"^[0-9]+$")
_PID_SUBSTITUTION = re.compile(r"^\$(!|[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$")

_RULE = (
    "A role that executes may only signal a process whose own working directory -- "
    "`/proc/<pid>/cwd`, resolved -- falls inside its checkout; no pattern, program "
    "name or port ever decides that.\n"
    "To bring down something you started: `kill $!` for your last background job, or "
    "`kill <pid>` for a pid whose `/proc/<pid>/cwd` is inside your checkout.\n"
    "If a port is already held by a process that is not yours, start yours on another "
    "port when the product allows it; if it does not, deliver anyway and name the gap "
    "in `summary`.\n"
    "This is not a syntax problem, and no other phrasing of the same command gets "
    "around it."
)
PATTERN_OR_PORT = "selects by pattern or port, not by a pid you own.\n\n" + _RULE
NOT_IN_YOUR_CHECKOUT = "pid not in your checkout.\n\n" + _RULE
FROM_SUBSTITUTION_OR_PIPE = "pid only known at run time, from a substitution or a pipe.\n\n" + _RULE
MALFORMED_OPTION = "malformed option.\n\n" + _RULE


def _skip_options(words: list[str], value: tuple[str, ...], glued: tuple[str, ...] = ()) -> int:
    """How many of `words`' leading tokens are one wrapper's own options. A known
    `value` option consumes the next word, unless it already carries its value after
    `=`; a short option glued to its value (`glued`, e.g. `-n1`, `-I{}`) consumes only
    itself. Anything else that starts with `-` is an option this wrapper's table does
    not name -- read with no value, and the scan goes on regardless."""
    i = 0
    while i < len(words) and words[i].startswith("-") and words[i] != "--":
        word = words[i]
        name = word.split("=", 1)[0]
        if "=" in word and name in value:
            i += 1
        elif word in value:
            i += 2
        elif any(word != g and word.startswith(g) for g in glued):
            i += 1
        else:
            i += 1
    return i


def _peel(words: list[str]) -> tuple[list[str], bool]:
    """The real command behind every leading assignment (`NAME=value`) and recognized
    wrapper, peeled in one pass, in whatever order they come; plus whether `xargs` was
    among them -- the one fact `denial` needs that the peeled words alone no longer
    carry, since a `kill` fed by a pipe is denied regardless of xargs's own options."""
    via_xargs = False
    while words:
        word = words[0]
        if _ASSIGNMENT.match(word):
            words = words[1:]
        elif word in _SIMPLE_WRAPPERS:
            words = words[1:]
        elif word == "xargs":
            via_xargs = True
            words = words[1:]
            words = words[_skip_options(words, _XARGS_VALUE, _XARGS_GLUED) :]
        elif word == "env":
            words = words[1:]
            words = words[_skip_options(words, _ENV_VALUE) :]
        elif word == "sudo":
            words = words[1:]
            words = words[_skip_options(words, _SUDO_VALUE) :]
        elif word == "timeout":
            words = words[1:]
            words = words[_skip_options(words, _TIMEOUT_VALUE) :]
            if words:  # the duration itself, always consumed when there is one
                words = words[1:]
        elif word == "setsid":
            words = words[1:]
            words = words[_skip_options(words, ()) :]
        elif word == "nice":
            words = words[1:]
            words = words[_skip_options(words, _NICE_VALUE) :]
        else:
            break
    return words, via_xargs


def command_of(words: list[str]) -> list[str]:
    """The command `words` run once every leading assignment and recognized wrapper
    (`xargs`, `env`, `sudo`, `timeout`, `setsid`, `nice`, `builtin`, `command`, `exec`,
    `nohup`, `time`) is peeled away."""
    peeled, _ = _peel(words)
    return peeled


def _after_runner(words: list[str]) -> list[str]:
    """`words` with a JS package runner's own prefix (`npx`, `npm exec`, `pnpm dlx`,
    `yarn dlx`) stripped, so `npx kill-port 8000` is seen for what it names."""
    for prefix in _RUNNERS:
        if tuple(words[: len(prefix)]) == prefix:
            return words[len(prefix) :]
    return words


def _fuser_kills(args: list[str]) -> bool:
    return any(
        a == "--kill" or (a.startswith("-") and not a.startswith("--") and "k" in a[1:])
        for a in args
    )


def _kill_denial(
    args: list[str], checkout: Path, owner: Callable[[int], Path | None]
) -> str | None:
    """`args` is `kill`'s own words, after the program name. The whitelist grammar: at
    most one signal spec, then an optional `--`, then operands -- each of which passes
    only as a literal pid `owner` places inside `checkout`, or as one of the handful of
    shell forms (`$!`, `$NAME`, `${NAME}`) that always name the caller's own last job or
    a variable it set, never somebody else's process."""
    if args[:1] in (["-l"], ["-L"]):
        return None  # a query, first position only: sends no signal
    i = 0
    if args and args[0] != "--":
        word = args[0]
        if word.startswith("--signal="):
            i = 1
        elif word in _SPEC_VALUE:
            if len(args) < 2:
                return MALFORMED_OPTION
            i = 2
        elif _SIGNAL_SHORT.match(word):
            i = 1
        elif word.startswith("-"):
            return MALFORMED_OPTION
    if i < len(args) and args[i] == "--":
        i += 1
    operands = args[i:]
    if not operands:
        return MALFORMED_OPTION
    for operand in operands:
        if _PID_NUMBER.match(operand):
            try:
                owned = owner(int(operand))
            except Exception:
                owned = None
            if owned is not None and owned.is_relative_to(checkout):
                continue
            return NOT_IN_YOUR_CHECKOUT
        if _PID_SUBSTITUTION.match(operand):
            continue
        return FROM_SUBSTITUTION_OR_PIPE
    return None


def _denial(words: list[str], checkout: Path, owner: Callable[[int], Path | None]) -> str | None:
    peeled, via_xargs = _peel(list(words))
    if not peeled:
        return None
    program = peeled[0]
    after_runner = _after_runner(peeled)
    if after_runner and after_runner[0] in _PATTERN_PROGRAMS:
        return PATTERN_OR_PORT
    if after_runner and after_runner[0] == "fuser" and _fuser_kills(after_runner[1:]):
        return PATTERN_OR_PORT
    if program != "kill":
        return None
    if via_xargs:
        return FROM_SUBSTITUTION_OR_PIPE
    return _kill_denial(peeled[1:], checkout, owner)


def denial(words: list[str], checkout: Path, owner: Callable[[int], Path | None]) -> str | None:
    """Why `words` -- one already-split simple command -- may not run, or `None`. Total:
    a malformed input, or an `owner` that misbehaves, answers `None` rather than raise,
    because a `PreToolUse` hook that blows up is worse than one that lets a call through
    for the presubmit and the review to still catch."""
    try:
        return _denial(words, checkout, owner)
    except Exception:
        return None
