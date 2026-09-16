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
# The graph's checkpointer, relative to the product root, outside version control
# (README.md, 8; ADR-0003).
STATE_FILE = Path(".agentic/state.sqlite")

RoleKind = Literal["actor", "verifier"]


class MissingProductDir(ValueError):
    pass


class Configuration(BaseModel):
    # Actors make most of the calls; verifiers need a model the actor did not
    # have, which is the cheapest asymmetry to build and measure (README.md, 10).
    actor_model: str = "claude-sonnet-5"
    verifier_model: str = "claude-opus-5"
    # Per-role override, keyed by role slug. Wins over the kind mapping.
    model_overrides: dict[str, str] = Field(default_factory=dict)
    max_returns_per_pair: int = Field(default=2, ge=0)
    role_retries: int = Field(default=1, ge=0)
    # Money cap of one attempt of any role (README.md, 4): a safety net against a
    # runaway role, well above what a subtask costs (3-4 USD in GH-12), not a work
    # budget. Every role gets it, not only the ones that execute: a review with
    # sub-agents spent 10 USD a pass in GH-12 without running a thing (ADR-0004,
    # second addendum). Exceeding it is a role failure, not a return: the return limit
    # measures quality, not money.
    max_budget_usd: float = Field(default=10.0, gt=0)
    # Money cap of the whole run, checked before every attempt of any role: reached,
    # the task escalates and pauses instead of spending more (ADR-0004, third and fourth
    # addenda). It grows with the plan: the base until there is one, then the base plus
    # a share per subtask, the integration counted, never over the ceiling. A fixed 100
    # cut run 4 of GH-12 one step from its pull request, with seven subtasks where five
    # were expected. The numbers come from that run and are recalibrated after the first
    # run without the MCP leak (issue #18), which should cost 35-45 USD. Parallel
    # branches see the total at the fan-out, so the overrun is bounded by the attempt cap.
    task_budget_base_usd: float = Field(default=30.0, gt=0)
    task_budget_per_subtask_usd: float = Field(default=15.0, ge=0)
    task_max_budget_usd: float = Field(default=250.0, gt=0)
    # The cap the human sets for one run, `agentic resume --task-budget`: it raises the
    # cap in force above the formula and the ceiling, never lowers it.
    task_budget_usd: float | None = Field(default=None, gt=0)
    # The model of every sub-agent a role spawns through the Agent tool, the two of
    # the `code-review` skill among them. They read and summarise for a parent that
    # keeps its own model; on the parent's model they cost 5-10 USD a review
    # (ADR-0013, addendum). Forced: an agent type whose definition names a model
    # (`Explore` inherits the parent's) would otherwise ignore it (issue #19).
    subagent_model: str = "claude-sonnet-5"
    # How long one product check may run inside the presubmit before it counts as
    # failed (issue #22). The presubmit's hook waits for all of them.
    check_timeout_s: float = Field(default=600.0, gt=0)
    # Roles that may run at once across the whole process (ADR-0004, fifth addendum).
    # With the review inside each branch of the fan-out, a wave of four subtasks puts
    # four Developers and four reviewers in flight, each reviewer with its own
    # sub-agents. A 429 arrives as a role failure and two of those escalate the task,
    # which is dearer than waiting for a slot.
    max_concurrent_roles: int = Field(default=4, ge=1)
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
        return self.actor_model if kind == "actor" else self.verifier_model

    @property
    def agentic_dir(self) -> Path:
        return self.product_dir / ".agentic"

    @property
    def state_file(self) -> Path:
        return self.product_dir / STATE_FILE

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

    @property
    def role_env(self) -> dict[str, str]:
        """What every role's process gets on top of the inherited environment: the git
        identity and the sub-agent model, which Claude Code reads from
        `CLAUDE_CODE_SUBAGENT_MODEL` for every Agent tool call, skills included. Alone,
        the variable is a default: an agent type that names its model in its definition
        or a call that passes `model` wins over it, and that is how 5 of 10 review passes
        ran `Explore` on Opus (issue #19). `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` drops both
        overrides, so the variable decides for every sub-agent."""
        return {
            **self.git_identity,
            "CLAUDE_CODE_SUBAGENT_MODEL": self.subagent_model,
            "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
        }


def _from_env(raw: str, annotation: Any) -> Any:
    # Pydantic parses numbers and paths from strings; dicts need explicit JSON.
    if annotation is dict or getattr(annotation, "__origin__", None) is dict:
        return json.loads(raw)
    return raw
