"""The merge gate's fresh GitHub run, decided purely (GH-463, ADR-0080 addendum).

For a product that declares `ci:` the suite never runs on the owner's machine at merge: when
no recorded pass covers the head on the current master, the gate gets one fresh GitHub verdict
for the head merged with that master. This module holds the three questions the decision asks
about that run, with full commit ids and `rules.same_commit` and never a clock or a short
sha: does a decided run stand for the head (`run_stands`), does a recorded pass already cover
it (`masters_covered_ci`, #353 for a `ci:` product) and how many pairs the head has spent
against each bound (`pair_counts`). It reads no file and calls no port; the paths master
gained come from the workspace, through the caller."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# The module, not the names: `rules` imports this one too, and reads it at call time.
from . import master_gain, rules
from .task import FullChecksRecord, MergeCiRecord

# A recorded verdict is accepted only when GitHub gave it (invariant 7); `local` and an absent
# `ran_on` never are.
_GITHUB = frozenset({"ci", "ci_dispatch"})
# The outcomes of a pair whose run decides nothing for the head.
_UNDECIDED = frozenset({"stale", "no_verdict"})


@dataclass(frozen=True)
class PairCounts:
    """What a head spent against the bounds: `stale` pairs (a decided run master overtook with
    a gain that is not inert) and `no_verdict` pairs (GitHub gave none after the republish or
    second dispatch, counted since the last release). Each against its own bound in `ci`."""

    stale: int
    no_verdict: int


def latest(records: Sequence[MergeCiRecord], head: str | None) -> MergeCiRecord | None:
    """The newest pair recorded for `head`, or None. Full ids only: a short head names nothing."""
    if not rules.full_commit_id(head):
        return None
    for record in reversed(records):
        if rules.same_commit(record["head"], head):
            return record
    return None


def run_belongs(record: MergeCiRecord | None, pr_head: str | None, ran: str | None = None) -> bool:
    """Whether a verdict read for the commit `ran` (the record's `tested` when not given) is the
    one this record asked for on `pr_head`: the record's head is `pr_head` and `ran` is its
    `tested`, as whole ids, and `ran` is not a sha the pair superseded. Master plays no part:
    a red or pending run is read whatever master did since."""
    if record is None:
        return False
    tested = record["tested"] if ran is None else ran
    return (
        rules.full_commit_id(record["head"])
        and rules.full_commit_id(pr_head)
        and rules.same_commit(record["head"], pr_head)
        and rules.full_commit_id(record["tested"])
        and rules.full_commit_id(tested)
        and rules.same_commit(record["tested"], tested)
        and not any(rules.same_commit(old, tested) for old in record["superseded"])
    )


def run_stands(
    record: MergeCiRecord | None,
    pr_head: str | None,
    current_master: str | None,
    inert: bool | None,
    *,
    ran: str | None = None,
) -> bool:
    """Whether a fresh run on `record['tested']` stands for `pr_head` on `current_master`: it
    belongs to the head (`run_belongs`), its pair did not end `stale` or `no_verdict`, and the
    master it merged is the current one -- or master gained something since that `inert` says
    cannot change a check (None: not read, not inert). The only comparison of a fresh run
    with the merge-ready head."""
    if record is None or not run_belongs(record, pr_head, ran):
        return False
    if record.get("outcome") in _UNDECIDED:
        return False
    if not (rules.full_commit_id(record["master"]) and rules.full_commit_id(current_master)):
        return False
    return rules.same_commit(record["master"], current_master) or inert is True


def decided_red(
    records: Sequence[MergeCiRecord], pr_head: str | None, current_master: str | None
) -> MergeCiRecord | None:
    """The head's newest pair when a fresh run on the current master ended `red` and still
    stands for the head (`run_stands`, no inert gain assumed), else None. What the landing
    reads of the gate's fresh run: it never reads GitHub for it, the pair holds the outcome,
    the run id and the failing names (a pair the gate left behind for a newer master is no
    longer the newest, so it is never reported here)."""
    pair = latest(records, pr_head)
    if pair is None or pair.get("outcome") != "red":
        return None
    return pair if run_stands(pair, pr_head, current_master, None) else None


def reopened(record: MergeCiRecord, at: str) -> MergeCiRecord:
    """`record` after the landing reran its run (`rerun_at` `at`): the pair is undecided again,
    so it carries no `outcome` and no `failing`, and the gate reads the run once more. Every
    other field is kept, the `run_id` included."""
    row = MergeCiRecord(
        head=record["head"],
        master=record["master"],
        tested=record["tested"],
        mode=record["mode"],
        published_at=record["published_at"],
        republished=record["republished"],
        superseded=list(record["superseded"]),
        rerun_at=at,
    )
    if "run_id" in record:
        row["run_id"] = record["run_id"]
    if "released_at" in record:
        row["released_at"] = record["released_at"]
    if "cause" in record:
        row["cause"] = record["cause"]
    if "reread_at" in record:
        row["reread_at"] = record["reread_at"]
    return row


def rereadable(
    records: Sequence[MergeCiRecord], pr_head: str | None, current_master: str | None
) -> MergeCiRecord | None:
    """The head's newest pair when the gate may reread its run once: it ended `no_verdict`
    (GitHub gave none inside the grace, but the run may have finished after), it was neither
    reread (`reread_at`) nor released (`released_at`), its run belongs to `pr_head`
    (`run_belongs`) and the master it merged is the current one, whole ids and no inert gain.
    The only place that decides a pair is rereadable."""
    if not (rules.full_commit_id(pr_head) and rules.full_commit_id(current_master)):
        return None
    pair = latest(records, pr_head)
    if pair is None or pair.get("outcome") != "no_verdict":
        return None
    if "reread_at" in pair or "released_at" in pair:
        return None
    if not run_belongs(pair, pr_head):
        return None
    if not rules.full_commit_id(pair["master"]):
        return None
    return pair if rules.same_commit(pair["master"], current_master) else None


def masters_covered_ci(
    full_checks: FullChecksRecord | None,
    fresh: MergeCiRecord | None,
    *,
    pr_head: str | None,
    current_master: str | None,
    merge_base: str | None,
    required: Sequence[str],
    gains: Mapping[str, Sequence[str] | None],
    globs: Sequence[str],
    setup_commands: Sequence[str],
    task_touched: Sequence[str] | None,
) -> bool:
    """#353 for a `ci:` product: whether a recorded GitHub pass already covers `pr_head` on
    `current_master`, so no fresh run is asked for. Two records count. A passing `full_checks`
    whose `ran_on` is `ci` or `ci_dispatch` (never `local`, never absent), whose head is
    `pr_head`, whose rows pass every name in `required` (the plan's `ci_plan_only` included)
    and whose master is `merge_base`. Or the `fresh` pair of the head that ended `green`, whose
    master is its own. Either covers when its master is the current one or master only gained
    paths `master_gain.gain_inert` calls inert (`gains` maps each earlier master to those
    paths, None when unread)."""
    if not rules.full_commit_id(pr_head) or not rules.full_commit_id(current_master):
        return False
    if _full_checks_stands(full_checks, pr_head, required) and _master_ok(
        merge_base, current_master, gains, globs, setup_commands, task_touched
    ):
        return True
    if fresh is None or fresh.get("outcome") != "green" or not run_belongs(fresh, pr_head):
        return False
    return _master_ok(fresh["master"], current_master, gains, globs, setup_commands, task_touched)


def pair_counts(records: Sequence[MergeCiRecord], head: str | None) -> PairCounts:
    """The pairs `head` spent against the bounds. `stale` counts every pair of the head that
    ended `stale`. `no_verdict` counts those that ended `no_verdict` after their republish (or
    second dispatch) was spent, and only the ones recorded after the last pair the landing
    released (`released_at`), by position in the list and never by a clock."""
    if not rules.full_commit_id(head):
        return PairCounts(0, 0)
    own = [record for record in records if rules.same_commit(record["head"], head)]
    released = max((i for i, record in enumerate(own) if record.get("released_at")), default=-1)
    stale = sum(1 for record in own if record.get("outcome") == "stale")
    no_verdict = sum(
        1
        for record in own[released + 1 :]
        if record.get("outcome") == "no_verdict" and record["republished"]
    )
    return PairCounts(stale, no_verdict)


def _full_checks_stands(
    record: FullChecksRecord | None, pr_head: str | None, required: Sequence[str]
) -> bool:
    if record is None or record["passed"] is not True or record.get("ran_on") not in _GITHUB:
        return False
    if not (rules.full_commit_id(record["head"]) and rules.same_commit(record["head"], pr_head)):
        return False
    passed = {row["name"] for row in record["results"] if row["passed"] is True}
    return all(name in passed for name in required)


def _master_ok(
    master: str | None,
    current_master: str | None,
    gains: Mapping[str, Sequence[str] | None],
    globs: Sequence[str],
    setup_commands: Sequence[str],
    task_touched: Sequence[str] | None,
) -> bool:
    """A record's master is the current one, or only behind by an inert gain."""
    if not (rules.full_commit_id(master) and rules.full_commit_id(current_master)):
        return False
    assert master is not None and current_master is not None
    if rules.same_commit(master, current_master):
        return True
    return (
        master_gain.masters_covered(
            current_master, [master], gains, globs, setup_commands, task_touched
        )
        is True
    )
