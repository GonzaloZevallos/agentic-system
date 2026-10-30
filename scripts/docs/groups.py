"""Which phase each node of the role graph belongs to, as data only (GH-430, ADR-0076).

`render.role_graph_pages` splits the role graph into one page per phase from this table. One
entry per line, sorted: a new node is one line in the phase it belongs to. A node of the graph
that is in no phase fails the generator by name, with the line to add; there is no default
group (ADR-0076).

`CLUSTERS` does the same for the modules of the process package diagrams (GH-430 part 2).
`CLASS_GROUPING` says how the class pages of each layer are grouped and `MEASUREMENT_CLUSTERS`
holds the clusters of the one layer of `measurement` that needs them (GH-430 part 4).
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

# The cluster each module of `agentic/core/process/domain` and `.../application` belongs to,
# keyed by the layer-qualified module name. `packages.build_pages` draws one page per cluster
# from this table. One entry per line, sorted: a new module is one line. A module with no entry
# fails the generator by name, with the line to paste; there is no default cluster (ADR-0076).
CLUSTERS: Mapping[str, str] = {
    "application.adr_collision": "landing",
    "application.archive": "lifecycle",
    "application.board_sync": "lifecycle",
    "application.checkouts": "support",
    "application.ci_ref_sweep": "checks",
    "application.ci_review": "checks",
    "application.classify": "definition",
    "application.close_issues": "landing",
    "application.closed": "landing",
    "application.copy": "definition",
    "application.coverage_guard": "checks",
    "application.define": "definition",
    "application.deliver": "build",
    "application.delivered": "build",
    "application.design": "definition",
    "application.develop": "build",
    "application.diagnose": "definition",
    "application.discard": "definition",
    "application.escalate": "build",
    "application.final_validation": "definition",
    "application.full_checks": "checks",
    "application.integrate": "build",
    "application.interview": "definition",
    "application.landing": "landing",
    "application.landing_fix": "landing",
    "application.landing_flakes": "landing",
    "application.landing_plan": "landing",
    "application.landing_slice": "landing",
    "application.landing_sync": "landing",
    "application.limits": "support",
    "application.merge": "landing",
    "application.owner_rules": "reflection",
    "application.perf": "checks",
    "application.plan": "build",
    "application.poll": "lifecycle",
    "application.ports": "support",
    "application.pre_pr_fix": "build",
    "application.preflight": "support",
    "application.presubmit": "checks",
    "application.qa_performance": "definition",
    "application.reflect": "reflection",
    "application.reflect_run": "reflection",
    "application.registration": "lifecycle",
    "application.release": "support",
    "application.restore": "lifecycle",
    "application.resume": "lifecycle",
    "application.review": "build",
    "application.run_check": "checks",
    "application.run_role": "support",
    "application.schedule": "lifecycle",
    "application.schema_guard": "checks",
    "application.scratch_sweep": "lifecycle",
    "application.split": "build",
    "application.stack_review": "reflection",
    "application.tracker_only": "build",
    "application.validate_prd": "definition",
    "domain.account_reset": "machine",
    "domain.adr_numbers": "landing",
    "domain.board": "landing",
    "domain.boot": "machine",
    "domain.check_results": "checks",
    "domain.check_schedule": "checks",
    "domain.ci": "checks",
    "domain.copy": "roles",
    "domain.cost_history": "landing",
    "domain.coverage": "checks",
    "domain.denials": "roles",
    "domain.design": "roles",
    "domain.docs": "roles",
    "domain.document": "state",
    "domain.landing": "landing",
    "domain.legacy_names": "state",
    "domain.master_gain": "checks",
    "domain.performance": "checks",
    "domain.plan_settlement": "landing",
    "domain.plan_view": "landing",
    "domain.playbook": "playbook",
    "domain.playbook_candidates": "playbook",
    "domain.presubmit": "checks",
    "domain.pull_request": "landing",
    "domain.reaping": "machine",
    "domain.returns": "state",
    "domain.roles": "roles",
    "domain.rules": "state",
    "domain.run_check": "checks",
    "domain.schema_guard": "roles",
    "domain.schemas": "roles",
    "domain.scratch": "machine",
    "domain.signals": "state",
    "domain.stack_review": "roles",
    "domain.stack_signals": "roles",
    "domain.stall": "machine",
    "domain.suppressions": "checks",
    "domain.system_lessons": "playbook",
    "domain.task": "state",
    "domain.turn_notice": "roles",
}

# How the class pages of each layer are grouped, keyed `<context>/<layer>`: `clusters` takes the
# cluster of each module from `CLUSTERS` (process) or `MEASUREMENT_CLUSTERS` (measurement), `kind`
# one page per adapter kind with the loose modules in `toplevel`, `layer` the whole layer in one
# page (it fails when the layer has more classes than a diagram holds). One entry per line,
# sorted; a layer with no entry fails the generator by name, with the line to paste.
CLASS_GROUPING: Mapping[str, str] = {
    "knowledge/adapters": "layer",
    "knowledge/domain": "layer",
    "measurement/adapters": "layer",
    "measurement/application": "layer",
    "measurement/domain": "clusters",
    "process/adapters": "kind",
    "process/application": "clusters",
    "process/domain": "clusters",
}

# The cluster of each module of `agentic/core/measurement/domain`, keyed like `CLUSTERS`
# (`domain.<module>`): the layer has 26 public classes, more than a diagram holds. One entry per
# line, sorted; a module with a class and no entry fails the generator by name.
MEASUREMENT_CLUSTERS: Mapping[str, str] = {
    "domain.dashboards": "langfuse",
    "domain.evaluation": "scorecard",
    "domain.findings_report": "reports",
    "domain.playbook_window": "reports",
    "domain.role_usage": "reports",
    "domain.scorecard": "scorecard",
    "domain.task_scores": "langfuse",
}
