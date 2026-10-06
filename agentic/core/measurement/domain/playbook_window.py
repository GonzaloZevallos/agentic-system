"""The playbook window: did a role's playbook move its return rate? (ADR-0043)

Pure: raw task documents (frontmatter dicts) and a role's current `Playbook` in, verdicts
and the summary text out. Nothing here opens an issue, edits code or writes a file: a
verdict is a proposal the summary shows to a person.

A role's tasks, in `closed_at` order, are cut into *segments*: maximal runs that first ran
with the same playbook section (`playbooks[slug]`, `empty` when it had none). At each
boundary between two segments the last `WINDOW` tasks before are compared with the first
`WINDOW` after, per category, on the returns attributable to the role (`accountable`).

Only tasks that say something about the playbook enter: closed, on the `default` arm (an
experiment's arm changes the rate by itself), and without a `playbooks_changed` entry for
the slug (they ran under two playbooks). A comparison is inconclusive when a side is
short or when its tasks span more than one `system_baseline` (the system itself changed).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentic.core.process.domain.playbook import Delta, Item, Playbook, accountable

from . import metrics

# Tasks per side of a comparison, and the returns of one category in the after window
# that mean a rule is needed beyond a lesson: the README's 10/10 window and the issue's 3.
WINDOW = 10
CHECK_THRESHOLD = 3
# The segment of a task that ran with no playbook section.
EMPTY = "empty"
# Items gained since the last summary that justify running `eval compare` over the set of
# five: that run costs a full set, so it is not due on every summary.
EVAL_COMPARE_ITEMS = 3

CHECK_TEMPLATE = (
    "Propose a mechanical check for {category} returns attributed to {slug}: {count} in "
    "the last {window} tasks despite a playbook item for it. Decide what a script can "
    "verify before review (what to check) and run it as a presubmit, guard or hook."
)


@dataclass(frozen=True)
class Segment:
    key: str
    tasks: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class Comparison:
    """One boundary between two segments of one role. `before` and `after` map a
    category to its return rate (returns per task) and hold only categories with a return;
    `returns_after` the same count in the after window. `retire` is the id of the item a
    strictly worse category puts up for retirement, per category: always empty when
    `conclusive` is false, and then `reason` says why."""

    slug: str
    before_key: str
    after_key: str
    before_n: int
    after_n: int
    conclusive: bool
    reason: str = ""
    before: Mapping[str, float] = field(default_factory=dict)
    after: Mapping[str, float] = field(default_factory=dict)
    returns_after: Mapping[str, int] = field(default_factory=dict)
    retire: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckProposal:
    slug: str
    category: str
    count: int
    text: str


def _eligible(document: Mapping[str, Any], slug: str) -> bool:
    if not document.get("closed_at"):
        return False
    if metrics.arm_label(document.get("experiment") or {}) != "default":
        return False
    return slug not in (document.get("playbooks_changed") or {})


def segments(documents: Sequence[Mapping[str, Any]], slug: str) -> list[Segment]:
    """The eligible tasks of `slug` in `closed_at` order, cut where the playbook section
    hash changes."""
    eligible = sorted(
        (d for d in documents if _eligible(d, slug)), key=lambda d: str(d["closed_at"])
    )
    cut: list[Segment] = []
    for document in eligible:
        key = (document.get("playbooks") or {}).get(slug, EMPTY)
        if cut and cut[-1].key == key:
            cut[-1] = Segment(key, cut[-1].tasks + (document,))
        else:
            cut.append(Segment(key, (document,)))
    return cut


def _counts(tasks: Sequence[Mapping[str, Any]], slug: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for document in tasks:
        for entry in document.get("return_history") or []:
            if accountable(entry, str(document.get("type"))) == slug:
                category = str(entry["category"])
                counts[category] = counts.get(category, 0) + 1
    return counts


def _rates(counts: Mapping[str, int], tasks: int) -> dict[str, float]:
    return {category: count / tasks for category, count in sorted(counts.items())}


def _id_order(item: Item) -> tuple[str, int, str]:
    tail = item.id.rpartition("-")[2]
    return (item.added_at, int(tail) if tail.isdigit() else -1, item.id)


def _newest_per_category(items: Sequence[Item]) -> list[Item]:
    newest: dict[str, Item] = {}
    for item in items:
        held = newest.get(item.category)
        if held is None or _id_order(item) > _id_order(held):
            newest[item.category] = item
    return [newest[category] for category in sorted(newest)]


def _unfit(before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]) -> str:
    """Why this pair of sides cannot be compared, or the empty string."""
    for name, side in (("before", before), ("after", after)):
        if len(side) < WINDOW:
            return f"the {name} side has {len(side)} tasks, needs {WINDOW}"
    baselines = sorted({str(d.get("system_baseline") or "none") for d in (*before, *after)})
    if len(baselines) > 1:
        return f"the tasks span system baselines {', '.join(baselines)}"
    return ""


def compare(documents: Sequence[Mapping[str, Any]], playbook: Playbook) -> list[Comparison]:
    """One comparison per boundary between segments of `playbook.slug`, oldest first."""
    slug = playbook.slug
    cut = segments(documents, slug)
    comparisons: list[Comparison] = []
    for previous, following in zip(cut, cut[1:], strict=False):
        before = previous.tasks[-WINDOW:]
        after = following.tasks[:WINDOW]
        reason = _unfit(before, after)
        if reason:
            comparisons.append(
                Comparison(
                    slug=slug,
                    before_key=previous.key,
                    after_key=following.key,
                    before_n=len(before),
                    after_n=len(after),
                    conclusive=False,
                    reason=reason,
                )
            )
            continue
        before_counts = _counts(before, slug)
        after_counts = _counts(after, slug)
        introduced = [i for i in playbook.items if i.since == following.key]
        retire = tuple(
            item.id
            for item in _newest_per_category(introduced)
            if after_counts.get(item.category, 0) > before_counts.get(item.category, 0)
        )
        comparisons.append(
            Comparison(
                slug=slug,
                before_key=previous.key,
                after_key=following.key,
                before_n=len(before),
                after_n=len(after),
                conclusive=True,
                before=_rates(before_counts, len(before)),
                after=_rates(after_counts, len(after)),
                returns_after=after_counts,
                retire=retire,
            )
        )
    return comparisons


def check_proposals(comparisons: Sequence[Comparison], playbook: Playbook) -> list[CheckProposal]:
    """A mechanical check to consider for each category the role keeps being returned for
    (`CHECK_THRESHOLD` or more in a conclusive after window) although the playbook has an
    item for it. One per (slug, category), the highest count seen."""
    with_item = {item.category for item in playbook.items}
    found: dict[tuple[str, str], int] = {}
    for comparison in comparisons:
        if not comparison.conclusive or comparison.slug != playbook.slug:
            continue
        for category, count in comparison.returns_after.items():
            if count >= CHECK_THRESHOLD and category in with_item:
                key = (comparison.slug, category)
                found[key] = max(found.get(key, 0), count)
    return [
        CheckProposal(
            slug=slug,
            category=category,
            count=count,
            text=CHECK_TEMPLATE.format(category=category, slug=slug, count=count, window=WINDOW),
        )
        for (slug, category), count in sorted(found.items())
    ]


def _delta_lines(title: str, deltas: Sequence[Delta]) -> list[str]:
    lines = [f"- {title}: {len(deltas)}"]
    for delta in deltas:
        what = delta.lesson if delta.op == "add" else delta.item_id
        lines.append(f"  - {delta.slug}: {what}")
    return lines


def _rate_lines(comparison: Comparison) -> list[str]:
    before = comparison.before
    after = comparison.after
    lines = [
        f"- {comparison.slug}, {comparison.before_key} -> {comparison.after_key}"
        f" ({comparison.before_n} / {comparison.after_n} tasks)"
    ]
    categories = sorted(set(before) | set(after))
    if not categories:
        lines.append("  - no returns on either side")
    for category in categories:
        lines.append(
            f"  - {category}: {before.get(category, 0.0):.2f} -> {after.get(category, 0.0):.2f}"
        )
    return lines


def summary_markdown(
    comparisons: Sequence[Comparison],
    proposals: Sequence[CheckProposal],
    applied: Sequence[Delta],
    *,
    items_gained: int = 0,
) -> str:
    """The summary every 10 closed tasks (README, PjM): what the curator changed, the
    per-category rates around each boundary, what to retire, which checks to consider,
    how many comparisons could not be made and when `eval compare` is worth a full set.

    `applied` are the effective deltas the curator applied since the last summary;
    `items_gained` the items the accumulated playbooks gained over the same time."""
    conclusive = [c for c in comparisons if c.conclusive]
    inconclusive = [c for c in comparisons if not c.conclusive]
    retirements = [(c.slug, item_id) for c in conclusive for item_id in c.retire]
    lines = ["### Playbook items"]
    lines += _delta_lines("Added", [d for d in applied if d.op == "add"])
    lines += _delta_lines("Merged", [d for d in applied if d.op == "merge"])
    lines += _delta_lines("Retired", [d for d in applied if d.op == "retire"])
    lines += ["", "### Return rates per category (returns per task, before -> after)"]
    lines += [line for c in conclusive for line in _rate_lines(c)] or ["- no conclusive comparison"]
    lines += ["", "### Proposed retirements"]
    lines += [
        f"- Retire {item_id} ({slug}): its category was returned more after it"
        for slug, item_id in retirements
    ]
    lines += [] if retirements else ["- none"]
    lines += ["", "### Proposed mechanical checks"]
    lines += [f"- {p.slug} / {p.category} ({p.count} returns): {p.text}" for p in proposals]
    lines += [] if proposals else ["- none"]
    lines += ["", f"### Inconclusive comparisons: {len(inconclusive)}"]
    lines += [f"- {c.slug}, {c.before_key} -> {c.after_key}: {c.reason}" for c in inconclusive]
    lines += ["", "### Evaluation over the set of five"]
    if retirements or items_gained >= EVAL_COMPARE_ITEMS:
        why = (
            "a conclusive comparison got worse"
            if retirements
            else f"the playbooks gained {items_gained} items since the last summary"
        )
        lines.append(f"- Run `agentic eval compare` over the set of five: {why}.")
    else:
        lines.append(
            "- Not due: no conclusive worse comparison and fewer than "
            f"{EVAL_COMPARE_ITEMS} items gained."
        )
    return "\n".join(lines) + "\n"
