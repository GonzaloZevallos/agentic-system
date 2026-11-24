"""What `gh api -i` printed, read as an HTTP response, how long a refusal says to wait
(GH-1090), and the rules of the write turn (GH-1083): pure rules, the one place that parses
that text and the one place that says which `gh` calls write.

`split_response` turns the stdout of `gh api -i` into an `HttpResponse`; `wait_from` reads the
`retry-after` and `x-ratelimit-reset` headers of one. No other module parses either. Nothing
here reads a clock, the environment, a file or `gh`'s stderr: `now` and `backoff` arrive as
parameters, and capping a wait belongs to whoever sleeps, so the cap happens once.

`is_write` says whether a `gh` argument list may change GitHub, by verb, HTTP method and query
document only; anything it cannot read counts as a write. `last_slot_of` reads the slot file's
text and `write_slot` computes the next slot, with `now`, `horizon`, `spacing` and `cap` as
parameters. `Turn` is what a `WriteGate` answers when a write asks for its turn.

`GithubAccess` is the access object later parts fill in: its `gate` is a `WriteGate`; `cache`
and `budget` are still placeholders.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

__all__ = [
    "GithubAccess",
    "HttpResponse",
    "NotAnHttpResponse",
    "Turn",
    "WriteGate",
    "is_write",
    "last_slot_of",
    "split_response",
    "wait_from",
    "write_slot",
]

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


# The verbs that only read. Every other verb, and every form not listed, is a write: that
# includes all of `label` (even `label list`), `run rerun` and `workflow run`.
_READ_SUBCOMMANDS = frozenset({"view", "list", "checks", "diff", "status"})
_READ_GROUPS = frozenset({"pr", "issue", "run", "repo", "auth"})

# `gh api` flags that take a value (the next token, or joined: `--flag=value`, `-Xvalue`).
_API_VALUE_FLAGS = frozenset(
    {"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"}
    | {
        "-H",
        "--header",
        "-q",
        "--jq",
        "-t",
        "--template",
        "--hostname",
        "--cache",
        "-p",
        "--preview",
    }
)
_API_FIELD_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field"})


@dataclass(frozen=True)
class _ApiCall:
    method: str | None
    fields: tuple[str, ...]
    has_input: bool


def _read_api(args: Sequence[str]) -> _ApiCall:
    """The method, the `key=value` fields and whether `--input` is given, in the arguments that
    follow `api`."""
    method: str | None = None
    fields: list[str] = []
    has_input = False
    pos = 0
    while pos < len(args):
        token = args[pos]
        pos += 1
        flag, value = token, None
        if token.startswith("--"):
            flag, equals, joined = token.partition("=")
            value = joined if equals else None
        elif token.startswith("-") and len(token) > 2 and token[:2] in _API_VALUE_FLAGS:
            flag, value = token[:2], token[2:]
        if flag not in _API_VALUE_FLAGS:
            continue
        if value is None:
            value = args[pos] if pos < len(args) else ""
            pos += 1
        if flag in {"-X", "--method"}:
            method = value
        elif flag == "--input":
            has_input = True
        elif flag in _API_FIELD_FLAGS:
            fields.append(value)
    return _ApiCall(method, tuple(fields), has_input)


def _document_mutates(document: str) -> bool:
    """Whether a GraphQL document, after whitespace and `#` comment lines, starts with
    `mutation`; a document with nothing visible, or a `@file`, cannot be read: a write."""
    rest = document.lstrip()
    while rest.startswith("#"):
        _, _, rest = rest.partition("\n")
        rest = rest.lstrip()
    return rest == "" or rest.startswith("mutation") or document.startswith("@")


def _graphql_is_write(call: _ApiCall) -> bool:
    if call.has_input:
        return True
    queries = [field[len("query=") :] for field in call.fields if field.startswith("query=")]
    return not queries or any(_document_mutates(query) for query in queries)


def is_write(args: Sequence[str]) -> bool:
    """Whether `gh <args>` may change GitHub. Decided by verb, HTTP method and query document
    alone; whatever it cannot read (`--input`, `query=@file`, an unknown verb or form) is a
    write.

    `api graphql` is a read unless its `query=` value starts with `mutation`. Any other `api`
    call is a read with `-X GET` (any case), or with no method and no `-f`, `-F`, `--field`,
    `--raw-field` or `--input`, as `gh` itself does. `pr`, `issue`, `run`, `repo` and `auth`
    read with view, list, checks, diff and status; everything else writes."""
    if not args:
        return True
    if args[0] == "api":
        call = _read_api(args[1:])
        paths = [token for token in args[1:] if not token.startswith("-")]
        if "graphql" in paths[:1]:
            return _graphql_is_write(call)
        if call.method is not None:
            return call.method.upper() != "GET"
        return bool(call.fields) or call.has_input
    return not (args[0] in _READ_GROUPS and len(args) > 1 and args[1] in _READ_SUBCOMMANDS)


def last_slot_of(text: str, now: float, horizon: float) -> float:
    """The slot the file holds, or 0.0 when `text` is empty, torn or not a float, when the value
    is non-finite or negative, or when it is above `now + horizon` (a clock stepped back).
    Exactly `now + horizon` is accepted."""
    try:
        value = float(text.strip())
    except ValueError:
        return 0.0
    if not math.isfinite(value) or value < 0 or value > now + horizon:
        return 0.0
    return value


def write_slot(last_slot: float, now: float, spacing: float, cap: float) -> float | None:
    """The slot of the next write, `max(last_slot + spacing, now)`; `None` (refused) when that
    is more than `cap` seconds after `now`. Computes the slot and writes nothing.

    Raises `ValueError` when any input is non-finite, or when `spacing` or `cap` is negative:
    a time that cannot be read never falls through to a granted turn."""
    if not all(math.isfinite(value) for value in (last_slot, now, spacing, cap)):
        raise ValueError("write_slot takes finite numbers")
    if spacing < 0 or cap < 0:
        raise ValueError("write_slot takes a spacing and a cap that are not negative")
    slot = max(last_slot + spacing, now)
    return None if slot - now > cap else slot


@dataclass(frozen=True)
class Turn:
    """What a write got when it asked for its turn: `granted`, `refused` (the wait was over the
    cap) or `stopped`, and how long it waited."""

    outcome: Literal["granted", "refused", "stopped"]
    waited_s: float


class WriteGate(Protocol):
    """The port that gives writes their turns: at most `cap_s` seconds of wait, of which
    `remaining_s` is what the caller can still spend."""

    @property
    def cap_s(self) -> float: ...

    def take(self, remaining_s: float) -> Turn: ...


@dataclass(frozen=True)
class GithubAccess:
    """How a process reaches GitHub. `gate` is the write-turn port; `cache` and `budget` are
    opaque placeholders that later parts type as their ports."""

    gate: WriteGate | None = None
    cache: object | None = None
    budget: object | None = None
