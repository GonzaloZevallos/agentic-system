"""The dry-run proxy (GH-622, part 1b): its tables against the Protocols, its neutral values
against their annotations, and the real filesystem adapters' reads against the disk."""

import collections.abc
import contextlib
import dataclasses
import types
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic.core.process.adapters.filesystem.documents import FilesystemTaskDocuments
from agentic.core.process.adapters.filesystem.lock import FilesystemRunLock
from agentic.core.process.adapters.filesystem.run_registry import FilesystemRunRegistry
from agentic.core.process.adapters.filesystem.scratch import FilesystemScratch
from agentic.core.process.application import dry_run, ports
from agentic.core.process.application.dry_run import (
    NONE_WRITES,
    READS,
    UNREACHED,
    WRITES_VALUED,
    DryRunLog,
    DryRunRefused,
    ReadOnly,
    read_only_ports,
)
from tests.fakes import memory_ports

PORTS = sorted(READS)
NOW = datetime(2026, 11, 13, tzinfo=UTC)


def _members(port: str) -> tuple[frozenset[str], ...]:
    empty: frozenset[str] = frozenset()
    return (
        READS[port],
        NONE_WRITES.get(port, empty),
        frozenset(WRITES_VALUED.get(port, {})),
        UNREACHED.get(port, empty),
    )


def _hint(port: str, name: str) -> object:
    return typing.get_type_hints(getattr(getattr(ports, port), name)).get("return")


def _accepts(hint: object, value: object) -> bool:
    """`value` is of the annotated type; a shape this does not know fails the test."""
    if hint is None or hint is type(None):
        return value is None
    origin = typing.get_origin(hint)
    if origin in (types.UnionType, typing.Union):
        return any(_accepts(arm, value) for arm in typing.get_args(hint))
    if origin is typing.Literal:
        return value in typing.get_args(hint)
    if origin is contextlib.AbstractContextManager:
        return hasattr(value, "__enter__") and hasattr(value, "__exit__")
    if origin is collections.abc.Iterator:
        return hasattr(value, "__iter__")
    if isinstance(origin, type):
        return isinstance(value, origin)
    if isinstance(hint, type):
        return isinstance(value, hint)
    raise AssertionError(f"unhandled annotation {hint!r}")


class Raises:
    """An adapter double that fails on any touch: a write must never get here."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"reached the adapter: {name}")


class Paths:
    """What the document factories ask of their adapter."""

    task_id = "T"

    def path_for(self, task_id: object) -> Path:
        return Path("tasks/T.md")


def _wrapped(inner: object, port: str) -> tuple[ReadOnly, DryRunLog]:
    log = DryRunLog()
    return dry_run._wrap(inner, port, log), log


@pytest.mark.parametrize("port", PORTS)
def test_tables_classify_every_protocol_member_once(port):
    protocol = getattr(ports, port)
    assert typing.is_protocol(protocol)
    tables = _members(port)
    union = frozenset().union(*tables)
    assert union == typing.get_protocol_members(protocol)
    assert sum(len(table) for table in tables) == len(union)


@pytest.mark.parametrize("port", PORTS)
def test_neutral_values_fit_the_annotations(port):
    for name in NONE_WRITES.get(port, frozenset()):
        assert _accepts(_hint(port, name), None), f"{port}.{name} cannot return None"
    for name, make in WRITES_VALUED.get(port, {}).items():
        value = make(Paths(), (Paths(), {}))
        assert _accepts(_hint(port, name), value), f"{port}.{name} -> {value!r}"


def test_a_valued_write_hands_back_a_fresh_value_each_call():
    wrapped, log = _wrapped(Raises(), "RunRegistry")
    first, second = wrapped.publish_spend("root", "p", [], NOW), wrapped.publish_spend()
    assert first == [] and first is not second
    assert log.writes[0].startswith("would runregistry.publish_spend 'root', 'p', []")


@pytest.mark.parametrize(
    ("port", "name"),
    [(p, n) for p in PORTS for n in sorted(NONE_WRITES.get(p, frozenset()))],
)
def test_a_write_never_reaches_the_adapter(port, name):
    wrapped, log = _wrapped(Raises(), port)
    assert getattr(wrapped, name)("arg") is None
    assert log.writes == [f"would {port.lower()}.{name} 'arg'"] and log.refused == []


def test_a_document_save_says_the_path_it_would_write():
    wrapped, log = _wrapped(Paths(), "TaskDocuments")
    assert wrapped.save(Paths()) == Path("tasks/T.md")
    assert wrapped.record_closure("T", {"a": 1}) == Path("tasks/T.md")
    assert len(log.writes) == 2


def test_a_read_delegates():
    inner = types.SimpleNamespace(queue=lambda: [1, 2])
    wrapped, log = _wrapped(inner, "Tracker")
    assert wrapped.queue() == [1, 2]
    assert log.writes == [] and log.refused == []


def test_an_unclassified_name_is_recorded_before_it_raises():
    wrapped, log = _wrapped(Raises(), "Tracker")
    with pytest.raises(DryRunRefused, match=r"dry-run: unclassified Tracker\.merge"):
        wrapped.merge("url", "sha")  # an unreached write: no neutral value
    with pytest.raises(DryRunRefused):
        getattr(wrapped, "x", None)  # not swallowed as if it were an AttributeError
    assert log.refused == ["Tracker.merge", "Tracker.x"] and log.writes == []


def test_a_dunder_or_private_name_is_an_attribute_error_and_is_not_recorded():
    wrapped, log = _wrapped(Raises(), "Tracker")
    assert getattr(wrapped, "__deepcopy__", None) is None
    assert getattr(wrapped, "_missing", None) is None
    assert log.refused == []


def test_the_refusal_is_no_error_a_gatherer_handles():
    banned = {
        ValueError,
        OSError,
        KeyError,
        LookupError,
        AttributeError,
        RuntimeError,
        ports.GitError,
        ports.TrackerCallFailed,
    }
    assert DryRunRefused.__bases__ == (Exception,)
    assert not banned & set(DryRunRefused.__mro__)


def test_every_port_comes_back_wrapped_and_the_untabled_ones_refuse():
    base = memory_ports(experiment=Raises(), scratch=Raises(), free_space=Raises())
    wrapped, reg, lck, launcher, log = read_only_ports(base, Raises(), Raises(), None)
    for field in dataclasses.fields(wrapped):
        value = getattr(wrapped, field.name)
        if field.name in ("limits", "now"):
            assert not isinstance(value, ReadOnly)
        elif getattr(base, field.name) is None:
            assert value is None
        else:
            assert isinstance(value, ReadOnly), field.name
    assert all(isinstance(each, ReadOnly) for each in (reg, lck, launcher))
    with pytest.raises(DryRunRefused):
        wrapped.runner.run
    with pytest.raises(DryRunRefused):
        wrapped.commands.anything
    assert launcher.spawn(["x"]) == 0
    assert log.refused == ["runner.run", "commands.anything"]
    assert log.writes == ["would launcher.spawn ['x']"]


def _tree(root: Path) -> dict[str, int]:
    return {str(p): p.stat().st_mtime_ns for p in [root, *sorted(root.rglob("*"))]}


def test_the_reads_of_the_real_filesystem_adapters_leave_the_disk_alone(tmp_path):
    root = tmp_path / "not-yet"
    root.mkdir()
    before = _tree(tmp_path)
    audited = {
        "RunLock": (FilesystemRunLock(root / "runs"), {"read": ("T",), "refusal": ("T",)}),
        "RunRegistry": (
            FilesystemRunRegistry(root / "registry"),
            {
                "exempt_attempts": ("p",),
                "exempt_waits": (NOW,),
                "land_waits": (NOW,),
                "launch_attempts": ("p", 1),
                "live": (),
                "spend": (NOW,),
            },
        ),
        "Scratch": (
            FilesystemScratch(root / "scratch", "p", root / "product"),
            {"kept_folders": (), "listing": (), "partitions": ()},
        ),
        "TaskDocuments": (
            FilesystemTaskDocuments(root / "product"),
            {
                "flakes_since": (NOW,),
                "ids": (),
                "ids_touched_since": (NOW,),
                "load": ("T",),
                "path_for": ("T",),
                "text": ("T",),
            },
        ),
    }
    for port, (adapter, calls) in audited.items():
        assert READS[port] - {"product_key"} == set(calls), f"{port}: audit every read"
        for name, args in calls.items():
            getattr(adapter, name)(*args)
    assert _tree(tmp_path) == before


def test_the_audits_reject_what_they_should(tmp_path):
    # A neutral value of the wrong type, and a write the tree audit would catch.
    assert not _accepts(list[str], 5) and not _accepts(Path | None, "x")
    with pytest.raises(AssertionError, match="unhandled"):
        _accepts(typing.Annotated[int, "a"], 1)
    registry = FilesystemRunRegistry(tmp_path / "registry")
    before = _tree(tmp_path)
    with registry.exclusive():
        pass
    assert _tree(tmp_path) != before
