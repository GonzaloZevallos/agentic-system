"""`RoleSchemaGuard`, the one implementation of the `SchemaGuard` port (glossary: Guarda
de esquema): wraps `schemas.validate` -- the only source of validity anywhere in this
mechanism -- and caps its own denials at one per instance, the shape
`CheckoutPresubmit.refusals` already has for the Puerta de entrega."""

from agentic.core.process.application.schema_guard import RoleSchemaGuard
from agentic.core.process.domain.schemas import Delivery

INVALID = {"markdown": "### Changes\n\nx"}
VALID = {"markdown": "### Changes\n\nx", "summary": "s", "assumptions": []}


def test_denies_an_invalid_delivery_with_the_missing_keys():
    guard = RoleSchemaGuard(Delivery, {})
    reason = guard(INVALID)
    assert reason is not None
    assert "`summary`" in reason
    assert "`assumptions`" in reason


def test_lets_a_valid_delivery_through():
    guard = RoleSchemaGuard(Delivery, {})
    assert guard(VALID) is None


def test_denies_at_most_once_per_instance_then_lets_the_invalid_shape_through():
    guard = RoleSchemaGuard(Delivery, {})
    assert guard(INVALID) is not None
    assert guard.refusals == 1
    # Cap spent: the same invalid shape now passes, same as `CheckoutPresubmit` past
    # `presubmit.REFUSALS` (the schema failure `_attempt` raises downstream is where
    # this run ends up instead, not a second denial that would only cost a turn).
    assert guard(INVALID) is None
    assert guard.refusals == 1
