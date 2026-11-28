"""The notifier's three `Ports` members come from the environment and nowhere else (GH-1057)."""

from dataclasses import replace
from datetime import datetime

import pytest

from agentic.composition import evaluation_ports, notify_members
from agentic.core.process.adapters.filesystem.notified import FilesystemNotified
from agentic.core.process.adapters.smtp.notifier import SmtpNotifier
from agentic.core.process.application.dry_run import read_only_ports
from agentic.core.process.application.ports import (
    ActivityRow,
    Mail,
    NotifySettings,
)
from agentic.core.process.domain.notify import NotifiedRecord
from tests.fakes import memory_ports


class _Stub:
    """Fills all four members; none of them may be called."""

    def send(self, mail: Mail) -> None:
        raise AssertionError("must not send")

    def load(self) -> NotifiedRecord:
        raise AssertionError("must not load")

    def save(self, record: NotifiedRecord) -> None:
        raise AssertionError("must not save")

    def merged_prs(self, since: datetime, limit: int) -> list[ActivityRow]:
        raise AssertionError("must not read")

    def closed_issues(self, since: datetime, limit: int) -> list[ActivityRow]:
        raise AssertionError("must not read")


class _Raises:
    def __getattr__(self, name):
        raise AssertionError(f"must not touch {name}")


@pytest.mark.parametrize("to", [None, "", "   "])
def test_notify_members_are_none_without_a_recipient(tmp_path, to):
    environ = {"AGENTIC_NOTIFY_SMTP_HOST": "smtp.example.com"}
    if to is not None:
        environ["AGENTIC_NOTIFY_EMAIL_TO"] = to
    assert notify_members(environ, tmp_path / "notify" / "p.json") == (None, None, None)


def test_notify_members_are_built_from_the_environment_without_touching_the_disk(tmp_path):
    path = tmp_path / "notify" / "p.json"
    notifier, notified, notify_settings = notify_members(
        {
            "AGENTIC_NOTIFY_EMAIL_TO": "me@example.com",
            "AGENTIC_NOTIFY_SMTP_HOST": "smtp.example.com",
            "AGENTIC_NOTIFY_DIGEST_HOUR": "nine",
            "AGENTIC_NOTIFY_EVENTS": "waiting, release",
        },
        path,
    )
    assert isinstance(notifier, SmtpNotifier)
    assert isinstance(notified, FilesystemNotified)
    assert notified.path == path
    assert notify_settings == NotifySettings("nine", ("waiting", "release"))
    assert not path.parent.exists()


def test_a_malformed_value_does_not_raise_when_composing(tmp_path):
    members = notify_members(
        {"AGENTIC_NOTIFY_EMAIL_TO": "me@example.com", "AGENTIC_NOTIFY_SMTP_PORT": "x"},
        tmp_path / "n.json",
    )
    assert all(member is not None for member in members)


def test_a_dry_round_drops_the_notifier_members():
    filled = replace(
        memory_ports(),
        notifier=_Stub(),
        notified=_Stub(),
        activity=_Stub(),
        notify_settings=NotifySettings("8", None),
    )
    world, *_ = read_only_ports(filled, _Raises(), _Raises(), None)  # type: ignore[arg-type]
    assert (world.notifier, world.notified, world.activity, world.notify_settings) == (
        None,
        None,
        None,
        None,
    )


def test_an_evaluation_root_drops_the_notifier_members():
    filled = replace(
        memory_ports(),
        notifier=_Stub(),
        notified=_Stub(),
        activity=_Stub(),
        notify_settings=NotifySettings("8", ("waiting",)),
    )
    ports = evaluation_ports(filled, _Raises())  # type: ignore[arg-type]
    assert (ports.notifier, ports.notified, ports.activity, ports.notify_settings) == (
        None,
        None,
        None,
        None,
    )
