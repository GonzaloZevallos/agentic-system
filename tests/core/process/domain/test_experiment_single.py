"""The `single` value of the `review_mode` switch (GH-473): the experiment validator takes it,
a configuration with any `max_concurrent_roles` constructs under every mode, and the two
shadow fields are no arm switch: they never reach the experiment snapshot."""

import pytest

from agentic.core.process.domain.experiment import InvalidExperiment, parse_experiment
from agentic.core.process.domain.roles import TECH_LEAD_REVIEWER, reviewer_for
from agentic.shared.configuration import ARM_SWITCHES, Configuration, experiment_snapshot


def declaration(mode):
    return {
        "id": "single-review",
        "assignment": "alternate",
        "arms": [
            {"name": "control", "share": 0.5, "switches": {}},
            {"name": "single", "share": 0.5, "switches": {"review_mode": mode}},
        ],
    }


def test_the_validator_accepts_single():
    parsed = parse_experiment(declaration("single"), "exp.yaml")
    assert parsed.arms[1].switches == {"review_mode": "single"}


def test_the_validator_still_rejects_a_value_that_is_no_mode():
    with pytest.raises(InvalidExperiment, match="review_mode must be one of"):
        parse_experiment(declaration("shadow"), "exp.yaml")


@pytest.mark.parametrize("mode", ["full", "light", "single"])
def test_any_mode_constructs_with_one_role_slot(mode):
    cfg = Configuration(product_dir="/tmp/p", review_mode=mode, max_concurrent_roles=1)
    assert cfg.review_mode == mode and cfg.shadow_parallel == 2 and cfg.shadow_grace_s == 180


def test_the_shadow_fields_are_not_arm_switches_and_stay_out_of_the_snapshot():
    assert "shadow_parallel" not in ARM_SWITCHES and "shadow_grace_s" not in ARM_SWITCHES
    plain = Configuration(product_dir="/tmp/p", review_mode="single")
    tuned = Configuration(
        product_dir="/tmp/p", review_mode="single", shadow_parallel=5, shadow_grace_s=0
    )
    assert experiment_snapshot(plain, "m") == experiment_snapshot(tuned, "m")
    assert "shadow" not in str(experiment_snapshot(tuned, "m"))


def test_single_gets_the_full_reviewer_for_every_subtask():
    assert reviewer_for({}, "api", mode="single").slug == TECH_LEAD_REVIEWER.slug
