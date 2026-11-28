"""`SmtpNotifier`: sends a `Mail` over SMTP with verified TLS (GH-1057, ADR-0106).

Port 465 is implicit TLS (`SMTP_SSL`); any other port must offer STARTTLS and is upgraded with
the default verifying context before anything else. `login` is never called on a connection
that is not TLS. The message is plain text built by `EmailMessage`, so a header with a CR or LF
is refused before connecting. Every failure is `NotifyFailed` carrying the exception type name
and nothing else: no text, no server reply, no configured value.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from ...application.ports import Mail, NotifyFailed
from .config import SmtpSettings

TIMEOUT_S = 10
IMPLICIT_TLS_PORT = 465


class SmtpNotifier:
    def __init__(
        self,
        settings: SmtpSettings,
        smtp: type[smtplib.SMTP] = smtplib.SMTP,
        smtp_ssl: type[smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
    ):
        self.settings = settings
        self._smtp = smtp
        self._smtp_ssl = smtp_ssl

    def send(self, mail: Mail) -> None:
        """Sends `mail`. Raises `NotifyFailed(type name)` on any failure."""
        failed: str | None = None
        try:
            self._send(mail)
        except NotifyFailed as e:
            failed = e.cause
        except Exception as e:
            failed = type(e).__name__
        if failed is not None:
            # Raised outside the `except`, so the original is neither `__cause__` nor
            # `__context__`: its text and the server reply live nowhere reachable.
            raise NotifyFailed(failed) from None

    def _send(self, mail: Mail) -> None:
        s = self.settings
        if "," in s.to:
            raise ValueError("one recipient only")
        msg = EmailMessage()
        msg["Subject"] = mail.subject
        msg["From"] = s.user if s.user and "@" in s.user else s.to
        msg["To"] = s.to
        msg.set_content(mail.body)
        conn = self._connect(s.host, int(s.port))
        if s.user is not None:
            conn.login(s.user, s.password or "")
        conn.send_message(msg)
        conn.quit()

    def _connect(self, host: str, port: int) -> smtplib.SMTP:
        if port == IMPLICIT_TLS_PORT:
            return self._smtp_ssl(
                host, port, timeout=TIMEOUT_S, context=ssl.create_default_context()
            )
        conn = self._smtp(host, port, timeout=TIMEOUT_S)
        conn.ehlo()
        if not conn.has_extn("starttls"):
            raise NotifyFailed("StartTlsMissing")
        conn.starttls(context=ssl.create_default_context())
        conn.ehlo()
        return conn
