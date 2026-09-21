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
- Edit and Write: paths inside `.git/` or outside the checkout, and a new
  `# type: ignore` or `# noqa` in a Python file (engineering standards, 6).

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

The turn-budget notice (ADR-0004, seventh addendum; issue #85) is a `PostToolUse`
matcher, wired only for a role `domain.turn_notice.margin_for` covers: it counts
main-thread events (an event carrying `agent_id`, a sub-agent's own, is not this
role's turn and is ignored — `.get()`, never a subscript, since the SDK's own type
only ever promises the field on a sub-agent call) and, once the count crosses the
role's margin, returns `additionalContext` once and never again this attempt. Never a
`permissionDecision`: `PostToolUseHookSpecificOutput` structurally carries none
(ADR-0011, fourth addendum), so this can only ever add context, never narrow what the
role may run.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import HookCallback, HookContext, HookInput, HookJSONOutput, HookMatcher

from ...application.ports import Presubmit
from ...domain import turn_notice
from ...domain.roles import Role

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
_SILENCED = re.compile(r"#\s*(type:\s*ignore|noqa)\b")
_PYTHON = {".py", ".pyi"}

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
SILENCED = (
    "this adds `# type: ignore` or `# noqa`. The engineering standards say the type "
    "checker and the linter pass without being silenced: fix the code. When a type "
    "genuinely cannot be expressed, narrow it with `cast` to the exact type and a comment "
    "that says why."
)


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


def silenced_denial(file_path: str, before: str, after: str) -> str | None:
    """Why replacing `before` with `after` may not happen, or None: it adds a
    suppression. One that was already there is not the task's."""
    if not is_python(file_path):
        return None
    if len(_SILENCED.findall(after)) > len(_SILENCED.findall(before)):
        return _DENIED + SILENCED
    return None


def is_python(file_path: str) -> bool:
    return Path(file_path).suffix in _PYTHON


def hooks_for(
    role: Role,
    cwd: Path,
    presubmit: Presubmit | None,
    on_notice: Callable[[], None] | None = None,
    on_presubmit_denied: Callable[[], None] | None = None,
) -> dict[str, list[HookMatcher]]:
    """The hooks of one run: the cage for a role that executes, the presubmit when
    given, and the turn-budget notice for a role `turn_notice.margin_for` covers."""
    matchers: list[HookMatcher] = []
    if role.executes:
        matchers += [
            HookMatcher(matcher="Bash", hooks=[_bash(cwd)], timeout=GUARD_TIMEOUT_S),
            HookMatcher(matcher="Edit|Write", hooks=[_edit(cwd)], timeout=GUARD_TIMEOUT_S),
        ]
    if presubmit is not None:
        matchers.append(
            HookMatcher(
                matcher=DELIVERY_TOOL,
                hooks=[_presubmit(presubmit, on_presubmit_denied)],
                # The presubmit runs the product's checks: its own budget, plus the answer.
                timeout=presubmit.timeout_s + GUARD_TIMEOUT_S,
            )
        )
    hooks: dict[str, list[HookMatcher]] = {}
    if matchers:
        hooks["PreToolUse"] = matchers
    if (notice_hook := _turn_notice(role, on_notice)) is not None:
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


def _edit(cwd: Path) -> HookCallback:
    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        tool_input = _tool_input(data)
        file_path = str(tool_input.get("file_path", ""))
        here = _where(data, cwd)
        reason = path_denial(file_path, cwd, here)
        if reason is None and is_python(file_path):
            if "content" in tool_input:  # Write: the whole file replaces what was there
                target = Path(_normal(file_path, here))
                before = target.read_text(errors="replace") if target.is_file() else ""
                after = str(tool_input["content"])
            else:
                before = str(tool_input.get("old_string", ""))
                after = str(tool_input.get("new_string", ""))
            reason = silenced_denial(file_path, before, after)
        return _deny(reason) if reason else {}

    return hook


def _turn_notice(role: Role, on_notice: Callable[[], None] | None) -> HookCallback | None:
    """The `PostToolUse` callback that fires the live turn-budget notice, or `None` for
    a role `turn_notice.margin_for` does not cover. `count` is this attempt's own
    closure state: a `PostToolUse` count of main-thread events, the enforced-cap-turn
    unit the spike established. `fired` makes the notice a one-time thing per attempt:
    once the margin is crossed, later calls go back to answering nothing."""
    margin = turn_notice.margin_for(role)
    if margin is None:
        return None
    count = 0
    fired = False

    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        nonlocal count, fired
        if data.get("agent_id") is not None:
            return {}  # a sub-agent's own call: not this role's turn count
        count += 1
        if fired or not turn_notice.should_notice(role, count):
            return {}
        fired = True
        if on_notice is not None:
            on_notice()
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": turn_notice.notice_context(role.max_turns - count),
            }
        }

    return hook


def _presubmit(presubmit: Presubmit, on_denied: Callable[[], None] | None = None) -> HookCallback:
    """The `PreToolUse` callback the presubmit answers through. `on_denied` counts its
    own refusals (glossary: Puerta de entrega), the same closure shape `_deny_writes`
    has for `denials`: a presubmit refusal is the gate working, never the re-emission
    of an invalid shape a schema rejection is, and the two travel as separate counts."""

    async def hook(
        data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        # The checks are subprocesses: off the event loop that talks to the CLI.
        reason = await anyio.to_thread.run_sync(presubmit)
        if not reason:
            return {}
        if on_denied is not None:
            on_denied()
        return _deny(reason)

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
