"""The one fake of `gh api -i` that tests of GitHub access share (GH-1090).

Later parts of GH-1071 import `gh_response` from here; the older `fake_gh` helpers in
`test_inbox.py` and `test_cli.py` migrate to it in a later part, and new code must not add
another.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic.core.process.domain.github_access import Turn


@dataclass(frozen=True)
class StubGate:
    """A frozen, hashable `WriteGate` that grants every turn at once (GH-1083)."""

    cap_s: float = 30.0

    def take(self, remaining_s: float) -> Turn:
        return Turn("granted", 0.0)


class RecordingRun:
    """A `Run` (the callable `run_gh` receives) that answers every call with the same
    response and records each call's argv in `argv` and its full kwargs in `kwargs`."""

    def __init__(self, stdout: str, stderr: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.argv: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.argv.append(list(args))
        self.kwargs.append(kwargs)
        if self.returncode != 0:
            raise subprocess.CalledProcessError(
                self.returncode, args, output=self.stdout, stderr=self.stderr
            )
        return subprocess.CompletedProcess(args, 0, self.stdout, self.stderr)


def gh_response(
    status: int,
    headers: Sequence[tuple[str, str]],
    body: str,
    stderr: str = "",
    returncode: int | None = None,
) -> RecordingRun:
    """A run fake whose stdout is what `gh api -i` prints: status line, headers, blank line,
    body, CRLF line ends. `returncode=None` is 0 for 2xx and 304 and 1 otherwise, as `gh`
    exits; a non-zero one raises `CalledProcessError` carrying stdout and stderr."""
    lines = [f"HTTP/2.0 {status}", *(f"{name}: {value}" for name, value in headers), ""]
    stdout = "\r\n".join(lines) + "\r\n" + body
    if returncode is None:
        returncode = 0 if 200 <= status < 300 or status == 304 else 1
    return RecordingRun(stdout, stderr, returncode)


FIXTURES = Path(__file__).parent / "fixtures"


def gh_fixture(name: str, **over: Any) -> RecordingRun:
    """The `gh_response` a fixture file under `fixtures/` describes: its `status`, `headers`
    (name/value pairs), `body` (the text GitHub answered) and `stderr`. Each file carries a
    `note` saying whether it was captured or derived; `over` replaces any of the four."""
    data = {**json.loads((FIXTURES / name).read_text(encoding="utf-8")), **over}
    return gh_response(
        data["status"],
        [(key, value) for key, value in data["headers"]],
        data["body"],
        data["stderr"],
    )


class ScriptedRun:
    """A `Run` that plays `steps` in order, one per call: each is a `RecordingRun` answering
    that call. Every call's argv is kept in `argv`; a call past the last step fails the test.
    `bodies[n]` is what the file named by `-F body=@<file>` held when call n ran (`None` when
    that call had none), since the caller removes the file when it ends."""

    def __init__(self, *steps: RecordingRun) -> None:
        self.steps = list(steps)
        self.argv: list[list[str]] = []
        self.bodies: list[str | None] = []

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert self.steps, f"no step left for {list(args)}"
        self.argv.append(list(args))
        named = [arg[len("body=@") :] for arg in args if arg.startswith("body=@")]
        self.bodies.append(Path(named[0]).read_bytes().decode("utf-8") if named else None)
        return self.steps.pop(0)(args, **kwargs)
