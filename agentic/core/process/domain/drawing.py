"""The approved drawing's rule (glossary: Dibujo aprobado), pure.

`rule_for` is the only producer of the rule wording: no other module builds or copies it.
"""

from __future__ import annotations

from .design import Design

DRAWING_RULE = (
    "Anything visible that departs from an approved drawing is decided by the owner, "
    "never by a role: do not choose it yourself, raise it."
)


def rule_for(design: Design | None) -> str:
    """The rule when the product declares a `design`, "" when `None`.

    `None` means the stack was read and declares no `design`; an unreadable stack never
    reaches here, it raises where it is read."""
    return "" if design is None else DRAWING_RULE
