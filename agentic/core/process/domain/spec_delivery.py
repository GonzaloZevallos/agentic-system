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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

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
# A slice id, and a risk id's shape: declared only here, matched with `fullmatch`.
SLICE_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_SLICE = re.compile(SLICE_SLUG)


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


def normalize_text(text: str) -> str:
    """The one line-ending normalisation of spec text: one leading U+FEFF stripped, then `\\r\\n`
    and `\\r` become `\\n`. Other separators (U+2028, `\\x0b`, `\\x85`) are left alone."""
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def split_front_matter(text: str) -> tuple[str, str] | None:
    """(front matter text, body) of a spec, or `None` without an opening and a closing `---`
    line. One leading U+FEFF is stripped and line endings become `\\n` before looking."""
    lines = normalize_text(text).split("\n")
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


@dataclass(frozen=True)
class SpecRefusal:
    """A typed refusal: `cause` and `detail`, the failing field names or risk ids (never text)."""

    cause: SpecCause
    detail: tuple[str, ...]


@dataclass(frozen=True)
class SpecLine:
    path: str
    slice: str | None


@dataclass(frozen=True)
class SpecLineMalformed:
    pass


class SpecStatePatch(TypedDict):
    """What a spec delivery writes to the state: the contract (`state["spec"]`; its being
    non-empty is the whole signal, see `rules.spec_delivery`) and the flags that
    `design_applies` and `copy_applies` read (`state["flags"]`)."""

    spec: SpecContract
    flags: dict[str, bool]


def parse_spec_line(body: str) -> SpecLine | SpecLineMalformed | None:
    """The `Spec: <path>` or `Spec: <path>#<slice>` line of an issue body: `None` without a
    line that starts at column 0 with `Spec:`, malformed with two or more of them (even equal
    ones) or one that is not exactly one of the two forms. Fences and comments are not looked
    at; only `\\n` splits lines."""
    candidates = [line for line in normalize_text(body).split("\n") if line.startswith("Spec:")]
    if not candidates:
        return None
    if len(candidates) > 1 or not candidates[0].startswith("Spec: "):
        return SpecLineMalformed()
    path, hash_, slice_id = candidates[0].removeprefix("Spec: ").partition("#")
    if not spec_path_ok(path) or (hash_ and not _SLICE.fullmatch(slice_id)):
        return SpecLineMalformed()
    return SpecLine(path, slice_id if hash_ else None)


_TEXT_FIELDS: dict[str, re.Pattern[str]] = {
    "path": SPEC_PATH,
    "slice": _SLICE,
    "commit": SPEC_COMMIT,
    "sha256": SHA256,
}


def _contract_invalid_fields(contract: Mapping[str, Any]) -> tuple[str, ...]:
    """The names of the `SpecContract` fields that are missing or have the wrong form."""
    bad: list[str] = []
    for name in SpecContract.__annotations__:
        if name not in contract:
            bad.append(name)
            continue
        value = contract[name]
        if name == "path":
            ok = isinstance(value, str) and spec_path_ok(value)
        elif name in _TEXT_FIELDS:
            ok = isinstance(value, str) and _TEXT_FIELDS[name].fullmatch(value) is not None
        elif name in ("criteria", "risks"):
            ok = isinstance(value, list) and all(isinstance(x, str) for x in value)
        elif name == "screens":
            ok = isinstance(value, list) and all(_screen_ok(x) for x in value)
        else:
            ok = isinstance(value, bool)
        if not ok:
            bad.append(name)
    return tuple(bad)


def _screen_ok(screen: object) -> bool:
    return (
        isinstance(screen, dict)
        and isinstance(screen.get("name"), str)
        and isinstance(screen.get("copy"), list)
        and all(isinstance(c, str) for c in screen["copy"])
    )


def spec_state_patch(contract: SpecContract) -> SpecStatePatch | SpecRefusal:
    """The state patch of a spec delivery, or `SpecRefusal("spec_invalid", fields)` naming the
    fields of `contract` that are missing or malformed. The flags come only from
    `derive_flags` over the screens, and overwrite whatever the contract carried."""
    bad = _contract_invalid_fields(contract)
    if bad:
        return SpecRefusal("spec_invalid", bad)
    ui, copy = derive_flags(contract["screens"])
    return SpecStatePatch(
        spec={**contract, "touches_ui": ui, "touches_copy": copy},
        flags={"touches_ui": ui, "touches_copy": copy},
    )


def spec_unchanged(recorded_sha256: str, text: str) -> bool:
    """True only when the body hash of `text` equals `recorded_sha256`; `False` without front
    matter, on a body that cannot be encoded, or on a mismatch. The caller reads `text`."""
    try:
        return body_sha256(text) == recorded_sha256
    except UnicodeError:
        return False


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
