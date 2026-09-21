"""The schema guard (glossary: Guarda de esquema) over one role run: the `SchemaGuard`
that answers before the CLI even considers a role's structured-output call, from the
gate's own evidence (ADR-0011, fifth addendum) that a `PreToolUse` on that tool sees
exactly the `tool_input` a downstream `schemas.validate` would validate. One instance
per role run, built by `run_with_retry` outside its attempt loop so its one-denial cap
survives the retry the same way `CheckoutPresubmit.refusals` does for the Puerta de
entrega."""

from __future__ import annotations

from typing import Any

from ..domain import schema_guard, schemas
from ..domain.schemas import RoleOutput


class RoleSchemaGuard:
    """The `SchemaGuard` over one role's `schema` and flag `vocabulary` -- the same two
    things `_attempt` already passes to `schemas.validate` downstream -- bound in at
    construction so the hook needs nothing but the call's own `tool_input`. Denies with
    `schemas.validate`'s own reason (`schema_guard.reason_for`); decides nothing on its
    own. Past `schema_guard.REFUSALS` denials it lets the invalid shape through: the
    run ends up at the same schema failure `_attempt` raises today, and a second denial
    would only cost the role a turn for no gain."""

    def __init__(self, schema: type[RoleOutput], vocabulary: dict[str, str]):
        self.schema = schema
        self.vocabulary = vocabulary
        self.refusals = 0

    def __call__(self, tool_input: dict[str, Any]) -> str | None:
        try:
            schemas.validate(self.schema, tool_input, self.vocabulary)
        except ValueError as exc:
            if self.refusals >= schema_guard.REFUSALS:
                return None
            self.refusals += 1
            return schema_guard.reason_for(exc)
        return None
