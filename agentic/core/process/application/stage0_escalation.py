"""The escalation of a stage0 rotation (GH-632, part 2e): every rejected sha, and a sha that
stayed unavailable `UNAVAILABLE_AT` passes in a row, is told to the human once, as a comment on
one fixed issue of the system repo. Nothing outside tests builds it yet; the CLI command of part
2d plugs it into `rotate`, which calls `run` inside its flock.

Only what this module wrote reaches the tracker: its own constants, a sha that matches
`^[0-9a-f]{40}$`, and a cause that is one of `CandidateCause` (else `unknown`). No other string
read from the store, a port or an exception is sent or printed, and `run` never raises.

The issue is recognised by its exact title together with its marker on a line of its own; a
sha is escalated when its marker is a line of its own in a comment. The post and the match read
the same constants through the same two helpers, so they cannot drift apart. A failure on the
stored URL ends the pass: it never searches for or opens a second issue.

Contract for the wiring of part 2d: `tracker` and `inbox` are the system repo's own, never the
product's; the stage0 root is machine-wide."""

from __future__ import annotations

import re
from typing import get_args

from ..domain.stage0_rotation import CandidateCause
from .ports import Inbox, Stage0Store, Tracker
from .stage0_rotate import Out

ISSUE_TITLE = "stage0 rotation: escalations"
ISSUE_MARKER = "<!-- agentic-stage0-escalations -->"
ESCALATED_MARKER = "<!-- agentic-stage0-escalated: {sha} -->"
UNAVAILABLE_MARKER = "<!-- agentic-stage0-unavailable: {sha} -->"
UNAVAILABLE_AT = 3

_ISSUE_BODY = (
    "Stage0's rotation escalates here the commits it rejected and the commits it could not "
    "prepare or smoke. One comment per commit; delete a marker line to escalate it again."
)
_ESCALATED_TEXT = "Stage0 rejected commit {sha}: its smoke failed beside a green control."
_UNAVAILABLE_TEXT = (
    "Stage0 could not prepare or smoke commit {sha} on {count} passes in a row; "
    "the cause class is {cause}."
)
_SHA40 = re.compile(r"[0-9a-f]{40}")
_ISSUE_NUMBER = re.compile(r"/issues/(\d+)$")
_FAILED = "escalation: failed"
_UNKNOWN = "unknown"


def render_marker(template: str, sha: str) -> str:
    """The marker of `sha` made from one of the marker constants."""
    return template.format(sha=sha)


def has_line(text: str, marker: str) -> bool:
    """Whether `marker` is a line of `text` on its own (a quote inside a line does not count)."""
    return any(line.strip() == marker for line in text.splitlines())


class Stage0Escalation:
    """The `Stage0Escalation` port over the system repo's tracker and inbox and the rotation's
    store. Holds the four collaborators and nothing else."""

    def __init__(self, tracker: Tracker, inbox: Inbox, store: Stage0Store, out: Out) -> None:
        self._tracker = tracker
        self._inbox = inbox
        self._store = store
        self._out = out

    def run(self) -> None:
        try:
            self._run()
        except Exception:
            self._out(_FAILED)

    def _run(self) -> None:
        pending = self._pending()
        if not pending:
            return
        url = self._issue()
        if url is None:
            return
        number = _ISSUE_NUMBER.search(url)
        if number is None:
            self._out(_FAILED)
            return
        try:
            comments = self._tracker.issue_comments(f"GH-{number.group(1)}")
        except Exception:
            self._out(_FAILED)
            return
        for marker, text, line in pending:
            if any(has_line(comment, marker) for comment in comments):
                continue
            try:
                self._tracker.comment_on_issue(url, f"{text}\n\n{marker}")
            except Exception:
                self._out(_FAILED)
                continue
            self._out(line)

    def _pending(self) -> list[tuple[str, str, str]]:
        """What is owed, as `(marker, text, out line)`, built from the store alone."""
        pending: list[tuple[str, str, str]] = []
        invalid = False
        for sha in sorted(self._store.rejected()):
            if _SHA40.fullmatch(sha) is None:
                invalid = True
                continue
            pending.append(
                (
                    render_marker(ESCALATED_MARKER, sha),
                    _ESCALATED_TEXT.format(sha=sha),
                    f"escalation: escalated {sha}",
                )
            )
        record = self._store.unavailable_record()
        if record is not None and record[1] >= UNAVAILABLE_AT:
            sha, count, cause = record
            if _SHA40.fullmatch(sha) is None:
                invalid = True
            else:
                pending.append(
                    (
                        render_marker(UNAVAILABLE_MARKER, sha),
                        _UNAVAILABLE_TEXT.format(
                            sha=sha, count=UNAVAILABLE_AT, cause=_cause(cause)
                        ),
                        f"escalation: unavailable {sha}",
                    )
                )
        if invalid:
            self._out(_FAILED)
        return pending

    def _issue(self) -> str | None:
        """The fixed issue's URL, or None after saying `escalation: failed`."""
        stored = self._store.escalation_issue()
        if stored is not None:
            return stored
        try:
            refs = self._inbox.open_issue_refs()
        except Exception:
            self._out(_FAILED)
            return None
        found = next(
            (r.url for r in refs if r.title == ISSUE_TITLE and has_line(r.body, ISSUE_MARKER)),
            None,
        )
        if found is None:
            found = self._tracker.open_issue(ISSUE_TITLE, f"{_ISSUE_BODY}\n\n{ISSUE_MARKER}")
            self._out("escalation: opened")
        self._store.write_escalation_issue(found)
        return found


def _cause(cause: str) -> str:
    return cause if cause in get_args(CandidateCause) else _UNKNOWN
