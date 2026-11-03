from collections import Counter
from dataclasses import FrozenInstanceError

import pytest

from agentic.core.process.domain.experiment import (
    ARM_FLAG,
    EXPERIMENT_FILE_FLAG,
    Arm,
    InvalidExperiment,
    assign,
    conflicts,
    parse_experiment,
)
from agentic.shared.configuration import Configuration


def declaration(**override):
    data = {
        "id": "light-review",
        "assignment": "alternate",
        "arms": [
            {"name": "control", "share": 0.5, "switches": {}},
            {"name": "light", "share": 0.5, "switches": {"review_mode": "light"}},
        ],
    }
    data.update(override)
    return data


def with_arms(*arms, **override):
    return declaration(arms=list(arms), **override)


def arm(name="x", share=0.5, switches=None):
    return {"name": name, "share": share, "switches": switches or {}}


def cfg(**fields):
    return Configuration(product_dir="/tmp/p", **fields)


CONTROL = arm("control", 0.5)
LIGHT = arm("light", 0.5, {"review_mode": "light"})


def test_flag_names_are_the_ones_the_poller_and_the_cli_share():
    assert (ARM_FLAG, EXPERIMENT_FILE_FLAG) == ("--arm", "--experiment-file")


def test_a_valid_declaration_parses_to_frozen_arms_in_declaration_order():
    parsed = parse_experiment(declaration(), "experiment.yaml")
    assert parsed.id == "light-review"
    assert parsed.assignment == "alternate"
    assert parsed.seed is None
    assert [a.name for a in parsed.arms] == ["control", "light"]
    assert parsed.arms[1].switches == {"review_mode": "light"}
    with pytest.raises(FrozenInstanceError):
        setattr(parsed, "id", "other")


def test_switch_values_are_normalised_to_strings():
    parsed = parse_experiment(
        with_arms(
            arm("a", 0.5, {"skip_ephemeral": True}),
            arm("b", 0.5, {"review_mode": "light"}),
        ),
        "experiment.yaml",
    )
    assert parsed.arms[0].switches == {"skip_ephemeral": "True"}
    assert parsed.arms[1].switches == {"review_mode": "light"}


def test_a_seeded_declaration_carries_its_seed():
    parsed = parse_experiment(declaration(assignment="seeded", seed=7), "experiment.yaml")
    assert (parsed.assignment, parsed.seed) == ("seeded", 7)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param("not a mapping", "mapping", id="not-a-mapping"),
        pytest.param({k: v for k, v in declaration().items() if k != "id"}, "id", id="no-id"),
        pytest.param(declaration(id=""), "id", id="empty-id"),
        pytest.param(declaration(id=3), "id", id="id-not-a-string"),
        pytest.param(declaration(extra=1), "extra", id="unknown-top-level-key"),
        pytest.param(declaration(assignment="random"), "assignment", id="unknown-assignment"),
        pytest.param(declaration(arms=[]), "arms", id="empty-arms"),
        pytest.param(with_arms(CONTROL, arm("Bad_Name", 0.5)), "name", id="name-charset"),
        pytest.param(with_arms(CONTROL, arm("a" * 25, 0.5)), "name", id="name-too-long"),
        pytest.param(with_arms(CONTROL, arm("", 0.5)), "name", id="name-empty"),
        pytest.param(with_arms(CONTROL, dict(LIGHT, name="control")), "duplicate", id="dup-name"),
        pytest.param(with_arms(dict(CONTROL, share=0), dict(LIGHT, share=1)), "share", id="zero"),
        pytest.param(with_arms(dict(CONTROL, share=-1), dict(LIGHT, share=2)), "share", id="neg"),
        pytest.param(with_arms(dict(CONTROL, share="half"), LIGHT), "share", id="share-type"),
        pytest.param(with_arms(CONTROL, dict(LIGHT, share=0.4)), "sum", id="shares-not-one"),
        pytest.param(
            with_arms(CONTROL, dict(LIGHT, switches={"reviewer_model": "x"})),
            "reviewer_model",
            id="switch-outside-ARM_SWITCHES",
        ),
        pytest.param(
            with_arms(CONTROL, dict(LIGHT, switches={"review_mode": "medium"})),
            "review_mode",
            id="review_mode-outside-annotation",
        ),
        pytest.param(
            with_arms(CONTROL, arm("e", 0.5, {"skip_ephemeral": "true"})),
            "skip_ephemeral",
            id="skip_ephemeral-string",
        ),
        pytest.param(declaration(assignment="seeded"), "seed", id="seeded-without-seed"),
        pytest.param(declaration(assignment="seeded", seed="7"), "seed", id="seed-not-int"),
        pytest.param(declaration(assignment="seeded", seed=True), "seed", id="seed-is-a-bool"),
        pytest.param(declaration(seed=7), "seed", id="seed-under-alternate"),
        pytest.param(
            with_arms(CONTROL, arm("full", 0.5, {"review_mode": "full"})),
            "same",
            id="same-canonical-form-as-the-default",
        ),
        pytest.param(
            with_arms(
                arm("a", 0.5, {"review_mode": "light"}),
                arm("b", 0.5, {"review_mode": "light", "skip_ephemeral": False}),
            ),
            "same",
            id="same-canonical-form-explicit",
        ),
    ],
)
def test_parse_refuses_with_one_line_naming_the_source(data, reason):
    with pytest.raises(InvalidExperiment) as raised:
        parse_experiment(data, "/p/.agentic/experiment.yaml")
    message = str(raised.value)
    assert message.startswith("/p/.agentic/experiment.yaml: ")
    assert reason in message
    assert "\n" not in message


def test_arms_that_differ_only_in_the_switch_they_name_are_distinct():
    parse_experiment(
        with_arms(
            arm("a", 0.5, {"review_mode": "light"}),
            arm("b", 0.5, {"skip_ephemeral": True}),
        ),
        "experiment.yaml",
    )


def test_assign_alternates_by_deficit_and_never_runs_three_to_nothing():
    spec = parse_experiment(declaration(), "experiment.yaml")
    counts: Counter[str] = Counter()
    picks = []
    for issue in (10, 11, 12):
        picked = assign(spec, issue, counts)
        counts[picked.name] += 1
        picks.append(picked.name)
    assert picks == ["control", "light", "control"]


def test_assign_ties_go_to_declaration_order():
    spec = parse_experiment(declaration(), "experiment.yaml")
    assert assign(spec, 1, {}).name == "control"
    assert assign(spec, 1, {"control": 4, "light": 4}).name == "control"


def test_assign_follows_unequal_shares_in_the_long_run():
    spec = parse_experiment(
        with_arms(dict(CONTROL, share=0.3), dict(LIGHT, share=0.7)), "experiment.yaml"
    )
    counts: Counter[str] = Counter()
    for issue in range(100):
        counts[assign(spec, issue, counts).name] += 1
    assert counts == {"control": 30, "light": 70}


def test_assign_ignores_counts_of_names_the_declaration_lacks():
    spec = parse_experiment(declaration(), "experiment.yaml")
    assert assign(spec, 1, {"retired": 50}).name == "control"
    assert assign(spec, 1, {"retired": 50, "control": 1}).name == "light"


def test_assign_seeded_is_deterministic_per_issue():
    spec = parse_experiment(declaration(assignment="seeded", seed=3), "experiment.yaml")
    first = [assign(spec, issue, {}).name for issue in range(50)]
    assert first == [assign(spec, issue, {"control": 9}).name for issue in range(50)]
    other = parse_experiment(declaration(assignment="seeded", seed=4), "experiment.yaml")
    assert first != [assign(other, issue, {}).name for issue in range(50)]


def test_assign_seeded_matches_its_shares_on_a_large_sample():
    spec = parse_experiment(
        with_arms(dict(CONTROL, share=0.25), dict(LIGHT, share=0.75), assignment="seeded", seed=1),
        "experiment.yaml",
    )
    seen = Counter(assign(spec, issue, {}).name for issue in range(4000))
    assert abs(seen["control"] / 4000 - 0.25) < 0.03


def test_assign_returns_an_arm_of_the_declaration():
    spec = parse_experiment(declaration(), "experiment.yaml")
    assert isinstance(assign(spec, 1, {}), Arm)
    assert assign(spec, 1, {"control": 1}) is spec.arms[1]


def test_conflicts_names_the_switches_an_arm_names_and_the_env_moved():
    spec = parse_experiment(declaration(), "experiment.yaml")
    assert conflicts(spec, cfg()) == ()
    assert conflicts(spec, cfg(review_mode="light")) == ("review_mode",)


def test_conflicts_ignores_a_moved_switch_no_arm_names():
    spec = parse_experiment(declaration(), "experiment.yaml")
    assert conflicts(spec, cfg(skip_ephemeral=True)) == ()


def test_conflicts_lists_in_arm_switches_order():
    spec = parse_experiment(
        with_arms(
            arm("a", 0.5, {"skip_ephemeral": True}),
            arm("b", 0.5, {"review_mode": "light"}),
        ),
        "experiment.yaml",
    )
    assert conflicts(spec, cfg(review_mode="light", skip_ephemeral=True)) == (
        "review_mode",
        "skip_ephemeral",
    )
