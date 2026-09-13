"""Runtime configuration. Imports nothing from the project (README.md, 14).

Precedence: environment variable `AGENTIC_<FIELD>` > `config["configurable"]` > default.
Every node reads it at the start of its run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

ENV_PREFIX = "AGENTIC_"

RoleKind = Literal["thinker", "validator"]


class MissingProductDir(ValueError):
    pass


class Configuration(BaseModel):
    # Thinkers make most of the calls; validators need a model the thinker did not
    # have, which is the cheapest asymmetry to build and measure (README.md, 10).
    thinker_model: str = "claude-sonnet-5"
    validator_model: str = "claude-opus-5"
    # Per-role override, keyed by role slug. Wins over the kind mapping.
    model_overrides: dict[str, str] = Field(default_factory=dict)
    max_returns_per_pair: int = Field(default=2, ge=0)
    role_retries: int = Field(default=1, ge=0)
    # Closed tasks PjM watches before judging a process change (README.md, 4).
    evaluation_window: int = Field(default=10, ge=1)
    # Stream each role's generation to stderr (`agentic run --verbose`).
    verbose: bool = False
    # Root of the product repo, the product context (README.md, 8). Required: the
    # system never writes inside its own repo (ADR-0002), so there is no sane default.
    product_dir: Path

    @classmethod
    def from_runnable_config(cls, config: dict[str, Any] | None = None) -> Configuration:
        configurable = (config or {}).get("configurable", {}) or {}
        values: dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            env = os.environ.get(ENV_PREFIX + name.upper())
            if env:  # an empty variable is unset, not Path(".")
                values[name] = _from_env(env, field.annotation)
            elif name in configurable:
                values[name] = configurable[name]
        if "product_dir" not in values:
            raise MissingProductDir(
                "product_dir is required: set AGENTIC_PRODUCT_DIR or pass "
                "config['configurable']['product_dir'] pointing at the product repo"
            )
        return cls(**values)

    def model_for(self, slug: str, kind: RoleKind) -> str:
        if slug in self.model_overrides:
            return self.model_overrides[slug]
        return self.thinker_model if kind == "thinker" else self.validator_model

    @property
    def agentic_dir(self) -> Path:
        return self.product_dir / ".agentic"


def _from_env(raw: str, annotation: Any) -> Any:
    # Pydantic parses numbers and paths from strings; dicts need explicit JSON.
    if annotation is dict or getattr(annotation, "__origin__", None) is dict:
        return json.loads(raw)
    return raw
