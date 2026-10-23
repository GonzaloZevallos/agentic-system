"""`PerfRunner` over bash: one `seed`, `measure` or perf `setup` command of a product, run by
the system in a throwaway checkout (GH-318, ADR-0058).

**What the command gets.** An environment built from nothing, never the system's: `PATH`,
every `LANG`/`LC_*` and `TZ` copied from the system's, the checkout's `.venv` when it has one
(`VIRTUAL_ENV`, its `bin` in front of `PATH`), `PERF_VOLUME` and `PERF_VOLUME_UNIT` from the
caller, `HOME` and `TMPDIR` in a scratch directory made for this one run, and the reaper mark.
No `GH_*`, `GITHUB_*`, `ANTHROPIC_*`, `LANGFUSE_*` or `AGENTIC_*` variable, nothing from the
system's `.env`: `extra_env` may only name the two volume variables, and anything else is
refused before a process starts. The command runs with the checkout as its cwd, no stdin and
its own session. The network stays open: this is a scrub of credentials, not a sandbox.

**What is stopped.** Every run carries its own mark (`<product>/perf/<system pid>/<uuid>`)
and, however it ends, the session is killed and the reaper stops what is left. The guarantee,
exactly: what is stopped is what carries the command's mark, or is unmarked with its cwd
inside the checkout, at the moment of the reap. A child that drops the environment
(`env -i`) and leaves the checkout (`cd /`) is neither, and survives the verb: a known gap
the tests pin and ADR-0058 lists.

**What comes back.** A `Run`, always: a non-zero exit and a timeout are fields of it, never
an exception. A timed-out command has no measurement (its output is cut). The one-minute
load average is read before and after, `None` where the machine has none.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from ...application.ports import ProcessReaper
from ...domain import performance, reaping

# How long to wait for the output of a command already killed.
_DRAIN_S = 5.0
# The only variables the caller may add: what the product's commands read as their volume.
_EXTRA_ENV = ("PERF_VOLUME", "PERF_VOLUME_UNIT")


class PerfShellRunner:
    def __init__(self, product_key: str, reaper: ProcessReaper) -> None:
        self._product = product_key
        self._reaper = reaper

    def run(
        self, checkout: Path, command: str, extra_env: Mapping[str, str], timeout_s: float
    ) -> performance.Run:
        if refused := [name for name in extra_env if name not in _EXTRA_ENV]:
            raise ValueError(f"a perf command's environment may only add {_EXTRA_ENV}: {refused}")
        token = uuid4().hex
        scratch = Path(tempfile.mkdtemp(prefix="perf-home-"))
        process: subprocess.Popen[str] | None = None
        try:
            env = self._environment(checkout, scratch, extra_env, token)
            before = _load()
            process = subprocess.Popen(
                ["bash", "-c", command],
                cwd=checkout,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",  # output is read, and only one line of it parsed
                start_new_session=True,
            )
            try:
                output, _ = process.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self._stop(process, token, checkout)
                try:
                    process.communicate(timeout=_DRAIN_S)
                except subprocess.TimeoutExpired:
                    # A descendant that left the group still holds the pipes: give up on it.
                    process.kill()
                return performance.Run(None, process.returncode or -1, True, before, _load())
            return performance.Run(
                performance.parse_line(output or ""), process.returncode, False, before, _load()
            )
        finally:
            if process is not None:
                _kill_group(process.pid)
            self._reap(token, checkout)
            shutil.rmtree(scratch, ignore_errors=True)

    def _environment(
        self, checkout: Path, scratch: Path, extra_env: Mapping[str, str], token: str
    ) -> dict[str, str]:
        path = os.environ.get("PATH", os.defpath)
        env = {
            name: value
            for name, value in os.environ.items()
            if name in ("LANG", "TZ") or name.startswith("LC_")
        }
        venv = checkout / ".venv"
        if venv.is_dir():
            env["VIRTUAL_ENV"] = str(venv)
            path = f"{venv / 'bin'}{os.pathsep}{path}"
        env["PATH"] = path
        env["HOME"] = env["TMPDIR"] = str(scratch)
        env.update(extra_env)
        env[reaping.MARK_NAME] = reaping.mark(self._product, reaping.PERF_TASK, os.getpid(), token)
        return env

    def _stop(self, process: subprocess.Popen[str], token: str, checkout: Path) -> None:
        _kill_group(process.pid)
        self._reap(token, checkout)

    def _reap(self, token: str, checkout: Path) -> None:
        """What still carries the command's mark, then what is unmarked with its cwd in the
        checkout (the reaper prints its own lines for those): one stderr line per process
        the mark reap stopped."""
        for entry in self._reaper.reap_attempt(reaping.PERF_TASK, os.getpid(), token):
            if entry["outcome"] != "survived":  # a survivor has its own line already
                print(
                    f"reaper: stopped {entry['pid']} {entry['command']} ({entry['outcome']})",
                    file=sys.stderr,
                )
        self._reaper.reap_within([checkout])


def _kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError):  # it ended before the kill
        os.killpg(pgid, signal.SIGKILL)


def _load() -> float | None:
    try:
        return os.getloadavg()[0]
    except OSError:
        return None
