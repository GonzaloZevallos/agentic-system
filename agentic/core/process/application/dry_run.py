"""Read-only ports for a dry poll round (GH-622, part 1b): a protection proxy.

`ReadOnly` wraps one port. A name in the port's reads table delegates to the adapter; a name in
its write tables records `would <port>.<method> <args>` and returns a neutral value; any other
name is recorded in `DryRunLog.refused` and raises `DryRunRefused`, so an unclassified member
can never reach an effect. A port with no table at all is wrapped with empty tables: every touch
is refused.

What a dry round does differently from a live one (also in `agentic poll --help`):
- The four sweeps (`close_issues.sweep`, `scratch_sweep.sweep`, `ci_ref_sweep.sweep`,
  `board_sync.mirror`) are skipped (GH-348: a blocker the live close sweep would close this
  round stays open, so its dependent is not launched).
- No first-seen exempt stamp is recorded, so the exempt order can differ.
- `publish_spend` returns `[]`: this product's own spend is not published.
- The land and exempt waits the round would publish are not visible to other pollers.
- `exclusive` is a `nullcontext()`: no atomicity between pollers, harmless as nothing launches.
- `Workspace.upstream` (the declared exception, a read) is the one network effect. The other
  fetchers write git objects: `fetch_commit` says `False`, `contains` `"unknown"`, `merge_conflicts`
  `None`, and `merge_base` raises `GitError` (nothing was fetched, the base is unreadable), so
  a landing candidate's criteria probe (`poll.read_criteria`) reads as unreadable and the
  decision refuses the merge, as it does when git fails.
"""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field, fields, replace
from typing import Any

from agentic.core.process.application.ports import (
    GitError,
    Launcher,
    Ports,
    RunLock,
    RunRegistry,
)

Factory = Callable[[object, tuple[object, ...]], object]
# `replace` is checked field by field against the keywords it is given; the wrapped ports are
# chosen by name at run time, so it is called through a signature that takes any keywords.
_replace: Callable[..., Ports] = replace
_ARG_WIDTH = 40


class DryRunRefused(Exception):
    """A port member the tables do not classify: the dry round stops rather than guess."""


@dataclass
class DryRunLog:
    writes: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


class ReadOnly:
    def __init__(
        self,
        inner: object,
        port_name: str,
        reads: frozenset[str],
        none_writes: frozenset[str],
        valued_writes: dict[str, Factory],
        log: DryRunLog,
    ) -> None:
        self._inner = inner
        self._port = port_name
        self._reads = reads
        self._none_writes = none_writes
        self._valued = valued_writes
        self._log = log

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._reads:
            return getattr(self._inner, name)
        if name in self._none_writes or name in self._valued:
            return self._noting(name)
        self._log.refused.append(f"{self._port}.{name}")
        raise DryRunRefused(f"dry-run: unclassified {self._port}.{name}")

    def _noting(self, name: str) -> Callable[..., object]:
        def call(*args: object, **kwargs: object) -> object:
            shown = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
            short = ", ".join(text[:_ARG_WIDTH] for text in shown)
            self._log.writes.append(f"would {self._port.lower()}.{name} {short}".rstrip())
            make = self._valued.get(name)
            return make(self._inner, args) if make else None

        return call


def _names(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Each table is classified by what the production adapter does: a mkdir, a file written or
# deleted, a lock taken or settled, a ref pushed or deleted, a comment, label or close, a
# document saved, a spawn is a write. `RunRegistry.exclusive` mkdirs, so it is a write. The
# adapters are `adapters/github/tracker.py` (Tracker), `filesystem/documents.py`,
# `filesystem/lock.py`, `filesystem/run_registry.py`, `filesystem/scratch.py`,
# `filesystem/free_space.py`, `filesystem/experiment_file.py`, `github/inbox.py`, and
# `git/workspace.py`; the READS of each create and delete nothing (the filesystem ones are
# audited in test_dry_run). The one declared exception is `Workspace.upstream` (the fetch), a
# read. The other fetchers (`fetch_commit`, `merge_base`, `merge_conflicts`, `contains`) write
# git objects, so they are writes below.
READS = {
    "Tracker": _names(
        """branch_ci changed_files ci_report dispatched_ci issue_comments issue_created_at
        issue_state list_pull_requests pull_request pull_request_body pull_requests_on queue
        timestamped_comments"""
    ),
    "TaskDocuments": _names("flakes_since ids ids_touched_since load path_for text"),
    "Workspace": _names(
        """added_adrs added_lines adr_first_date ancestry base_of branch_head branches
        changed_lines changed_paths checkout_names checkout_path commit_date commit_messages
        dependency_state differs_outside fast_forward_view fetched_upstream first_parent_commits
        full_head gain_paths git_version head introduced_paths is_ancestor is_clean is_committed
        is_ignored merge_head merge_in_progress merge_resolution numstat owned_drift paths_naming
        remote_dispatch_refs remote_refs
        repository_at setup_tree show touched_paths tree tree_changes tree_names upstream
        worktrees"""
    ),
    "RunLock": _names("read refusal"),
    "RunRegistry": _names("exempt_attempts exempt_waits land_waits launch_attempts live spend"),
    "Scratch": _names("kept_folders listing partitions product_key"),
    "FreeSpace": _names("read"),
    "Inbox": _names("issue_of open_issue_bodies open_issue_refs open_issues task task_id"),
    "ProductStack": _names(
        """adr_naming affected_checks affected_fallback api_break_line check_budgets
        check_timeouts checks ci ci_covers ci_dispatch delivery_checks design docs docs_pathspecs
        forbidden generated merge_inert not_ready_exit parallel_checks performance product
        rerun_checks risk_areas setup verifications witness"""
    ),
    "ExperimentFile": _names("read"),
    "Launcher": frozenset(),
}

# Writes whose return annotation admits `None`: noted, and `None` comes back.
NONE_WRITES = {
    "Tracker": _names(
        """close_issue close_pull_request comment comment_on_issue dispatch_ci drop_label
        ensure_label label note reopen_pull_request rerun_ci_run unlabel update_pull_request_body"""
    ),
    "TaskDocuments": _names("write_into"),
    "Workspace": _names(
        """abort_merge build_slice cleanup commit_all commit_archived_document commit_paths
        create_branch delete_ci_ref delete_dispatch_ref delete_remote_ref discard
        integration_paths merge merge_settling overlay prune publish_ci_ref publish_dispatch_ref
        push record_setup_tree remote_tip remove_checkout rename_branch reset_hard restore_clean
        settle_owned write_index"""
    ),
    "RunLock": _names(
        "acquire keep_at_shutdown keep_crashed keep_stopped mark_stopped_recordless release"
    ),
    "RunRegistry": _names(
        """clear_exempt_attempt clear_launch mark_exempt_starved publish_exempt_wait
        publish_land_wait record_exempt_attempt record_exempt_seen record_launch register
        unregister"""
    ),
    "Scratch": _names("sweep"),
}


def _unreadable_base(inner: object, args: tuple[object, ...]) -> object:
    raise GitError("dry-run: nothing is fetched, so no merge base is read")


# Writes that must hand back a value of their annotated type: a fresh one on each call.
WRITES_VALUED: dict[str, dict[str, Factory]] = {
    "Tracker": {"rerun_failed_ci": lambda inner, args: []},
    "TaskDocuments": {
        "save": lambda inner, args: getattr(inner, "path_for")(getattr(args[0], "task_id")),
        "record_closure": lambda inner, args: getattr(inner, "path_for")(args[0]),
    },
    "RunRegistry": {
        "exclusive": lambda inner, args: nullcontext(),
        "flake_lock": lambda inner, args: nullcontext(False),
        "publish_spend": lambda inner, args: [],
    },
    # Nothing fetched: the head is unknown, the probe is no verdict (the planner doubts, it
    # never launches on it).
    "Workspace": {
        "contains": lambda inner, args: "unknown",
        "merge_conflicts": lambda inner, args: None,
        "fetch_commit": lambda inner, args: False,
        "merge_base": _unreadable_base,  # its Protocol answer is a sha or a `GitError`
    },
    "Launcher": {"spawn": lambda inner, args: 0},
}

# Writes with no neutral value: no round reaches them, and if one does it is refused like an
# unclassified name. They are listed so the tables stay exhaustive against the Protocols.
UNREACHED = {
    "Tracker": _names("merge open_issue open_pull_request"),
    "Workspace": _names(
        """detached_checkout merge_keeping_conflicts merge_upstream_into restore_owned
        subtask_checkout task_checkout"""
    ),
    "Scratch": _names("ensure_kept open"),
}

# Ports that are not ports: `Ports` carries the run's limits and its clock beside them.
_NOT_PORTS = frozenset({"limits", "now"})
_LABELS = {
    "documents": "TaskDocuments",
    "stack": "ProductStack",
    "experiment": "ExperimentFile",
    "tracker": "Tracker",
    "workspace": "Workspace",
    "inbox": "Inbox",
    "scratch": "Scratch",
    "free_space": "FreeSpace",
}


def _wrap(inner: object, label: str, log: DryRunLog) -> ReadOnly:
    return ReadOnly(
        inner,
        label,
        READS.get(label, frozenset()),
        NONE_WRITES.get(label, frozenset()),
        WRITES_VALUED.get(label, {}),
        log,
    )


def read_only_ports(
    ports: Ports, registry: RunRegistry, lock: RunLock, launcher: Launcher | None
) -> tuple[Ports, RunRegistry, RunLock, Launcher, DryRunLog]:
    """Every port of `ports` (plus the registry, the lock and the launcher) behind a `ReadOnly`;
    a port without a table gets empty ones. `launcher` is `None` in a dry round: no process
    exists to spawn with, and its `spawn` only notes."""
    log = DryRunLog()
    wrapped = {
        f.name: _wrap(getattr(ports, f.name), _LABELS.get(f.name, f.name), log)
        for f in fields(ports)
        if f.name not in _NOT_PORTS and getattr(ports, f.name) is not None
    }
    return (
        _replace(ports, **wrapped),
        _wrap(registry, "RunRegistry", log),
        _wrap(lock, "RunLock", log),
        _wrap(launcher, "Launcher", log),
        log,
    )
