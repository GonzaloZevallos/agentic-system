"""Runtime configuration. Imports nothing from the project (README.md, 14).

Precedence: environment variable `AGENTIC_<FIELD>` > overrides > default. The
overrides are whatever the entry point carries: the graph's `config["configurable"]`,
nothing for the CLI. Every node reads it at the start of its run.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ENV_PREFIX = "AGENTIC_"
# The legacy, shared checkpointer file, relative to the product root, outside version
# control (README.md, 8; ADR-0003). A task with rows already there keeps using it until
# it is restarted; every other task gets its own file under `STATE_DIR` (ADR-0003,
# addendum 2026-10-04).
STATE_FILE = Path(".agentic/state.sqlite")
# One checkpoint store per task, relative to the product root, outside version control:
# `STATE_DIR / f"{task_id}.sqlite"` (`state_file_for`). Concurrent runs on one product
# never contend for the same file (ADR-0003, addendum 2026-10-04).
STATE_DIR = Path(".agentic/state")
# Where every invocation's log lives, relative to the product root, outside version
# control: one file per invocation under this, named `<task>-<UTC timestamp>.log`
# (README.md, 14; issue #33).
LOGS_DIR = Path(".agentic/logs")

_BOARD_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[1-9][0-9]*")
RoleKind = Literal["actor", "verifier"]
# What `max_parallel_subtasks` resolves to when unset, before the role-slot cap (GH-364).
DEFAULT_PARALLEL_SUBTASKS = 4


class MissingProductDir(ValueError):
    pass


class Configuration(BaseModel):
    # Actors make most of the calls; verifiers need a model the actor did not
    # have, which is the cheapest asymmetry to build and measure (README.md, 10).
    actor_model: str = "claude-sonnet-5-5"
    verifier_model: str = "claude-opus-5-5"
    # Per-role override, keyed by role slug. Wins over the kind mapping.
    model_overrides: dict[str, str] = Field(default_factory=dict)
    max_changes_requested_per_pair: int = Field(default=2, ge=0)
    role_retries: int = Field(default=1, ge=0)
    # Money cap of one attempt of any role (README.md, 4): a safety net against a
    # runaway role, well above what a subtask costs (3-4 USD in GH-12), not a work
    # budget. Every role gets it, not only the ones that execute: a review with
    # sub-agents spent 10 USD a pass in GH-12 without running a thing (ADR-0004,
    # second addendum). Exceeding it is a role failure, not a return: the return limit
    # measures quality, not money.
    max_budget_usd: float = Field(default=10.0, gt=0)
    # What one attempt of the reflector may cost (ADR-0043): it reads a closed task's
    # returns and proposes lessons, nothing that needs the 10 above. `reflect` also stops
    # at `reflect_max_tasks_per_run` tasks per invocation, so the most one run spends is
    # that many tasks times two attempts times this.
    reflect_max_budget_usd: float = Field(default=1.0, gt=0)
    # How many closed tasks one `agentic reflect` invocation reflects on (ADR-0043); the
    # rest wait for the next run, so one invocation never costs more than this times two
    # attempts times the cap above.
    reflect_max_tasks_per_run: int = Field(default=5, ge=1)
    # Money cap of the whole run, checked before every attempt of any role: reached,
    # the task escalates and pauses instead of spending more (ADR-0004, third addendum).
    # `task_max_budget_usd` is the flat task cap, the same for any plan size (ADR-0004,
    # 2026-10-05 addendum). Parallel branches see the total at the fan-out, so the
    # overrun is bounded by the attempt cap.
    task_max_budget_usd: float = Field(default=250.0, gt=0)
    # The cap the human sets for one run, `agentic resume --task-budget`: it raises the
    # cap in force above the flat ceiling, never lowers it.
    task_budget_usd: float | None = Field(default=None, gt=0)
    # The model of every sub-agent a role spawns through the Agent tool, the two of
    # the `code-review` skill among them. They read and summarise for a parent that
    # keeps its own model; on the parent's model they cost 5-10 USD a review
    # (ADR-0013, addendum). Forced: an agent type whose definition names a model
    # (`Explore` inherits the parent's) would otherwise ignore it (issue #19).
    subagent_model: str = "claude-sonnet-5-5"
    # How long one product check may run inside the presubmit before it counts as
    # failed (issue #22). The presubmit's hook waits for all of them.
    check_timeout_s: float = Field(default=600.0, gt=0)
    # How long a role attempt may go with no sign of life before the watchdog kills it
    # and fails it as a stall (GH-299, ADR-0057): above the longest legitimate silence,
    # which the in-flight hook counter and the pending-tool rule cover on their own, so
    # no cross-check against `check_timeout_s`. The floor is `domain.stall.STALL_FLOOR_S`
    # (600), written out because `shared` imports nothing of the project.
    stall_timeout_s: float = Field(default=2400.0, ge=600.0)
    # Roles that may run at once across the whole process (ADR-0004, fifth addendum).
    # With the review inside each branch of the fan-out, a wave of four subtasks puts
    # four Developers and four reviewers in flight, each reviewer with its own
    # sub-agents. A 429 arrives as a role failure and two of those escalate the task,
    # which is dearer than waiting for a slot.
    max_concurrent_roles: int = Field(default=4, ge=1)
    # Subtasks one task runs at once (GH-364). Unset resolves to `min(4, max_concurrent_roles)`
    # (`parallel_subtasks`): 4 is the default until the machine-wide scheduler (#361) makes a
    # higher value safe. Only an explicit value above `max_concurrent_roles` is rejected, so
    # no environment that was valid before this field is refused, and one role per subtask
    # in flight means a review never waits for a slot behind a new Developer.
    max_parallel_subtasks: int | None = Field(default=None, ge=1)
    # Rounds an interview may take before the interviewer closes with what it has
    # (ADR-0014).
    interview_max_rounds: int = Field(default=4, ge=1)
    # Whether a request that carries `## Decided` runs its interviews in one round
    # (`rules.entering`, ADR-0014 addendum of 2026-10-06). A switch for the human's
    # counterfactual, read in one place only (`cli._run`), and deliberately not an arm:
    # it is absent from `EXPERIMENT_KEYS` and `experiment_snapshot`.
    decided_cap: bool = True
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
    # Where this invocation streams a role's generation (README.md, 14). `None` outside
    # the CLI door -- `langgraph dev` never passes `configurable`, so no file is opened
    # (issue #33). Coerced from the `str` the CLI puts in `configurable`, the same way
    # `product_dir` is coerced from a plain string today.
    log_file: Path | None = None
    # The review-arm switch (issue #28): `full` is today's path. The review-arm
    # subtask is what picks a role and a model off it; this subtask only declares
    # the field and reads it back into `experiment_snapshot` below for the record.
    # Both new fields reproduce today's path at their default; no module but the two
    # arms and `experiment_snapshot` reads either one.
    review_mode: Literal["full", "light"] = "full"
    # The ephemeral-arm switch (issue #28): whether the solution stage's interview is
    # skipped. `False` is today's path; the ephemeral-arm subtask is the only other
    # reader.
    skip_ephemeral: bool = False
    # Runs alive at once across every product on this machine (GH-137, the poller's
    # first piece): the human's own call is one at a time, default **1** -- two in
    # parallel already hit the account's own session limit (one 429 for the whole
    # account, never per product) and clashed merging. The poller counts the live entries
    # under `run_registry_dir` that can spend a model session (role runs and model-spending
    # land children) against this before it launches a role run, plus one reserved slot that
    # only a land step for a delivered task may use (`rules.LAND_HEADROOM`, ADR-0045); a
    # merge or checks-only land child runs no model and takes no slot (GH-263, ADR-0045
    # addendum).
    max_concurrent_runs: int = Field(default=1, ge=1)
    # Spend-paced concurrency (GH-177): off by default, so the poller behaves exactly as
    # before. When on, a launch beyond the first live run goes out only while the
    # machine's trailing 5 h spend is under `spend_pace_usd_per_h`; `max_concurrent_runs`
    # stays the hard ceiling. The pace is read only when `spend_pacing` is true, and 35
    # is a starting value taken from one 41 h sample (ADR-0039), not a measured one.
    spend_pacing: bool = False
    spend_pace_usd_per_h: float = Field(default=35.0, gt=0)
    # Where `run`/`resume` register the pid, host, product and task of a live
    # invocation and its kind, and the poller counts those that can spend a model against
    # `max_concurrent_runs` (GH-137, GH-263).
    # Outside every product, one per machine: `$XDG_STATE_HOME/agentic/runs`, or
    # `~/.local/state/agentic/runs` when that variable is unset, the same fallback the
    # XDG base-directory spec gives. A plain field, not a property reading the
    # environment straight: the override precedence above (`AGENTIC_RUN_REGISTRY_DIR`)
    # is what lets a test redirect it, the same way every other field already does, so
    # no test ever writes to the real registry.
    run_registry_dir: Path = Field(default_factory=lambda: _default_run_registry_dir())
    # `agentic perf` (GH-318, ADR-0059). One command (`seed` or `measure`) is killed with
    # its process group after `perf_timeout_s`, and the whole invocation after
    # `perf_node_timeout_s`: what remains of it caps each command. The machine-wide lock is
    # waited for at most `perf_lock_wait_s` (0 never waits) before every scenario is
    # `inconclusive/busy`.
    perf_timeout_s: float = Field(default=1800, gt=0)
    perf_node_timeout_s: float = Field(default=3600, gt=0)
    perf_lock_wait_s: float = Field(default=1800, ge=0)
    # The widest (p90 - p10) / median a metric may show and still be judged. UNCALIBRATED:
    # 0.25 is a first guess until the human's run over the old apostrophe commit settles it
    # (ADR-0059); with five runs it is a range, not a percentile band.
    perf_spread_max: float = Field(default=0.25, gt=0)
    # The machine-wide lock two measurements take so they never share a CPU: outside every
    # product, built like `run_registry_dir`.
    perf_lock_path: Path = Field(default_factory=lambda: _default_perf_lock_path())
    # The machine's store of passing check results (ADR-0065): outside every checkout and
    # shared by every product, built like `run_registry_dir`.
    check_results_dir: Path = Field(default_factory=lambda: _default_check_results_dir())
    # The GitHub Project the poller mirrors each issue's stage to (GH-375, ADR-0069), as
    # `<owner>/<number>`. `None` mirrors nothing and makes no call.
    board_project: str | None = None

    @field_validator("board_project")
    @classmethod
    def _board_project_shape(cls, value: str | None) -> str | None:
        if value is not None and _BOARD_PROJECT.fullmatch(value) is None:
            raise ValueError(f"board_project must be <owner>/<number>, got {value!r}")
        return value

    @property
    def board_owner_and_number(self) -> tuple[str, int] | None:
        """`board_project` split into the owner login and the project number."""
        if self.board_project is None:
            return None
        owner, _, number = self.board_project.partition("/")
        return owner, int(number)

    @model_validator(mode="after")
    def _parallel_within_role_slots(self) -> Configuration:
        if (
            self.max_parallel_subtasks is not None
            and self.max_parallel_subtasks > self.max_concurrent_roles
        ):
            raise ValueError(
                f"max_parallel_subtasks ({self.max_parallel_subtasks}) exceeds "
                f"max_concurrent_roles ({self.max_concurrent_roles}): a review would wait "
                "for a slot behind a new Developer"
            )
        return self

    @property
    def parallel_subtasks(self) -> int:
        """Subtasks one task runs at once: the explicit setting, or `min(4, roles)`."""
        if self.max_parallel_subtasks is not None:
            return self.max_parallel_subtasks
        return min(DEFAULT_PARALLEL_SUBTASKS, self.max_concurrent_roles)

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


# The three keys `experiment_snapshot` writes, in the order every writer uses
# (`document.render`'s frontmatter, `evaluation.baseline_id`'s digest): the two
# switches above, plus the model resolved for whichever reviewer actually ran.
EXPERIMENT_KEYS = ("review_mode", "skip_ephemeral", "reviewer_model")


def experiment_snapshot(cfg: Configuration, reviewer_model: str) -> dict[str, str]:
    """The `experiment` state/frontmatter value (`TaskState.experiment`,
    `document.render`): `{}` when both switches above are at their default, so a
    document produced on the default path stays byte-identical to one written before
    this task (the plan's omit-at-default pattern). Otherwise all three
    `EXPERIMENT_KEYS`, verbatim and in that order -- `reviewer_model` travels even
    when it happens to equal the default model, so an operator who sets
    `AGENTIC_REVIEW_MODE=light` without a matching `model_overrides` entry can be
    caught reading this key after the run, never prevented (out of this subtask's
    scope; said in the pull request body)."""
    defaults = Configuration.model_fields
    if (
        cfg.review_mode == defaults["review_mode"].default
        and cfg.skip_ephemeral == defaults["skip_ephemeral"].default
    ):
        return {}
    return {
        "review_mode": cfg.review_mode,
        "skip_ephemeral": str(cfg.skip_ephemeral),
        "reviewer_model": reviewer_model,
    }


def state_file_for(task_id: str) -> Path:
    """`STATE_DIR / f"{task_id}.sqlite"`, relative to the product root like `STATE_FILE`
    (`composition.checkpointer`'s routing, ADR-0003 addendum 2026-10-04). `task_id`
    reaches here straight from the CLI (an issue number or a title's slug), so it is
    rejected rather than trusted: empty, or carrying `/`, `\\` or `..`, could otherwise
    name a file outside `STATE_DIR` or collide with another task's."""
    if not task_id or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError(f"not a valid task id for a state file: {task_id!r}")
    return STATE_DIR / f"{task_id}.sqlite"


def _default_run_registry_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "agentic" / "runs"


def _default_check_results_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "agentic" / "check-results"


def _default_perf_lock_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "agentic" / "perf.lock"


def _from_env(raw: str, annotation: Any) -> Any:
    # Pydantic parses numbers and paths from strings; dicts need explicit JSON.
    if annotation is dict or getattr(annotation, "__origin__", None) is dict:
        return json.loads(raw)
    return raw
