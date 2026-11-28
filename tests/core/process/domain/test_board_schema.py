"""The board schema's pure core (GH-1166): what `schema_steps` says to do to a Project's
fields, and which field `etapa_field` calls the Etapa one. No option or field name is written
as a literal here: they all come from `get_args(Etapa)` and the `board_schema` constants."""

from __future__ import annotations

from typing import get_args

import pytest

from agentic.core.process.domain.board import Etapa
from agentic.core.process.domain.board_schema import (
    DEFAULT_OPTION_COLOR,
    DEFAULT_OPTION_DESCRIPTION,
    DROPPED_ETAPAS,
    ETAPA_FIELD,
    KNOWN_ETAPAS,
    MOTIVO_FIELD,
    PREVIOUS_ETAPAS,
    PREVIOUS_MOTIVO_FIELD,
    STATUS_FIELD,
    FieldNode,
    OptionNode,
    RenameField,
    ReplaceOptions,
    SchemaRefusal,
    etapa_field,
    schema_steps,
)

NEW = get_args(Etapa)
OLD = tuple(PREVIOUS_ETAPAS)
OLD_OF = {new: old for old, new in PREVIOUS_ETAPAS.items()}
SINGLE_SELECT = "SINGLE_SELECT"
TEXT = "TEXT"


def option(name: str) -> OptionNode:
    """An option whose id, colour and description say which option it is."""
    return OptionNode(f"id-{name}", name, f"color-{name}", f"description-{name}")


def select(names: tuple[str, ...] | list[str], *, called: str = ETAPA_FIELD) -> FieldNode:
    return FieldNode("F_select", called, SINGLE_SELECT, tuple(option(n) for n in names))


def text(called: str, kind: str = TEXT) -> FieldNode:
    return FieldNode(f"F_{called}", called, kind, ())


OTHER = FieldNode("F_other", "Other", "NUMBER", ())
MOTIVO = text(MOTIVO_FIELD)

# name of the case -> (the Etapa field's option names or None for no Etapa field, what is
# expected: "same" (no options step), "replace", or "refuse")
OPTIONS: dict[str, tuple[list[str] | None, str]] = {
    "exact": (list(reversed(NEW)), "same"),
    "old": (list(OLD), "replace"),
    "mixed": ([OLD[0], NEW[1], OLD[2], NEW[3]], "replace"),
    "new_incomplete": (list(NEW[:3]), "replace"),
    "old_and_dropped": ([*OLD, *DROPPED_ETAPAS], "replace"),
    "only_dropped": (list(DROPPED_ETAPAS), "replace"),
    "unknown": ([*OLD, "not-an-etapa"], "refuse"),
    "no_field": (None, "refuse"),
}
TEXTS: dict[str, tuple[list[FieldNode], str]] = {
    "bloqueo": ([text(PREVIOUS_MOTIVO_FIELD)], "rename"),
    "motivo": ([MOTIVO], "same"),
    "both": ([text(PREVIOUS_MOTIVO_FIELD), MOTIVO], "refuse"),
    "neither": ([], "refuse"),
    "bloqueo_wrong_type": ([text(PREVIOUS_MOTIVO_FIELD, "NUMBER")], "refuse"),
    "motivo_wrong_type": ([text(MOTIVO_FIELD, "NUMBER")], "refuse"),
}


def nodes_of(options: str, texts: str) -> tuple[FieldNode, ...]:
    names = OPTIONS[options][0]
    fields = [] if names is None else [select(names)]
    return (OTHER, *fields, *TEXTS[texts][0])


def replacing(steps: tuple[object, ...]) -> ReplaceOptions:
    found = [step for step in steps if isinstance(step, ReplaceOptions)]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("texts", TEXTS)
@pytest.mark.parametrize("options", OPTIONS)
def test_schema_steps_matrix(options: str, texts: str) -> None:
    result = schema_steps(nodes_of(options, texts))
    option_verdict, text_verdict = OPTIONS[options][1], TEXTS[texts][1]
    if "refuse" in (option_verdict, text_verdict):
        assert isinstance(result, SchemaRefusal)
        assert result.found
        return
    assert not isinstance(result, SchemaRefusal)
    expected: list[type] = []
    if option_verdict == "replace":
        expected.append(ReplaceOptions)
    if text_verdict == "rename":
        expected.append(RenameField)
    assert [type(step) for step in result] == expected


@pytest.mark.parametrize(
    "options", [name for name, (_, verdict) in OPTIONS.items() if verdict == "replace"]
)
def test_replace_options_carries_what_the_option_had(options: str) -> None:
    names = OPTIONS[options][0]
    assert names is not None
    result = schema_steps(nodes_of(options, "motivo"))
    assert not isinstance(result, SchemaRefusal)
    step = replacing(result)
    assert step.field_id == "F_select"
    assert [o.name for o in step.options] == list(NEW)
    for new in step.options:
        source = (
            new.name
            if new.name in names
            else OLD_OF[new.name]
            if OLD_OF[new.name] in names
            else None
        )
        if source is None:
            assert new == OptionNode(
                None, new.name, DEFAULT_OPTION_COLOR, DEFAULT_OPTION_DESCRIPTION
            )
        else:
            assert new == OptionNode(
                f"id-{source}", new.name, f"color-{source}", f"description-{source}"
            )
        assert new.id is None or new.id.removeprefix("id-") not in DROPPED_ETAPAS


def test_mixed_options_prefer_the_new_named_one() -> None:
    both = [OLD_OF[NEW[0]], NEW[0]]
    result = schema_steps((select(both), MOTIVO))
    assert not isinstance(result, SchemaRefusal)
    first = replacing(result).options[0]
    assert first.id == f"id-{NEW[0]}"


def test_a_field_of_only_dropped_options_gets_five_new_options() -> None:
    result = schema_steps(nodes_of("only_dropped", "motivo"))
    assert not isinstance(result, SchemaRefusal)
    assert all(
        o == OptionNode(None, o.name, DEFAULT_OPTION_COLOR, DEFAULT_OPTION_DESCRIPTION)
        for o in replacing(result).options
    )


def test_rename_targets_the_bloqueo_field() -> None:
    result = schema_steps(nodes_of("exact", "bloqueo"))
    assert result == (RenameField(f"F_{PREVIOUS_MOTIVO_FIELD}", MOTIVO_FIELD),)


def test_options_step_comes_before_the_rename() -> None:
    result = schema_steps(nodes_of("old", "bloqueo"))
    assert not isinstance(result, SchemaRefusal)
    assert [type(step) for step in result] == [ReplaceOptions, RenameField]


def test_nothing_to_do_is_an_empty_tuple() -> None:
    assert schema_steps(nodes_of("exact", "motivo")) == ()


def test_status_field_stands_in_for_etapa() -> None:
    status = select(OLD, called=STATUS_FIELD)
    result = schema_steps((status, MOTIVO))
    assert not isinstance(result, SchemaRefusal)
    assert replacing(result).field_id == status.id


def test_refusals_name_what_was_found() -> None:
    unknown = schema_steps(nodes_of("unknown", "motivo"))
    assert isinstance(unknown, SchemaRefusal) and "not-an-etapa" in unknown.found
    both = schema_steps(nodes_of("exact", "both"))
    assert isinstance(both, SchemaRefusal)
    assert MOTIVO_FIELD in both.found and PREVIOUS_MOTIVO_FIELD in both.found
    neither = schema_steps(nodes_of("exact", "neither"))
    assert isinstance(neither, SchemaRefusal) and MOTIVO_FIELD in neither.found
    wrong = schema_steps(nodes_of("exact", "bloqueo_wrong_type"))
    assert isinstance(wrong, SchemaRefusal) and "NUMBER" in wrong.found
    missing = schema_steps(nodes_of("no_field", "motivo"))
    assert isinstance(missing, SchemaRefusal) and ETAPA_FIELD in missing.found


def test_an_etapa_problem_is_refused_before_a_text_problem() -> None:
    result = schema_steps(nodes_of("unknown", "both"))
    assert isinstance(result, SchemaRefusal) and "not-an-etapa" in result.found


def test_previous_etapas_map_onto_the_five_options() -> None:
    assert set(PREVIOUS_ETAPAS.values()) == set(get_args(Etapa))
    assert len(PREVIOUS_ETAPAS) == len(get_args(Etapa))


def test_known_etapas_are_old_new_and_dropped() -> None:
    assert KNOWN_ETAPAS == {*PREVIOUS_ETAPAS, *DROPPED_ETAPAS, *get_args(Etapa)}


def test_etapa_field_keeps_an_incomplete_etapa_over_status() -> None:
    incomplete = FieldNode("F_etapa", ETAPA_FIELD, SINGLE_SELECT, (option(NEW[0]),))
    status = FieldNode("F_status", STATUS_FIELD, SINGLE_SELECT, tuple(option(n) for n in NEW))
    assert etapa_field((status, incomplete)) == incomplete


def test_etapa_field_falls_back_to_status() -> None:
    status = FieldNode("F_status", STATUS_FIELD, SINGLE_SELECT, ())
    assert etapa_field((OTHER, status)) == status


def test_etapa_field_ignores_a_text_field_of_that_name() -> None:
    assert etapa_field((text(ETAPA_FIELD), text(STATUS_FIELD))) is None
    assert etapa_field(()) is None
