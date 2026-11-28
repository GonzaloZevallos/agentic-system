"""The `BoardSchema` port (GH-1166): the schema writes live there and nowhere on `Board`."""

from __future__ import annotations

import inspect

from agentic.core.process.application.ports import (
    Board,
    BoardSchema,
    BoardSchemaRejected,
    TrackerCallFailed,
    TrackerTransient,
)


def methods(protocol: type) -> set[str]:
    return {
        name
        for name, _ in inspect.getmembers(protocol, inspect.isfunction)
        if not name.startswith("_")
    }


def test_board_keeps_its_five_methods_and_none_changes_the_schema() -> None:
    assert methods(Board) == {"project_id", "open_issues", "all_issues", "issue", "write"}
    assert methods(Board).isdisjoint(methods(BoardSchema))


def test_board_schema_declares_fields_and_apply() -> None:
    assert methods(BoardSchema) == {"fields", "apply"}


def test_a_rejected_schema_is_a_plain_exception() -> None:
    assert issubclass(BoardSchemaRejected, Exception)
    assert not issubclass(BoardSchemaRejected, TrackerCallFailed | TrackerTransient)
