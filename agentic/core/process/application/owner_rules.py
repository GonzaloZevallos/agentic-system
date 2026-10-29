"""The `owner_rules` node (GH-431): a system step, no role and no model, that writes the rules
the owner marked (`Owner's rule:`) in the issue's comments into the task's own checkout.

Reached from `rules.after_integrate` once every unit is approved and before `full_checks`
(`rules.owner_rules_due`: the head it last observed is not the current one); always back to
the fan-in. It reads the issue's comments, keeps those whose author is the configured
`AGENTIC_OWNER_LOGIN` (none when unset: nothing is written), appends each rule to the product's
decisions file (or the system's, which the repo has only if it is agentic-system itself), commits
only those files on the task branch and records the commit in `close_docs`, so
`rules.head_reviewed` covers it. A rule that cannot be written here (a system rule in a product
without the system file, or one that broke a text limit) is kept in `owner_rules_elsewhere` for
the ready comment. A failure to read the tracker or to commit is a note in the document, never
an escalation."""

from __future__ import annotations

from pathlib import Path

from agentic.shared.configuration import Configuration
from agentic.shared.owner_decisions import OWNER_DECISIONS

from ..domain import rules
from ..domain.roles import DEVELOPER_INTEGRATOR
from ..domain.task import OwnerRuleElsewhere, TaskState
from .checkouts import SetupFailed, task_checkout_in_thread
from .ports import GitError, OwnerRuleEntry, Ports
from .run_role import finish

SECTION = "Owner rules"
PRODUCT_FILE, SYSTEM_FILE = OWNER_DECISIONS
_UNSET = "owner rules not written: AGENTIC_OWNER_LOGIN unset"
_UNREAD = "owner rules not written: the issue's comments could not be read"
_UNCOMMITTED = "owner rules not written: the commit failed"


async def owner_rules(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    seen = _compare_head(state)
    if state.get("pull_request"):
        # A landing pass reaching the fan-in again: rules given under an open pull request are
        # the owner's small issue's channel, and a landing head carries no close-time cover.
        return finish(state, {"owner_rules_seen": seen}, ports)
    if not cfg.owner_login:
        return finish(state, _observed(state, seen, _UNSET), ports)
    try:
        comments = ports.tracker.timestamped_comments(state["id"])
    except Exception:
        return finish(state, _observed(state, seen, _UNREAD), ports)
    entries, elsewhere = ports.knowledge.owner_parse(comments, cfg.owner_login)
    if not entries and not elsewhere:
        return finish(state, _observed(state, seen), ports)
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(DEVELOPER_INTEGRATOR), ports)
    system_here = (task.path / SYSTEM_FILE).exists()
    target = SYSTEM_FILE if system_here else PRODUCT_FILE
    # A system rule goes to the system's file only where it exists; elsewhere it is told.
    written = [e for e in entries if e.scope == "product" or system_here]
    elsewhere = [
        *elsewhere,
        *({"rule": e.text, "cls": "system"} for e in entries if e not in written),
    ]
    files = {target: written} if written else {}
    patch = _write(state, cfg, ports, task.path, files, elsewhere, seen)
    return finish(state, patch, ports)


def _write(
    state: TaskState,
    cfg: Configuration,
    ports: Ports,
    root: Path,
    files: dict[str, list[OwnerRuleEntry]],
    elsewhere: list[OwnerRuleElsewhere],
    seen: str | None,
) -> TaskState:
    changed: list[str] = []
    for relative, entries in files.items():
        target = root / relative
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        after = ports.knowledge.owner_append(before, entries)
        if after != before:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(after, encoding="utf-8")
            changed.append(relative)
    patch = _observed(state, seen)
    if elsewhere:
        patch["owner_rules_elsewhere"] = elsewhere
    if not changed:
        return patch
    try:
        head = ports.workspace.commit_paths(root, changed, f"{state['id']}: owner rules")
    except GitError:
        ports.workspace.restore_clean(root)
        return _observed(state, seen, _UNCOMMITTED)
    reviewed = _reviewed_head(state)
    close = rules.extend_close_docs(state.get("close_docs"), reviewed, head, changed)
    if head is None or close is None:
        return patch
    patch["close_docs"] = close
    # A tiered product re-stamps `task_head` with the commit at the next fan-in; one without
    # tiers compares against the reviewed head, which the commit does not move.
    patch["owner_rules_seen"] = head if state.get("task_head") else seen
    return patch


def _observed(state: TaskState, seen: str | None, note: str | None = None) -> TaskState:
    """Nothing written: the head compared is observed, and a `close_docs` made on another
    reviewed head is dropped."""
    patch: TaskState = {"owner_rules_seen": seen}
    close = state.get("close_docs")
    if close and not rules.same_commit(close["parent"], _reviewed_head(state)):
        patch["close_docs"] = None
    if note:
        patch["sections"] = {SECTION: rules.system_note(note)}
    return patch


def _reviewed_head(state: TaskState) -> str | None:
    name = rules.reviewed_unit_name(state)
    return state.get("subtasks", {}).get(name, {}).get("reviewed_head") if name else None


def _compare_head(state: TaskState) -> str | None:
    """The head `rules.owner_rules_due` compares `owner_rules_seen` with."""
    return state.get("task_head") or _reviewed_head(state)
