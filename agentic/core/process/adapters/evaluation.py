"""The ports of an evaluation run (GH-470, part 2, ADR on the evaluation run): decorators
over the real `Tracker`, `Workspace` and `ProductStack` that take away what an evaluation
may not do -- write to the tracker, push a ref -- so no use case needs an `if` on evaluation.
`composition.default_ports` wraps the ports with them when, and only when, the product root
is an evaluation root (`evaluation.is_evaluation_root`); a document's `evaluation` key never
decides it.

**Fail closed.** Each decorator names what it lets through (`ALLOWED`) and what it
overrides; any other name -- a member added to the port tomorrow and left unclassified --
answers a call that raises `EvaluationWrite`, so a new write cannot reach the real tracker
by being forgotten. `tests/core/process/adapters/test_evaluation.py` fails on an unclassified
member of the port, which is the loud version of the same rule.

**What a write keyed by task id does.** `comment`, `label`, `unlabel`, `note` and
`drop_label` are the informative writes the process makes on its own issue; an evaluation
run has no issue of its own (its id is `EVAL-...`), so they do nothing and report success.
Anything else that writes (a pull request, a new issue, a merge, a dispatched workflow)
raises, because an evaluation reaching it is a bug in the wiring, not a thing to hide.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from ..application.ports import IssueComment, ProductStack, Tracker, Workspace
from ..domain.landing import IssueRef
from ..domain.task import TaskInput, TrackerFailure


class EvaluationWrite(RuntimeError):
    """An evaluation run tried to write where it may not: the tracker or a remote ref."""


def _refusal(port: str, name: str) -> Callable[..., NoReturn]:
    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise EvaluationWrite(
            f"{port}.{name}: an evaluation run writes neither the tracker nor a ref"
        )

    return refuse


class EvaluationTracker:
    """`Tracker` for an evaluation root. `ALLOWED` are the reads that pass through; the
    informative writes and the two comment listings are overridden below."""

    ALLOWED = frozenset(
        {
            "branch_ci",
            "changed_files",
            "ci_report",
            "dispatched_ci",
            "issue_created_at",
            "issue_state",
            "list_pull_requests",
            "pull_request",
            "pull_requests_on",
            "queue",
        }
    )

    # The writes that raise. Listed so the classification is exhaustive; `__getattr__`
    # refuses them, as it refuses a name nobody classified.
    REFUSED = frozenset(
        {
            "close_issue",
            "close_pull_request",
            "comment_on_issue",
            "dispatch_ci",
            "ensure_label",
            "merge",
            "open_issue",
            "open_pull_request",
            "reopen_pull_request",
            "rerun_ci_run",
            "rerun_failed_ci",
            "update_pull_request_body",
        }
    )

    def __init__(self, inner: Tracker) -> None:
        self._inner = inner

    def issue_comments(self, task_id: str) -> list[str]:
        """None: the task of an evaluation is its frozen entry, never what humans said on
        the real issue afterwards."""
        return []

    def timestamped_comments(self, task_id: str) -> list[IssueComment]:
        return []

    def comment(self, task_id: str, text: str) -> None:
        return None

    def label(self, task_id: str, label: str) -> None:
        return None

    def unlabel(self, task_id: str, label: str) -> None:
        return None

    def note(self, task_id: str, text: str) -> TrackerFailure | None:
        return None

    def drop_label(self, task_id: str, label: str) -> TrackerFailure | None:
        return None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.ALLOWED:
            return getattr(self._inner, name)
        return _refusal("tracker", name)


class EvaluationWorkspace:
    """`Workspace` for an evaluation root. Every local operation passes through (`ALLOWED`:
    the run builds and merges on its own checkouts); the members that write to a
    remote are overridden and raise, for any ref -- `agentic-ci/<id>` included."""

    ALLOWED = frozenset(
        {
            "abort_merge",
            "added_adrs",
            "added_lines",
            "base_of",
            "branch_head",
            "branches",
            "build_slice",
            "changed_lines",
            "changed_paths",
            "commit_messages",
            "checkout_names",
            "checkout_path",
            "cleanup",
            "commit_all",
            "commit_archived_document",
            "commit_date",
            "commit_paths",
            "contains",
            "create_branch",
            "dependency_state",
            "detached_checkout",
            "differs_outside",
            "discard",
            "fast_forward_view",
            "fetch_commit",
            "fetched_upstream",
            "first_parent_commits",
            "full_head",
            "gain_paths",
            "git_version",
            "head",
            "integration_paths",
            "introduced_paths",
            "is_ancestor",
            "is_clean",
            "is_committed",
            "is_ignored",
            "merge",
            "merge_base",
            "merge_conflicts",
            "merge_head",
            "merge_in_progress",
            "merge_keeping_conflicts",
            "merge_resolution",
            "merge_upstream_into",
            "numstat",
            "overlay",
            "paths_naming",
            "prune",
            "record_setup_tree",
            "remote_dispatch_refs",
            "remote_refs",
            "remote_tip",
            "remove_checkout",
            "rename_branch",
            "repository_at",
            "reset_hard",
            "restore_clean",
            "setup_tree",
            "show",
            "subtask_checkout",
            "task_checkout",
            "tree",
            "tree_changes",
            "tree_names",
            "upstream",
            "worktrees",
            "write_index",
        }
    )

    def __init__(self, inner: Workspace) -> None:
        self._inner = inner

    def push(self, branch: str, *, lease: str | None = None) -> None:
        raise EvaluationWrite(f"workspace.push({branch}): an evaluation run pushes no ref")

    def publish_ci_ref(self, path: Path, ref: str) -> None:
        raise EvaluationWrite(f"workspace.publish_ci_ref({ref}): an evaluation run pushes no ref")

    def delete_ci_ref(self, path: Path, ref: str) -> None:
        raise EvaluationWrite(f"workspace.delete_ci_ref({ref}): an evaluation run pushes no ref")

    def publish_dispatch_ref(self, path: Path, ref: str) -> None:
        raise EvaluationWrite(
            f"workspace.publish_dispatch_ref({ref}): an evaluation run pushes no ref"
        )

    def delete_dispatch_ref(self, ref: str) -> None:
        raise EvaluationWrite(
            f"workspace.delete_dispatch_ref({ref}): an evaluation run deletes no remote ref"
        )

    def delete_remote_ref(self, ref: str) -> None:
        raise EvaluationWrite(
            f"workspace.delete_remote_ref({ref}): an evaluation run deletes no remote ref"
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.ALLOWED:
            return getattr(self._inner, name)
        return _refusal("workspace", name)


class EvaluationStack:
    """`ProductStack` for an evaluation root: it declares no `ci:`, so `full_checks` takes
    its existing local path -- the whole suite runs on this machine and nothing is pushed
    for a hosted CI to judge. Everything else is the product's own."""

    def __init__(self, inner: ProductStack) -> None:
        self._inner = inner

    def ci(self, revision: str) -> tuple[str, ...]:
        return ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)


class ClosedInbox:
    """The inbox of an evaluation root whose frozen entry is not at the conventional path
    (`file_inbox.frozen_input_path`): it lists no issue and holds no task, so nothing is
    ever read from the tracker through it. A run handed its entry with `--issue-input`
    replaces it with the file's own `FileInbox` (`cli`)."""

    def task(self, number: int) -> TaskInput:
        raise ValueError(f"issue {number}: an evaluation root has no frozen entry to read")

    def task_id(self, number: int) -> str:
        raise ValueError(f"issue {number}: an evaluation root has no frozen entry to read")

    def issue_of(self, task_id: str) -> int | None:
        return None

    def open_issues(self) -> list[tuple[str, str]]:
        return []

    def open_issue_bodies(self) -> list[tuple[str, str]]:
        return []

    def open_issue_refs(self) -> list[IssueRef]:
        return []
