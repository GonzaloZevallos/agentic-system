"""`Commands` over bash: a product's check, run by the system in a checkout.

The command runs in its own process group, so a timeout stops what it spawned too
(`uv run` starts pytest, pytest may start workers) instead of leaving them holding the
pipe. It gets the system's environment without the system's own virtualenv and without
the system's own `AGENTIC_*`, the same the roles get (`shared.environment`): a check
must find the product's toolchain, and when the product is this repo (ADR-0017) its
checks read those variables themselves.

Every run carries its own mark (`<product>/check/<system pid>/<uuid>`, replacing any role
mark it inherited) and reaps that token however it ends (GH-315, ADR-0052): what the check
started and left behind, even through `setsid` or a redirected `&`, is stopped with it.
When it times out the reaper also takes the unmarked processes whose cwd is the checkout
(`env -i setsid ...` drops the mark), which the reaper confines to the worktrees root. The
`CommandResult` is the same as without the reap; the only difference is what is left alive.
Each process the mark reap stops is one `reaper: stopped ...` line on stderr; the cwd reap
prints its own.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from agentic.shared.environment import without_own_configuration, without_own_toolchain

from ...application.ports import CommandResult, ProcessReaper
from ...domain import reaping

# How long to wait for the output of a command already killed.
_DRAIN_S = 5.0


class ShellCommands:
    def __init__(self, product_key: str, reaper: ProcessReaper) -> None:
        self._product = product_key
        self._reaper = reaper

    def run(
        self,
        cwd: Path,
        command: str,
        timeout_s: float,
        *,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        token = uuid4().hex
        child_env = without_own_configuration(without_own_toolchain(os.environ))
        # The grant's variables go over the cleaned environment and under the mark: whatever
        # the caller passes, the check is reaped by the token this run minted.
        child_env.update(env or {})
        child_env[reaping.MARK_NAME] = reaping.mark(
            self._product, reaping.CHECK_TASK, os.getpid(), token
        )
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",  # a check's output is read, never parsed
            start_new_session=True,
        )
        try:
            try:
                output, _ = process.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # it ended between the timeout and the kill
                self._reap_token(token)
                self._reaper.reap_within([cwd])  # prints its own lines
                try:
                    output, _ = process.communicate(timeout=_DRAIN_S)
                except subprocess.TimeoutExpired:
                    # A descendant that left the group still holds the pipe: give up on it.
                    process.kill()
                    output = ""
                return CommandResult(process.returncode or -1, output or "", timed_out=True)
            return CommandResult(process.returncode, output or "")
        finally:
            self._reap_token(token)

    def _reap_token(self, token: str) -> None:
        """Stop what still carries the check's mark: one stderr line per process stopped."""
        for entry in self._reaper.reap_attempt(reaping.CHECK_TASK, os.getpid(), token):
            if entry["outcome"] != "survived":  # a survivor has its own line already
                print(
                    f"reaper: stopped {entry['pid']} {entry['command']} ({entry['outcome']})",
                    file=sys.stderr,
                )
