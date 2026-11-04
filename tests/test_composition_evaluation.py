"""`default_ports` wires an evaluation root and only an evaluation root (GH-470, part 2)."""

import pytest

from agentic.composition import default_ports
from agentic.core.process.adapters.evaluation import (
    EvaluationStack,
    EvaluationTracker,
    EvaluationWorkspace,
)
from agentic.core.process.adapters.filesystem.file_inbox import FileInbox, frozen_input_path
from agentic.shared.configuration import Configuration


def _cfg(root):
    return Configuration(product_dir=root)


def test_an_ordinary_root_keeps_the_real_ports(tmp_path):
    ports = default_ports(_cfg(tmp_path))

    assert not isinstance(ports.tracker, EvaluationTracker)
    assert not isinstance(ports.workspace, EvaluationWorkspace)
    assert not isinstance(ports.stack, EvaluationStack)


def test_an_evaluation_root_wraps_the_ports_and_reads_its_frozen_entry(tmp_path):
    root = tmp_path / ".agentic" / "eval" / "EVAL-7-aaaaaaaa-default-1"
    path = frozen_input_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(
        "id: EVAL-7-aaaaaaaa-default-1\nissue: 7\nbase: " + "a" * 40 + "\ntitle: t\nbody: b\n"
    )

    ports = default_ports(_cfg(root))

    assert isinstance(ports.tracker, EvaluationTracker)
    assert isinstance(ports.workspace, EvaluationWorkspace)
    assert isinstance(ports.stack, EvaluationStack)
    assert isinstance(ports.inbox, FileInbox)
    assert (ports.board, ports.board_sent) == (None, None)


def test_an_evaluation_root_without_its_frozen_entry_reads_no_task_from_anywhere(tmp_path):
    root = tmp_path / ".agentic" / "eval" / "EVAL-7-aaaaaaaa-default-1"
    root.mkdir(parents=True)

    ports = default_ports(_cfg(root))

    assert ports.inbox.open_issues() == []
    with pytest.raises(ValueError, match="no frozen entry"):
        ports.inbox.task(7)
