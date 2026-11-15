"""The approved drawing's rule (glossary: Dibujo aprobado), pure.

`rule_for` is the only producer of the rule wording: no other module builds or copies it.
`normalize_drawing` is the only owner of the drawing-path rule and `departure_question` of the
escalation text of a departure: callers use them, never repeat them.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

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


@dataclass(frozen=True)
class Departure:
    """Something visible that would differ from an approved drawing, raised for the owner."""

    drawing: str
    element: str
    shows: str
    would_ship: str
    recommendation: str
    reason: str
    raised_by: str


_NUMBER_SUFFIX = re.compile(r":\d+$")


def normalize_drawing(path: str) -> str:
    """The drawing path as the key sees it: normalized, never casefolded.

    Raises `ValueError` for an empty or blank path, a `:`, an absolute path or one that
    leaves the design folder."""
    if not path.strip():
        raise ValueError("the drawing path is empty")
    suffix = _NUMBER_SUFFIX.search(path)
    if suffix:
        raise ValueError(
            f"the drawing path {path!r} ends in {suffix.group()!r}: cite the element apart"
        )
    if ":" in path:
        raise ValueError(f"the drawing path {path!r} may not contain ':'")
    if path.startswith("/"):
        raise ValueError(f"the drawing path {path!r} must be relative")
    normal = posixpath.normpath(path)
    if normal == "." or normal == ".." or normal.startswith("../"):
        raise ValueError(f"the drawing path {path!r} must stay inside the design")
    return normal


def departure_key(drawing: str, element: str) -> str:
    """`path:element`, the identity of a departure: only the path and the cited element."""
    folded = " ".join(element.casefold().split())
    if not folded:
        raise ValueError("the departure element is empty")
    return f"{normalize_drawing(drawing)}:{folded}"


def departure_question(d: Departure, number: int) -> str:
    """The owner's question for a departure: it quotes the path and the number, never the key."""
    return (
        f"Departure #{number}: the approved drawing {d.drawing} shows {d.shows}, "
        f"but what would ship is {d.would_ship}. "
        f"Recommendation: {d.recommendation}, because {d.reason}. "
        f"Answer by the number #{number}."
    )


_NO_DRAWING_RULE: dict[str, str] = {}
for _reason, _names in (
    (
        "pending GH-766 part 3",
        "pm_interviewee pm_consultant pm_bug product_analyst_interviewer spec/pm_consultant",
    ),
    ("pending GH-766 part 4", "tech_lead spec/plan"),
    ("pending GH-766 part 5", "ux_lead_final ui_lead_final"),
    (
        "covered by tech_lead",
        "developer developer_integrator tech_lead_review tech_lead_review_light",
    ),
    ("covered by ux_lead_final", "ux_designer ux_lead tech_writer tech_writer_review"),
    (
        "covered by ui_lead_final",
        "ui_designer ui_lead pm_final spec/pm_final pm_consolidator",
    ),
    ("covered by pm_interviewee", "pm"),
    ("covered by product_analyst_interviewer", "product_analyst"),
    ("covered by pm_bug", "product_analyst_bug"),
    (
        "plan carries the rule",
        "tech_lead_critic tech_lead_ephemeral tech_lead_interviewee spec/review spec/copy_review",
    ),
    (
        "no visible change",
        "tech_lead_platform tech_lead_stack tech_lead_critic_platform "
        "tech_lead_ephemeral_platform tech_lead_ephemeral_stack "
        "tech_lead_interviewee_platform tech_lead_interviewee_stack",
    ),
    (
        "never decides what a person sees",
        "pjm plan_sizer landing_diagnoser reflector qa_performance tech_lead_carve_out",
    ),
):
    _NO_DRAWING_RULE.update(dict.fromkeys(_names.split(), _reason))

# The prompts that do not carry `{{drawing_rule}}`, each with a reason from a closed grammar.
# Only the prompt-registry test reads it: no production module consults it.
NO_DRAWING_RULE: Mapping[str, str] = MappingProxyType(_NO_DRAWING_RULE)
