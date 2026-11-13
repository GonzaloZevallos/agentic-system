"""The Spec format (GH-594): parse a spec file's text and validate it into a `SpecContract`
(which lives in Process, `agentic.core.process.domain.spec_delivery`) or a closed cause.

Whether a spec is valid, approved and names a known slice is decided only by `validate`
(invariant 1); the body hash is `spec_delivery.body_sha256`. Pure: text in, result out, it
never raises on any text (invariant 4). Only the text is read; nothing here knows where it
came from.

Causes, in this order, each stage cutting before the next:
1. structure -> `spec_invalid`; 2. approval -> `spec_unapproved`;
3. slice -> `slice_missing` (several slices, none named), then `slice_unknown`.

Rejected outright: more than `SPEC_MAX_BYTES` bytes, text that cannot be encoded, `{{`, `<!--`,
a line equal to `SPEC_FENCE` once stripped, and a fence left open.

Front matter: a first line `---`, a closing `---` line, and between them (at most 1024
chars) a flat YAML mapping of
scalars with the keys `status` (`draft` or `approved`), `approved_by`, `approved_at` and
`approved_sha256`: no alias or anchor, no duplicate or other key, a `draft` with no `approved_*`
value. Values are read as raw text (an unquoted date stays text). `approved` holds only when
`approved_by` is `owner_login` (casefold; a missing login approves nothing), `approved_sha256`
is 64 lowercase hex equal to the body hash, and `approved_at` is an ISO-8601 UTC instant.

Body: an optional `# Title`, then `##` sections from `SECTIONS`, in any order, each once and
not empty (names compared with casefold and strip). Headings are `##` to `####` lines outside a
CommonMark fence (backticks or tildes, at most 3 spaces of indent); any other `#` line, a setext
heading, and text before the first `##` are rejected, and so is a heading inside a section that
has no subsections. Bullets are `- text` lines.
- `Acceptance criteria`: at least one bullet.
- `Decisions`: only top-level bullets, each with exactly one indented sub-bullet `- Reason: <why>`.
- `Screens`: any text (say "none" when there are no screens), then one `### <name>` per
  screen: a first line `Slices: all` or `Slices: <id>, <id>` naming known slices, then one
  bullet per copy string (none is fine). Screen names are unique (casefold).
- `Slices`: one `### <slug>` per slice (ids unique), each with `#### Acceptance criteria`
  (at least one bullet) and `#### Risks` (slug bullets, unique, or a single `- none`).
A slice is chosen with `slice_id`: `None` is the only slice; an exact slice id otherwise.
Risk ids are checked for shape only; Process checks them against the risk menu.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import yaml

from agentic.core.process.domain.spec_delivery import (
    SHA256,
    SPEC_FENCE,
    SPEC_MAX_BYTES,
    Screen,
    SpecCause,
    SpecContract,
    body_sha256,
    derive_flags,
    split_front_matter,
)

SECTIONS = (
    "Goal",
    "Acceptance criteria",
    "Out of scope",
    "Decisions",
    "Screens",
    "Risks",
    "Slices",
)
_FRONT_MAX = 1024  # a real front matter is ~200 chars; PyYAML is superlinear on deep nesting
_APPROVAL_KEYS = ("approved_by", "approved_at", "approved_sha256")
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_HEADING = re.compile(r"(#{2,4}) +(.+?)(?: +#+)?")
_TITLE = re.compile(r"# +\S.*")
_HASHES = re.compile(r"#{1,6}( |$)")
_BULLET = re.compile(r"- +(\S.*)")
_SUB_BULLET = re.compile(r"[ \t]+- +(\S.*)")
_SCREEN_SLICES = re.compile(rf"Slices: (all|{_SLUG}(?:, {_SLUG})*)")

Token = tuple[int, str]  # (heading level, or 0 for a plain line; the title or the line)
Parsed = tuple[
    dict[str, str], dict[str, tuple[list[str], list[str]]], list[tuple[Screen, set[str]]]
]


def validate(
    text: str, path: str, commit: str, slice_id: str | None, owner_login: str | None
) -> SpecContract | SpecCause:
    """The contract of the slice `slice_id` of the spec in `text`, or the cause that stops it."""
    parsed = _structure(text)
    if parsed is None:
        return "spec_invalid"
    front, slices, screens = parsed
    if not _approved(text, front, owner_login):
        return "spec_unapproved"
    if slice_id is None:
        if len(slices) > 1:
            return "slice_missing"
        slice_id = next(iter(slices))
    elif slice_id not in slices:
        return "slice_unknown"
    criteria, risks = slices[slice_id]
    mine = [s for s, ids in screens if not ids or slice_id in ids]
    ui, copy = derive_flags(mine)
    return SpecContract(
        path=path,
        slice=slice_id,
        commit=commit,
        sha256=front["approved_sha256"],
        criteria=criteria,
        screens=mine,
        risks=risks,
        touches_ui=ui,
        touches_copy=copy,
    )


def _approved(text: str, front: dict[str, str], owner_login: str | None) -> bool:
    if front["status"] != "approved" or not owner_login or not owner_login.strip():
        return False
    if front["approved_by"].casefold() != owner_login.casefold():
        return False
    if not SHA256.fullmatch(front["approved_sha256"]):
        return False
    if front["approved_sha256"] != body_sha256(text):
        return False
    at = front["approved_at"]
    try:
        return datetime.fromisoformat(
            at[:-1] + "+00:00" if at.endswith("Z") else at
        ).utcoffset() == timedelta(0)
    except ValueError:
        return False


def _structure(text: str) -> Parsed | None:
    """Stage 1: the front matter (as a dict with every key) and the slices and screens of the
    body, or `None` when anything is off."""
    try:
        if len(text.encode("utf-8")) > SPEC_MAX_BYTES:
            return None
    except UnicodeError:
        return None
    if "{{" in text or "<!--" in text or any(x.strip() == SPEC_FENCE for x in text.splitlines()):
        return None
    split = split_front_matter(text)
    front = _front(split[0]) if split else None
    tokens = _tokens(split[1]) if split else None
    if front is None or tokens is None:
        return None
    head, parts = _split(tokens, 2)
    if (
        any(lv > 1 or (lv == 0 and t.strip()) for lv, t in head)
        or sum(lv == 1 for lv, _ in head) > 1
    ):
        return None
    sections = {t.casefold().strip(): toks for t, toks in parts}
    if len(sections) != len(parts) or set(sections) != {s.casefold() for s in SECTIONS}:
        return None
    for name in ("goal", "out of scope", "risks"):
        if not any(x.strip() for x in _plain(sections[name]) or []):
            return None
    if not any(_BULLET.fullmatch(x) for x in _plain(sections["acceptance criteria"]) or []):
        return None
    if not _decisions(_plain(sections["decisions"])):
        return None
    slices = _slices(sections["slices"])
    screens = _screens(sections["screens"], slices)
    return None if slices is None or screens is None else (front, slices, screens)


def _front(front_text: str) -> dict[str, str] | None:
    """The front matter as text values. Read as parser events, never composed or loaded: that
    cannot recurse on a deep nesting, and an anchor or alias is visible on its event."""
    if len(front_text) > _FRONT_MAX:
        return None
    try:
        events = list(yaml.parse(front_text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        return None
    kinds = [type(e).__name__.removesuffix("Event") for e in events]
    shape = ["StreamStart", "DocumentStart", "MappingStart"]
    tail = ["MappingEnd", "DocumentEnd", "StreamEnd"]
    middle = kinds[3:-3]
    if kinds[:3] != shape or kinds[-3:] != tail or len(middle) % 2 or set(middle) - {"Scalar"}:
        return None
    if any(getattr(e, "anchor", None) is not None for e in events):
        return None
    values = [e.value for e in events[3:-3]]
    keys = values[0::2]
    if len(set(keys)) != len(keys) or set(keys) - {"status", *_APPROVAL_KEYS}:
        return None
    front = {"status": "", **dict.fromkeys(_APPROVAL_KEYS, ""), **dict(zip(keys, values[1::2]))}
    if front["status"] not in ("draft", "approved"):
        return None
    if front["status"] == "draft" and any(front[k].strip() for k in _APPROVAL_KEYS):
        return None
    return front


def _tokens(body: str) -> list[Token] | None:
    """The body lines with their heading level; `None` on a stray `#` line or an open fence."""
    out: list[Token] = []
    fence = ""
    for raw in body.split("\n"):
        line = raw.rstrip()
        opening = _FENCE.fullmatch(line)
        if fence:
            if (
                opening
                and opening[1][0] == fence[0]
                and len(opening[1]) >= len(fence)
                and not opening[2].strip()
            ):
                fence = ""
            out.append((0, line))
        elif opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = opening[1]
            out.append((0, line))
        elif heading := _HEADING.fullmatch(line):
            out.append((len(heading[1]), heading[2]))
        elif _TITLE.fullmatch(line):
            out.append((1, line))
        elif _HASHES.match(line):
            return None
        else:
            out.append((0, line))
    return None if fence else out


def _split(tokens: list[Token], level: int) -> tuple[list[Token], list[tuple[str, list[Token]]]]:
    head: list[Token] = []
    parts: list[tuple[str, list[Token]]] = []
    for token in tokens:
        if token[0] == level:
            parts.append((token[1], []))
        else:
            (parts[-1][1] if parts else head).append(token)
    return head, parts


def _plain(tokens: list[Token]) -> list[str] | None:
    return None if any(lv for lv, _ in tokens) else [t for _, t in tokens]


def _bullets(lines: list[str] | None) -> list[str] | None:
    """The text of every bullet, or `None` when a line is not blank or a bullet."""
    found = [_BULLET.fullmatch(x) for x in lines or [] if x.strip()]
    return [m[1].strip() for m in found if m] if all(found) else None


def _decisions(lines: list[str] | None) -> bool:
    groups: list[list[str]] = []
    for line in lines or [""]:
        if not line.strip():
            continue
        if _BULLET.fullmatch(line):
            groups.append([])
        elif groups and (sub := _SUB_BULLET.fullmatch(line)):
            groups[-1].append(sub[1])
        else:
            return False
    return bool(groups) and all(
        len(g) == 1 and g[0].casefold().startswith("reason:") and g[0][7:].strip() for g in groups
    )


def _slices(tokens: list[Token]) -> dict[str, tuple[list[str], list[str]]] | None:
    head, parts = _split(tokens, 3)
    if _plain(head) is None or not parts:
        return None
    slices: dict[str, tuple[list[str], list[str]]] = {}
    for slice_id, toks in parts:
        subhead, subs = _split(toks, 4)
        named = {t.casefold().strip(): _bullets(_plain(ts)) for t, ts in subs}
        criteria, risks = named.get("acceptance criteria"), named.get("risks")
        if (
            not re.fullmatch(_SLUG, slice_id)
            or slice_id in slices
            or len(subs) != 2
            or not criteria
            or not risks
        ):
            return None
        if any(lv or t.strip() for lv, t in subhead):
            return None
        if risks == ["none"]:
            risks = []
        elif len(set(risks)) != len(risks) or not all(
            re.fullmatch(_SLUG, r) and r != "none" for r in risks
        ):
            return None
        slices[slice_id] = (criteria, risks)
    return slices


def _screens(
    tokens: list[Token], slices: dict[str, tuple[list[str], list[str]]] | None
) -> list[tuple[Screen, set[str]]] | None:
    """The screens with the slice ids they name (empty set: all of them)."""
    head, parts = _split(tokens, 3)
    if slices is None or _plain(head) is None or not (parts or any(t.strip() for _, t in head)):
        return None
    screens: list[tuple[Screen, set[str]]] = []
    for name, toks in parts:
        lines = [x for x in _plain(toks) or [""] if x.strip()]
        named = _SCREEN_SLICES.fullmatch(lines[0]) if lines else None
        copy = _bullets(lines[1:])
        ids = set() if named and named[1] == "all" else set((named[1] if named else "").split(", "))
        if copy is None or not named or not ids <= set(slices):
            return None
        if any(name.casefold() == s["name"].casefold() for s, _ in screens):
            return None
        screens.append((Screen(name=name, copy=copy), ids))
    return screens
