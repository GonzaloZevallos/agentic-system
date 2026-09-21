"""The schema guard (glossary: Guarda de esquema): the reason a role reads when its
structured-output call is denied before the CLI even considers it (ADR-0011, fifth
addendum). `RoleSchemaGuard` (`application/schema_guard.py`) is the one implementation
of the `SchemaGuard` port; it calls `schemas.validate` -- the only source of validity
in the whole mechanism -- and this module only turns what that call raised into words,
same as `denials.py` does for the permission cage's own reason.

Not the Puerta de entrega (`domain/presubmit.py`): that door is commit, clean tree,
product checks, and this one never looks at any of it."""

from __future__ import annotations

from pydantic import ValidationError

from .rules import exception_motive

REFUSALS = 1


def reason_for(exc: ValueError) -> str:
    """The model's own retry cue: exactly the keys `schemas.validate` flagged missing,
    read off its `ValidationError` (never guessed), plus the line issue #88's incident
    needs -- the model wrote them as tags inside `markdown` instead of sibling JSON
    keys, and this says which keys belong at the top level instead. A `ValueError` that
    names no missing key (a `flags` mismatch, a non-dict input) falls back to
    `rules.exception_motive`, unchanged."""
    missing = _missing_keys(exc)
    if not missing:
        return exception_motive(exc)
    names = ", ".join(f"`{key}`" for key in missing)
    return (
        f"the structured output is missing required key(s): {names}. {names} must be "
        "top-level keys of the JSON object this tool receives, not tags written inside "
        "the `markdown` string."
    )


def _missing_keys(exc: ValueError) -> list[str]:
    """The top-level fields a pydantic `ValidationError` reports as missing, in the
    order it reports them; empty for anything else `schemas.validate` may raise."""
    if not isinstance(exc, ValidationError):
        return []
    return [
        str(error["loc"][0])
        for error in exc.errors()
        if error["type"] == "missing" and len(error["loc"]) == 1
    ]
