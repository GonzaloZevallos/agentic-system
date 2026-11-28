"""The one reader of the notifier's settings (GH-1057, ADR-0106).

`settings(environ)` takes the mapping it reads, never `os.environ` itself, and returns the raw
values: a malformed port or hour never raises here, it fails at send time as `NotifyFailed`.
Nothing but this module names these variables.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SmtpSettings:
    """What the notifier is configured with, as the environment gave it. `port` and
    `digest_hour` stay raw strings; `events` is `None` for all kinds."""

    to: str
    host: str
    port: str
    user: str | None
    password: str | None
    digest_hour: str | None
    events: tuple[str, ...] | None


def settings(environ: Mapping[str, str]) -> SmtpSettings | None:
    """The settings, or `None` when `AGENTIC_NOTIFY_EMAIL_TO` is unset, empty or blank:
    notifications are then off."""
    to = environ.get("AGENTIC_NOTIFY_EMAIL_TO", "").strip()
    if not to:
        return None
    kinds = tuple(
        item
        for part in environ.get("AGENTIC_NOTIFY_EVENTS", "").split(",")
        if (item := part.strip())
    )
    return SmtpSettings(
        to=to,
        host=environ.get("AGENTIC_NOTIFY_SMTP_HOST", ""),
        port=environ.get("AGENTIC_NOTIFY_SMTP_PORT", ""),
        user=environ.get("AGENTIC_NOTIFY_SMTP_USER") or None,
        password=environ.get("AGENTIC_NOTIFY_SMTP_PASSWORD") or None,
        digest_hour=environ.get("AGENTIC_NOTIFY_DIGEST_HOUR") or None,
        events=kinds or None,
    )
