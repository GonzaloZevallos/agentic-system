"""The GitHub rate limit as a pause of its own (GH-989): pure rules.

A run that exhausts its wait for GitHub's rate limit pauses as an external pause under the
reason `github_rate_limit`, never `account_limit`: the account's limit decisions
(`rules.ACCOUNT_LIMIT_*`) never read it and it is never written under that reason. This module
is the one writer of such an entry (`rate_limit_pause_patch`), the reader of how long the wait
has lasted (`rate_limit_exhausted`) and the one definition of the per-product offset
(`reset_offset`). It imports `rules`; `rules` never imports it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from . import rules
from .rules import GITHUB_RATE_LIMIT_SCHEDULE, RATE_LIMIT_PERSISTED
from .task import Failure, PauseEntry, TaskState

__all__ = [
    "GITHUB_RATE_LIMIT_SCHEDULE",
    "RATE_LIMIT_PERSISTED",
    "RATE_LIMIT_WAIT_MAX",
    "notice_posted",
    "pause_at",
    "person_spoke",
    "persist_due",
    "poller_retakes",
    "rate_limit_exhausted",
    "rate_limit_notice_owed",
    "rate_limit_pause_patch",
    "rate_limit_persisted_patch",
    "reset_offset",
    "retake_due_at",
    "cleared_on_close",
    "governing_reset",
    "tracker_silent",
]

# How long a GitHub rate limit may keep a task paused, counted from the first pause of the
# limit (`rate_limit_paused_at`); also the farthest a stated reset may lie from its pause.
RATE_LIMIT_WAIT_MAX = timedelta(hours=12)

_PAUSE_TEXT = "GitHub rate limit outlasted the wait"
_OFFSET_SPAN_S = 31  # 0..30 s


def reset_offset(product: str) -> timedelta:
    """A deterministic 0-30 s offset for `product`, from the sha256 of its key, so the tasks of
    different products do not retake at the very instant GitHub's limit resets."""
    digest = hashlib.sha256(product.encode("utf-8")).digest()
    return timedelta(seconds=int.from_bytes(digest[:8], "big") % _OFFSET_SPAN_S)


def _instant(value: object) -> datetime | None:
    """`value` as an aware datetime; `None` when it is not a string, does not parse or is naive."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _previous_epoch(state: TaskState) -> list[PauseEntry]:
    """The entries strictly between the second-to-last and the last `resumed` entry: the epoch
    the last resume closed. Empty with no `resumed`; with exactly one, everything before it."""
    log = state.get("pause_log", [])
    resumed = [i for i, entry in enumerate(log) if entry["kind"] == "resumed"]
    if not resumed:
        return []
    start = resumed[-2] + 1 if len(resumed) > 1 else 0
    return list(log[start : resumed[-1]])


def _previous_was_github(state: TaskState) -> bool:
    """Whether the epoch the last resume closed ended on a `github_rate_limit` failure (one the
    run did not retry: `rules.terminal_failures`)."""
    failures = [
        entry
        for entry in rules.terminal_failures(_previous_epoch(state))
        if entry["kind"] == "failure"
    ]
    return bool(failures) and failures[-1]["value"] == "github_rate_limit"


def rate_limit_pause_patch(
    state: TaskState, *, resets_at: datetime | None, at: str
) -> dict[str, Any]:
    """The pause of a run whose GitHub rate-limit wait ran out: an escalation of class `failure`
    over one `System` failure of reason `github_rate_limit`, with no section.

    `resets_at` is stored raw (never offset) on the failure only when
    `at < resets_at <= at + RATE_LIMIT_WAIT_MAX`; otherwise, or when naive, it is left out.
    `rate_limit_paused_at` keeps the task's existing value only when the epoch the last resume
    closed was this same limit and the value is an aware instant not after `at`; any other
    value (a stale one from an unrelated epoch, a malformed one, a future one) becomes `at`."""
    now = datetime.fromisoformat(at)
    failure = Failure(
        role="System",
        attempt=0,
        error=_PAUSE_TEXT,
        cost_usd=0.0,
        session_id="",
        reason="github_rate_limit",
    )
    if resets_at is not None and resets_at.tzinfo is not None:
        if now < resets_at <= now + RATE_LIMIT_WAIT_MAX:
            failure["resets_at"] = resets_at.isoformat()
    kept = _instant(state.get("rate_limit_paused_at"))
    keep = _previous_was_github(state) and kept is not None and kept <= now
    patch = rules.logged({**rules.escalation("failure", _PAUSE_TEXT)}, [failure], at=at)
    patch["rate_limit_paused_at"] = state["rate_limit_paused_at"] if keep else at
    return patch


def rate_limit_exhausted(state: TaskState, now: datetime) -> bool:
    """Whether the GitHub limit has held the task for `RATE_LIMIT_WAIT_MAX` since
    `rate_limit_paused_at`. Pure; `False`, never an exception, when the field is absent, not a
    string, unparsable or naive, or when `now` is naive."""
    paused_at = _instant(state.get("rate_limit_paused_at"))
    if paused_at is None or now.tzinfo is None:
        return False
    return now >= paused_at + RATE_LIMIT_WAIT_MAX


def rate_limit_persisted_patch(*, at: str) -> dict[str, Any]:
    """The escalation of a GitHub limit that outlasted its wait: class `failure` with one failure
    entry of reason `other`, so no reader finds the pause eligible for a retake again (mirrors
    `rules.account_limit_persisted_patch`); written once, not each tick."""
    failure = Failure(
        role="Poller",
        attempt=0,
        error=RATE_LIMIT_PERSISTED,
        cost_usd=0.0,
        session_id="",
        reason="other",
    )
    return rules.logged({**rules.escalation("failure", RATE_LIMIT_PERSISTED)}, [failure], at=at)


def rate_limit_notice_owed(state: TaskState) -> bool:
    """Whether the human is owed the notice that the limit outlasted the wait: the task is
    escalated with `RATE_LIMIT_PERSISTED` and `rate_limit_noticed_at` is not yet set."""
    return state.get("escalated") == RATE_LIMIT_PERSISTED and not state.get("rate_limit_noticed_at")


def _silent(state: TaskState, log: list[PauseEntry], escalated: object) -> bool:
    """`tracker_silent`'s decision over `state` as a pause log and an `escalated` text would
    leave it."""
    if escalated == RATE_LIMIT_PERSISTED:
        return True
    return rules.auto_resumable({**state, "pause_log": log}) == "github_rate_limit"


def tracker_silent(state: TaskState) -> bool:
    """Whether `escalate` writes nothing to the tracker (no label, no comment): the one decision.
    True for a GitHub rate-limit pause that is not persisted (the poller retakes it, a human is
    owed nothing yet) and, as a defence, for the persisted escalation itself, whose only writer
    of the label and the notice is the poller. False for every other pause, the account's
    included."""
    return _silent(state, state.get("pause_log", []), state.get("escalated"))


def cleared_on_close(state: TaskState, patch: dict[str, Any]) -> dict[str, Any]:
    """`patch` with `rate_limit_paused_at` and `rate_limit_noticed_at` removed, for the final
    patch of an invocation that ends without a GitHub rate-limit pause (GH-989): the next limit
    is a new wait. Kept untouched when the state after `patch` is such a pause or the persisted
    escalation, and a no-op when neither the state nor the patch carries a field. A removed key
    is written `None`, which `document.render` leaves out: absent from the saved document, never
    null."""
    log = [*state.get("pause_log", []), *patch.get("pause_log", [])]
    if _silent(state, log, patch.get("escalated", state.get("escalated"))):
        return patch
    fields = [
        key
        for key in ("rate_limit_paused_at", "rate_limit_noticed_at")
        if state.get(key) or patch.get(key)
    ]
    return {**patch, **dict.fromkeys(fields)}


def pause_at(state: TaskState) -> datetime | None:
    """The `at` of the current epoch's last `escalation` entry: the instant the pause the poller
    now measures its retakes from was written (the pause carries no `Escalated:` comment to
    measure from). Everything after the last `resumed` entry is the epoch. `None` when the epoch
    has no escalation entry, its `at` is absent, unparsable or naive."""
    log = state.get("pause_log", [])
    last_resumed = max((i for i, entry in enumerate(log) if entry["kind"] == "resumed"), default=-1)
    escalations = [entry for entry in log[last_resumed + 1 :] if entry["kind"] == "escalation"]
    if not escalations:
        return None
    return _instant(escalations[-1].get("at"))


def person_spoke(
    comments: Sequence[tuple[str, datetime]], at: datetime, system_prefixes: tuple[str, ...]
) -> bool:
    """Whether a person commented after the pause: a comment with none of `system_prefixes` whose
    time is strictly later than both `at` and the last system comment's. `comments` are
    `(body, created_at)` pairs. A person's comment stops the automatic retakes, never the
    escalation at 12 h (`persist_due` reads no comment)."""
    last_system = max(
        (created for body, created in comments if body.startswith(system_prefixes)), default=at
    )
    cutoff = max(at, last_system)
    return any(
        created > cutoff for body, created in comments if not body.startswith(system_prefixes)
    )


def poller_retakes(comments: Sequence[tuple[str, datetime]], at: datetime, prefix: str) -> int:
    """How many comments start with `prefix` (the poller's own resume comment) and are strictly
    later than `at`: the retakes this pause already had. The pause spends no retake budget, so
    this count is the only memory of them; `prior > 0` is also whether one was made."""
    return sum(1 for body, created in comments if body.startswith(prefix) and created > at)


def governing_reset(resets_at: datetime | None, product: str) -> datetime | None:
    """The instant a stated reset lets the pause be retaken: `resets_at` plus the product's
    offset (`reset_offset`), `None` without a stated reset. The one place the offset is added;
    `retake_due_at` and the poller's candidate (`rules.retake_due` reads it as `resets_at`) both
    take it from here."""
    return resets_at + reset_offset(product) if resets_at is not None else None


def retake_due_at(
    resets_at: datetime | None, at: datetime, prior: int, product: str, now: datetime
) -> datetime | None:
    """The instant of the pause's next retake, only once it is due (`None` before). With a stated
    reset and no retake yet (`prior == 0`) it is `resets_at + reset_offset(product)`; otherwise
    (no stated reset, or one retake already made) the hourly `GITHUB_RATE_LIMIT_SCHEDULE` slot
    `prior` after `at`, `None` once its 12 slots are spent. The same decision
    `rules.retake_due` takes over a `ResumeCandidate` built from these facts; pure, `now` is an
    argument."""
    governing = governing_reset(resets_at, product)
    return rules.account_limit_due(
        at,
        prior,
        len(GITHUB_RATE_LIMIT_SCHEDULE),
        now,
        schedule=GITHUB_RATE_LIMIT_SCHEDULE,
        resets_at=governing,
        retaken=prior > 0,
    )


def persist_due(state: TaskState, now: datetime) -> bool:
    """Whether the sweep writes the persisted escalation: the task is escalated as a GitHub
    rate-limit pause (`rules.auto_resumable`) and has waited `RATE_LIMIT_WAIT_MAX`. Decided from
    the document alone: no comment is read, so a person's comment never stops it."""
    return (
        bool(state.get("escalated"))
        and rules.auto_resumable(state) == "github_rate_limit"
        and rate_limit_exhausted(state, now)
    )


def notice_posted(comments: Sequence[tuple[str, datetime]], at: datetime, text: str) -> bool:
    """Whether the persisted notice already landed: some comment whose first line is that of
    `text` (`Escalated: ` + `RATE_LIMIT_PERSISTED`, the heading `escalate.comment` writes) and
    whose time is strictly later than `at`. A comment of an earlier pause does not count."""
    heading = text.split("\n", 1)[0]
    return any(body.split("\n", 1)[0] == heading and created > at for body, created in comments)
