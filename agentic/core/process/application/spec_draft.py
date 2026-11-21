"""The draft Spec the definition chain ends with (GH-594 part 5a): PM returns the body, the system
writes the file, validates it, commits it on the task branch and records what became of it.

The one writer of the `spec_draft` record (outcomes `written`, `invalid`, `unvalidated`, `absent`
and `split_proposed`, the last when the approved preview had more than one slice: nothing written).
Only the system writes the file, always with `status: draft` (`spec_delivery.draft_text`); PM's
text is a body and a body that opens its own front matter is refused, so a draft is never on disk
as approved. A task that comes back to the definition writes to the draft it already has and never
creates a second file under `docs/specs/`. Nothing here returns a draft to PM and nothing
escalates: an `invalid` draft is a record, and the task still reaches `definition_ready`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentic.shared.configuration import Configuration

from ..domain import spec_approval
from ..domain.spec_delivery import (
    DRAFT_PREVIEW_PATH,
    SPEC_DIR,
    DraftReading,
    SpecDraftRecord,
    approval_instant,
    body_sha256,
    draft_body_refusal,
    draft_outcome,
    draft_path,
    draft_reading,
    draft_text,
    instant_ok,
    login_ok,
    spec_path_ok,
)
from ..domain.task import TaskState
from .ports import Checkout, Ports, SpecValidator


def deliver(
    state: TaskState, data: Mapping[str, Any], task: Checkout, cfg: Configuration, ports: Ports
) -> SpecDraftRecord:
    """The record of the draft in PM's approved output `data`; writes and commits the file only
    for `written`. Called from `validate_prd` under `rules.spec_fallback_due` and nowhere else."""
    body = data.get("spec_draft")
    if not isinstance(body, str) or not body.strip():
        return {"outcome": "absent"}
    validator = ports.spec_validator
    if validator is None:
        return {"outcome": "unvalidated"}
    if draft_body_refusal(body) is not None:
        return {"outcome": "invalid"}
    path = _path(state, task, ports)
    text = draft_text(body)
    sha256 = body_sha256(text)
    target = task.path / path
    if sha256 is None or target.is_symlink():
        return {"outcome": "invalid"}
    outcome = draft_outcome(draft_cause(text, validator, task.base, cfg.owner_login, ports.now()))
    if outcome == "split_proposed":
        return {"outcome": "split_proposed", "sha256": sha256}
    if outcome == "invalid":
        return {"outcome": "invalid"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="")
    ports.workspace.commit_all(task.path, f"{state['id']}: draft Spec")
    # The commit is recorded only when it holds exactly what was written, as a full sha (`head` is
    # short): `commit_all` may have had nothing to commit, and the head is then whatever was there.
    head = ports.workspace.full_head(task.path)
    if ports.workspace.show(head, path) != text:
        return {"outcome": "invalid"}
    return {"outcome": "written", "path": path, "sha256": sha256, "commit": head}


def draft_cause(
    text: str, validator: SpecValidator, base: str, owner_login: str | None, now: str
) -> DraftReading:
    """The one place a draft is validated and previewed as approved: the draft is read by the
    validator and, only with a well-formed `owner_login` and a clock that reads as an instant, its
    approved preview is read too (`spec_delivery.draft_reading`). Without either the preview is
    skipped, never an error. The login and the instant are decided here and nowhere else; the
    validator never sees a raw login. Nothing is read from the checkout and nothing is written."""
    login = owner_login if login_ok(owner_login) else None
    draft = validator.validate(text, DRAFT_PREVIEW_PATH, base, None, login)
    preview = None
    instant = approval_instant(now)
    if login is not None and instant_ok(instant):
        approved = spec_approval.approve(text, login, instant)
        preview = validator.validate(approved, DRAFT_PREVIEW_PATH, base, None, login)
    return draft_reading(draft, preview)


def _path(state: TaskState, task: Checkout, ports: Ports) -> str:
    """Where the draft goes: the recorded path, else this task's own draft, else a new name."""
    recorded = state.get("spec_draft", {}).get("path")
    if isinstance(recorded, str) and spec_path_ok(recorded):
        return recorded
    own = _own_draft(task, ports)
    if own is not None:
        return own
    day = ports.now()[:10]
    title = state.get("title", "")
    first = draft_path(day, title, state["id"], frozenset())
    taken = frozenset({first}) if _taken(first, task, ports) else frozenset()
    return draft_path(day, title, state["id"], taken)


def _own_draft(task: Checkout, ports: Ports) -> str | None:
    """A file under `docs/specs/` the checkout has and the task's base lacks: committed on the
    task branch or still untracked, so a run that stopped before the record finds its own file."""
    directory = task.path / SPEC_DIR
    if not directory.is_dir():
        return None
    found = sorted(
        relative
        for entry in directory.glob("*.md")
        if entry.is_file()
        and not entry.is_symlink()
        and spec_path_ok(relative := f"{SPEC_DIR}{entry.name}")
        and ports.workspace.show(task.base, relative) is None
    )
    return found[0] if found else None


def _taken(path: str, task: Checkout, ports: Ports) -> bool:
    """Whether `path` is in the checkout or in the fetched upstream tree."""
    if Path(task.path / path).exists():
        return True
    upstream = ports.workspace.upstream().sha
    return upstream is not None and ports.workspace.show(upstream, path) is not None
