"""A Developer's own check runs, noted by the Bash hooks (GH-467, ADR-0008).

One `CheckRunLog` per attempt, built by the runner beside `HookActivity`. The `PreToolUse`
Bash hook calls `pre` after its denial verdict, so a denied call leaves no row; the
`PostToolUse` and `PostToolUseFailure` hooks call `post` and `failed`, which close the row
of the same `tool_use_id`. Whether a call is a check run is `domain/check_runs.match`'s
decision alone; here are the clocks (the runner's monotonic `clock` for the duration, the
injected `Now` for the ISO instant), the scrub and the bounds.

Nothing recorded here reaches a hook's return value, and nothing raises out of it: any
exception is swallowed and counted in `errors`, so a bug in the recording is a number in the
document and never a Bash call the model could not make.

Exit codes. `PostToolUse` carries the code only when the tool response has one. The SDK's
types say a failing Bash call arrives as `PostToolUseFailure` (`Exit code N` in `error`), but
whether a `PostToolUse` without a code means "exited 0" has NOT been probed: the attempt to
probe it (GH-521) was refused by the CLI in the build environment ("Prompt is too long").
Until `EXIT_ZERO_PROBED_WITH` names an SDK version that a real probe confirmed, a
`PostToolUse` without a code is `exit_code: None`, never a guessed 0. A detached call, or one
whose status a pipe or a later chained segment decides, has no code of its own: `None`.
"""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import HookInput

from ...application.ports import CheckRuns
from ...domain import check_runs
from ...domain.ci_push import scrub
from ...domain.task import CheckRun

__all__ = ["EXIT_ZERO_PROBED_WITH", "KEEP", "CheckRunLog", "CheckRuns"]

# The `claude-agent-sdk` version a probe confirmed that a `PostToolUse` of a Bash call without
# an exit code means "exited 0". `None`: no probe has been run, so no version qualifies. Set it
# to the probed version, with the probe's command and output in the task, to turn the
# inference on for that version only.
EXIT_ZERO_PROBED_WITH: str | None = None
# The rows kept per attempt; the older ones are counted in `CheckRuns.dropped`.
KEEP = 50
# What ADR-0008 allows a command in the document: scrubbed and bounded.
COMMAND_LIMIT = 200
_EXIT_CODE = re.compile(r"^Exit code (-?\d+)")


@dataclass
class _Row:
    call_id: str
    run: CheckRun
    started: float
    detached: bool
    decided: bool


def _installed_version() -> str:
    try:
        return importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        return ""


class CheckRunLog:
    def __init__(
        self,
        declared: Mapping[str, str],
        clock: Callable[[], float],
        now: Callable[[], str],
        sdk_version: str | None = None,
    ) -> None:
        """`declared` maps a check's name to its command (a rerun or affected form under
        `<name>#<form>`); `clock` times a call, `now` stamps it."""
        self._declared = declared
        self._clock = clock
        self._now = now
        self._version = _installed_version() if sdk_version is None else sdk_version
        self._rows: list[_Row] = []
        self._open: dict[str, _Row] = {}
        self._dropped = 0
        self._errors = 0

    def pre(self, data: HookInput, tool_use_id: str | None) -> None:
        self._guarded(self._open_row, data, tool_use_id)

    def post(self, data: HookInput, tool_use_id: str | None) -> None:
        self._guarded(self._close_ok, data, tool_use_id)

    def failed(self, data: HookInput, tool_use_id: str | None) -> None:
        self._guarded(self._close_failed, data, tool_use_id)

    def snapshot(self) -> CheckRuns:
        """What the attempt recorded: a call still open is written with `None` duration and
        code, never closed with a guess."""
        return CheckRuns(
            rows=[row.run.copy() for row in self._rows],
            dropped=self._dropped,
            errors=self._errors,
        )

    def _guarded(
        self, step: Callable[[HookInput, str | None], None], data: HookInput, call_id: str | None
    ) -> None:
        try:
            step(data, call_id)
        except Exception:
            self._errors += 1

    def _open_row(self, data: HookInput, tool_use_id: str | None) -> None:
        call_id = _call_id(data, tool_use_id)
        if data.get("agent_id") is not None or call_id is None:
            return  # a sub-agent's call is not the Developer's own
        tool_input = data.get("tool_input")
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        found = check_runs.match(str(tool_input.get("command", "")), self._declared)
        if found is None:
            return
        row = _Row(
            call_id=call_id,
            run={
                "command": scrub(found.segment, limit=COMMAND_LIMIT),
                "check": found.check,
                "started_at": self._now(),
                "duration_s": None,
                "exit_code": None,
            },
            started=self._clock(),
            detached=found.detached or tool_input.get("run_in_background") is True,
            decided=found.status_decided,
        )
        self._rows.append(row)
        self._open[call_id] = row
        if len(self._rows) > KEEP:
            old = self._rows.pop(0)
            self._open.pop(old.call_id, None)
            self._dropped += 1

    def _close_ok(self, data: HookInput, tool_use_id: str | None) -> None:
        self._close(data, tool_use_id, self._ok_code(data.get("tool_response")))

    def _close_failed(self, data: HookInput, tool_use_id: str | None) -> None:
        code: int | None = None
        if data.get("is_interrupt") is not True:
            found = _EXIT_CODE.match(str(data.get("error", "")))
            code = int(found.group(1)) if found else None
        self._close(data, tool_use_id, code)

    def _close(self, data: HookInput, tool_use_id: str | None, code: int | None) -> None:
        call_id = _call_id(data, tool_use_id)
        row = self._open.pop(call_id, None) if call_id is not None else None
        if row is None or data.get("agent_id") is not None or row.detached:
            return
        row.run["duration_s"] = round(self._clock() - row.started, 3)
        row.run["exit_code"] = code if row.decided else None

    def _ok_code(self, response: Any) -> int | None:
        if isinstance(response, dict):
            for key in ("exit_code", "exitCode"):
                value = response.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
        probed = EXIT_ZERO_PROBED_WITH
        return 0 if probed is not None and self._version == probed else None


def _call_id(data: HookInput, tool_use_id: str | None) -> str | None:
    found = data.get("tool_use_id") or tool_use_id
    return found if isinstance(found, str) and found else None
