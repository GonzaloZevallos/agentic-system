"""`SmtpNotifier.send` over a fake SMTP class (GH-1057): TLS, From rule, errors without secrets."""

import smtplib
import ssl

import pytest

from agentic.core.process.adapters.smtp.config import SmtpSettings
from agentic.core.process.adapters.smtp.notifier import SmtpNotifier
from agentic.core.process.application.ports import Mail, NotifyFailed

PASSWORD = "hunter2-secret"
USER = "bot-user@example.com"
REPLY = b"535 5.7.8 server said no to bot-user"


class FakeSmtp:
    """Records the constructor kwargs and the order of calls. `offers_starttls` and `boom` set
    per test through class attributes of a subclass made by `fake`."""

    offers_starttls = True
    boom: dict[str, Exception] = {}
    constructed: list["FakeSmtp"]

    def __init__(self, host, port, **kwargs):
        self.args = (host, port)
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.login_args: tuple[str, str] | None = None
        self.context: ssl.SSLContext | None = kwargs.get("context")
        self.message = None
        type(self).constructed.append(self)
        self._maybe_raise("init")

    def _maybe_raise(self, name):
        if name in self.boom:
            raise self.boom[name]

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        self.calls.append(f"has_extn:{name}")
        return self.offers_starttls

    def starttls(self, context=None):
        self.calls.append("starttls")
        self.context = context

    def login(self, user, password):
        self.calls.append("login")
        self.login_args = (user, password)
        self._maybe_raise("login")

    def send_message(self, msg):
        self.calls.append("send_message")
        self.message = msg

    def quit(self):
        self.calls.append("quit")


def fake(**attrs):
    return type("Fake", (FakeSmtp,), {"constructed": [], "boom": {}, **attrs})


def settings(**over):
    base = {
        "to": "me@example.com",
        "host": "smtp.example.com",
        "port": "587",
        "user": USER,
        "password": PASSWORD,
        "digest_hour": None,
        "events": None,
    }
    return SmtpSettings(**{**base, **over})


MAIL = Mail("A subject", "A body")


def notifier(cfg, plain=None, ssl_cls=None):
    return SmtpNotifier(cfg, smtp=plain or fake(), smtp_ssl=ssl_cls or fake())


def test_port_465_uses_implicit_tls_with_a_verifying_context_and_the_timeout():
    ssl_cls, plain = fake(), fake()
    SmtpNotifier(settings(port="465"), smtp=plain, smtp_ssl=ssl_cls).send(MAIL)
    assert plain.constructed == []
    (conn,) = ssl_cls.constructed
    assert conn.args == ("smtp.example.com", 465)
    assert conn.kwargs["timeout"] == 10
    assert conn.context is not None
    assert conn.context.verify_mode == ssl.CERT_REQUIRED
    assert conn.context.check_hostname is True
    assert conn.calls == ["login", "send_message", "quit"]


def test_other_ports_upgrade_with_starttls_before_login():
    plain, ssl_cls = fake(), fake()
    SmtpNotifier(settings(), smtp=plain, smtp_ssl=ssl_cls).send(MAIL)
    assert ssl_cls.constructed == []
    (conn,) = plain.constructed
    assert conn.args == ("smtp.example.com", 587)
    assert conn.kwargs == {"timeout": 10}
    assert conn.calls == [
        "ehlo",
        "has_extn:starttls",
        "starttls",
        "ehlo",
        "login",
        "send_message",
        "quit",
    ]
    assert conn.login_args == (USER, PASSWORD)
    assert conn.context is not None
    assert conn.context.verify_mode == ssl.CERT_REQUIRED
    assert conn.context.check_hostname is True


def test_without_starttls_it_fails_before_any_login():
    plain = fake(offers_starttls=False)
    with pytest.raises(NotifyFailed) as err:
        notifier(settings(), plain=plain).send(MAIL)
    assert err.value.cause == "StartTlsMissing"
    (conn,) = plain.constructed
    assert "login" not in conn.calls
    assert "send_message" not in conn.calls


def test_no_user_means_no_login():
    plain = fake()
    notifier(settings(user=None, password=None), plain=plain).send(MAIL)
    assert "login" not in plain.constructed[0].calls
    assert "send_message" in plain.constructed[0].calls


def test_from_is_the_user_when_it_has_an_at_sign():
    plain = fake()
    notifier(settings(user="bot@example.com"), plain=plain).send(MAIL)
    msg = plain.constructed[0].message
    assert msg["From"] == "bot@example.com"
    assert msg["To"] == "me@example.com"
    assert msg["Subject"] == "A subject"
    assert msg.get_content_type() == "text/plain"
    assert not msg.is_multipart()


def test_from_is_the_recipient_when_the_user_has_no_at_sign():
    plain = fake()
    notifier(settings(user="plainuser"), plain=plain).send(MAIL)
    assert plain.constructed[0].message["From"] == "me@example.com"


def test_from_is_the_recipient_with_no_user():
    plain = fake()
    notifier(settings(user=None, password=None), plain=plain).send(MAIL)
    assert plain.constructed[0].message["From"] == "me@example.com"


@pytest.mark.parametrize(
    ("mail", "cfg"),
    [
        (Mail("hi\r\nBcc: x@example.com", "b"), settings()),
        (Mail("hi\nBcc: x", "b"), settings()),
        (MAIL, settings(to="me@example.com\r\nBcc: x@example.com")),
        (MAIL, settings(to="me@example.com\nBcc: x")),
        (MAIL, settings(user="a\r\nb@example.com")),
    ],
)
def test_a_cr_or_lf_in_a_header_fails_before_connecting(mail, cfg):
    plain, ssl_cls = fake(), fake()
    with pytest.raises(NotifyFailed):
        SmtpNotifier(cfg, smtp=plain, smtp_ssl=ssl_cls).send(mail)
    assert plain.constructed == []
    assert ssl_cls.constructed == []


def test_a_comma_in_the_recipient_fails_before_connecting():
    plain = fake()
    with pytest.raises(NotifyFailed):
        notifier(settings(to="a@example.com,b@example.com"), plain=plain).send(MAIL)
    assert plain.constructed == []


@pytest.mark.parametrize("port", ["abc", "", "58 7"])
def test_a_bad_port_is_a_value_error(port):
    plain = fake()
    with pytest.raises(NotifyFailed) as err:
        notifier(settings(port=port), plain=plain).send(MAIL)
    assert err.value.cause == "ValueError"
    assert plain.constructed == []


def test_a_failure_carries_only_the_type_name_never_a_secret():
    boom = smtplib.SMTPAuthenticationError(535, REPLY + b" " + PASSWORD.encode())
    plain = fake(boom={"login": boom})
    with pytest.raises(NotifyFailed) as caught:
        notifier(settings(), plain=plain).send(MAIL)
    err = caught.value
    assert err.cause == "SMTPAuthenticationError"
    assert err.__cause__ is None
    assert err.__context__ is None
    assert err.__suppress_context__ is True
    shown = " ".join([str(err), repr(err), *map(str, err.args)])
    for secret in (PASSWORD, USER, "bot-user", "server said no", "535"):
        assert secret not in shown


def test_a_connection_failure_carries_only_the_type_name():
    plain = fake(boom={"init": ConnectionRefusedError("smtp.example.com refused " + PASSWORD)})
    with pytest.raises(NotifyFailed) as caught:
        notifier(settings(), plain=plain).send(MAIL)
    assert caught.value.cause == "ConnectionRefusedError"
    assert PASSWORD not in str(caught.value)
    assert caught.value.__context__ is None
