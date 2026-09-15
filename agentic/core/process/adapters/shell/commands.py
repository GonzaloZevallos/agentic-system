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
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            return CommandResult(process.returncode, output or "", timed_out=True)
        return CommandResult(process.returncode, output or "")
