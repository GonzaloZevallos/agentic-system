"""Which phase each node of the role graph belongs to, as data only (GH-430, ADR-0076).

`render.role_graph_pages` splits the role graph into one page per phase from this table. One
entry per line, sorted: a new node is one line in the phase it belongs to. A node of the graph
that is in no phase fails the generator by name, with the line to add; there is no default
group (ADR-0076).
"""

from __future__ import annotations

from collections.abc import Mapping

PHASES: Mapping[str, tuple[str, ...]] = {
    "build": (
        "integrate",
        "pipeline",
    ),
    "classify": (
        "__start__",
        "classify",
    ),
    "define": (
        "consolidate",
        "define",
        "design_review",
        "diagnose",
        "discard",
        "ui_screens",
        "ux_flow",
        "ux_walk",
        "validate",
        "write_copy",
    ),
    "deliver": (
        "__end__",
        "deliver",
    ),
    "plan": (
        "plan",
        "split",
        "tracker_only",
    ),
    "review": (
        "copy_review",
        "final_validation",
        "fix_important",
        "full_checks",
        "owner_rules",
        "qa_performance",
    ),
}

# The nodes that escalate to a human and resume from the answer: a group of their own.
ESCALATION: tuple[str, ...] = (
    "escalate",
    "pause",
)
ESCALATION_TITLE = "Escalado y reanudación"

# Reserved for part 2 of GH-430 (the class diagrams); empty until then.
CLUSTERS: Mapping[str, tuple[str, ...]] = {}
