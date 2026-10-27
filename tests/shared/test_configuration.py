from pathlib import Path

import pytest

from agentic.shared.configuration import (
    Configuration,
    MissingProductDir,
    experiment_snapshot,
    state_file_for,
)

PRODUCT = {"product_dir": "/tmp/p"}


def test_state_file_for_names_the_tasks_own_store():
    assert state_file_for("GH-1") == Path(".agentic/state/GH-1.sqlite")


@pytest.mark.parametrize("bad_id", ["", "a/b", "a\\b", "..", "x/../y"])
def test_state_file_for_rejects_an_id_that_could_escape_state_dir(bad_id):
    with pytest.raises(ValueError):
        state_file_for(bad_id)


def test_max_concurrent_runs_defaults_to_one():
    # The human's own call (GH-137): one run at a time, because two in parallel hit
    # the account's own 429 and clashed merging.
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.max_concurrent_runs == 1
    raised = Configuration.from_sources(PRODUCT, {"AGENTIC_MAX_CONCURRENT_RUNS": "3"})
    assert raised.max_concurrent_runs == 3
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), max_concurrent_runs=0)


def test_no_board_project_means_no_board():
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.board_project is None
    assert cfg.board_owner_and_number is None


def test_board_project_reads_owner_and_number_from_the_environment():
    cfg = Configuration.from_sources(PRODUCT, {"AGENTIC_BOARD_PROJECT": "GonzaloZevallos/3"})
    assert cfg.board_owner_and_number == ("GonzaloZevallos", 3)


@pytest.mark.parametrize("bad", ["3", "owner/", "/3", "owner/0", "owner/x", "a/b/3", "o wner/3"])
def test_board_project_rejects_anything_but_owner_slash_number(bad):
    with pytest.raises(ValueError, match="board_project"):
        Configuration.from_sources(PRODUCT, {"AGENTIC_BOARD_PROJECT": bad})


def test_spend_pacing_is_off_by_default_and_the_pace_defaults_to_35():
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.spend_pacing is False
    assert cfg.spend_pace_usd_per_h == 35.0
    assert cfg.max_concurrent_runs == 1


def test_spend_pacing_and_pace_read_their_environment_overrides():
    cfg = Configuration.from_sources(
        PRODUCT,
        {"AGENTIC_SPEND_PACING": "true", "AGENTIC_SPEND_PACE_USD_PER_H": "42.5"},
    )
    assert cfg.spend_pacing is True
    assert cfg.spend_pace_usd_per_h == 42.5


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_spend_pace_usd_per_h_must_be_positive(bad):
    with pytest.raises(ValueError):
        Configuration.from_sources(PRODUCT, {"AGENTIC_SPEND_PACE_USD_PER_H": bad})
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), spend_pace_usd_per_h=float(bad))


def test_run_registry_dir_defaults_under_xdg_state_home(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/state")
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.run_registry_dir == Path("/tmp/state/agentic/runs")


def test_run_registry_dir_falls_back_to_the_home_dot_local_state(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/t"))
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.run_registry_dir == Path("/home/t/.local/state/agentic/runs")


def test_run_registry_dir_is_overridable_so_tests_never_touch_the_real_one():
    # `tests/conftest.py` of a future subtask redirects every test through this same
    # env override (invariant: no test writes to the real run registry).
    cfg = Configuration.from_sources(PRODUCT, {"AGENTIC_RUN_REGISTRY_DIR": "/tmp/fake-runs"})
    assert cfg.run_registry_dir == Path("/tmp/fake-runs")


def test_defaults():
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.max_changes_requested_per_pair == 2
    assert cfg.role_retries == 1
    assert cfg.evaluation_window == 10
    assert cfg.agentic_dir == Path("/tmp/p/.agentic")


def test_stall_timeout_defaults_to_forty_minutes_and_reads_its_environment():
    assert Configuration.from_sources(PRODUCT, {}).stall_timeout_s == 2400
    env = {"AGENTIC_STALL_TIMEOUT_S": "900"}
    assert Configuration.from_sources(PRODUCT, env).stall_timeout_s == 900


@pytest.mark.parametrize("bad", ["599", "0", "-1"])
def test_stall_timeout_below_the_floor_is_rejected_on_load(bad):
    with pytest.raises(ValueError, match="stall_timeout_s"):
        Configuration.from_sources(PRODUCT, {"AGENTIC_STALL_TIMEOUT_S": bad})


def test_stall_timeout_at_the_floor_is_accepted():
    cfg = Configuration.from_sources(PRODUCT, {"AGENTIC_STALL_TIMEOUT_S": "600"})
    assert cfg.stall_timeout_s == 600


def test_product_dir_is_required():
    # The system never writes inside its own repo (ADR-0002): no silent default.
    with pytest.raises(MissingProductDir, match="AGENTIC_PRODUCT_DIR"):
        Configuration.from_sources(None, {})


def test_product_dir_from_env():
    cfg = Configuration.from_sources(None, {"AGENTIC_PRODUCT_DIR": "/tmp/env"})
    assert cfg.product_dir == Path("/tmp/env")


def test_overrides_beat_default():
    cfg = Configuration.from_sources({"max_changes_requested_per_pair": 5, **PRODUCT}, {})
    assert cfg.max_changes_requested_per_pair == 5
    assert cfg.product_dir == Path("/tmp/p")


def test_env_beats_overrides():
    env = {
        "AGENTIC_MAX_CHANGES_REQUESTED_PER_PAIR": "3",
        "AGENTIC_MODEL_OVERRIDES": '{"pm": "claude-fable-5-1"}',
    }
    cfg = Configuration.from_sources({"max_changes_requested_per_pair": 5, **PRODUCT}, env)
    assert cfg.max_changes_requested_per_pair == 3
    assert cfg.model_overrides == {"pm": "claude-fable-5-1"}


def test_model_for_kind_and_override():
    cfg = Configuration(model_overrides={"pm": "custom"}, product_dir=Path("/tmp/p"))
    assert cfg.model_for("pjm", "actor") == cfg.actor_model
    assert cfg.model_for("product_analyst", "verifier") == cfg.verifier_model
    assert cfg.model_for("pm", "verifier") == "custom"


def test_empty_env_is_unset():
    # `AGENTIC_PRODUCT_DIR=` must not silently become Path(".") (ADR-0002).
    with pytest.raises(MissingProductDir):
        Configuration.from_sources(None, {"AGENTIC_PRODUCT_DIR": ""})


def test_counters_have_lower_bounds():
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), role_retries=-1)
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), evaluation_window=0)


def test_the_money_cap_and_the_interview_rounds_have_defaults():
    # One cap for every role, not only the one that executes: a review with sub-agents
    # spends dollars without executing anything (ADR-0004, second addendum).
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.max_budget_usd == 10.0
    assert cfg.interview_max_rounds == 4
    lower = Configuration.from_sources(PRODUCT, {"AGENTIC_MAX_BUDGET_USD": "4"})
    assert lower.max_budget_usd == 4.0


def test_the_task_cap_is_one_flat_ceiling_with_no_formula_fields():
    # One flat ceiling instead of a formula over the plan (ADR-0004, 2026-10-05
    # addendum); the human's own cap for a resumed run is unset unless given.
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.task_max_budget_usd == 250.0 and cfg.task_budget_usd is None
    assert "task_budget_base_usd" not in Configuration.model_fields
    assert "task_budget_per_subtask_usd" not in Configuration.model_fields
    env = {"AGENTIC_TASK_MAX_BUDGET_USD": "50", "AGENTIC_TASK_BUDGET_USD": "180.5"}
    lower = Configuration.from_sources(PRODUCT, env)
    assert lower.task_max_budget_usd == 50.0 and lower.task_budget_usd == 180.5
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), task_max_budget_usd=0)
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), task_budget_usd=0)


def test_the_old_formula_env_names_are_ignored_not_rejected():
    env = {
        "AGENTIC_PRODUCT_DIR": "/tmp/p",
        "AGENTIC_TASK_BUDGET_BASE_USD": "30",
        "AGENTIC_TASK_BUDGET_PER_SUBTASK_USD": "15",
    }
    cfg = Configuration.from_sources({}, env)
    assert cfg.task_max_budget_usd == 250.0


def test_role_env_carries_the_git_identity_and_the_subagent_model():
    # What every role's process gets on top of the inherited environment: the git
    # identity and the model the Agent tool hands to sub-agents (ADR-0013, addendum),
    # forced over the model an agent type or a call names (issue #19).
    cfg = Configuration.from_sources(PRODUCT, {})
    subagent_model = Configuration.model_fields["subagent_model"].default
    assert cfg.subagent_model == subagent_model
    assert cfg.role_env == {
        **cfg.git_identity,
        "CLAUDE_CODE_SUBAGENT_MODEL": subagent_model,
        "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
    }
    custom = Configuration.from_sources(PRODUCT, {"AGENTIC_SUBAGENT_MODEL": "haiku"})
    assert custom.role_env["CLAUDE_CODE_SUBAGENT_MODEL"] == "haiku"


def test_git_identity_is_the_environment_every_git_honours():
    # One helper for the git adapter and for the roles that execute: the four
    # variables win over any `user.*` in the product's config.
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.git_identity == {
        "GIT_AUTHOR_NAME": "agentic-system",
        "GIT_AUTHOR_EMAIL": "agentic-system@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "agentic-system",
        "GIT_COMMITTER_EMAIL": "agentic-system@users.noreply.github.com",
    }
    custom = Configuration(
        product_dir=Path("/tmp/p"), git_author_name="bot", git_author_email="b@x"
    )
    assert custom.git_identity["GIT_COMMITTER_NAME"] == "bot"
    assert custom.git_identity["GIT_AUTHOR_EMAIL"] == "b@x"


def test_review_mode_and_skip_ephemeral_default_to_todays_path():
    # Both defaults reproduce today's path (issue #28): no other module reads either
    # field in this subtask, only that the switch itself is off by default.
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.review_mode == "full"
    assert cfg.skip_ephemeral is False


def test_review_mode_and_skip_ephemeral_read_from_env():
    env = {"AGENTIC_REVIEW_MODE": "light", "AGENTIC_SKIP_EPHEMERAL": "1"}
    cfg = Configuration.from_sources(PRODUCT, env)
    assert cfg.review_mode == "light"
    assert cfg.skip_ephemeral is True


def test_review_mode_rejects_a_value_outside_the_closed_set():
    # The Literal is the guard: an operator's typo must fail loudly, not run some
    # silent third arm nobody built.
    with pytest.raises(ValueError):
        Configuration.from_sources(PRODUCT, {"AGENTIC_REVIEW_MODE": "medium"})


def test_experiment_snapshot_is_empty_on_the_default_path():
    # Omit-at-default (Tech Lead plan): with both switches at their default, the
    # helper the other two arms call returns nothing to write.
    cfg = Configuration.from_sources(PRODUCT, {})
    assert experiment_snapshot(cfg, "claude-opus-5") == {}


def test_experiment_snapshot_carries_all_three_keys_verbatim_and_in_order():
    cfg = Configuration.from_sources(PRODUCT, {"AGENTIC_REVIEW_MODE": "light"})
    snapshot = experiment_snapshot(cfg, "claude-sonnet-5")
    assert list(snapshot) == ["review_mode", "skip_ephemeral", "reviewer_model"]
    assert snapshot == {
        "review_mode": "light",
        "skip_ephemeral": "False",
        "reviewer_model": "claude-sonnet-5",
    }


def test_experiment_snapshot_is_non_empty_when_only_skip_ephemeral_moves():
    # `reviewer_model` travels even though only the other switch moved: the resolved
    # model is recorded, not only the two switch keys (Tech Lead plan invariant).
    cfg = Configuration.from_sources(PRODUCT, {"AGENTIC_SKIP_EPHEMERAL": "1"})
    snapshot = experiment_snapshot(cfg, "claude-opus-5")
    assert snapshot == {
        "review_mode": "full",
        "skip_ephemeral": "True",
        "reviewer_model": "claude-opus-5",
    }


def test_decided_cap_is_on_by_default_and_is_not_an_arm():
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.decided_cap is True
    off = Configuration.from_sources(PRODUCT, {"AGENTIC_DECIDED_CAP": "0"})
    assert off.decided_cap is False
    # A switch for the human's counterfactual, never a label: the snapshot is blind to it.
    assert experiment_snapshot(off, "claude-sonnet-5") == {}


def test_the_perf_settings_default_and_read_their_environment():
    cfg = Configuration.from_sources(PRODUCT, {})
    assert (cfg.perf_timeout_s, cfg.perf_node_timeout_s) == (1800, 3600)
    assert (cfg.perf_lock_wait_s, cfg.perf_spread_max) == (1800, 0.25)
    env = {"AGENTIC_PERF_TIMEOUT_S": "30", "AGENTIC_PERF_SPREAD_MAX": "0.1"}
    cfg = Configuration.from_sources(PRODUCT, env)
    assert (cfg.perf_timeout_s, cfg.perf_spread_max) == (30, 0.1)


@pytest.mark.parametrize("name", ["perf_timeout_s", "perf_node_timeout_s", "perf_spread_max"])
@pytest.mark.parametrize("bad", [0, -1])
def test_the_perf_limits_must_be_positive(name, bad):
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), **{name: bad})


def test_perf_lock_wait_may_be_zero_but_not_negative():
    assert Configuration(product_dir=Path("/tmp/p"), perf_lock_wait_s=0).perf_lock_wait_s == 0
    with pytest.raises(ValueError):
        Configuration(product_dir=Path("/tmp/p"), perf_lock_wait_s=-1)


def test_perf_lock_path_defaults_under_xdg_state_home_outside_every_product(monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/state")
    assert Configuration.from_sources(PRODUCT, {}).perf_lock_path == Path(
        "/tmp/state/agentic/perf.lock"
    )
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setattr(Path, "home", lambda: Path("/home/t"))
    assert Configuration.from_sources(PRODUCT, {}).perf_lock_path == Path(
        "/home/t/.local/state/agentic/perf.lock"
    )
