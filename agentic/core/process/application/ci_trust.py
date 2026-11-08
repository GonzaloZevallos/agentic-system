"""Whether the product's own CI may judge a head (GH-565, ADR-0080 addendum): the one rule the
`full_checks` node, `full_checks_mode`, the review's speculative push and the merge verb share, so
they cannot drift apart again.

A head is judged by what it brings in. GH-547 fixed the merge to read `merge-base(head,
master)...head`; a task that merged master past its base would otherwise count master's own
`.github/` commits as its own. `read_range` is that read. `read` is the lazy one for the callers
that start from the task base: it asks `task_base...head` first and resolves the master only for a
head that diff distrusts, so a head the task base trusts costs no fetch and no `merge_base`. The
rule only ever moves a head from untrusted to trusted, never the other way."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from ..domain import rules
from ..domain.presubmit import CiDispatch
from .ports import GitError, Ports, Upstream

# Why a read stayed on the task base's diff or could not say more: a closed value chosen here,
# never text read back from git. `none` is a read that needed nothing else (or had a master and
# was decided by it).
TrustFallback = Literal["none", "no_master", "unfetched", "merge_base_unreadable"]


@dataclass(frozen=True)
class TrustRead:
    """One reading of a head's trust: the revision its diff starts from, the diff (`None` when
    git could not give it, which `rules.ci_untrusted` fails closed on), the `ci_dispatch` an
    untrusted head would be judged by, the merge-base error text when `read_range` could not read
    the range, and which `TrustFallback` the read ended on."""

    range_base: str
    paths: tuple[str, ...] | None
    dispatch: CiDispatch | None
    merge_base_error: str | None = None
    fallback: TrustFallback = "none"

    @property
    def trusted(self) -> bool:
        """Whether the product's own CI may judge the head: false when the diff touches the CI's
        definition or could not be read."""
        return rules.ci_untrusted(self.paths) is False


def trust_master(upstream: Upstream) -> str | None:
    """The master a trust read may use: `upstream.sha` unless there is none or it is `HEAD`
    itself (no remote branch to compare with), which would only compare the head with itself."""
    if upstream.ref == "HEAD" or upstream.sha is None:
        return None
    return upstream.sha


def dispatch_at(ports: Ports, revision: str) -> CiDispatch | None:
    """The workflow the stack names for an untrusted head, read at `revision`; a stack that does
    not load there names none, and nothing is raised."""
    try:
        return ports.stack.ci_dispatch(revision)
    except ValueError:
        return None


def read_range(ports: Ports, head: str, master: str) -> TrustRead:
    """`merge-base(head, master)...head` and the `ci_dispatch` at `master` (the dispatch runs the
    default branch's workflow). A `GitError` from the merge base or the diff is the text in
    `merge_base_error` with no paths: the caller decides what an unreadable range means."""
    range_base = ""
    try:
        range_base = ports.workspace.merge_base(head, master)
        paths: tuple[str, ...] | None = ports.workspace.changed_paths(range_base, head)
    except GitError as exc:
        return TrustRead(range_base, None, dispatch_at(ports, master), str(exc))
    return TrustRead(range_base, paths, dispatch_at(ports, master))


def _task_base_read(ports: Ports, head: str, task_base: str) -> TrustRead:
    try:
        paths: tuple[str, ...] | None = ports.workspace.changed_paths(task_base, head)
    except GitError:
        paths = None
    return TrustRead(task_base, paths, dispatch_at(ports, task_base))


def read(
    ports: Ports,
    head: str,
    *,
    task_base: str,
    resolve_master: Callable[[], Upstream],
) -> TrustRead:
    """The trust of `head` for a caller that starts from the task base. A head `task_base...head`
    trusts is returned at once: `resolve_master` is not called and nothing is fetched. Otherwise
    the master is resolved (at most once, it may fetch) and the range is read at
    `merge-base(head, master)`; no real master or an unreadable merge base keeps the task base's
    read, never a better one, and says why in `fallback`."""
    own = _task_base_read(ports, head, task_base)
    if own.trusted:
        return own
    upstream = resolve_master()
    master = trust_master(upstream)
    if master is None:
        return replace(own, fallback="no_master")
    ranged = read_range(ports, head, master)
    if ranged.merge_base_error is not None:
        return replace(own, fallback="merge_base_unreadable")
    if not ranged.trusted and upstream.note is not None:
        return replace(ranged, fallback="unfetched")
    return ranged
