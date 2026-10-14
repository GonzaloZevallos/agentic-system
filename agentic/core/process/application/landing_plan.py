"""What the landing reads off a task document to ask `domain.landing.landing_step` for a
step (GH-239): the budgets already spent, counted from the document's own `landing` list.

Shared by the poller (which decides whether to spawn `agentic land`), `merge.py` (which
decides which comment a refusal gets) and `landing.py` (which dispatches), so the three
ask the same question the same way. It imports nothing from them."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agentic.shared.configuration import Configuration

from ..domain import landing, rules
from ..domain.landing import LandingStep, LandingStepKind
from ..domain.task import TaskState
from .ports import Launcher, Ports, Upstream

LIVE_STEPS: frozenset[LandingStepKind] = frozenset({"fix", "rerun", "file_followups_and_land"})
"""The steps whose executors are built. Empty while `landing_sync` and `landing_fix` are
inert: an inert step is neither recorded nor spawned, so the refusal logs stay as they
were. `landing_fix` built `fix`, `rerun` and `file_followups_and_land`; `sync_master`,
`re_review` and `diagnose` are not live yet."""


def takes_action(kind: LandingStepKind, live: frozenset[LandingStepKind]) -> bool:
    """Whether `kind` is a step the system takes now: built (`live`) and not `wait` (the
    next tick decides again) or `none` (the human's), which are never recorded or spawned
    whatever `live` says."""
    return kind in live and kind not in ("wait", "none")


def entries(raw: Mapping[str, object]) -> list[dict[str, object]]:
    """The document's `landing` list, tolerant of its absence or a malformed value."""
    value = raw.get("landing")
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def spent(
    raw: Mapping[str, object], head: str | None, waited_s: float | None
) -> landing.LandingSpent:
    """The passes, clean syncs and reruns on `head` the `landing` list records. A
    `sync_master` answering `checks_failed` is the conflicting one, a model pass."""
    done = entries(raw)

    def count(predicate: Callable[[dict[str, object]], bool]) -> int:
        return sum(1 for entry in done if predicate(entry))

    return landing.LandingSpent(
        passes=count(
            lambda e: (
                e.get("step") in ("re_review", "fix")
                or (e.get("step") == "sync_master" and e.get("refusal") == "checks_failed")
            )
        ),
        syncs=count(
            lambda e: e.get("step") == "sync_master" and e.get("refusal") != "checks_failed"
        ),
        reruns_on_head=count(lambda e: e.get("step") == "rerun" and e.get("head") == head),
        diagnosed=count(lambda e: e.get("step") == "diagnose") > 0,
        waited_s=waited_s,
    )


def step_for(raw: TaskState, refusal: rules.MergeRefusal, facts: rules.MergeFacts) -> LandingStep:
    """The one question every caller asks: `landing_step` over the document's spent budgets."""
    return landing.landing_step(
        refusal,
        facts,
        spent(raw, facts.pr_head, facts.ci_waited_s),
        landing.blocking_owners(raw),
    )


def pending(
    raw: TaskState,
    refusal: rules.MergeRefusal,
    facts: rules.MergeFacts,
    live: frozenset[LandingStepKind],
) -> LandingStep | None:
    """The step worth a `land` child: built (`live`) and not yet dispatched on this head."""
    step = step_for(raw, refusal, facts)
    if not takes_action(step.kind, live) or facts.pr_head is None:
        return None
    if landing.already_dispatched(entries(raw), facts.pr_head, step.kind):
        return None
    return step


@dataclass(frozen=True)
class LandingContext:
    """What an executor is handed: the step already recorded for `head`, never decided again."""

    task_id: str
    cfg: Configuration
    ports: Ports
    step: LandingStep
    head: str
    upstream: Upstream
    facts: rules.MergeFacts
    log: Callable[[str], None]
    launcher: Launcher | None = None
    argv0: str = "agentic"
