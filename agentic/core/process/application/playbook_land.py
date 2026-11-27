"""The landing of the playbook pull request by itself (owner's rule of 2026-11-16: playbook
lessons land with no human approval).

`land_playbook` reads, hands every fact to `playbook_land.land_verdict` (the one function that
says `Merge`), carries out what it says and records the attempt. It never commits, pushes,
merges master into a branch, closes a pull request or writes `in_prompt_since`; the only write
it makes is the `LandRecord`, and the only call that changes the world is `Tracker.merge`."""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from typing import Literal

from ..domain import ci, master_gain, rules
from ..domain import playbook_candidates as pc
from ..domain import playbook_land as pl
from .ports import (
    GitError,
    LandRecord,
    ListedPullRequest,
    MergeCallFailed,
    Ports,
    TrackerTurnRefused,
)
from .reflect_run import BRANCH

_PREFIX = pc.PLAYBOOKS_DIR + "/"


def _open_pull_request(ports: Ports) -> ListedPullRequest | None:
    listed = ports.tracker.list_pull_requests()
    return next((pr for pr in listed if pr.branch == BRANCH and pr.state == "OPEN"), None)


def _held_at(ports: Ports, head: str, path: str) -> bool:
    """Whether the tree of `head` holds `path` (`GitError` when git cannot say)."""
    directory, _, name = path.rpartition("/")
    return name in ports.workspace.tree_names(head, directory)


def _contents(
    ports: Ports, master_sha: str, head: str
) -> tuple[tuple[str, ...], dict[str, str | None]] | None:
    """The paths the pull request changes against master and, for each one inside the playbook
    directory, its text at `head` (`None`: deleted). `None` when git cannot say, so nothing is
    ever judged on an empty list a failed read left behind."""
    try:
        if not ports.workspace.fetch_commit(head):
            return None
        paths = ports.workspace.touched_paths(master_sha, head)
        files: dict[str, str | None] = {}
        for path in paths:
            # Only a path already inside the directory is read: any other is refused by name.
            inside = path.startswith(_PREFIX) and posixpath.normpath(path) == path
            text = ports.workspace.show(head, path) if inside else None
            if inside and text is None and _held_at(ports, head, path):
                return None  # present at `head` yet unreadable: a failed read is not a deletion
            files[path] = text
    except GitError:
        return None
    return paths, files


def _master_covered(ports: Ports, head: str, master_sha: str) -> bool:
    """Whether `head` has master's gain: it contains master, or what master gained since the
    merge base is inert under the globs `head` declares (the reading the merge gate uses).
    Whatever cannot be read is not covered."""
    containment = ports.workspace.contains(head, master_sha)
    if containment != "no":
        return containment == "yes"
    try:
        base = ports.workspace.merge_base(head, master_sha)
        gain = ports.workspace.gain_paths(base, master_sha)
        globs = ports.stack.merge_inert(head)
        setup = list(ports.stack.setup(master_sha).values())
    except (GitError, ValueError):
        return False
    return master_gain.gain_inert(gain, globs, setup)


def _verdict(
    ports: Ports,
    pr: ListedPullRequest,
    names: tuple[str, ...],
    master_sha: str | None,
) -> tuple[pl.Verdict, str]:
    """The verdict and the head it was taken on (`""` when no head was read)."""
    if (foreign := pl.foreign_refusal(pr.cross_repository)) is not None:
        return foreign, ""
    if master_sha is None:
        return pl.Wait("master_unreadable"), ""
    first = ports.tracker.ci_report(pr.url)
    if isinstance(first, ci.CiUnreadable):
        return pl.Wait("ci_unreadable"), ""
    head = first.head_sha
    read = _contents(ports, master_sha, head)
    if read is None:
        return pl.land_verdict((), "ok", first, names, False, False), head
    paths, files = read
    report: ci.CiReport | ci.CiUnreadable = first
    confirmed = False
    if ci.unreported(first, names, True):
        # A name that never reported is only believed after a second read of the same head.
        report = ports.tracker.ci_report(pr.url)
        confirmed = True
        if isinstance(report, ci.CiReport) and not rules.same_commit(report.head_sha, head):
            return pl.Wait("ci_pending"), head
    verdict = pl.land_verdict(
        paths,
        pl.content_verdict(files),
        report,
        names,
        _master_covered(ports, head, master_sha),
        confirmed,
    )
    return verdict, head


def _merged(ports: Ports, pr: ListedPullRequest, verdict: pl.Merge) -> pl.Verdict:
    try:
        ports.tracker.merge(pr.url, verdict.head, method="merge")
    except TrackerTurnRefused:
        return pl.Failed("turn_refused")  # `gh` never launched: not a failed merge call
    except MergeCallFailed as exc:
        if exc.failure == "no_merge_method":
            return pl.Refuse("no_merge_commit")
        return pl.Failed(exc.failure)
    return verdict


def _kind(verdict: pl.Verdict) -> str:
    return type(verdict).__name__.lower()


def _summary(verdict: pl.Verdict, head: str) -> str:
    cause = "" if isinstance(verdict, pl.Merge) else f" {verdict.cause}"
    return f"agentic: reflect: playbook landing {_kind(verdict)}{cause} {head or '-'}".rstrip()


def land_playbook(
    ports: Ports,
    record: LandRecord,
    product: str,
    names: Sequence[str],
    now: str,
    master_sha: str | None,
) -> tuple[pl.Verdict | Literal["none"], str]:
    """One attempt to land the open playbook pull request: `("none", ...)` and nothing written
    when there is none, else the verdict taken, a one-line summary, and the attempt recorded.
    `names` are the checks the stack declares on master and `master_sha` the master this
    reads against (`None`: it could not be read, which waits)."""
    pr = _open_pull_request(ports)
    if pr is None:
        return "none", "agentic: reflect: playbook landing: no open playbook pull request"
    verdict, head = _verdict(ports, pr, tuple(names), master_sha)
    if isinstance(verdict, pl.Merge):
        verdict = _merged(ports, pr, verdict)
    record.save(product, pl.next_attempt(record.load(product), verdict, head, now, master_sha))
    return verdict, _summary(verdict, head)
