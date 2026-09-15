"""Runtime configuration. Imports nothing from the project (README.md, 14).

Precedence: environment variable `AGENTIC_<FIELD>` > overrides > default. The
overrides are whatever the entry point carries: the graph's `config["configurable"]`,
nothing for the CLI. Every node reads it at the start of its run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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
    # Money cap of one run of a role that executes (README.md, 4). Exceeding it is a
    # role failure, not a return: the return limit measures quality, not money.
    developer_max_budget_usd: float = Field(default=3.0, gt=0)
    # Rounds an interview may take before the interviewer closes with what it has
    # (ADR-0014).
    interview_max_rounds: int = Field(default=4, ge=1)
    # Closed tasks PjM watches before judging a process change (README.md, 4).
    evaluation_window: int = Field(default=10, ge=1)
    # Stream each role's generation to stderr (`agentic run --verbose`).
    verbose: bool = False
    # Identity of every commit made in the product, the adapter's and the roles' that
    # execute (ADR-0011, addendum). The system's own, so a machine without `git config
    # user.*` still commits and the author says who did it.
    git_author_name: str = "agentic-system"
    git_author_email: str = "agentic-system@users.noreply.github.com"
    # Root of the product repo, the product context (README.md, 8). Required: the
    # system never writes inside its own repo (ADR-0002), so there is no sane default.
    product_dir: Path

    @classmethod
    def from_sources(
        cls, overrides: Mapping[str, Any] | None, env: Mapping[str, str]
    ) -> Configuration:
        overrides = overrides or {}
        values: dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            raw = env.get(ENV_PREFIX + name.upper())
            if raw:  # an empty variable is unset, not Path(".")
                values[name] = _from_env(raw, field.annotation)
            elif name in overrides:
                values[name] = overrides[name]
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

    def budget_for(self, role: Any) -> float | None:
        """The money cap of one run: only roles that execute have one. `role` is duck
        typed (`.executes`) because this module imports nothing from the project."""
        return self.developer_max_budget_usd if getattr(role, "executes", False) else None

    @property
    def agentic_dir(self) -> Path:
        return self.product_dir / ".agentic"

    @property
    def git_identity(self) -> dict[str, str]:
        """The identity as environment, the one form every `git` honours and that beats
        any `user.*` in a config: for the adapter's own commands and for the process
        of a role that commits."""
        return {
            "GIT_AUTHOR_NAME": self.git_author_name,
            "GIT_AUTHOR_EMAIL": self.git_author_email,
            "GIT_COMMITTER_NAME": self.git_author_name,
            "GIT_COMMITTER_EMAIL": self.git_author_email,
        }


def _from_env(raw: str, annotation: Any) -> Any:
    # Pydantic parses numbers and paths from strings; dicts need explicit JSON.
    if annotation is dict or getattr(annotation, "__origin__", None) is dict:
        return json.loads(raw)
    return raw
