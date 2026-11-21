"""The filing of a format refusal (GH-1011): the system's own issue, one per refusal rule, that
a role's schema or check refusal opens or comments on. Best effort: it never raises and what
it writes never changes the escalation the caller keeps.

The text it publishes goes to a public repository under the owner's `gh` login, so it comes
only from `domain.format_issue.evidence` (closed fields; the product reduced to a slug or
`unknown`). The refused value, a failure's `error` and a refusal's `detail` are never read
here, and a log line holds the closed rule id and the outcome, nothing else.

The locks and the set of filed pairs live on the instance, one `FormatFiling` per run. They
serialise the threads of one process only: two processes can open two issues for one rule, and
`domain.format_issue.find` then picks the lowest-numbered.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from typing import get_args

from ..domain import format_issue as fi
from ..domain.task import (
    Failure,
    FormatIssueRecord,
    RefusalRule,
    TrackerFailureClass,
    TrackerFailureRecord,
    TrackerTarget,
    format_issue_for,
)
from .ports import SystemRepo, tracker_failure_class

_RULES: dict[str, RefusalRule] = {rule: rule for rule in get_args(RefusalRule)}
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_LABELS = ("needs-triage",)


def _write_class(error: Exception) -> TrackerFailureClass:
    """The closed class of a failed write by the type of the error, never by its text."""
    if isinstance(error, ValueError):
        return tracker_failure_class(error) or "refused"
    if isinstance(error, OSError):
        return "unreachable"
    return "refused"


class FormatFiling:
    """Files the issue of a refusal rule; implements `ports.FormatFiler`."""

    def __init__(
        self, system: SystemRepo, log: Callable[[str], None], now: Callable[[], str]
    ) -> None:
        self._system = system
        self._log = log
        self._now = now
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._filed: set[tuple[str, str]] = set()

    def file(
        self,
        *,
        task_id: str,
        issue_number: int,
        product: str,
        role: str,
        failures: Sequence[Failure],
        existing: Sequence[FormatIssueRecord],
    ) -> tuple[FormatIssueRecord | None, list[TrackerFailureRecord]]:
        """Opens or comments the issue of the last failure's rule and returns the record to
        keep, or the `tracker_failures` row of a write that did not land. Never raises."""
        label = "unknown"
        try:
            last = failures[-1]
            rule = last["rule"]
            label = rule if rule in _RULES else "unknown"
            return self._file(
                label, rule, last, task_id, issue_number, product, role, failures, existing
            )
        except Exception:
            self._say(label, "write failed")
            return None, []

    def _say(self, label: str, outcome: str) -> None:
        try:
            self._log(f"agentic: format issue: {label}: {outcome}")
        except Exception:
            return

    def _lock(self, label: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(label, threading.Lock())

    def _file(
        self,
        label: str,
        rule: str,
        last: Failure,
        task_id: str,
        issue_number: int,
        product: str,
        role: str,
        failures: Sequence[Failure],
        existing: Sequence[FormatIssueRecord],
    ) -> tuple[FormatIssueRecord | None, list[TrackerFailureRecord]]:
        pair = (task_id, rule)
        if format_issue_for(existing, rule) is not None or pair in self._filed:
            self._say(label, "skipped")
            return None, []
        with self._lock(label):
            if pair in self._filed:
                self._say(label, "skipped")
                return None, []
            try:
                refs = self._system.inbox.open_issue_refs()
            except Exception:
                self._say(label, "read failed")
                return None, []
            try:
                title, body = fi.evidence(
                    rule,
                    last.get("field", ""),
                    role,
                    product if _SLUG.fullmatch(product) else "unknown",
                    issue_number,
                    last.get("value_len"),
                    len(failures),
                    fi.earlier_rules(failures),
                )
            except Exception:
                # Not a failed tracker write: no row, the escalation is all there is to see.
                self._say(label, "write failed")
                return None, []
            target: TrackerTarget = "issue"
            try:
                match = fi.find(refs, rule)
                if match is not None:
                    target = "comment"
                    self._system.tracker.comment_on_issue(match.url, body)
                    url, outcome = match.url, "commented"
                else:
                    url = self._system.tracker.open_issue(title, body, labels=_LABELS)
                    outcome = "opened"
            except Exception as exc:
                self._say(label, "write failed")
                try:
                    row: TrackerFailureRecord = {
                        "target": target,
                        "cls": _write_class(exc),
                        "at": self._now(),
                    }
                except Exception:
                    return None, []
                return None, [row]
            # The write landed: the pair is marked before the clock is read, so a clock that
            # raises here never lets a retry open a second issue.
            self._filed.add(pair)
            self._say(label, outcome)
            at = self._now()
            return {"rule": _RULES[rule], "url": url, "at": at}, []
