"""A product's experiment declaration and the per-task arm assignment (GH-470, ADR-0030
amended). Pure: nothing here reads a document, the registry, the environment or the clock;
the caller hands in the parsed YAML, the counts and the configuration.

`.agentic/experiment.yaml` declares an `id`, an `assignment` rule (`alternate` or
`seeded`, the latter with a `seed`) and `arms`: a name, a share and the `ARM_SWITCHES` the
arm sets. Shares sum to 1. One arm may be the control with `switches: {}`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args, get_origin

from agentic.shared.configuration import ARM_SWITCHES, Configuration

from .task import ArmAssignment

# The two flags `agentic run` takes and the poller's `_argv` writes: spelled here and
# nowhere else, so the launcher and the parser cannot drift apart.
ARM_FLAG = "--arm"
EXPERIMENT_FILE_FLAG = "--experiment-file"

_ARM_NAME = re.compile(r"[a-z0-9-]{1,24}")
_SHARE_TOLERANCE = 1e-9
_TOP_KEYS = frozenset({"id", "assignment", "seed", "arms"})
_ARM_KEYS = frozenset({"name", "share", "switches"})
_ASSIGNMENTS = ("alternate", "seeded")


class InvalidExperiment(ValueError):
    """A declaration that cannot be used; the message is one line and names its source."""


@dataclass(frozen=True)
class Arm:
    name: str
    share: float
    # Switch name -> value as a string (`review_mode` verbatim, `skip_ephemeral` as
    # `str(bool)`), the shape `experiment_snapshot` and `metrics.arm_label` read.
    switches: Mapping[str, str]


@dataclass(frozen=True)
class Experiment:
    id: str
    assignment: Literal["alternate", "seeded"]
    seed: int | None
    arms: tuple[Arm, ...]


def _default(key: str) -> str:
    return str(Configuration.model_fields[key].default)


def _canonical(switches: Mapping[str, str]) -> tuple[str, ...]:
    """What `metrics.arm_label` resolves: every `ARM_SWITCHES` value, the default where the
    arm names none. Two arms with the same tuple would be one arm to the measurement."""
    return tuple(switches.get(key, _default(key)) for key in ARM_SWITCHES)


def _switch_value(key: str, value: object, where: str) -> str:
    annotation = Configuration.model_fields[key].annotation
    if get_origin(annotation) is Literal:
        allowed = get_args(annotation)
        if not isinstance(value, str) or value not in allowed:
            raise InvalidExperiment(f"{where}: {key} must be one of {list(allowed)}, got {value!r}")
        return value
    if annotation is bool:
        if not isinstance(value, bool):
            raise InvalidExperiment(f"{where}: {key} must be a YAML bool, got {value!r}")
        return str(value)
    raise InvalidExperiment(f"{where}: {key} has a type an arm cannot set")


def _parse_arm(raw: object, index: int, source: str) -> Arm:
    where = f"{source}: arms[{index}]"
    if not isinstance(raw, Mapping):
        raise InvalidExperiment(f"{where} must be a mapping")
    if unknown := sorted(str(k) for k in raw.keys() - _ARM_KEYS):
        raise InvalidExperiment(f"{where} has unknown key {unknown[0]!r}")
    name = raw.get("name")
    if not isinstance(name, str) or not _ARM_NAME.fullmatch(name):
        raise InvalidExperiment(f"{where} name must match [a-z0-9-]{{1,24}}, got {name!r}")
    share = raw.get("share")
    if isinstance(share, bool) or not isinstance(share, int | float) or share <= 0:
        raise InvalidExperiment(f"{where} share must be a positive number, got {share!r}")
    raw_switches = raw.get("switches", {})
    if not isinstance(raw_switches, Mapping):
        raise InvalidExperiment(f"{where} switches must be a mapping")
    switches: dict[str, str] = {}
    for key, value in raw_switches.items():
        if key not in ARM_SWITCHES:
            raise InvalidExperiment(f"{where} switch {key!r} is not one of {list(ARM_SWITCHES)}")
        switches[key] = _switch_value(key, value, where)
    return Arm(name=name, share=float(share), switches=switches)


def parse_experiment(data: object, source: str) -> Experiment:
    """The declaration `data` (parsed YAML) as an `Experiment`, or `InvalidExperiment`
    with a one-line reason that starts with `source`."""
    if not isinstance(data, Mapping):
        raise InvalidExperiment(f"{source}: must be a mapping")
    if unknown := sorted(str(k) for k in data.keys() - _TOP_KEYS):
        raise InvalidExperiment(f"{source}: unknown key {unknown[0]!r}")
    experiment_id = data.get("id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise InvalidExperiment(f"{source}: id must be a non-empty string")
    assignment = data.get("assignment")
    if assignment not in _ASSIGNMENTS:
        raise InvalidExperiment(f"{source}: assignment must be one of {list(_ASSIGNMENTS)}")
    seed = data.get("seed")
    if assignment == "seeded":
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise InvalidExperiment(f"{source}: seeded assignment needs an int seed")
    elif seed is not None:
        raise InvalidExperiment(f"{source}: seed is only for seeded assignment")
    raw_arms = data.get("arms")
    if not isinstance(raw_arms, list) or not raw_arms:
        raise InvalidExperiment(f"{source}: arms must be a non-empty list")
    arms = tuple(_parse_arm(raw, i, source) for i, raw in enumerate(raw_arms))
    names = [arm.name for arm in arms]
    if duplicate := next((n for n in names if names.count(n) > 1), None):
        raise InvalidExperiment(f"{source}: duplicate arm name {duplicate!r}")
    total = sum(arm.share for arm in arms)
    if abs(total - 1) > _SHARE_TOLERANCE:
        raise InvalidExperiment(f"{source}: arm shares sum to {total}, not 1")
    seen: dict[tuple[str, ...], str] = {}
    for arm in arms:
        form = _canonical(arm.switches)
        if form in seen:
            raise InvalidExperiment(
                f"{source}: arms {seen[form]!r} and {arm.name!r} resolve to the same switches"
            )
        seen[form] = arm.name
    return Experiment(
        id=experiment_id,
        assignment=assignment,
        seed=seed if assignment == "seeded" else None,
        arms=arms,
    )


def assign(declaration: Experiment, issue: int, counts: Mapping[str, int]) -> Arm:
    """The arm of `issue`. `alternate`: deficit round-robin over `counts` (arm name ->
    tasks already given it), the arm furthest below its share, ties to declaration order;
    names the declaration lacks are ignored. `seeded`: `sha256(seed:issue)` placed on the
    cumulative shares, independent of `counts`."""
    arms = declaration.arms
    if declaration.assignment == "seeded":
        point = int(hashlib.sha256(f"{declaration.seed}:{issue}".encode()).hexdigest(), 16) / 2**256
        cumulative = 0.0
        for arm in arms:
            cumulative += arm.share
            if point < cumulative:
                return arm
        return arms[-1]
    total = sum(counts.get(arm.name, 0) for arm in arms)
    scores = [arm.share * (total + 1) - counts.get(arm.name, 0) for arm in arms]
    best = max(scores)
    return arms[next(i for i, score in enumerate(scores) if score >= best - _SHARE_TOLERANCE)]


def conflicts(declaration: Experiment, cfg: Configuration) -> tuple[str, ...]:
    """The switches some arm names that `cfg` holds at a non-default value, in
    `ARM_SWITCHES` order: the environment would override what the arm sets."""
    named = {key for arm in declaration.arms for key in arm.switches}
    defaults = Configuration.model_fields
    return tuple(
        key for key in ARM_SWITCHES if key in named and getattr(cfg, key) != defaults[key].default
    )


def assignment_of(declaration: Experiment, arm: Arm) -> ArmAssignment:
    """`TaskState.assignment` for `arm` of `declaration`: what `agentic run --arm` writes."""
    return {"experiment": declaration.id, "arm": arm.name, "switches": dict(arm.switches)}


def _typed(key: str, value: str) -> object:
    """A switch as its `Configuration` field holds it: `skip_ephemeral` is `"True"`/`"False"`
    in the document and a bool in the field, `review_mode` is the string itself."""
    return value == "True" if Configuration.model_fields[key].annotation is bool else value


def with_switches(cfg: Configuration, switches: Mapping[str, str]) -> Configuration:
    """`cfg` with the arm's switches in force, whatever the environment holds: the one
    place an assignment becomes configuration (the graph's nodes and `agentic run` call it)."""
    if not switches:
        return cfg
    return cfg.model_copy(update={key: _typed(key, value) for key, value in switches.items()})


def disagreements(cfg: Configuration, switches: Mapping[str, str]) -> tuple[str, ...]:
    """The arm's switches `cfg` holds at another value, in `ARM_SWITCHES` order: what `resume`
    says before it keeps the document's arm."""
    return tuple(
        key for key in ARM_SWITCHES if key in switches and str(getattr(cfg, key)) != switches[key]
    )


def carried(previous: Mapping[str, object] | None, declared_id: str | None) -> ArmAssignment | None:
    """The assignment of a previous document that a `--fresh` run keeps: only when its
    experiment is the one now declared. Any other id, or none, starts the task unassigned."""
    if previous is None or declared_id is None:
        return None
    assigned = previous.get("assignment")
    if not isinstance(assigned, Mapping) or assigned.get("experiment") != declared_id:
        return None
    raw = assigned.get("switches")
    switches = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, Mapping) else {}
    return {"experiment": declared_id, "arm": str(assigned.get("arm")), "switches": switches}
