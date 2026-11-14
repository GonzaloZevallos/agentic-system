"""The default path stays byte for byte what it was before `plan_shape` (GH-471).

Every literal below was captured from the code before the arm existed (master with the
plan-review switch, GH-472, and without `plan_shape`): the snapshot, the
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
        "plan_review": "interview",
        "reviewer_model": "claude-sonnet-5",
    }
    light = "review_mode=light,skip_ephemeral=False,plan_review=interview"
    assert metrics.arm_label(snapshot) == light
    assert metrics.arm_of({"experiment": snapshot}) == light
    assert evaluation.baseline_id("v1", "decl", snapshot) == "v1+941e2927"
    skipped = experiment_snapshot(cfg(skip_ephemeral=True), "m")
    assert skipped == {
        "review_mode": "full",
        "skip_ephemeral": "True",
        "plan_review": "interview",
        "reviewer_model": "m",
    }
    assert (
        metrics.arm_label(skipped) == "review_mode=full,skip_ephemeral=True,plan_review=interview"
    )


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
# GH-529 rewrote the two sentences about the estimate floors in both Tech Lead prompts, and
# GH-535 the sentence about a matrix job's id in `ci_required`, so these two moved twice;
# GH-592 added the size budget paragraph and the three size fields to both, so they moved a
# third time; GH-592 split-depth reworded the `### Parts` step (a part splits again, no
# build-whole sentence) and moved them a fourth; everything else about the default path is
# unchanged. GH-591 part 2 added the `promote` line to the Output list of both, so they
# moved a fifth time; GH-610 added the `api_break` field to the Output list of both and
# moved them a sixth; GH-619 rewrote `TRACKER_WITH_PARTS` (the system re-points) and moved
# them a seventh; GH-663 taught `tracker_items` in place of `tracker_checklist` and moved them
# an eighth; GH-691 appended the max-declarable sentence to the size budget paragraph and
# moved them a ninth time; GH-689 added the `comment` kind to `tracker_items` and moved them a
# tenth; GH-690 added the `open_issue` kind and moved them an eleventh.
PROMPT_GOLDENS = {
    "tech_lead": "2f037ed205fcfca477e747c1f7ad458db7ec940884970d751651ea35315cc223",
    "tech_lead_platform": "0beb3df2f732c8503c42f2a42daaeaee5068d174820b312acac0b3e6ec262c4f",
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
