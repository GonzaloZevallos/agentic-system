"""The forward affected base (GH-1125, domain half): the last commit G a check was green on,
the one decision between G and the task base, and the kept-verdict row of a check whose affected
form selected nothing since G. Pure: nothing here reads git, a file or the clock; the gate hands
in what it read. Imports from `presubmit`, never the reverse."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agentic.core.process.domain.presubmit import (
    AFFECTED_EMPTY_EXIT,
    CheckResult,
    final_results,
)


def green_source(row: CheckResult, stuck: frozenset[str]) -> bool:
    """Whether the final row of a check moves its G to the head: it passed, it is none of
    timed out, truncated, not ready, a rerun or a kept row, and the check is not stuck. A plain
    reused row, a content hit on a stored pass, is a source."""
    return (
        row["passed"]
        and row["name"] not in stuck
        and not (
            row.get("timed_out", False)
            or row.get("truncated", False)
            or row.get("not_ready", False)
            or row.get("rerun", False)
            or "reused_from" in row
        )
    )


def base_for(
    green: str | None,
    is_ancestor: bool | None,
    paths: Sequence[str] | None,
    task_base: str,
) -> str:
    """The base one check's affected form is measured against: `green` only when it exists, is
    an ancestor of the head (`is_ancestor` True: an amend or a rebase makes it False) and
    `green..head` changed something (`paths` non-empty); otherwise `task_base`. `None` means
    unreadable and `[]` means empty: they are different inputs and neither yields `green`.
    The only place that chooses between G and the task base. The changed paths `plan_form`
    reads and the affected command must both come from this return value, and no reader
    recomputes the choice."""
    if green is not None and is_ancestor is True and paths:
        return green
    return task_base


def advance_greens(
    previous: Mapping[str, str],
    rows: Sequence[CheckResult],
    head: str,
    stuck: frozenset[str],
) -> dict[str, str]:
    """Each check's G after a gate: `head` for a check whose final row is a `green_source`,
    its previous G otherwise (none when it had none). A kept, failed or timed-out row, or a
    stuck check, never moves G."""
    greens = dict(previous)
    for row in final_results(rows):
        if green_source(row, stuck):
            greens[row["name"]] = head
    return greens


def kept_row(row: CheckResult, green: str, paths: Sequence[str] | None) -> CheckResult | None:
    """The one builder of the kept-verdict row, and the exit-8 decision: an `affected-only`
    row that ran against `green` (`affected_base`), did not time out and exited
    `AFFECTED_EMPTY_EXIT`, while `green..head` is non-empty (`paths`, where `None` is
    unreadable and `[]` empty), gives a passed row that ran nothing and names G in
    `reused_from`. Exit 9, any other exit, a timeout or no new paths give None: today's flow."""
    if (
        row.get("mode") != "affected-only"
        or row.get("affected_base") != green
        or row.get("timed_out", False)
        or row.get("exit_code") != AFFECTED_EMPTY_EXIT
        or not paths
    ):
        return None
    return {
        "name": row["name"],
        "command": row["command"],
        "passed": True,
        "output": "",
        "duration_s": 0.0,
        "mode": "affected-only",
        "reused_from": green,
    }
