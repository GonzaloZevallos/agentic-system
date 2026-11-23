"""What `gh api -i` printed, read as an HTTP response, and how long a refusal says to wait
(GH-1090): pure rules, the one place that parses that text.

`split_response` turns the stdout of `gh api -i` into an `HttpResponse`; `wait_from` reads the
`retry-after` and `x-ratelimit-reset` headers of one. No other module parses either. Nothing
here reads a clock, the environment, a file or `gh`'s stderr: `now` and `backoff` arrive as
parameters, and capping a wait belongs to whoever sleeps, so the cap happens once.

`GithubAccess` is the placeholder of the access object later parts fill in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["GithubAccess", "HttpResponse", "NotAnHttpResponse", "split_response", "wait_from"]

# `HTTP/1.1 200 OK`, `HTTP/2 200` and `HTTP/2.0 200 OK`: the three forms `gh` prints; the
# reason phrase is optional (HTTP/2 has none). The status is exactly three ASCII digits.
_STATUS_LINE = re.compile(r"HTTP/(?:1\.1|2|2\.0) ([0-9]{3})(?: .*)?", re.ASCII)


class NotAnHttpResponse(ValueError):
    """The text is not the output of `gh api -i`: empty, or no HTTP status line first."""


@dataclass(frozen=True)
class HttpResponse:
    """One response: its status, its headers (lower-case names, in order of first appearance,
    the values of a repeated name joined with `, `) and its body exactly as printed."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: str

    def header(self, name: str) -> str | None:
        """The value of header `name` (any case); `None` when the response has none."""
        wanted = name.lower()
        values = [value for key, value in self.headers if key.lower() == wanted]
        return ", ".join(values) if values else None


def _read_status(line: str) -> int:
    found = _STATUS_LINE.fullmatch(line)
    if found is None:
        raise NotAnHttpResponse(f"not an HTTP status line: {line[:40]!r}")
    return int(found.group(1))


def _read_headers(text: str, pos: int) -> tuple[tuple[tuple[str, str], ...], int | None]:
    """The headers from `pos` to the first blank line, and where the text after that line
    starts; `None` when the text ends inside the headers."""
    joined: dict[str, str] = {}
    while pos < len(text):
        end = text.find("\n", pos)
        line = text[pos:] if end < 0 else text[pos:end]
        pos = len(text) if end < 0 else end + 1
        if line == "":
            return tuple(joined.items()), pos
        name, colon, value = line.partition(":")
        if not colon:
            raise NotAnHttpResponse(f"not a header line: {line[:40]!r}")
        key = name.strip().lower()
        joined[key] = f"{joined[key]}, {value.strip()}" if key in joined else value.strip()
    return tuple(joined.items()), None


def split_response(stdout: str) -> HttpResponse:
    """The response in the stdout of `gh api -i`: CRLF and LF alike, the last status block
    when interim `1xx` blocks come first, everything after the blank line that ends the
    headers as the body (blank lines and lines starting `HTTP/` included).

    Raises `NotAnHttpResponse` when `stdout` is empty, does not start with `HTTP/`, has a
    status line other than `HTTP/1.1`, `HTTP/2` or `HTTP/2.0` plus three digits, or has a
    header line with no colon."""
    text = stdout.replace("\r\n", "\n")
    if not text.startswith("HTTP/"):
        raise NotAnHttpResponse("not an HTTP response: stdout does not start with HTTP/")
    pos = 0
    while True:
        end = text.find("\n", pos)
        status = _read_status(text[pos:] if end < 0 else text[pos:end])
        if end < 0:
            return HttpResponse(status, (), "")
        headers, body_at = _read_headers(text, end + 1)
        if body_at is None:
            return HttpResponse(status, headers, "")
        if 100 <= status < 200 and text.startswith("HTTP/", body_at):
            pos = body_at
            continue
        return HttpResponse(status, headers, text[body_at:])


def _seconds(value: str | None) -> float | None:
    """A header value that is a plain decimal integer, as a number; `None` for anything else
    (an HTTP-date, a sign, a fraction, a non-ASCII digit, a comma-joined repeat)."""
    if value is None or not (value.isascii() and value.isdigit()):
        return None
    try:
        return float(int(value))
    except (ValueError, OverflowError):
        return None


def wait_from(response: HttpResponse, now: float, backoff: float) -> float:
    """Seconds to wait before asking again: a readable `retry-after`; else the time from `now`
    to a readable `x-ratelimit-reset` (epoch seconds) strictly later than `now`; else
    `backoff`. A value that cannot be read falls to the next source. Never capped."""
    retry_after = _seconds(response.header("retry-after"))
    if retry_after is not None:
        return retry_after
    reset = _seconds(response.header("x-ratelimit-reset"))
    if reset is not None and reset > now:
        return reset - now
    return backoff


@dataclass(frozen=True)
class GithubAccess:
    """How a process reaches GitHub. The three fields are opaque placeholders: later parts
    type them as the ports of the gate, the cache and the budget."""

    gate: object | None = None
    cache: object | None = None
    budget: object | None = None
