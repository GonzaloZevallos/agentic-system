"""The owner approving a draft Spec (GH-594 part 5b): the notice the system posts on the
issue, the reading of the owner's answer, and the approved front matter. Pure; nothing calls
it yet.

An approval is the owner's own `Approved` comment after a notice whose hash is the current
draft's body hash. Only the owner's notices count (invariant 3): a notice from anyone else is
a plain comment, so nobody can plant a hash. The login comparison is
`spec_delivery.owner_matches` (invariant 1). Comment bodies arrive already normalised by
`resume._text` and are never normalised here (invariant 4). The hash is computed from the
text received, never taken as a parameter (invariant 2).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .spec_delivery import (
    SHA256,
    body_sha256,
    owner_matches,
    render_approved_front_matter,
    split_front_matter,
)

NOTICE_PREFIX = "Spec ready for approval"
APPROVED = "Approved"
DECISION = "Decision:"
_MARKER_OPEN = "<!-- agentic-spec-approval: "
_MARKER_CLOSE = " -->"
_MARKER = re.compile(rf"{re.escape(_MARKER_OPEN)}({SHA256.pattern}){re.escape(_MARKER_CLOSE)}")
_SPACE = re.compile(r"\s+")

Outcome = Literal["approved", "revise", "none"]


@dataclass(frozen=True)
class Comment:
    """A human or system comment of the issue. `body` is already normalised by
    `resume._text` (stripped, line endings joined with `\\n`); this module never
    normalises it."""

    body: str
    author: str


@dataclass(frozen=True)
class ApprovalReading:
    """What the owner answered: `author` is the approving comment's author, only for `approved`."""

    outcome: Outcome
    author: str | None


def _flat(summary: str) -> str:
    """The summary on one line with no HTML comment delimiters (so it cannot forge a marker)."""
    text = _SPACE.sub(" ", summary).strip()
    while "<!--" in text or "-->" in text:
        text = text.replace("<!--", "").replace("-->", "")
    return text


def notice(draft_text: str, summary: str) -> str:
    """The comment that asks the owner to approve `draft_text`: the one-line summary, how to
    answer, and the marker with the body hash as the last line. `ValueError` without front
    matter or on a body that cannot be encoded."""
    try:
        sha = body_sha256(draft_text)
    except UnicodeError as error:
        raise ValueError("the draft body cannot be encoded") from error
    if sha is None:
        raise ValueError("the draft has no front matter")
    return (
        f"{NOTICE_PREFIX}: {_flat(summary)}\n\n"
        f"Reply `{APPROVED}` to approve this draft, or `{DECISION} <what to change>` to "
        f"revise it.\n\n{_MARKER_OPEN}{sha}{_MARKER_CLOSE}"
    )


def _noticed_hash(comment: Comment, owner_login: str | None) -> str | None:
    """The hash of an owner's notice, `None` for any other comment."""
    if not comment.body.startswith(NOTICE_PREFIX) or not owner_matches(comment.author, owner_login):
        return None
    found = _MARKER.fullmatch(comment.body.rsplit("\n", 1)[-1])
    return found[1] if found else None


def _current_hash(draft_text: str) -> str | None:
    try:
        return body_sha256(draft_text)
    except UnicodeError:
        return None


def read_approval(
    comments: Sequence[Comment],
    draft_text: str,
    owner_login: str | None,
    system_prefixes: Sequence[str],
    escalation_prefix: str,
) -> ApprovalReading:
    """What the owner answered after the latest notice, in one pass over `comments`.

    `approved`: the owner's last answer is `Approved` and the notice's hash is the current
    draft's. `revise`: it is a `Decision:` answer, unless an escalation closed the window since
    the notice. Anything else, a stale hash or no notice is `none`."""
    notice_hash: str | None = None
    closed = False
    last: ApprovalReading | None = None
    for comment in comments:
        found = _noticed_hash(comment, owner_login)
        if found is not None:
            notice_hash, closed, last = found, False, None
            continue
        if notice_hash is None:
            continue
        if comment.body.startswith(escalation_prefix):
            closed = True
        if comment.body.startswith(tuple(system_prefixes)):
            continue
        if not owner_matches(comment.author, owner_login):
            continue
        if comment.body == APPROVED:
            last = ApprovalReading("approved", comment.author)
        elif comment.body.startswith(DECISION) and not closed:
            last = ApprovalReading("revise", None)
    if last is None or last.outcome == "none":
        return ApprovalReading("none", None)
    if last.outcome == "approved" and notice_hash != _current_hash(draft_text):
        return ApprovalReading("none", None)
    return last


def approve(draft_text: str, approved_by: str, approved_at: str) -> str:
    """The draft with `status: approved` and the hash of its own body: the only writer of an
    approved front matter (invariant 2). `ValueError` without front matter or when the front
    matter is not exactly `status: draft`."""
    split = split_front_matter(draft_text)
    if split is None or split[0] != "status: draft":
        raise ValueError("only a draft with the front matter `status: draft` can be approved")
    sha = body_sha256(draft_text)
    if sha is None:
        raise ValueError("the draft has no front matter")
    return render_approved_front_matter(approved_by, approved_at, sha) + split[1]
