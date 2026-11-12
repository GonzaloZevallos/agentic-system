"""What a task's pull request changed, read for the record: the files (ADR-0054) the plan's
file estimate is compared with, and the hand-written size (GH-592) its size budget is compared
with. A read that fails never fails the close or the merge that asked for it; the task just
carries no `actual_files` or no `actual_size`."""

from __future__ import annotations

from collections.abc import Callable

from ..domain import rules, size_budget
from .ports import GitError, Ports


def files_patch(reference: str, ports: Ports, log: Callable[[str], None]) -> dict[str, int]:
    """`{"actual_files": n}` for the pull request at `reference`, written only through
    `rules.delivered_files`; empty when the tracker cannot answer."""
    try:
        return rules.delivered_files(ports.tracker.changed_files(reference))
    except (ValueError, OSError) as exc:
        log(f"agentic: changed files of {reference} unreadable, actual_files omitted: {exc}")
        return {}


def size_patch(
    task_id: str, upstream: str, head: str, ports: Ports, log: Callable[[str], None]
) -> dict[str, object]:
    """`{"actual_size": {...}}` for what `head` adds over `upstream`: the numstat rows of the
    workspace, measured against the globs the product declares generated at the task's base
    commit (`docs.paths` and `generated:`; the lockfiles are `size_budget`'s own). The record is
    written only through `size_budget`. Empty, with one log line, when git or the stack cannot
    answer."""
    try:
        base = ports.workspace.base_of(rules.task_branch(task_id)) or "HEAD"
        docs = ports.stack.docs(base)
        globs = (*(docs.paths if docs else ()), *ports.stack.generated(base))
        rows = ports.workspace.numstat(upstream, head)
    except (GitError, ValueError, OSError) as exc:
        log(f"agentic: size of {head} unreadable, actual_size omitted: {exc}")
        return {}
    return dict(size_budget.measure(rows, globs).to_frontmatter())
