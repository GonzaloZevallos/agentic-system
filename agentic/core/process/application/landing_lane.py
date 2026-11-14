"""The facts the fast lane needs of a landing round (GH-681): transcribed, never decided.

`lane_facts` reads, for the head of the landing order, its pushed paths and the syncs its budget
still allows, and for each candidate whether its branch holds master and what it touches. The
lane predicate and its reasons live in `domain.landing_order` and nowhere else; this module hands
it `Member`s and takes no part in the decision. A read that fails is `None` in that fact, never
`False`, `0` or an empty set: the domain reads `None` as "unknown" and never opens a lane on it.

Local git only, and one `tracker.pull_request` read of the head per round. It imports nothing from
`application.poll` (which calls it), so it takes plain values and domain types.

It is also the only writer of the fast lane's informative record (GH-681 part 2): `lane_admitted`
and `lane_recorded` on a member's own document, `lane_merges` on the head's. `admit`,
`record_lane_merges` and `clear_admissions` run only in a round that is not held, and never write
a document of a task at work (`at_work`). No decision reads what they write, and nothing of it goes
into a `landing` list or a `head_*` key: the head's budget and `rules.landing_pass` ignore it."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ..domain import landing, landing_order
from ..domain.task import NOT_LANE, LaneAdmitted, LaneMerge
from . import landing_plan
from .ports import GitError, Ports


@dataclass(frozen=True)
class LaneHead:
    """The head of the order as `lane_facts` reads it: its `issue`, the `reference` of its pull
    request (the URL its document stores), its document `raw` (for the syncs it has spent) and
    whether a live child of an integrating kind runs on it (`landing_order.integrates`, which the
    caller applies)."""

    issue: int
    reference: str
    raw: Mapping[str, object]
    integrating: bool


def _head_oid(ports: Ports, reference: str) -> str | None:
    """The head sha the pull request has now, `None` when it could not be read."""
    try:
        pull_request = ports.tracker.pull_request(reference)
    except (ValueError, OSError):
        return None
    return pull_request.head_oid if pull_request is not None else None


def _touched(ports: Ports, master: str, head: str) -> frozenset[str] | None:
    """What `head` changed since it left `master`, `None` when git could not say."""
    try:
        return frozenset(ports.workspace.touched_paths(master, head))
    except GitError:
        return None


def _syncs_left(raw: Mapping[str, object], head_oid: str) -> int | None:
    """The syncs the head's budget allows still: `LANDING_SYNCS_MAX` less the `sync_master`
    entries of the `landing` list inside the sync epoch, per task and not per sha."""
    try:
        return landing.LANDING_SYNCS_MAX - landing_plan.spent(raw, head_oid, None).syncs
    except (ValueError, TypeError):
        return None


def _head_member(
    ports: Ports, current_master: str, head: LaneHead, member: landing_order.Member
) -> landing_order.Member:
    """`member` (the head) with its own facts: nothing is read for a head that does not
    integrate, and nothing else than the pull request when its head sha is unknown."""
    if not head.integrating:
        return replace(member, integrating=False)
    head_oid = _head_oid(ports, head.reference)
    if head_oid is None:
        return replace(member, integrating=True, paths=None, head_syncs_left=None)
    return replace(
        member,
        integrating=True,
        paths=_touched(ports, current_master, head_oid),
        head_syncs_left=_syncs_left(head.raw, head_oid),
    )


def _holds_master(
    ports: Ports, product_dir: Path, current_master: str, pr_head: str
) -> bool | None:
    try:
        return ports.workspace.is_ancestor(product_dir, current_master, pr_head)
    except GitError:
        return None


def _candidate_member(
    ports: Ports,
    product_dir: Path,
    current_master: str,
    member: landing_order.Member,
    pr_head: str,
) -> landing_order.Member:
    """`member` as a candidate: `merge_now` and clean by the caller's own reading; its branch
    holds master or not, and only when it does are its paths read."""
    holds = _holds_master(ports, product_dir, current_master, pr_head)
    paths = _touched(ports, current_master, pr_head) if holds is True else None
    return replace(member, merge_now=True, clean=True, holds_master=holds, paths=paths)


def lane_facts(
    ports: Ports,
    product_dir: Path,
    current_master: str,
    members: Sequence[landing_order.Member],
    head: LaneHead,
    candidates: Mapping[int, str],
) -> tuple[landing_order.Member, ...]:
    """A copy of `members`, in order, with the lane facts read. `candidates` maps an issue to its
    pull request head sha for each member the caller knows to be decided `merge`, not backed off
    and conflict-free; the others come back unchanged.

    A head that does not integrate is read for nothing, and the candidates still carry `merge_now`
    and `clean` but no master or path fact. No candidate is read when the head's paths are `None`:
    the lane cannot open on them."""
    result: list[landing_order.Member] = []
    read_head: landing_order.Member | None = None
    for member in members:
        if member.issue == head.issue:
            read_head = _head_member(ports, current_master, head, member)
            result.append(read_head)
        else:
            result.append(member)
    readable = read_head is not None and read_head.integrating and read_head.paths is not None
    for index, member in enumerate(result):
        pr_head = candidates.get(member.issue)
        if pr_head is None or member.issue == head.issue:
            continue
        if readable:
            result[index] = _candidate_member(ports, product_dir, current_master, member, pr_head)
        else:
            result[index] = replace(member, merge_now=True, clean=True)
    return tuple(result)


def _instant_text(now: datetime) -> str:
    return now.isoformat()


def _admission(raw: Mapping[str, object]) -> Mapping[str, object] | None:
    admitted = raw.get("lane_admitted")
    return admitted if isinstance(admitted, Mapping) else None


def admit(
    ports: Ports,
    task_id: str,
    raw: Mapping[str, object],
    head_issue: int,
    pr_head: str,
    master: str,
    now: datetime,
    at_work: Callable[[str], bool],
    out: list[str],
) -> None:
    """Record on the lane member's own document that the poller let it merge beside the busy head
    `head_issue`, on pull request head `pr_head` and `master`. Idempotent per `pr_head`: an
    admission already holding that sha is left as it is, with its `at`. A task at work is not
    written; a failed write is one line and the next round tries again."""
    held = _admission(raw)
    if (held is not None and held.get("pr_head") == pr_head) or at_work(task_id):
        return
    admitted: LaneAdmitted = {
        "head_issue": head_issue,
        "pr_head": pr_head,
        "master": master,
        "at": _instant_text(now),
    }
    try:
        ports.documents.record_closure(task_id, {"lane_admitted": admitted})
    except (ValueError, OSError) as error:
        out.append(f"agentic: poll: fast lane admission of {task_id} not recorded: {error}")


def clear_admissions(
    ports: Ports,
    members: Iterable[tuple[str, int]],
    lane: int | None,
    at_work: Callable[[str], bool],
    out: list[str],
) -> None:
    """Clear `lane_admitted` (to `None`) from each `(task_id, issue)` of `members` that is not the
    round's `lane`, is not at work and whose admission no merge followed (`auto_merged_at` later
    than `at`, which recording keeps): a deferred admission is then never credited if the member
    later merges as head. The documents are read fresh. A failed write is one line."""
    for task_id, issue in members:
        if issue == lane or at_work(task_id):
            continue
        raw = _load(ports, task_id)
        admitted = _admission(raw) if raw is not None else None
        if raw is None or admitted is None:
            continue
        admitted_at, merged_at = (
            landing_order.instant(admitted.get("at")),
            landing_order.instant(raw.get("auto_merged_at")),
        )
        if admitted_at is not None and merged_at is not None and merged_at > admitted_at:
            continue
        try:
            ports.documents.record_closure(task_id, {"lane_admitted": None})
        except (ValueError, OSError) as error:
            out.append(f"agentic: poll: fast lane admission of {task_id} not cleared: {error}")


def _load(ports: Ports, task_id: str) -> Mapping[str, object] | None:
    try:
        return ports.documents.load(task_id)
    except (ValueError, OSError):
        return None


def _task_of_issue(ports: Ports, issue: int) -> str | None:
    for task_id in ports.documents.ids():
        if ports.inbox.issue_of(task_id) == issue:
            return task_id
    return None


def _mark(ports: Ports, task_id: str, mark: str, out: list[str]) -> None:
    try:
        ports.documents.record_closure(task_id, {"lane_recorded": mark})
    except (ValueError, OSError) as error:
        out.append(f"agentic: poll: fast lane record of {task_id} not marked: {error}")


def _record(
    ports: Ports,
    task_id: str,
    raw: Mapping[str, object],
    admitted: Mapping[str, object],
    now: datetime,
    at_work: Callable[[str], bool],
    out: list[str],
) -> None:
    """Append the member's merge to its head's `lane_merges`, then mark the member."""
    issue, head_issue = ports.inbox.issue_of(task_id), admitted.get("head_issue")
    pr_head, master, merged_at = (
        admitted.get("pr_head"),
        admitted.get("master"),
        raw.get("auto_merged_at"),
    )
    if not (
        isinstance(issue, int)
        and isinstance(head_issue, int)
        and isinstance(pr_head, str)
        and isinstance(master, str)
        and isinstance(merged_at, str)
    ):
        return
    head_id = _task_of_issue(ports, head_issue)
    if head_id is None:
        out.append(
            f"agentic: poll: fast lane merge of #{issue} not recorded: no #{head_issue} document"
        )
        _mark(ports, task_id, NOT_LANE, out)
        return
    if at_work(head_id):
        return
    try:
        head_raw = ports.documents.load(head_id)
        if head_raw is None:
            raise FileNotFoundError(head_id)
        existing = head_raw.get("lane_merges")
        merges: list[LaneMerge] = list(existing) if isinstance(existing, list) else []
        if not any(m.get("issue") == issue and m.get("pr_head") == pr_head for m in merges):
            merges.append(
                {"issue": issue, "pr_head": pr_head, "master": master, "merged_at": merged_at}
            )
            ports.documents.record_closure(
                head_id, {"lane_merges": merges[-landing_order.LANE_MERGES_MAX :]}
            )
    except (ValueError, OSError) as error:
        out.append(
            f"agentic: poll: fast lane merge of #{issue} not recorded on #{head_issue}: {error}"
        )
        return
    _mark(ports, task_id, _instant_text(now), out)


def record_lane_merges(
    ports: Ports,
    open_ids: Iterable[str],
    now: datetime,
    at_work: Callable[[str], bool],
    out: list[str],
) -> None:
    """Record each fast-lane merge of the round in its head's document (`lane_merges`).

    The candidates are `open_ids` and every document written within `LANE_RECORD_WINDOW` (a merged
    member's document is closed by then), read fresh; those with a `lane_admitted` and no
    `lane_recorded` get `landing_order.lane_merge_verdict`. `record` appends to the head's
    document once per `(issue, pr_head)` and only then marks the member; `not_lane` marks the
    member. A member or head at work is left for a later round, a document that cannot be read or
    written is one line and the member stays unmarked: the dedupe lets the next round finish it."""
    touched = ports.documents.ids_touched_since(now - landing_order.LANE_RECORD_WINDOW)
    for task_id in dict.fromkeys([*open_ids, *touched]):
        raw = _load(ports, task_id)
        admitted = _admission(raw) if raw is not None else None
        if raw is None or admitted is None:
            continue
        verdict = landing_order.lane_merge_verdict(raw)
        if verdict is None or at_work(task_id):
            continue
        if verdict == "record":
            _record(ports, task_id, raw, admitted, now, at_work, out)
        else:
            _mark(ports, task_id, NOT_LANE, out)
