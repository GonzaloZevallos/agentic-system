"""The Tech Writer's two nodes (README.md, 4): `write_copy` writes every person-facing text of
a feature that touches copy over the validated flow and design, last of the definition
(`rules.design_step`), and `copy_review` checks, after the implementation and before the final
validation, that the texts in the diff are those and meet the product's writing criteria
(`rules.copy_review_due`).

Both are read-only roles. Neither rewrites the `Tech Writer` section: the review reads it
untouched, and the texts it settled travel as a separate entry the system computes
(`domain.copy.current_texts`) in the review's own turn message. The review never records a
return, a pair or a change request; its findings are rows of the same table as the Tech
Lead's, built only by `domain.copy.copy_records`, and a text finding the review rates
`important` is fixed by the final pass (`landing_fix.fix_important`, ADR-0053)."""

from __future__ import annotations

from collections.abc import Mapping

from agentic.shared.configuration import Configuration

from ..domain import rules
from ..domain.copy import (
    NO_WRITING_CLAIM,
    NO_WRITING_NOTE,
    copy_location_parts,
    copy_records,
    current_texts,
)
from ..domain.roles import TECH_WRITER, TECH_WRITER_REVIEW
from ..domain.schemas import CopyReview
from ..domain.task import INTEGRATION, TaskState, apply
from .checkouts import SetupFailed, task_base, task_checkout_in_thread
from .ports import GitError, Ports
from .run_role import finish, run_role, with_context


async def write_copy(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """Tech Writer writes the texts of the change over the PRD and the validated flow and
    screens. With no `WRITING_STANDARDS.md` in the checkout the system says so at the head of
    the section, the same line-in-front treatment `NO_DESIGN_NOTE` gets."""
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(TECH_WRITER), ports)
    output, patch = await run_role(state, TECH_WRITER, cfg, ports, cwd=task.path)
    if output is not None and not ports.knowledge.writing(task.path):
        written = patch["sections"][TECH_WRITER.name]
        note = rules.system_note(NO_WRITING_NOTE)
        patch = apply(patch, {"sections": {TECH_WRITER.name: f"{note}\n\n{written}"}})
    return finish(state, patch, ports)


async def copy_review(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    """Tech Writer's review of the integrated piece, in the checkout `deliver` uses. It reads
    the diff against the task's base, its own open rows by identifier and the corrected texts
    in force; what it finds and settles becomes rows and closures of the table."""
    try:
        task = await task_checkout_in_thread(state, cfg, ports)
    except SetupFailed as unprepared:
        return finish(state, unprepared.patch(TECH_WRITER_REVIEW), ports)
    base = task_base(state, ports)
    branch = rules.task_branch(state["id"])
    standards_missing = not ports.knowledge.writing(task.path)
    prompt = with_context(
        state,
        TECH_WRITER_REVIEW,
        ports,
        task.path,
        _turn(state, base, _paths(ports, base, branch), standards_missing),
    )
    output, patch = await run_role(
        state, TECH_WRITER_REVIEW, cfg, ports, cwd=task.path, prompt=prompt
    )
    if output is None:
        return finish(state, patch, ports)
    review = CopyReview.model_validate({**output.data, "markdown": output.text})
    unit = rules.reviewed_unit_name(state) or next(iter(state.get("subtasks", {})), "")
    records, settled = copy_records(
        state,
        rules.next_version(state, TECH_WRITER_REVIEW.name),
        review,
        _owners(state, base, ports),
        unit,
        standards_missing=standards_missing,
    )
    patch = apply(patch, {"findings": records, "settled_findings": settled})
    return finish(state, patch, ports)


def _paths(ports: Ports, base: str, head: str) -> tuple[str, ...]:
    try:
        return ports.workspace.changed_paths(base, head)
    except GitError:
        return ()


def _owners(state: TaskState, base: str, ports: Ports) -> Mapping[str, tuple[str, ...]]:
    """Path -> the approved subtasks whose own diff against the task's base touches it. The
    integration is left out: its branch is the whole task's, so it would own every file, and
    it is the fallback unit when no other does."""
    real = [sid for sid in state.get("subtasks", {}) if sid != INTEGRATION]
    owners: dict[str, list[str]] = {}
    for sid in real:
        if state["subtasks"][sid].get("status") != "approved":
            continue
        branch = (
            rules.task_branch(state["id"])
            if len(real) == 1
            else rules.subtask_branch(state["id"], sid)
        )
        for path in _paths(ports, base, branch):
            owners.setdefault(path, []).append(sid)
    return {path: tuple(names) for path, names in owners.items()}


def _turn(state: TaskState, base: str, paths: tuple[str, ...], standards_missing: bool) -> str:
    """The input of the review's turn, after the sections it reads: where the diff is, the
    open text rows with the identifiers `settled` must repeat exactly, and the corrected
    texts in force. The `Tech Writer` section is not here: it is read as the role wrote it."""
    changed = "\n".join(f"- {path}" for path in paths) or "None found."
    open_rows = [row for row in rules.open_findings(state) if copy_location_parts(row["location"])]
    rows = "\n".join(
        f"- `{row['location']}` ({row['severity']}): {row['claim']}" for row in open_rows
    )
    corrected = "\n".join(f"- {where}: {text}" for where, text in current_texts(state))
    parts = [
        "## Diff to review",
        f"Run `git diff {base}...HEAD` for the integrated work against the task's base. "
        f"Files it changes:\n\n{changed}",
        "## Open text findings",
        rows or "None open.",
        "## Corrected texts in force",
        corrected or "None.",
    ]
    if standards_missing:
        parts += ["## Writing standards", NO_WRITING_CLAIM]
    return "\n\n".join(parts)
