"""The notifier's one config reader (GH-1057): raw values, off without a recipient, never raises."""

import pytest

from agentic.core.process.adapters.smtp.config import SmtpSettings, settings

FULL = {
    "AGENTIC_NOTIFY_EMAIL_TO": " me@example.com ",
    "AGENTIC_NOTIFY_SMTP_HOST": "smtp.example.com",
    "AGENTIC_NOTIFY_SMTP_PORT": "587",
    "AGENTIC_NOTIFY_SMTP_USER": "bot@example.com",
    "AGENTIC_NOTIFY_SMTP_PASSWORD": "pw",
    "AGENTIC_NOTIFY_DIGEST_HOUR": "8",
    "AGENTIC_NOTIFY_EVENTS": "waiting,digest",
}


@pytest.mark.parametrize("to", [None, "", "   ", "\t\n"])
def test_a_missing_or_blank_recipient_is_off(to):
    env = {k: v for k, v in FULL.items() if k != "AGENTIC_NOTIFY_EMAIL_TO"}
    if to is not None:
        env["AGENTIC_NOTIFY_EMAIL_TO"] = to
    assert settings(env) is None


def test_all_seven_variables_are_read():
    assert settings(FULL) == SmtpSettings(
        to="me@example.com",
        host="smtp.example.com",
        port="587",
        user="bot@example.com",
        password="pw",
        digest_hour="8",
        events=("waiting", "digest"),
    )


def test_events_are_split_stripped_and_cleaned():
    env = {**FULL, "AGENTIC_NOTIFY_EVENTS": "waiting, release"}
    s = settings(env)
    assert s is not None
    assert s.events == ("waiting", "release")


@pytest.mark.parametrize("events", [",", " , ", "", " "])
def test_events_with_nothing_left_mean_all_kinds(events):
    s = settings({**FULL, "AGENTIC_NOTIFY_EVENTS": events})
    assert s is not None
    assert s.events is None


def test_unset_events_mean_all_kinds():
    s = settings({"AGENTIC_NOTIFY_EMAIL_TO": "me@example.com"})
    assert s is not None
    assert s.events is None
    assert (s.user, s.password, s.digest_hour) == (None, None, None)


def test_empty_user_and_password_are_none():
    s = settings({**FULL, "AGENTIC_NOTIFY_SMTP_USER": "", "AGENTIC_NOTIFY_SMTP_PASSWORD": ""})
    assert s is not None
    assert (s.user, s.password) == (None, None)


def test_a_malformed_port_or_hour_does_not_raise():
    s = settings({**FULL, "AGENTIC_NOTIFY_SMTP_PORT": "x587", "AGENTIC_NOTIFY_DIGEST_HOUR": "noon"})
    assert s is not None
    assert (s.port, s.digest_hour) == ("x587", "noon")
