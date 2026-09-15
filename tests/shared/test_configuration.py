from pathlib import Path

import pytest

from agentic.shared.configuration import Configuration, MissingProductDir

PRODUCT = {"product_dir": "/tmp/p"}


def test_defaults():
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.max_returns_per_pair == 2
    assert cfg.role_retries == 1
    assert cfg.evaluation_window == 10
    assert cfg.agentic_dir == Path("/tmp/p/.agentic")


def test_product_dir_is_required():
    # The system never writes inside its own repo (ADR-0002): no silent default.
    with pytest.raises(MissingProductDir, match="AGENTIC_PRODUCT_DIR"):
        Configuration.from_sources(None, {})


def test_product_dir_from_env():
    cfg = Configuration.from_sources(None, {"AGENTIC_PRODUCT_DIR": "/tmp/env"})
    assert cfg.product_dir == Path("/tmp/env")


def test_overrides_beat_default():
    cfg = Configuration.from_sources({"max_returns_per_pair": 5, **PRODUCT}, {})
    assert cfg.max_returns_per_pair == 5
    assert cfg.product_dir == Path("/tmp/p")


def test_env_beats_overrides():
    env = {
        "AGENTIC_MAX_RETURNS_PER_PAIR": "3",
        "AGENTIC_MODEL_OVERRIDES": '{"pm": "claude-fable-5-1"}',
    }
    cfg = Configuration.from_sources({"max_returns_per_pair": 5, **PRODUCT}, env)
    assert cfg.max_returns_per_pair == 3
    assert cfg.model_overrides == {"pm": "claude-fable-5-1"}


def test_model_for_kind_and_override():
    cfg = Configuration(model_overrides={"pm": "custom"}, product_dir=Path("/tmp/p"))
    assert cfg.model_for("pjm", "thinker") == cfg.thinker_model
    assert cfg.model_for("product_analyst", "validator") == cfg.validator_model
    assert cfg.model_for("pm", "validator") == "custom"


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


def test_role_env_carries_the_git_identity_and_the_subagent_model():
    # What every role's process gets on top of the inherited environment: the git
    # identity and the model the Agent tool hands to sub-agents (ADR-0013, addendum).
    cfg = Configuration.from_sources(PRODUCT, {})
    assert cfg.subagent_model == "claude-sonnet-5"
    assert cfg.role_env == {**cfg.git_identity, "CLAUDE_CODE_SUBAGENT_MODEL": "claude-sonnet-5"}
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
