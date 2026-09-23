"""SDK hooks: the hard cage of a role that executes, and the presubmit.

Permission rules match a command by its prefix, so `git -C path config` or an Edit of
`.git/config` walked past them (ADR-0011, addendum). A `PreToolUse` hook sees the
parsed call instead, and its deny holds in `bypassPermissions` too. The decisions are
pure functions over the tool input; `hooks_for` wires them as `HookMatcher`s.

What the cage covers, for a role that executes:

- Bash: `git config` (reading is fine) and `git -c user.*`, `git push`, `git worktree`,
  `--git-dir` and `GIT_DIR=`, `git -C` outside the checkout, skipping the commit hooks
  (`--no-verify`, `-n`, `-c core.hooksPath`), and switching branches (`git switch`,
  `git checkout` without `--`). Found anywhere in the command: after `;`, `&&`, `|`, a
  newline, inside `$(...)`, `sh -c '...'` or `bash -lc '...'`, or behind `VAR=value`.
- Edit and Write: paths inside `.git/` or outside the checkout, and a new occurrence of
  a suppression the product declares under `forbidden` in `.agentic/stack.yaml`
  (`core.knowledge.domain.stack`, `core.process.domain.suppressions`; issue #38,
  engineering standards 6). The system spells none of its own: what a silencing
  comment looks like, in whatever language the product is in, is the product's to say.

What it does not cover, and does not try to: a shell that moves first
(`cd ..; echo > file`), files written by Bash (`sed -i`, `cat >`), a command built at
run time (`$VAR`, `eval`, an alias, `$(...)` inside double quotes), a script fed to a
shell on stdin (`bash <<EOF`), symlinks out of the checkout, and any command shlex
cannot parse, which passes. The cage raises the bar
over the prompt; the system's identity in the environment still decides who signs.

The presubmit is a `PreToolUse` deny on the structured-output tool, not a `Stop`
block: with `output_format` the turn ends at that tool call, `Stop` fires after it and
a block there is logged but ends the session anyway (spike of issue #22, SDK 0.2.152).
A denied delivery call reaches the model as the reason, and it delivers again.

The schema guard (glossary: Guarda de esquema; ADR-0011, fifth addendum) answers the
same tool call, ahead of the presubmit: one `PreToolUse` matcher on the structured-
output tool, one callback, guard first and presubmit second, written in that order
here rather than left to how the SDK would combine two matchers of its own. A guard
denial short-circuits the callback, so the presubmit never runs the product's checks
over an input that is not even shaped right.

The turn-budget notice (ADR-0004, seventh addendum, revised by its 2026-09-22
addendum, issue #102) is a `PostToolUse` matcher, wired only for a role
`domain.turn_notice.threshold_for` covers. It reads a count it never keeps itself:
`TurnCount`, one mutable holder per attempt, constructed and incremented by
`ClaudeSdkRunner.run_once`'s own stream loop (the runner's main-thread
`AssistantMessage` tally, `domain/turn_notice.py`'s own counted quantity) and passed
down the same parameter path `on_notice` already travels. No `agent_id` filter here
any more: the counter it reads is already main-thread-only by construction
(`message.parent_tool_use_id is None`), so a `PostToolUse` event that happens to carry
one changes nothing about whether this fires. Once the count reaches the role's
threshold it returns `additionalContext` on every later call too, each one carrying
its own firing index (`domain/turn_notice.notice_context`) — a re-firing close-out
window, not the one-shot advisory it replaces, because that advisory fired in every
recorded `max_turns` cut and changed nothing (ADR-0004's revising addendum). Never a
`permissionDecision`: `PostToolUseHookSpecificOutput` structurally carries none
(ADR-0011, fourth addendum), so this can only ever add context, never narrow what the
role may run.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput, HookMatcher

from ...application.ports import Presubmit, SchemaGuard
from ...domain import suppressions, turn_notice
from ...domain.roles import Role
from ...domain.suppressions import Forbidden

# The tool Claude Code gives the model to hand back the structured output.
DELIVERY_TOOL = "StructuredOutput"
# A guard reads its input and answers; the CLI's default wait is not the point.
GUARD_TIMEOUT_S = 30.0

_DENIED = "Denied by the system: "
_SEPARATORS = set(";&|()<>\n")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"sudo", "env", "command", "exec", "time", "nohup", "xargs"}
_SHELLS = {"sh", "bash", "zsh", "dash"}
# `<<` and `<<-`, not the here-string `<<<`.
_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_GIT_ENV = {"GIT_DIR", "GIT_WORK_TREE"}
# Global git options that take the next word as their value.
_GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
_CONFIG_READS = {"--get", "--get-all", "--get-regexp", "--list", "-l"}
# Short options of `git commit` that take a value, glued or as the next word: `-mfix` is
# not `-n`. `-S` takes one only glued.
_COMMIT_VALUED = set("mFcCt")
_COMMIT_GLUED = set("S")

CONFIG = (
    "`git config` changes the product's git: your commits are already signed as the "
    "system. Reading it (`--get`, `--list`) is fine."
)
PUSH = "`git push`: the system pushes the task branch when it delivers."
WORKTREE = "`git worktree`: the system creates and removes the worktrees; work in yours."
GIT_DIR = "`--git-dir`: work on the repository of your working directory."
OUTSIDE = "`git -C` outside your working directory: work on your own checkout."
NO_VERIFY = "`git commit --no-verify`: the product's hooks run on every commit; fix what they say."
SWITCH = (
    "switching branches: you work on the current branch. To restore files, use "
    "`git checkout -- <path>` or `git restore <path>`."
)
GIT_PATH = "a path inside `.git/`: the repository's internals are not yours to edit."
OUTSIDE_PATH = "a path outside your working directory: edit only your checkout."


@dataclass
class TurnCount:
    """The runner's own tally of main-thread `AssistantMessage`s, the quantity
    `domain/turn_notice.py`'s threshold is stated in: one instance per attempt,
    constructed in `ClaudeSdkRunner.run_once` beside its `_mark_notice` closure and
    incremented in exactly one place, inside its stream loop's existing `if
    message.parent_tool_use_id is None:` branch. `_turn_notice` below only ever reads
    `value`; it never increments it -- the count it needs and the count the harness
    enforces against `max_turns` are the same read, not two tallies kept in step by
    hand."""

    value: int = 0


def git_denial(command: str, cwd: Path, relative_to: Path | None = None) -> str | None:
    """Why the Bash `command` may not run, or None. `cwd` is the role's checkout;
    `relative_to` is where the shell stands, the checkout unless said otherwise."""
    reason = _reason(command, cwd, relative_to or cwd)
    return _DENIED + reason if reason else None


def path_denial(file_path: str, cwd: Path, relative_to: Path | None = None) -> str | None:
    """Why an Edit or Write of `file_path` may not happen, or None."""
    path = _normal(file_path, relative_to or cwd)
    if ".git" in Path(path).parts:
        return _DENIED + GIT_PATH
    if not _inside(path, cwd):
        return _DENIED + OUTSIDE_PATH
    return None


def forbidden_denial(
    file_path: str, before: str, after: str, forbidden: Sequence[Forbidden]
) -> str | None:
    """Why replacing `before` with `after` in `file_path` may not happen, or None: it
    adds an occurrence of a suppression the product declared under `forbidden`
    (`process.domain.suppressions.denial`, issue #38). The system authors nothing past
    its own prefix; `reason` is the product's, verbatim."""
    reason = suppressions.denial(file_path, before, after, forbidden)
    return _DENIED + reason if reason else None


def hooks_for(
    role: Role,
    cwd: Path,
    presubmit: Presubmit | None,
    on_notice: Callable[[], None] | None = None,
    turns: TurnCount | None = None,
    on_presubmit_denied: Callable[[], None] | None = None,
    guard: SchemaGuard | None = None,
    forbidden: Sequence[Forbidden] | None = None,
) -> dict[str, list[HookMatcher]]:
    """The hooks of one run: the cage for a role that executes, the schema guard and
    the presubmit sharing one matcher on the delivery tool when either is given, and
    the turn-budget notice for a role `turn_notice.threshold_for` covers. `turns` is
    the counter that notice reads (`TurnCount`'s own docstring); `None` reads as a
    count that never reaches any threshold, which is every call this module's own
    tests make that are not about the notice itself.

    `forbidden` is the product's own declared suppressions (issue #38): `None` or
    empty is the explicit no-op -- the Edit/Write matcher is installed either way (the
    path cage still applies), and denies no suppression when there is none declared.
    It never fails closed or open on a broken declaration, because a broken one never
    reaches here (`stack.forbidden` raises at load, in `preflight`)."""
    matchers: list[HookMatcher] = []
    if role.executes:
        matchers += [
            HookMatcher(matcher="Bash", hooks=[_bash(cwd)], timeout=GUARD_TIMEOUT_S),
            HookMatcher(
                matcher="Edit|Write", hooks=[_edit(cwd, forbidden or ())], timeout=GUARD_TIMEOUT_S
            ),
        ]
    if guard is not None or presubmit is not None:
        matchers.append(
            HookMatcher(
                matcher=DELIVERY_TOOL,
                hooks=[_delivery_gate(guard, presubmit, on_presubmit_denied)],
                # The presubmit runs the product's checks: its own budget, plus the
                # answer. The guard is in-process and fast, so it adds nothing here:
                # `GUARD_TIMEOUT_S` alone already covers its share (unchanged from
                # before the guard existed, when this was the presubmit's own timeout).
                timeout=GUARD_TIMEOUT_S + (presubmit.timeout_s if presubmit is not None else 0.0),
            )
        )
    hooks: dict[str, list[HookMatcher]] = {}
    if matchers:
        hooks["PreToolUse"] = matchers
    if (notice_hook := _turn_notice(role, turns, on_notice)) is not None:
        hooks["PostToolUse"] = [HookMatcher(hooks=[notice_hook], timeout=GUARD_TIMEOUT_S)]
    return hooks


# -- the callbacks ---------------------------------------------------------------


def _bash(cwd: Path) -> HookCallback:
    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_input = _tool_input(data)
        reason = git_denial(str(tool_input.get("command", "")), cwd, _where(data, cwd))
        return _deny(reason) if reason else {}

    return hook


def _edit(cwd: Path, forbidden: Sequence[Forbidden]) -> HookCallback:
    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_input = _tool_input(data)
        file_path = str(tool_input.get("file_path", ""))
        here = _where(data, cwd)
        reason = path_denial(file_path, cwd, here)
        if reason is None and forbidden:
            if "content" in tool_input:  # Write: the whole file replaces what was there
                target = Path(_normal(file_path, here))
                before = target.read_text(errors="replace") if target.is_file() else ""
                after = str(tool_input["content"])
            else:
                before = str(tool_input.get("old_string", ""))
                after = str(tool_input.get("new_string", ""))
            reason = forbidden_denial(file_path, before, after, forbidden)
        return _deny(reason) if reason else {}

    return hook


def _turn_notice(
    role: Role, turns: TurnCount | None, on_notice: Callable[[], None] | None
) -> HookCallback | None:
    """The `PostToolUse` callback that fires the live turn-budget notice, or `None` for
    a role `turn_notice.threshold_for` does not cover. `turns` is read, never
    incremented, here (`TurnCount`'s own docstring: the stream loop owns the write).
    `firings` is this callback's own closure state, distinct from `turns.value`: how
    many times *this* hook has fired this attempt, which is what `notice_context`
    reports as each firing's own index. No latch: once `turns.value` reaches the
    threshold, every later `PostToolUse` event fires again, each with its own index --
    the re-firing close-out window `domain/turn_notice.py`'s docstring argues for,
    replacing the one-shot advisory that fired in every recorded cut and changed
    nothing."""
    threshold = turn_notice.threshold_for(role)
    if threshold is None:
        return None
    firings = 0

    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal firings
        count = turns.value if turns is not None else 0
        if not turn_notice.should_notice(role, count):
            return {}
        firings += 1
        if on_notice is not None:
            on_notice()
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": turn_notice.notice_context(firings),
            }
        }

    return hook


def _delivery_gate(
    guard: SchemaGuard | None,
    presubmit: Presubmit | None,
    on_presubmit_denied: Callable[[], None] | None = None,
) -> HookCallback:
    """The one `PreToolUse` callback on the delivery tool: the schema guard first, the
    presubmit second, the order this module's docstring promises written here in code
    rather than left to how the SDK would combine two matchers on its own. Either
    absent skips its own step. `on_presubmit_denied` counts only the presubmit's own
    refusals (glossary: Puerta de entrega), the same closure shape `_deny_writes` has
    for `denials`: a presubmit refusal is the gate working, never the re-emission of an
    invalid shape a schema rejection is, and the two travel as separate counts."""

    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if guard is not None:
            reason = guard(_tool_input(data))
            if reason:
                return _deny(reason)
        if presubmit is not None:
            # The checks are subprocesses: off the event loop that talks to the CLI.
            reason = await anyio.to_thread.run_sync(presubmit)
            if reason:
                if on_presubmit_denied is not None:
                    on_presubmit_denied()
                return _deny(reason)
        return {}

    return hook


def _deny(reason: str) -> HookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _tool_input(data: HookInput) -> dict[str, Any]:
    tool_input = data.get("tool_input")
    return tool_input if isinstance(tool_input, dict) else {}


def _where(data: HookInput, cwd: Path) -> Path:
    return Path(str(data.get("cwd") or cwd))


# -- parsing -------------------------------------------------------------------------


def _reason(command: str, cwd: Path, here: Path) -> str | None:
    for words in _commands(command):
        if reason := _denial_of(words, cwd, here):
            return reason
    return None


def _commands(command: str) -> list[list[str]]:
    """The simple commands in `command`, as words, heredoc bodies left out."""
    lexer = shlex.shlex(_without_heredocs(command), posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []  # the shell would not parse it either, mostly; see the docstring
    commands: list[list[str]] = [[]]
    for token in tokens:
        if set(token) <= _SEPARATORS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def _without_heredocs(command: str) -> str:
    lines, delimiter = [], None
    for line in command.split("\n"):
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        lines.append(line)
        if found := _HEREDOC.search(line):
            delimiter = found.group(2)
    return "\n".join(lines)


def _denial_of(words: list[str], cwd: Path, here: Path) -> str | None:
    assigned = set()
    while words and (_ASSIGNMENT.match(words[0]) or words[0] in _WRAPPERS):
        if _ASSIGNMENT.match(words[0]):
            assigned.add(words[0].split("=", 1)[0])
        words = words[1:]
    if not words:
        return None
    program = os.path.basename(words[0])
    if program in _SHELLS:
        # The script follows the first option cluster that holds `c`: `-c`, `-lc`.
        for i, word in enumerate(words[1:-1], start=1):
            if word.startswith("-") and not word.startswith("--") and "c" in word:
                return _reason(words[i + 1], cwd, here)
        return None
    if program != "git":
        return None
    if assigned & _GIT_ENV:
        return GIT_DIR
    return _git(words[1:], cwd, here)


def _git(words: list[str], cwd: Path, here: Path) -> str | None:
    i = 0
    while i < len(words) and words[i].startswith("-"):
        option, value = words[i], None
        if "=" in option and option.split("=", 1)[0] in _GIT_VALUED:
            option, value = option.split("=", 1)
            i += 1
        elif option in _GIT_VALUED:
            value = words[i + 1] if i + 1 < len(words) else ""
            i += 2
        else:
            i += 1
        if option == "--git-dir":
            return GIT_DIR
        if option == "-c" and value is not None:
            key = value.split("=", 1)[0].lower()
            if key.startswith("user."):
                return CONFIG
            if key == "core.hookspath":
                return NO_VERIFY
        if option == "-C" and value is not None:
            here = Path(_normal(value, here))
            if not _inside(str(here), cwd):
                return OUTSIDE
    if i >= len(words):
        return None
    subcommand, args = words[i], words[i + 1 :]
    if subcommand == "config":
        reading = args[:1] in (["get"], ["list"]) or bool(_CONFIG_READS & set(args))
        return None if reading else CONFIG
    if subcommand == "push":
        return PUSH
    if subcommand == "worktree":
        return WORKTREE
    if subcommand == "switch" or (subcommand == "checkout" and "--" not in args):
        return SWITCH
    if subcommand == "commit" and _skips_hooks(args):
        return NO_VERIFY
    return None


def _skips_hooks(args: list[str]) -> bool:
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == "--":
            return False
        if arg.startswith("--no-v") and "--no-verify".startswith(arg):
            return True  # git takes any unambiguous prefix of a long option
        if arg.startswith("-") and not arg.startswith("--"):
            for position, letter in enumerate(arg[1:], start=1):
                if letter == "n":
                    return True
                if letter in _COMMIT_GLUED:
                    break
                if letter in _COMMIT_VALUED:
                    skip_value = position == len(arg) - 1  # the value is the next word
                    break
    return False


def _normal(file_path: str, relative_to: Path) -> str:
    path = Path(file_path)
    return os.path.normpath(path if path.is_absolute() else relative_to / path)


def _inside(path: str, cwd: Path) -> bool:
    root = os.path.normpath(cwd)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)
