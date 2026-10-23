"""`SampleApp` over bash: the product's `sample_app` command (ADR-0040), started in its
own process group so `stop` can kill everything it spawned, the same reason
`ShellCommands.run` starts one for a check's own timeout.

`start` reads output on a background thread so the main one can give up waiting at
`design.SAMPLE_APP_READY_S` without blocking on a pipe the process may never close
(the command's own job is to keep running, not to exit)."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from agentic.shared.environment import without_own_configuration, without_own_toolchain

from ...application.ports import SampleAppHandle
from ...domain.design import SAMPLE_APP_READY_S

# How long to wait for a killed process's last output to drain, same budget
# `ShellCommands.run` gives a timed-out check.
_DRAIN_S = 5.0

# A command that prints and exits at once (`echo boom; exit 7`) may be read before its exit
# is: wait this long after the first line, so a loaded machine does not turn a refusal
# into a handle on a dead process.
_SETTLE_S = 0.5


class ShellSampleApp:
    def start(self, command: str, cwd: Path) -> SampleAppHandle | str:
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd,
            env=without_own_configuration(without_own_toolchain(os.environ)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        lines: list[str] = []
        lock = threading.Lock()

        def _drain() -> None:
            assert process.stdout is not None
            for raw in process.stdout:
                with lock:
                    lines.append(raw.rstrip("\n"))

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()
        deadline = time.monotonic() + SAMPLE_APP_READY_S
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            with lock:
                if any(line.strip() for line in lines):
                    break
            time.sleep(0.05)
        try:
            process.wait(timeout=_SETTLE_S)
        except subprocess.TimeoutExpired:
            pass  # still running: it stays up after printing, as a server does
        reader.join(timeout=_SETTLE_S)  # the last lines of a process that has just exited
        with lock:
            printed = tuple(line for line in lines if line.strip())
        exit_code = process.poll()  # before `_kill`: once killed, every exit reads -9
        if exit_code is None and printed:
            return SampleAppHandle(lines=printed, token=process)
        self._kill(process)
        tail = "\n".join(printed) or "(no output)"
        detail = "printed nothing in time" if exit_code is None else f"exited {exit_code}"
        return f"`{command}` {detail}:\n{tail}"

    def stop(self, handle: SampleAppHandle) -> None:
        self._kill(handle.token)

    def _kill(self, process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # it ended on its own already
        try:
            process.communicate(timeout=_DRAIN_S)
        except subprocess.TimeoutExpired:
            process.kill()
