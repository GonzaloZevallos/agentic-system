"""Whether the declared signals justify a stack review, and the text the role reads
as its request (ADR-0032, `docs/adr/0032-la-revision-de-stack-corre-roles-sin-tarea.md`).

Pure: no port, no `Path`, no `Ports`. ADR-0019's own argument is what this module is
for — a guessed antecedent gets the step that decides everything else wrong — so every
fact (the anchor's sha and date, the Brief's date, each task's own delivery) is
computed by the caller and handed over as data, never asserted by the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# The count of merged tasks that alone justifies a review, and the closed set of
# delivery outcomes a merge counts as (`core.measurement.domain.metrics.DeliveryOutcome`
# has a third value, `closed`, which never counted as a delivery worth re-deciding the
# stack over). Declared here, not imported: this module is `core/process/domain`, the
# outcome lives in `core/measurement/domain`, and hexagonal keeps the two apart.
MERGED_TASKS_THRESHOLD = 10
MERGED_OUTCOMES = ("merged", "merged_with_changes")


@dataclass(frozen=True)
class Signals:
    """The facts a stack review's request is built from, computed once by `signals()`
    so `fired` and `description` always agree on what they are looking at."""

    anchor: str
    brief_changed: bool
    merged_since: int
    reason: str


def signals(
    *,
    anchor: str,
    anchor_date: str,
    brief_date: str,
    deliveries: Sequence[tuple[str, str]],
    reason: str,
) -> Signals:
    """`Signals` from the caller's own reads. `anchor` is the sha of the last commit to
    `.agentic/stack.yaml` (`""` when the file has none: the greenfield repo).
    `anchor_date`/`brief_date` are ISO-8601, compared lexicographically. `deliveries` is
    each task document's own `(closed_at, delivery_outcome)`, ISO-8601 `closed_at`; a
    delivery counts toward `merged_since` when its outcome is in `MERGED_OUTCOMES` and
    its `closed_at` is after `anchor_date`."""
    merged_since = sum(
        1
        for closed_at, outcome in deliveries
        if outcome in MERGED_OUTCOMES and closed_at > anchor_date
    )
    return Signals(
        anchor=anchor,
        brief_changed=brief_date > anchor_date,
        merged_since=merged_since,
        reason=reason,
    )


def _fired_names(s: Signals) -> list[str]:
    """The name of every signal that fired, in the order `description` shows them."""
    names = []
    if s.anchor == "":
        names.append("greenfield: no stack.yaml anchor yet")
    if s.brief_changed:
        names.append("the Brief changed since the last stack decision")
    if s.merged_since >= MERGED_TASKS_THRESHOLD:
        names.append(f"{s.merged_since} tasks merged since the last stack decision")
    if s.reason:
        names.append("a reason was given")
    return names


def fired(s: Signals) -> bool:
    """Whether any declared signal justifies a review: no anchor at all (the greenfield
    repo, which satisfies every signal vacuously), the Brief changed, the merged count
    reached the threshold, or a `--reason` was given."""
    return bool(_fired_names(s))


def description(s: Signals) -> str:
    """The text the stack review's synthetic state carries as its `ORIGINAL` section:
    which signals fired, the anchor sha, the merged count, and the `--reason` text
    verbatim. Nothing else — never a paraphrase of the Brief, which the roles already
    receive through `## Product knowledge`, and never a technology name."""
    names = _fired_names(s)
    return "\n".join(
        [
            f"Signals fired: {'; '.join(names) if names else 'none'}",
            f"Anchor: {s.anchor or '(none)'}",
            f"Merged since anchor: {s.merged_since}",
            f"--reason: {s.reason or '(none)'}",
        ]
    )
