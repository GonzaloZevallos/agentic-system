"""The words a `SchemaGuard` denial carries (glossary: Guarda de esquema; issue #88):
the exact keys `schemas.validate` rejected, from its own exception, plus the line of
contract the incident needs -- never a key this module invents on its own."""

from pydantic import ValidationError

from agentic.core.process.domain import schemas
from agentic.core.process.domain.schema_guard import reason_for


def _validation_error(raw: dict) -> ValidationError:
    try:
        schemas.validate(schemas.Delivery, raw, {})
    except ValueError as exc:
        assert isinstance(exc, ValidationError)
        return exc
    raise AssertionError("expected schemas.validate to reject this input")


def test_names_exactly_the_missing_keys_and_no_other():
    reason = reason_for(_validation_error({"markdown": "### Changes\n\nx"}))
    missing = reason.split(":", 1)[1].split(".", 1)[0]
    assert "`summary`" in missing
    assert "`assumptions`" in missing
    assert "`markdown`" not in missing


def test_states_the_contract_that_they_are_json_keys_not_tags():
    reason = reason_for(_validation_error({"markdown": "x"}))
    assert "top-level keys" in reason
    assert "`markdown`" in reason  # names the field the model wrote the tags inside


def test_falls_back_to_the_exceptions_own_message_when_nothing_is_missing():
    # `flags` mismatches raise a plain `ValueError`, not a `ValidationError` naming a
    # missing key: nothing here to list, so the exception's own text stands.
    exc = ValueError("flags must be exactly the vocabulary; unknown: [], missing: ['x']")
    assert reason_for(exc) == "flags must be exactly the vocabulary; unknown: [], missing: ['x']"
