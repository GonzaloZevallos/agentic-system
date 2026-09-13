from pathlib import Path

import pytest

from agentic.domain.configuration import Configuration, MissingProductDir

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
