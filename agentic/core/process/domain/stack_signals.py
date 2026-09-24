"""Whether the declared signals justify a stack review, and the text the role reads
as its request (ADR-0034, `docs/adr/0034-la-revision-de-stack-corre-roles-sin-tarea.md`).

Pure: no port, no `Path`, no `Ports`. ADR-0019's own argument is what this module is
for — a guessed antecedent gets the step that decides everything else wrong — so every
fact (the anchor's sha and position, the Brief's position, each task's own delivery) is
computed by the caller and handed over as data, never asserted by the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    anchor_pos: int | None,
    brief_pos: int | None,
    deliveries: Sequence[tuple[int | None, str]],
    reason: str,
) -> Signals:
    """`Signals` from the caller's own reads (ADR-0038). `anchor` is the sha of the commit
    that brought `.agentic/stack.yaml` to the main line (`""` when none: the greenfield
    repo). Every `*_pos` is a 0-based position on `HEAD`'s first-parent chain, oldest
    first, `None` when there is none; no clock decides anything here. `deliveries` is
    each task document's `(landing position of its merge commit, delivery_outcome)`; one
    counts toward `merged_since` when its outcome is in `MERGED_OUTCOMES`, its position
    is known and it is after the anchor (or there is no anchor). The Brief changed when
    it landed strictly after the anchor: a commit that brought both files decided the
    stack with that Brief in the tree."""
    merged_since = sum(
        1
        for pos, outcome in deliveries
        if outcome in MERGED_OUTCOMES
        and pos is not None
        and (anchor_pos is None or pos > anchor_pos)
    )
    return Signals(
        anchor=anchor,
        brief_changed=brief_pos is not None and (anchor_pos is None or brief_pos > anchor_pos),
        merged_since=merged_since,
        reason=reason,
    )


def needs_merge_commit(raw: Mapping[str, object]) -> bool:
    """Whether a task document is closed as merged yet lacks `merge_commit`: the one
    decision both `close --all` and `close --issue` take to backfill it (ADR-0038)."""
    return (
        bool(raw.get("closed_at"))
        and raw.get("delivery_outcome") in MERGED_OUTCOMES
        and not raw.get("merge_commit")
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
