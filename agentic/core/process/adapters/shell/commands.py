"""`Commands` over bash: a product's check, run by the system in a checkout.

The command runs in its own process group, so a timeout stops what it spawned too
(`uv run` starts pytest, pytest may start workers) instead of leaving them holding the
pipe. It gets the system's environment without the system's own virtualenv, the same
the roles get (`shared.environment`): a check must find the product's toolchain.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from agentic.shared.environment import without_own_toolchain

from ...application.ports import CommandResult

# How long to wait for the output of a command already killed.
_DRAIN_S = 5.0


class ShellCommands:
    def run(self, cwd: Path, command: str, timeout_s: float) -> CommandResult:
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd,
            env=without_own_toolchain(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",  # a check's output is read, never parsed
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # it ended between the timeout and the kill
            try:
                output, _ = process.communicate(timeout=_DRAIN_S)
            except subprocess.TimeoutExpired:
                # A descendant that left the group still holds the pipe: give up on it.
                process.kill()
                output = ""
            return CommandResult(process.returncode or -1, output or "", timed_out=True)
        return CommandResult(process.returncode, output or "")
