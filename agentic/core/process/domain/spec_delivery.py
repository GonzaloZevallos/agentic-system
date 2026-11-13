"""What Process needs of a Spec (GH-594), without importing Knowledge: the contract a spec
delivery carries, the front matter and body-hash rules, the flags derived from the screens and
the canonical render of the front matter.

Pure. Reading the YAML, the section grammar and `validate` live in
`agentic.core.knowledge.domain.spec`; the format itself is documented in its docstring.

Body hash rule (invariant 1: it is computed only here, in `body_sha256`): the sha256, as
lowercase hex, of the UTF-8 bytes of the body (everything after the closing `---` line of the
front matter) with line endings normalised to `\\n` and one leading U+FEFF stripped from the
text. Nothing else is trimmed: a trailing space or the final newline changes the hash, so
an editor that adds or drops the last newline invalidates an approval. The front matter is not
part of the hash, so writing `status: approved` does not move it.

Front matter text is produced only by `render_draft_front_matter` and
`render_approved_front_matter` (invariant 3).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Literal, TypedDict

SPEC_FENCE = "=== spec data ==="
SPEC_MAX_BYTES = 65536

SpecCause = Literal["spec_invalid", "spec_unapproved", "slice_unknown", "slice_missing"]
SpecReadRefusal = Literal["path", "commit", "missing", "not_blob", "oversize", "encoding", "git"]
SpecReadCause = Literal["spec_unreadable"]

# The forms a spec read accepts (GH-594): declared only here, always matched with `fullmatch`.
SPEC_PATH = re.compile(r"docs/specs/[A-Za-z0-9][A-Za-z0-9._-]*\.md")
SPEC_PATH_MAX_CHARS = 255
SPEC_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

_LOGIN = re.compile(r"[A-Za-z0-9]+(-[A-Za-z0-9]+)*")
_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")
SHA256 = re.compile(r"[0-9a-f]{64}")


class Screen(TypedDict):
    name: str
    copy: list[str]


class SpecContract(TypedDict):
    path: str
    slice: str
    commit: str
    sha256: str
    criteria: list[str]
    screens: list[Screen]
    risks: list[str]
    touches_ui: bool
    touches_copy: bool


def spec_path_ok(path: str) -> bool:
    """True only for a spec path in its one form (`SPEC_PATH`) of at most 255 characters."""
    return len(path) <= SPEC_PATH_MAX_CHARS and SPEC_PATH.fullmatch(path) is not None


def spec_commit_ok(commit: str) -> bool:
    """True only for a full commit id: 40 or 64 lowercase hex characters."""
    return SPEC_COMMIT.fullmatch(commit) is not None


def split_front_matter(text: str) -> tuple[str, str] | None:
    """(front matter text, body) of a spec, or `None` without an opening and a closing `---`
    line. One leading U+FEFF is stripped and line endings become `\\n` before looking."""
    lines = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None


def body_sha256(text: str) -> str | None:
    """Lowercase hex sha256 of the UTF-8 body of `text` (see the module docstring for the
    rule: line endings as `\\n`, no trimming, the final newline counts); `None` without front
    matter. Raises `UnicodeError` on a body that cannot be encoded."""
    split = split_front_matter(text)
    return None if split is None else hashlib.sha256(split[1].encode("utf-8")).hexdigest()


def derive_flags(screens: Sequence[Screen]) -> tuple[bool, bool]:
    """(touches_ui, touches_copy): any screen at all; any screen with a non-blank copy string."""
    return bool(screens), any(c.strip() for s in screens for c in s["copy"])


def render_draft_front_matter() -> str:
    return "---\nstatus: draft\n---\n"


def render_approved_front_matter(approved_by: str, approved_at: str, sha256: str) -> str:
    """The front matter the system writes on approval; `ValueError` unless `approved_by` is a
    GitHub login, `approved_at` an ISO-8601 instant ending in `Z` and `sha256` 64 lowercase hex."""
    if not _LOGIN.fullmatch(approved_by):
        raise ValueError(f"approved_by is not a GitHub login: {approved_by!r}")
    if not _AT.fullmatch(approved_at):
        raise ValueError(f"approved_at is not ISO-8601 UTC ending in Z: {approved_at!r}")
    if not SHA256.fullmatch(sha256):
        raise ValueError(f"sha256 is not 64 lowercase hex: {sha256!r}")
    return (
        f"---\nstatus: approved\napproved_by: {approved_by}\n"
        f"approved_at: {approved_at}\napproved_sha256: {sha256}\n---\n"
    )
