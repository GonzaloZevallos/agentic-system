"""PjM classifies the task (README.md, 2 and 4) and records its size, risks and path (GH-591)."""

from __future__ import annotations

from agentic.shared.configuration import Configuration

from ..domain import rules, task_path
from ..domain.roles import PJM
from ..domain.task import TaskState
from .ports import Ports
from .preflight import product_name, risk_areas
from .run_role import finish, run_role


async def classify(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    # The product's risk areas are read once: the same value feeds the menu PjM sees and the
    # set `decide` validates its answer against. `None` is a stack that could not be read.
    areas = risk_areas(ports, state)
    # The declared name as data, like `{{date}}` (ADR-0019): `product_name` is the path
    # that answers `unknown` when the stack cannot be read, never the bare port call
    # whose `ValueError` would kill this node mid-run.
    output, patch = await run_role(
        state,
        PJM,
        cfg,
        ports,
        product=product_name(ports),
        risk_areas=task_path.risk_menu(areas or ()),
    )
    if output is not None:
        guess = output.data["type"]
        patch["type"] = guess
        if output.data["confidence"] == "low":
            # Low confidence is an assumption, not an escalation (ADR-0048): the task
            # continues on the most likely path, and the PjM section says what was
            # guessed, what it resolved to and why, so a reviewer can undo it.
            resolved = rules.low_confidence_type(guess)
            patch["type"] = resolved
            assumption = (
                f"Assumption (low confidence): PjM guessed `{guess}` and the task "
                f"continues as `{resolved}`. Reason: {output.data['reason']}"
            )
            section = patch.get("sections", {}).get(PJM.name, "")
            patch["sections"] = {PJM.name: f"{section}\n\n{assumption}".strip()}
        _record_path(state, output.data, patch, areas, ports)
    return finish(state, patch, ports)


def _owner_labels(state: TaskState, ports: Ports) -> tuple[str, ...] | None:
    """The issue's labels, `None` when the task has no issue or they could not be read: an
    unread label set is never "no label" (`IssueState.labels`)."""
    try:
        number = ports.inbox.issue_of(state["id"])
        if number is None:
            return None
        return ports.tracker.issue_state(number).labels
    except (ValueError, OSError):
        return None


def _record_path(
    state: TaskState,
    data: dict,
    patch: dict,
    areas: tuple[tuple[str, str], ...] | None,
    ports: Ports,
) -> None:
    """Decide the path from PjM's size and risks and the owner label, store it, and say so in
    the PjM section. Records only: nothing reads `path` yet."""
    labels = _owner_labels(state, ports)
    known = None if areas is None else frozenset(identifier for identifier, _ in areas)
    size, risk = data.get("size"), data.get("risk")
    decision = task_path.decide(patch["type"], size, risk, labels, known)
    label = task_path.owner_label(labels)
    patch.update(task_path.classification_patch(decision, label))
    line = f"Size `{size}`, risks {_ids(risk)}, path `{decision.path}` ({decision.cause})."
    if risk and known is not None and (unknown := task_path.unknown_risks(risk, known)):
        line += f" Unknown risk ids: {_ids(unknown)}."
    if decision.cause == "owner_light" and risk:
        line += f" Override `{task_path.LABEL_LIGHT}` over risk {', '.join(risk)}."
    elif decision.cause == "owner_light" and size != "small":
        line += f" Override `{task_path.LABEL_LIGHT}` over size `{size}`."
    if size is None and risk is None:
        # PjM declared neither: the section has nothing to restate, and `path_cause`
        # (`missing_size`) already says why the path is `full`.
        return
    section = patch.get("sections", {}).get(PJM.name, "")
    patch["sections"] = {PJM.name: f"{section}\n\n{line}".strip()}


def _ids(ids: object) -> str:
    return ", ".join(f"`{i}`" for i in ids) if isinstance(ids, (list, tuple)) and ids else "none"
