"""The push policy of the CI path (GH-487): how often a push of an `agentic-ci/` ref is tried,
and how git's own words are made safe to write down. Pure: no I/O, no clock.

A push that fails is read through what git said on stderr. A refusal git states as permanent
(a missing scope, a protected ref, a hook, a non-fast-forward) is tried once. A network
failure is tried `PUSH_ATTEMPTS` times, with a growing wait. A text no list knows is tried
`PUSH_UNKNOWN_ATTEMPTS` times: it may be a blip, but nobody read it as one.

The kind and the text are prose for the owner, in a document and a log. They are never a
span attribute, a label or a metric (ADR-0008), and the closed cause that escalates stays
`publish_failed` (ADR-0029).
"""

from __future__ import annotations

import re
from typing import Literal

PushKind = Literal["transient", "permanent", "unknown"]

# How many times one git step is tried when git's error reads as transient.
PUSH_ATTEMPTS = 3
# How many times one git step is tried when git's error is in neither list.
PUSH_UNKNOWN_ATTEMPTS = 2
# The wait, in seconds, before the 2nd and before the 3rd attempt. Declared, not measured:
# long enough for a DNS or TLS blip to pass, short enough for `agentic stop` to unwind.
PUSH_BACKOFF_S: tuple[float, ...] = (2.0, 8.0)
# The most characters of git's error that are written into a document.
GIT_SAID_LIMIT = 400

_PERMANENT = re.compile(
    r"permission denied"
    r"|permission to \S+ denied"
    r"|denied to workflow"
    r"|workflow\W+scope"
    r"|protected branch"
    r"|hook declined"
    r"|pre-receive"
    r"|\[rejected\]"
    r"|non-fast-forward"
    r"|authentication failed"
    r"|returned error: 40[34]"
)
_TRANSIENT = re.compile(
    r"could not resolve host"
    r"|connection timed out"
    r"|connection reset"
    r"|connection refused"
    r"|failed to connect"
    r"|operation timed out"
    r"|early eof"
    r"|the remote end hung up unexpectedly"
    r"|rpc failed"
    r"|returned error: 5\d\d"
    r"|gnutls|\btls\b|\bssl|openssl"
)

_URL_USERINFO = re.compile(r"(?<=//)[^/\s@]+@")
_AUTHORIZATION = re.compile(
    r"(authorization\s*[:=]\s*(?:(?:basic|bearer|token)\s+)?)\S+", re.IGNORECASE
)
_BARE_SCHEME = re.compile(r"\b(basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_TOKEN = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]*")
_SPACES = re.compile(r"\s+")
_MASK = "***"


def classify(stderr: str) -> PushKind:
    """What git's `stderr` says about the push. Permanent wins over transient: a refusal
    that also mentions a hang-up is still a refusal. An empty or unfamiliar text is unknown."""
    said = stderr.lower()
    if _PERMANENT.search(said):
        return "permanent"
    if _TRANSIENT.search(said):
        return "transient"
    return "unknown"


def attempts_for(kind: PushKind) -> int:
    """How many times a push whose failure reads as `kind` is tried in all."""
    if kind == "permanent":
        return 1
    if kind == "transient":
        return PUSH_ATTEMPTS
    return PUSH_UNKNOWN_ATTEMPTS


def scrub(text: str, limit: int = GIT_SAID_LIMIT) -> str:
    """`text` fit to be written down: credentials masked, backticks removed, one line, at
    most `limit` characters ending in `…` when cut. Words and case are not changed. Git's
    text is third-party: it never goes into a document or a log any other way, and it is
    never an argument or a template of `str.format`."""
    masked = _URL_USERINFO.sub(f"{_MASK}@", text)
    masked = _AUTHORIZATION.sub(rf"\1{_MASK}", masked)
    masked = _BARE_SCHEME.sub(rf"\1 {_MASK}", masked)
    masked = _TOKEN.sub(_MASK, masked)
    line = _SPACES.sub(" ", masked.replace("`", "")).strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1] + "…"
