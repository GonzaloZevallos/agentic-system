"""The one fake of `gh api -i` that tests of GitHub access share (GH-1090).

Later parts of GH-1071 import `gh_response` from here; the older `fake_gh` helpers in
`test_inbox.py` and `test_cli.py` migrate to it in a later part, and new code must not add
another.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any


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
