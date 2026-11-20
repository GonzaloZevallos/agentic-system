"""The closed set of refusal ids and the error that carries one (GH-1011).

A leaf of the domain: it imports nothing from other domain modules, so `drawing` and
`spec_delivery` can raise an `OutputRefusal` without a cycle through `task`.
"""

from __future__ import annotations

from typing import Literal

# The closed set of ids a refusal of a role's structured output carries (GH-1011): decided
# only at the raise site in `run_role._attempt`, never read back from an error's text.
# `output_missing` the role delivered no structured output, `schema_invalid` the schema's
# own validation refused it, `unmapped` a `ValueError` no rule claims yet; the rest are the
# plan and drawing refusals a check raises as an `OutputRefusal`.
RefusalRule = Literal[
    "preview_slice_missing",
    "visible_without_drawing_check",
    "drawing_path_undeclared",
    "parts_when_fits",
    "estimate_does_not_fit",
    "part_over_budget",
    "depends_on_reason_too_long",
    "estimated_files_empty",
    "estimate_below_spent_floor",
    "output_missing",
    "schema_invalid",
    "unmapped",
]


class OutputRefusal(ValueError):
    """A refusal of a role's delivery that names its own rule. `str(exc)` is `detail`, so
    the error text a check raised stays what it was. `field` is a claim: `run_role.
    refusal_field` keeps it only when the role's schema declares it."""

    def __init__(self, rule: RefusalRule, field: str, detail: str):
        super().__init__(detail)
        self.rule = rule
        self.field = field
        self.detail = detail
