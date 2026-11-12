"""What the system does with the generated docs of a product whose `docs.mode` is `validate`
(GH-603, ADR-0066 and ADR-0074 addenda): a feature branch never keeps a file under
`docs.paths`, master regenerates them after each merge.

Three places call it, each only when the product's `Docs.mode` is `validate` -- in `commit` mode
none of this runs: the delivery gate (`presubmit`) restores what the branch changed, and the
merges of `integrate`, `develop` (a dependency) and `landing_sync` (the upstream) settle a
conflict confined to the owned paths from one `source`, the newer of the merge-bases with master.
The decisions are `domain.docs`'s (`owned`, `split_conflicts`, `settle_source`); this module
reads the facts and carries the answer to the `Workspace`, and every read that compares with
master receives it as an explicit sha."""

from __future__ import annotations

import logging
from pathlib import Path

from ..domain import docs as docs_rules
from ..domain.docs import Docs
from .ports import GitError, MergeConflict, MergeRefusal, Workspace

_LOG = logging.getLogger(__name__)

MASTER = "master"


def validating(docs: Docs | None) -> Docs | None:
    """`docs` when the product asks for the `validate` mode, else `None`: the one test every
    caller makes before it touches anything, so `commit` and no block are the system of before."""
    return docs if docs is not None and docs.mode == "validate" else None


def master_tip(workspace: Workspace, path: Path) -> str | None:
    """The sha master has on the remote (`remote_tip` fetches it), `None` when it has none
    or cannot be read."""
    try:
        return workspace.remote_tip(path, MASTER)
    except GitError:
        return None


def source_of(workspace: Workspace, path: Path, theirs: str, master: str) -> str | None:
    """The commit a conflict confined to the owned paths is restored from, merging `theirs`
    into the checkout at `path`: `domain.docs.settle_source` over the merge-bases of the
    checkout's head and of `theirs` with `master`. `None` when git cannot say or neither base is
    newer: the caller then does what it did before."""
    try:
        ours_base = workspace.merge_base(workspace.full_head(path), master)
        theirs_base = workspace.merge_base(theirs, master)
        ancestry = workspace.ancestry(path, ours_base, theirs_base)
    except GitError:
        return None
    return docs_rules.settle_source(ours_base, theirs_base, ancestry)


def settled_source(
    workspace: Workspace, path: Path, docs: Docs, conflicted: tuple[str, ...], theirs: str
) -> tuple[tuple[str, ...], tuple[str, ...], str] | None:
    """`(owned, others, source)` for a conflict of merging `theirs` that has an owned part, or
    `None` when it has none or the source cannot be read. Master is read here, once."""
    owned, others = docs_rules.split_conflicts(docs, conflicted)
    if not owned:
        return None
    master = master_tip(workspace, path)
    source = source_of(workspace, path, theirs, master) if master is not None else None
    return None if source is None else (owned, others, source)


def settle_refused_merge(
    workspace: Workspace, path: Path, branch: str, docs: Docs | None, refusal: MergeRefusal
) -> bool:
    """Whether `branch` is now merged into the checkout at `path`, after `Workspace.merge`
    refused with `refusal` (the integration's merge): a conflict confined to the owned paths
    of a `validate` product is merged again, settled from one source (`merge_settling`). A mixed
    conflict, a refusal that is not a conflict and a source that cannot be read stay refused,
    the checkout as `merge` left it."""
    block = validating(docs)
    if block is None or not isinstance(refusal, MergeConflict):
        return False
    found = settled_source(workspace, path, block, refusal.paths, branch)
    if found is None or found[1]:
        return False
    owned, _, source = found
    if workspace.merge_settling(path, branch, owned, source) is not None:
        return False
    _LOG.info("agentic: docs: merged %s settling %s from %s", branch, ", ".join(owned), source)
    return True


def settle_open_merge(
    workspace: Workspace, path: Path, docs: Docs | None, conflicted: tuple[str, ...], theirs: str
) -> tuple[str, ...] | None:
    """For a merge of `theirs` left open in the checkout at `path` with `conflicted` paths: the
    owned ones settled from one source and staged (`settle_owned`), and the paths still
    conflicted returned -- empty when nothing else was, in which case the merge is concluded
    here with no Developer. `None` when nothing was settled (no block in `validate` mode, no
    owned path, an unreadable source or a git failure): the caller treats the merge as
    before, with every path still its own."""
    block = validating(docs)
    if block is None:
        return None
    found = settled_source(workspace, path, block, conflicted, theirs)
    if found is None:
        return None
    owned, others, source = found
    try:
        workspace.settle_owned(path, owned, source)
        if not others:
            workspace.commit_all(path, f"Merge {theirs}, settling {', '.join(owned)} from master")
    except GitError as error:
        _LOG.warning("agentic: docs: settling %s failed: %s", ", ".join(owned), error)
        return None
    _LOG.info("agentic: docs: settled %s from %s", ", ".join(owned), source)
    return others
