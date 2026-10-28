"""What a poller round does about the `agentic-ci/<id>` refs `full_checks` leaves on
`origin` (GH-376): delete those of tasks that ended and have no live run.

The decision is pure (`rules.stale_ci_refs`); this module gathers the facts from the ports
and carries it out through `Workspace.delete_remote_ref`. It is the only deleter of those
refs, and it never ends a round: a listing, a document or a delete that fails is a line."""

from __future__ import annotations

from collections.abc import Collection

from ..domain import rules
from .ports import GitError, Ports
from .scratch_sweep import is_ended


def _ended(ports: Ports, task_id: str) -> bool:
    """Whether the document of `task_id` says it ended; a missing document is not ended."""
    return is_ended(ports.documents.load(task_id) or {})


def sweep(ports: Ports, live_ids: Collection[str]) -> list[str]:
    """Deletes the CI refs whose task ended and has no run in `live_ids`. One line when some
    were deleted and one per failure; nothing when there is nothing to do. A ref whose
    document cannot be read is kept. A product that declares no `ci:` never published such a
    ref: it makes no call to the remote at all (one line when its `ci:` cannot be read)."""
    try:
        if not ports.stack.ci("HEAD"):
            return []
    except ValueError as exc:
        return [f"agentic: poll: CI ref sweep skipped, ci: unreadable: {exc}"]
    try:
        refs = ports.workspace.remote_refs(rules.CI_REF_PREFIX)
    except GitError as exc:
        return [f"agentic: poll: CI ref sweep failed to list refs: {exc}"]
    ended: set[str] = set()
    lines: list[str] = []
    for ref in refs:
        task_id = ref.removeprefix(rules.CI_REF_PREFIX)
        if not ref.startswith(rules.CI_REF_PREFIX) or task_id in live_ids:
            continue
        try:
            if _ended(ports, task_id):
                ended.add(task_id)
        except (OSError, ValueError) as exc:
            lines.append(f"agentic: poll: CI ref sweep kept {ref}: document unreadable: {exc}")
    deleted = 0
    for ref in rules.stale_ci_refs(refs, live_ids, ended):
        try:
            ports.workspace.delete_remote_ref(ref)
        except GitError as exc:
            lines.append(f"agentic: poll: CI ref sweep failed to delete {ref}: {exc}")
        else:
            deleted += 1
    if deleted:
        lines.insert(0, f"agentic: poll: swept {deleted} CI ref(s)")
    return lines
