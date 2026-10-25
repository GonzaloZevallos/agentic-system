"""GH-338 (ADR-0063): an idle poll round stops reparsing the closed task documents. The
guard counts `document.parse` calls over a real `FilesystemTaskDocuments`, never seconds:
a regression that walks every document again shows up as parses, on any machine."""

from __future__ import annotations

import time

import pytest

from agentic.core.process.adapters.filesystem import FilesystemTaskDocuments
from agentic.core.process.adapters.filesystem import documents as filesystem_documents
from agentic.core.process.application import poll, reflect_run
from agentic.core.process.domain import document
from tests.core.process.application.test_poll import ESCALATED_AT, _cfg, _round
from tests.fakes import FakeLauncher, MemoryRunRegistry, MemoryTracker, memory_ports

HOUR_NS = 3_600 * 1_000_000_000
IDLE = ["agentic: poll: nothing to do this round"]


def _aged() -> int:
    return time.time_ns() + HOUR_NS


@pytest.fixture
def parses(monkeypatch):
    calls: list[object] = []
    real = document.parse

    def spy(raw, source="document"):
        calls.append(source)
        return real(raw, source)

    monkeypatch.setattr(document, "parse", spy)
    # 400 `save`s would each fork a `git rev-parse`; the fixture does not need the sha.
    monkeypatch.setattr(filesystem_documents, "system_commit", lambda: None)
    return calls


def _closed_corpus(tmp_path, count: int):
    ports = memory_ports(
        tracker=MemoryTracker(), documents=FilesystemTaskDocuments(tmp_path, clock=_aged)
    )
    for number in range(1, count + 1):
        ports.documents.save(
            {
                "id": f"GH-{number}",
                "title": "t",
                "description": "d",
                "status": "done",
                "pull_request": f"https://github.com/o/r/pull/{number}",
                "closed_at": "2026-10-01T00:00:00Z",
                "sections": {"HUMAN": "approved"},
            }
        )
    return ports


@pytest.mark.parametrize("count", [50, 150, 200])
def test_the_second_idle_round_parses_no_document(tmp_path, parses, count):
    ports = _closed_corpus(tmp_path, count)
    registry, launcher = MemoryRunRegistry(), FakeLauncher()

    first = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    after_first = len(parses)
    second = _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)

    assert first == second == IDLE
    assert after_first >= count  # the first round did read the corpus
    assert len(parses) == after_first  # the second one parsed nothing


def test_a_young_corpus_is_reparsed_so_the_guard_can_fail(tmp_path, parses):
    """The guard's own negative: the same rounds over documents inside the racy window
    parse on every scan, which is exactly what the guard above must be able to see."""
    ports = _closed_corpus(tmp_path, 5)
    ports.documents.clock = time.time_ns  # the files were just written: all racy
    registry, launcher = MemoryRunRegistry(), FakeLauncher()

    _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)
    after_first = len(parses)
    _round(_cfg(tmp_path), ports, registry, launcher, ESCALATED_AT)

    assert len(parses) > after_first


@pytest.mark.parametrize(
    "scan",
    [
        pytest.param(poll.awaiting_merge, id="awaiting_merge"),
        pytest.param(poll.gather_closes, id="gather_closes"),
        pytest.param(lambda ports: poll.gather_resumes(ports, ESCALATED_AT), id="gather_resumes"),
        pytest.param(lambda ports: reflect_run._documents(ports, None), id="reflect_run_documents"),
    ],
)
def test_each_scan_reads_an_unchanged_corpus_from_the_cache(tmp_path, parses, scan):
    ports = _closed_corpus(tmp_path, 60)
    scan(ports)
    after_first = len(parses)

    scan(ports)

    assert after_first >= 1
    assert len(parses) == after_first
