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
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

SPEC_FENCE = "=== spec data ==="
SPEC_MAX_BYTES = 65536

SpecCause = Literal["spec_invalid", "spec_unapproved", "slice_unknown", "slice_missing"]
# What the system did with the draft Spec PM returned (GH-594 part 5a): closed.
SpecDraftOutcome = Literal["written", "invalid", "unvalidated", "absent"]
# Why a draft Spec body is refused (GH-594 part 5c-1), by stage: the body alone, the draft read by
# the validator, and the approved preview of it. Closed; `DRAFT_REFUSAL_REASONS` has one sentence each.
DraftCause = Literal[
    "draft_empty",
    "draft_front_matter",
    "draft_unhashable",
    "draft_invalid",
    "draft_not_draft",
    "preview_slice_missing",
    "preview_invalid",
    "preview_unapproved",
]
SpecReadRefusal = Literal["path", "commit", "missing", "not_blob", "oversize", "encoding", "git"]
SpecReadCause = Literal["spec_unreadable"]
# Every cause a spec refusal can escalate with (GH-594): the validator's four, the read's one and
# the intake's three. Closed; `spec_route.refusal_escalation` refuses any other.
SpecRouteCause = Literal[
    "spec_invalid",
    "spec_unapproved",
    "slice_unknown",
    "slice_missing",
    "spec_unreadable",
    "spec_line_malformed",
    "spec_type",
    "spec_changed",
]

# The forms a spec read accepts (GH-594): declared only here, always matched with `fullmatch`.
SPEC_PATH = re.compile(r"docs/specs/[A-Za-z0-9][A-Za-z0-9._-]*\.md")
SPEC_PATH_MAX_CHARS = 255
SPEC_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

SPEC_DIR = "docs/specs/"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
DRAFT_SLUG_MAX = 60
DRAFT_SLUG_FALLBACK = "spec"

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


def honoured(reader: object | None, validator: object | None) -> bool:
    """True only when a spec can be read and validated: both ports are present. With either
    missing, a `Spec:` line in an issue is not looked at, as before it existed."""
    return reader is not None and validator is not None


def body_source(state: Mapping[str, Any]) -> Literal["description", "inbox"]:
    """Where `spec_intake` reads the `Spec:` line from (GH-809): `"inbox"` only on a resume
    (the `pause_log` holds a `resumed` entry) of a task whose `description` had a candidate
    line, parsed or malformed, because that is the line an owner fixes in the issue; every other
    case parses `description`. `pause_log` only grows, so the resume is never forgotten."""
    resumed = any(entry.get("kind") == "resumed" for entry in state.get("pause_log", []))
    candidate = parse_spec_line(state.get("description", "")) is not None
    return "inbox" if resumed and candidate else "description"


def recorded(state: Mapping[str, Any]) -> bool:
    """Whether the task document carries the `spec_delivery` record: a spec is in the state, or
    the record was already written (it is sticky). A measurement record read only by
    `eval compare`; never read by a decision (`rules.spec_delivery` reads `state["spec"]`)."""
    return bool(state.get("spec")) or state.get("spec_delivery") is True


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


def owner_matches(author: str, owner_login: str | None) -> bool:
    """True only when `author` is the owner: the one login comparison for specs (invariant 1),
    used by the approval reader and by the validator. A missing, empty or blank `owner_login`
    matches nobody; otherwise the logins are compared with `casefold`."""
    if not owner_login or not owner_login.strip():
        return False
    return author.casefold() == owner_login.casefold()


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


class SpecRefusalRecord(TypedDict):
    """The `spec_refusal` key of a task: why a spec was refused and what it names, never text
    (see `spec_route.refusal_escalation` for what `detail` may hold)."""

    cause: SpecRouteCause
    detail: list[str]


class _SpecDraftOutcome(TypedDict):
    outcome: SpecDraftOutcome


class SpecDraftRecord(_SpecDraftOutcome, total=False):
    """The `spec_draft` key of a task (GH-594 part 5a): what became of the draft Spec PM
    returned. `path`, `sha256` (the body hash) and `commit` are there only for `written`; `commit`
    is a full sha at which `path` holds exactly the text whose body hash is `sha256` (a record
    from before it existed has none). Distinct from `state["spec"]`: a draft is never a spec
    delivery's contract."""

    path: str
    sha256: str
    commit: str


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


def protected(state: Mapping[str, Any], path: str) -> bool:
    """True only under a spec delivery (`state["spec"]` non-empty) and only for the delivery's
    own spec path: the one file no unit of the task may change unseen. `rules.forbidden_paths`
    and `UNSTAGEABLE` do not know it, on purpose: outside a delivery it is an ordinary path."""
    spec = state.get("spec")
    return spec is not None and path == spec.get("path")


def spec_intact(recorded_sha256: str, recorded_text: str | None, head_text: str | None) -> bool:
    """True only when the spec at a head is what the delivery recorded: both texts were read,
    they are equal as written (a front-matter-only edit counts) and the body hash still holds.
    The one place spec texts are compared (a deleted or unreadable spec is `None`)."""
    return (
        head_text is not None
        and recorded_text is not None
        and head_text == recorded_text
        and spec_unchanged(recorded_sha256, head_text)
    )


def spec_carry_ok(holds: bool, intact: bool) -> bool:
    """Whether a unit may be carried over a moved head: its review still holds and the spec is
    intact at the new head."""
    return holds and intact


@dataclass(frozen=True)
class DraftReading:
    """What a draft Spec reading came to: `ok`, or refused with a `DraftCause`."""

    ok: bool
    cause: DraftCause | None = None


def draft_body_refusal(body: object) -> DraftCause | None:
    """Why PM's draft body is refused before any validator sees it, or `None`: not a string or
    blank, a body that opens its own front matter, or one whose hash cannot be computed."""
    if not isinstance(body, str) or not body.strip():
        return "draft_empty"
    if split_front_matter(body) is not None:
        return "draft_front_matter"
    try:
        sha256 = body_sha256(draft_text(body))
    except UnicodeError:
        return "draft_unhashable"
    return "draft_unhashable" if sha256 is None else None


def draft_reading(
    draft_result: SpecContract | SpecCause, preview_result: SpecContract | SpecCause | None
) -> DraftReading:
    """The one reading of the validator on a draft Spec (the only place its answer counts as a
    draft): the draft must be `spec_unapproved`; then the approved preview, when given, must be a
    contract read with `slice_id=None`, so a draft with more than one slice is `slice_missing`.
    The draft is judged before the preview. A validator verdict never depends on the path."""
    if draft_result == "spec_invalid":
        return DraftReading(False, "draft_invalid")
    if draft_result != "spec_unapproved":
        return DraftReading(False, "draft_not_draft")
    if preview_result is None or not isinstance(preview_result, str):
        return DraftReading(True)
    if preview_result == "slice_missing":
        return DraftReading(False, "preview_slice_missing")
    if preview_result == "spec_unapproved":
        return DraftReading(False, "preview_unapproved")
    return DraftReading(False, "preview_invalid")


def approved_contract(result: SpecContract | SpecCause) -> SpecContract | SpecCause:
    """The contract of an approved spec, or the cause the validator refused it with."""
    return result


DRAFT_PREVIEW_PATH = f"{SPEC_DIR}draft.md"

# What PM is told when its draft is refused, one fixed sentence per cause: never validator detail,
# a path or a section list (those belong to Knowledge; the draft prompt block names them).
DRAFT_REFUSAL_REASONS: dict[DraftCause, str] = {
    "draft_empty": "the draft Spec body is empty; return the body as text, without front matter.",
    "draft_front_matter": "the draft Spec body must not open its own front matter; the system adds it.",
    "draft_unhashable": "the draft Spec body cannot be encoded as text; return plain UTF-8 text.",
    "draft_invalid": (
        "the draft Spec body lacks the structure required by the draft Spec section of your "
        "prompt (pm_draft); return every section that block names, in order, with no front matter."
    ),
    "draft_not_draft": "the draft Spec body must read as an unapproved draft; return only the body.",
    "preview_slice_missing": "the draft Spec must have exactly one slice.",
    "preview_invalid": "the draft Spec body must still validate once it is approved.",
    "preview_unapproved": "the draft Spec could not be previewed as approved; return the body again.",
}


def draft_refusal_message(cause: DraftCause) -> str:
    return f"draft Spec refused: {cause}: {DRAFT_REFUSAL_REASONS[cause]}"


def login_ok(login: str | None) -> bool:
    """True only for a non-empty string in the form of a GitHub login."""
    return isinstance(login, str) and _LOGIN.fullmatch(login) is not None


def approval_instant(now: str) -> str:
    """The clock's instant in the form approval writes: the clock (`Ports.now`) ends in `+00:00`,
    the approved front matter in `Z`. Anything else is returned as it is, for `instant_ok` to judge."""
    return f"{now.removesuffix('+00:00')}Z" if now.endswith("+00:00") else now


def instant_ok(at: str) -> bool:
    """True only for an ISO-8601 UTC instant ending in `Z`, the form approval writes."""
    return isinstance(at, str) and _AT.fullmatch(at) is not None


def _slug(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = _NOT_SLUG.sub("-", ascii_title.lower()).strip("-")[:DRAFT_SLUG_MAX].strip("-")
    return slug or DRAFT_SLUG_FALLBACK


def draft_path(date: str, title: str, task_id: str, taken: Collection[str]) -> str:
    """`docs/specs/<yyyy-mm-dd>-<slug>.md` for a draft Spec: the slug is the issue title in ASCII,
    lowercase, hyphen-separated, at most 60 characters (`spec` when nothing is left). A path in
    `taken` gets `-<task id>` appended (also slugged), so two tasks never share a file. `ValueError`
    unless `date` is `yyyy-mm-dd`; the result always satisfies `spec_path_ok`."""
    if not _DAY.fullmatch(date):
        raise ValueError(f"date is not yyyy-mm-dd: {date!r}")
    path = f"{SPEC_DIR}{date}-{_slug(title)}.md"
    if path in taken:
        path = f"{SPEC_DIR}{date}-{_slug(title)}-{_slug(task_id)}.md"
    return path


def draft_text(body: str) -> str:
    """The file text of a draft Spec: the draft front matter, then PM's body. The only place
    a draft's text is put together, so PM's text never carries the front matter."""
    return render_draft_front_matter() + body
