"""The words of an `Auto-merge refused:` comment and the thread read they depend on (GH-885).

The merge verb and the poller both comment a refusal a person can act on; what the comment says
lives here, so neither module owns the other's text. This module imports only `..domain*` and
`.ports`: never `poll`, `merge`, `resume` or `landing_plan`, which import it."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import rules
from ..domain.landing import LandingStep
from .ports import IssueComment, Ports

# The comment the automatic merge posts (GH-178, ADR-0045), registered into
# `resume.SYSTEM_COMMENTS`: a refusal the human can act on. Imported by `resume.py`, so it is
# never read as a human's answer.
AUTO_MERGE_REFUSED = "Auto-merge refused: "

# What a stacking refusal says in a comment (GH-859): one fixed phrase per value, keyed by the
# refusal, never a word of the parent, its issue or the record. Only `stack_base_orphaned` is
# commented; the other two are listed so that no stacking value has a comment without a phrase.
STACK_EVIDENCE: dict[str, str] = {
    "stack_parent_unmerged": "The parent this task is stacked on has not merged yet.",
    "stack_restack_due": "The parent merged; this task takes the base branch in once.",
    "stack_base_orphaned": (
        "The commit this task was built on will never reach the base branch: its parent was "
        "rebuilt, closed without merging, merged without a merge commit, or the record of the "
        "parent could not be read."
    ),
}

LEFT_TO_HUMAN = "Left for the human to merge."

# The only list of the refusals the poller comments (GH-885): each is a person's to act on and
# is in `merge.COMMENTED` and `landing.HUMAN_OWNED_REFUSALS`.
POLLER_NOTED: tuple[str, ...] = (
    "perf_unverified",
    "criteria_unreadable",
    "criteria_over_cap",
    "stack_base_orphaned",
)


def read_comments(ports: Ports, task_id: str) -> list[IssueComment] | None:
    """The issue's comments with their instants, read once per merge decision (GH-784); `None`
    when the read fails, which `escalation_hold` turns into an unknown fact, never a clear one.
    The poller's refusal note (GH-885) reads the thread through it as well."""
    try:
        return ports.tracker.timestamped_comments(task_id)
    except (ValueError, OSError):
        return None


def _perf_evidence(facts: rules.MergeFacts) -> str:
    """What a `perf_unverified` refusal says, one fixed line from the structured facts: the
    run's head when it differs from the pull request's, else its verdict (`none`: no run)."""
    if facts.perf_verdict == "approved":
        return f"measured {facts.perf_head or 'none'}, head {facts.pr_head}"
    return f"verdict {facts.perf_verdict or 'none'}"


def _criteria_evidence(facts: rules.MergeFacts) -> str:
    """What a `criteria_over_cap` refusal says: the messages `knowledge` wrote, one per
    line (file, count, cap), and nothing this module counted. An unreadable read has none."""
    return "\n".join(f"- {message}" for message in facts.criteria_regressed or ())


def closing_for(step: LandingStep | None) -> str:
    """The closing sentence of a refusal: `LEFT_TO_HUMAN` when the landing takes no step for it
    (no step, or a `none` one), else nothing. The one place this rule is written."""
    return LEFT_TO_HUMAN if step is None or step.kind == "none" else ""


def said(bodies: Sequence[str], head: str, reason: str) -> bool:
    """Whether ANY of the bodies opens with this (head, reason)'s `Auto-merge refused:` line.

    This is not the merge verb's rule: the verb (`merge._refuse`, the `refused[-1]` test) skips
    only when the LAST `Auto-merge refused:` body has the same first line. For a head refused
    for A, then B, then A again, `said` skips where the verb posts. A caller (the poller) must
    choose one of the two rules and say which."""
    first = f"{AUTO_MERGE_REFUSED}{head} {reason}"
    return any(body.splitlines()[:1] == [first] for body in bodies)
