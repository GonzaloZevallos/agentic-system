"""The default path stays byte for byte what it was before `plan_shape` (GH-471).

Every literal below was captured from the code before the arm existed: the snapshot, the
label, the baseline id, the rendered documents and the rendered Tech Lead prompts of a task
with no experiment, of the old `light` arm and of a control arm with `switches: {}`."""

import hashlib
from pathlib import Path

from agentic.core.measurement.domain import evaluation, metrics
from agentic.core.process.application.preflight import PLACEHOLDERS
from agentic.core.process.domain import document, roles
from agentic.shared.configuration import Configuration, experiment_snapshot


def cfg(**fields):
    return Configuration(product_dir="/tmp/p", **fields)


def test_snapshot_label_and_baseline_of_the_default_path_are_the_pre_arm_literals():
    assert experiment_snapshot(cfg(), "m") == {}
    assert metrics.arm_label({}) == "default"
    assert metrics.arm_of({"id": "GH-1"}) == "default"
    assert evaluation.baseline_id("v1", "decl", {}) == "v1+3ba23714"


def test_snapshot_label_and_baseline_of_the_light_arm_are_the_pre_arm_literals():
    snapshot = experiment_snapshot(cfg(review_mode="light"), "claude-sonnet-5")
    assert snapshot == {
        "review_mode": "light",
        "skip_ephemeral": "False",
        "reviewer_model": "claude-sonnet-5",
    }
    assert metrics.arm_label(snapshot) == "review_mode=light,skip_ephemeral=False"
    assert metrics.arm_of({"experiment": snapshot}) == "review_mode=light,skip_ephemeral=False"
    assert evaluation.baseline_id("v1", "decl", snapshot) == "v1+226aa58e"
    skipped = experiment_snapshot(cfg(skip_ephemeral=True), "m")
    assert skipped == {"review_mode": "full", "skip_ephemeral": "True", "reviewer_model": "m"}
    assert metrics.arm_label(skipped) == "review_mode=full,skip_ephemeral=True"


GOLDEN_CONTROL = """\
---
id: GH-9
title: Golden
type: null
status: open
current_role: null
flags: {}
returns: {}
return_history: []
escalated: null
escalation_class: null
questions_for_human: []
proposed_invariants: []
discard_reason: null
branch: null
pull_request: null
subtasks: {}
invariants: []
integration_brief: ''
findings: []
settled_findings: []
cost_usd: 0.0
prompts: {}
assignment:
  experiment: x
  arm: control
  switches: {}
plugins: {}
system_commit: null
runs: []
failures: []
pause_log: []
design_items: []
---

# Golden

## Original request

d
"""

BARE = {"id": "GH-9", "title": "Golden", "description": "d"}


def test_a_document_with_no_experiment_renders_as_it_did():
    digest = hashlib.sha256(document.render(BARE).encode()).hexdigest()
    assert digest == "0ab6d027b6d8acf712d381f45a1d00e6376dcd555d90ac2d64cf7b009e639737"


def test_a_control_arm_document_renders_as_it_did():
    state = {**BARE, "assignment": {"experiment": "x", "arm": "control", "switches": {}}}
    assert document.render(state) == GOLDEN_CONTROL


# sha256 of the prompt text rendered with the gate's own placeholders and a fixed date.
PROMPT_GOLDENS = {
    "tech_lead": "798280358ef5d22d836bd640c903a51e3935dc02a0a0f3b36104ea37cc18e527",
    "tech_lead_platform": "037676d249c349beedb1f59758f2c22282157dbaa99cdcad1d4da190f280e399",
}


def rendered(name, **more):
    raw = (roles.PROMPTS_DIR / f"{name}.md").read_text()
    return roles.render(raw, Path(name), **{**PLACEHOLDERS, "date": "2026-01-01", **more})


def test_the_tech_lead_prompts_render_as_they_did_under_the_default():
    for name, digest in PROMPT_GOLDENS.items():
        assert hashlib.sha256(rendered(name).text.encode()).hexdigest() == digest


def test_the_template_hash_is_the_same_for_both_arms_and_the_free_text_adds_nothing():
    from agentic.core.process.domain import rules

    assert rules.plan_shape_text("free") == ""
    for name in PROMPT_GOLDENS:
        free = rendered(name, plan_shape_rule=rules.plan_shape_text("free"))
        single = rendered(name, plan_shape_rule=rules.plan_shape_text("single"))
        assert free.hash == single.hash
        assert hashlib.sha256(free.text.encode()).hexdigest() == PROMPT_GOLDENS[name]
        assert "exactly one subtask" in single.text and "exactly one subtask" not in free.text
