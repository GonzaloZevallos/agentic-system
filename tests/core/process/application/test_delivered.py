"""The delivered-size patch (GH-592): the numstat of a pull request, measured against what the
product declares generated, as the `actual_size` a task document records."""

from __future__ import annotations

import pytest

from agentic.core.process.application.delivered import size_patch
from agentic.core.process.application.ports import GitError
from agentic.core.process.domain.docs import Docs
from agentic.core.process.domain.size_budget import ActualSize, NumstatRow
from tests.fakes import memory_ports

ROWS = (
    NumstatRow("agentic/core/new.py", 120, 4),
    NumstatRow("uv.lock", 900, 300),
    NumstatRow("tests/test_new.py", 200, 0),
    NumstatRow("generated_pages/stack.html", 50, 10),
    NumstatRow("agentic/moved.py", 0, 0),
    NumstatRow("web/src/api/client.ts", 70, 0),
)


def test_the_rows_are_measured_with_the_lockfiles_the_docs_paths_and_the_generated_globs():
    ports = memory_ports()
    ports.stack.declared_docs = Docs(generate="make docs", paths=("generated_pages/**",))
    ports.stack.declared_generated = ("web/src/api/*.ts",)
    ports.workspace.numstat_rows = ROWS
    logged: list[str] = []

    patch = size_patch("GH-1", "abc", "def", ports, logged.append)

    assert (
        patch
        == ActualSize(
            hand_files=1, hand_lines=120, test_lines=200, generated_files=3
        ).to_frontmatter()
    )
    assert ports.workspace.numstat_reads == [("abc", "def")]
    assert logged == []


def test_a_diff_that_cannot_be_read_omits_the_record_and_logs_one_line(monkeypatch):
    ports = memory_ports()

    def unreadable(upstream: str, head: str):
        raise GitError("bad revision")

    monkeypatch.setattr(ports.workspace, "numstat", unreadable)
    logged: list[str] = []

    assert size_patch("GH-1", "abc", "def", ports, logged.append) == {}
    assert len(logged) == 1 and "actual_size omitted" in logged[0]


def test_a_stack_that_cannot_be_read_omits_the_record(monkeypatch):
    ports = memory_ports()

    def unreadable(revision: str):
        raise ValueError("expected `generated` to be a list")

    monkeypatch.setattr(ports.stack, "generated", unreadable)
    logged: list[str] = []

    assert size_patch("GH-1", "abc", "def", ports, logged.append) == {}
    assert len(logged) == 1


@pytest.mark.parametrize("rows", [(), (NumstatRow("a.py", 0, 0),)])
def test_an_empty_or_free_diff_records_zeroes_not_nothing(rows):
    ports = memory_ports()
    ports.workspace.numstat_rows = rows

    patch = size_patch("GH-1", "abc", "def", ports, lambda _: None)

    assert patch == ActualSize(0, 0, 0, 0).to_frontmatter()
