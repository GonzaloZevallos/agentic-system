"""spec_guard (GH-594 part 4b-3): what a head makes of the delivery's spec, and the cut."""

from __future__ import annotations

import hashlib
from typing import Any, cast

import pytest

from agentic.core.process.application import spec_guard
from agentic.core.process.application.ports import SpecUnreadable, Upstream
from agentic.core.process.application.spec_guard import Changed, Intact, Unreadable
from agentic.core.process.domain import rules
from agentic.core.process.domain.spec_delivery import SpecReadRefusal
from tests.fakes import MemoryWorkspace, memory_ports

PATH = "docs/specs/a.md"
RECORDED = "a" * 40
HEAD = "b" * 40
UPSTREAM = "c" * 40
BODY = "## Goal\n\nText.\n"
TEXT = "---\nstatus: approved\n---\n" + BODY
SHA = hashlib.sha256(BODY.encode()).hexdigest()
STATE: Any = {"id": "GH-1", "spec": {"path": PATH, "commit": RECORDED, "sha256": SHA}}


class Reader:
    """Texts by commit; a `SpecUnreadable` instance in place of a text is raised."""

    def __init__(self, texts: dict[str, str | SpecReadRefusal]) -> None:
        self.texts = texts
        self.calls: list[tuple[str, str]] = []

    def read(self, commit: str, path: str) -> str:
        self.calls.append((commit, path))
        answer = self.texts.get(commit, "missing")
        if answer in ("missing", "oversize", "git", "commit", "path", "not_blob", "encoding"):
            raise SpecUnreadable(cast(SpecReadRefusal, answer))
        return answer


class Spy:
    def read(self, commit: str, path: str) -> str:
        raise AssertionError("a read was made")


def _ports(reader: Any, *, upstream: str | None = None, base: str | None = None):
    workspace = MemoryWorkspace()
    workspace.upstream_answer = Upstream("origin/main", upstream, None)
    if base is not None:
        workspace.existing_branches[rules.task_branch("GH-1")] = ("x", base)
    return memory_ports(workspace=workspace, spec_reader=reader)


def test_a_head_with_the_recorded_text_is_intact():
    reader = Reader({RECORDED: TEXT, HEAD: TEXT})
    assert spec_guard.check_at(STATE, _ports(reader), HEAD) == Intact()
    assert spec_guard.intact_at(STATE, _ports(reader), HEAD) is True


@pytest.mark.parametrize(
    "head_text",
    [TEXT.replace("Text.", "Other."), TEXT.replace("approved", "draft")],
    ids=["body", "front matter only"],
)
def test_a_changed_head_is_changed_with_the_path(head_text):
    reader = Reader({RECORDED: TEXT, HEAD: head_text})
    ports = _ports(reader)
    assert spec_guard.check_at(STATE, ports, HEAD) == Changed((PATH,))
    assert spec_guard.intact_at(STATE, ports, HEAD) is False


def test_a_deleted_spec_is_changed():
    reader = Reader({RECORDED: TEXT})  # HEAD: missing
    assert spec_guard.check_at(STATE, _ports(reader), HEAD) == Changed((PATH,))


def test_a_change_the_upstream_has_too_carries_the_upstream_detail():
    changed = TEXT.replace("Text.", "Other.")
    reader = Reader({RECORDED: TEXT, HEAD: changed, UPSTREAM: changed})
    assert spec_guard.check_at(STATE, _ports(reader, upstream=UPSTREAM), HEAD) == Changed(
        (PATH, "upstream")
    )


def test_an_upstream_that_differs_or_cannot_be_read_gives_the_plain_detail():
    changed = TEXT.replace("Text.", "Other.")
    differs = Reader({RECORDED: TEXT, HEAD: changed, UPSTREAM: TEXT})
    assert spec_guard.check_at(STATE, _ports(differs, upstream=UPSTREAM), HEAD) == Changed((PATH,))
    gone = Reader({RECORDED: TEXT, HEAD: changed})
    assert spec_guard.check_at(STATE, _ports(gone, upstream=UPSTREAM), HEAD) == Changed((PATH,))
    assert gone.calls[-1] == (UPSTREAM, PATH)
    no_sha = Reader({RECORDED: TEXT, HEAD: changed})
    assert spec_guard.check_at(STATE, _ports(no_sha, upstream=None), HEAD) == Changed((PATH,))
    assert len(no_sha.calls) == 2


@pytest.mark.parametrize("reason", ["oversize", "git"])
def test_a_head_that_cannot_be_read_is_unreadable(reason):
    reader = Reader({RECORDED: TEXT, HEAD: reason})
    assert spec_guard.check_at(STATE, _ports(reader), HEAD) == Unreadable(reason)


def test_a_recorded_commit_that_cannot_be_read_is_unreadable():
    reader = Reader({HEAD: TEXT})  # RECORDED: missing
    assert spec_guard.check_at(STATE, _ports(reader), HEAD) == Unreadable("missing")


def test_an_abbreviated_head_is_unreadable_and_nothing_is_read():
    reader = Reader({})
    assert spec_guard.check_at(STATE, _ports(reader), "abc123") == Unreadable("commit")
    assert reader.calls == []


def test_without_a_reader_the_check_is_unreadable_git():
    assert spec_guard.check_at(STATE, _ports(None), HEAD) == Unreadable("git")


@pytest.mark.parametrize("state", [{"id": "GH-1"}, {"id": "GH-1", "spec": {}}], ids=["no", "empty"])
def test_without_a_spec_nothing_is_read(state):
    ports = _ports(Spy(), base=HEAD)
    assert spec_guard.check_at(state, ports, HEAD) == Intact()
    assert spec_guard.intact_at(state, ports, "abc") is True
    assert spec_guard.cut(state, ports) == {}


def test_the_cut_re_records_the_commit_when_the_spec_is_the_same_at_the_base():
    reader = Reader({RECORDED: TEXT, HEAD: TEXT})
    patch = spec_guard.cut(STATE, _ports(reader, base=HEAD))
    assert patch == {"spec": {**STATE["spec"], "commit": HEAD}}


def test_the_cut_is_idempotent_at_the_recorded_commit():
    reader = Reader({RECORDED: TEXT})
    assert spec_guard.cut(STATE, _ports(reader, base=RECORDED)) == {}


def test_the_cut_refuses_when_the_spec_moved():
    reader = Reader({RECORDED: TEXT, HEAD: TEXT.replace("Text.", "Other.")})
    patch = spec_guard.cut(STATE, _ports(reader, base=HEAD))
    assert patch["spec_refusal"] == {"cause": "spec_changed", "detail": [PATH]}
    assert patch["status"] == "waiting_on_human"
    assert patch["escalation_class"] == "classification"
    assert "spec" not in patch


def test_the_cut_refuses_as_unreadable_when_both_reads_fail():
    reader = Reader({RECORDED: "git", HEAD: "git"})
    patch = spec_guard.cut(STATE, _ports(reader, base=HEAD))
    assert patch["spec_refusal"] == {"cause": "spec_unreadable", "detail": [PATH, "git"]}


def test_a_deleted_spec_at_the_cut_is_changed_not_unreadable():
    reader = Reader({RECORDED: TEXT})
    patch = spec_guard.cut(STATE, _ports(reader, base=HEAD))
    assert patch["spec_refusal"]["cause"] == "spec_changed"


def test_read_at_gives_the_text_at_a_full_commit():
    reader = Reader({HEAD: TEXT})
    assert spec_guard.read_at(_ports(reader), HEAD, PATH) == TEXT
    assert reader.calls == [(HEAD, PATH)]


@pytest.mark.parametrize("commit", ["abc123", "", "b" * 39, "z" * 40])
def test_read_at_refuses_a_commit_that_is_not_a_full_id_without_reading(commit: str):
    with pytest.raises(SpecUnreadable) as caught:
        spec_guard.read_at(_ports(Spy()), commit, PATH)
    assert caught.value.reason == "commit"


def test_read_at_without_a_reader_is_unreadable_git():
    with pytest.raises(SpecUnreadable) as caught:
        spec_guard.read_at(_ports(None), HEAD, PATH)
    assert caught.value.reason == "git"


def test_no_other_application_module_calls_the_spec_reader():
    """Invariant 4: the intake of `classify` reads through `spec_guard.read_at` too."""
    from pathlib import Path

    root = Path(spec_guard.__file__).parent
    callers = {p.name for p in root.glob("*.py") if "reader.read(" in p.read_text()}
    assert callers == {"spec_guard.py"}


def test_intact_at_never_asks_the_upstream_and_check_at_still_does():
    changed = TEXT.replace("Text.", "Other.")
    reader = Reader({RECORDED: TEXT, HEAD: changed, UPSTREAM: changed})
    ports = _ports(reader, upstream=UPSTREAM)

    assert spec_guard.intact_at(STATE, ports, HEAD) is False
    assert ports.workspace.upstream_calls == 0
    assert (UPSTREAM, PATH) not in reader.calls

    assert spec_guard.check_at(STATE, ports, HEAD) == Changed((PATH, "upstream"))
    assert ports.workspace.upstream_calls == 1
