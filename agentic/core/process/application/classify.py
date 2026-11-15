"""PjM classifies the task (README.md, 2 and 4) and records its size, risks and path (GH-591)."""

from __future__ import annotations

from typing import Final, get_args

from agentic.shared.configuration import Configuration

from ..domain import rules, spec_delivery, task_path
from ..domain.roles import PJM
from ..domain.spec_delivery import SpecLineMalformed, SpecRefusal, SpecRouteCause
from ..domain.spec_route import refusal_escalation
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .ports import Ports, SpecUnreadable
from .preflight import product_name, risk_areas
from .run_role import finish, run_role

# One fixed English sentence per cause (GH-594, invariant 1): the issue body is editable and the
# validator and git speak free text, so none of it reaches a reason, a detail or a comment.
_REASONS: Final[dict[SpecRouteCause, str]] = {
    "spec_line_malformed": "The issue's Spec: line is not exactly one `Spec: <path>` line.",
    "spec_unreadable": "The spec file could not be read at the product's upstream commit.",
    "spec_invalid": "The spec is not a valid contract.",
    "spec_unapproved": "The spec is not approved by the product owner.",
    "slice_unknown": "The Spec: line names a slice the spec does not have.",
    "slice_missing": "The spec has several slices and the Spec: line names none.",
    "spec_type": "A Spec: line is kept only for a feature, and PjM typed this task otherwise.",
}
_OWNER_LOGIN_REASON: Final = (
    "The spec cannot be checked as approved: the configuration key `owner_login` is empty."
)


def _refused(
    cause: SpecRouteCause, detail: tuple[str, ...], ports: Ports, reason: str | None = None
) -> TaskState:
    """The patch that pauses the task on a refused spec, with its `pause_log` entry."""
    patch = {
        "status": WAITING_ON_HUMAN,
        **refusal_escalation(cause, detail, reason or _REASONS[cause]),
    }
    return rules.logged(patch, at=ports.now())


def spec_intake(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState | None:
    """The spec route of a task whose issue body carries a `Spec:` line (GH-594): `None` when
    there is nothing to do (the route is not honoured, or the body has no line), a refusal patch
    (it has `escalated`) or the `{spec, flags}` patch. Each step cuts the next."""
    reader, validator = ports.spec_reader, ports.spec_validator
    if not spec_delivery.honoured(reader, validator) or reader is None or validator is None:
        return None
    line = spec_delivery.parse_spec_line(state.get("description", ""))
    if line is None:
        return None
    if isinstance(line, SpecLineMalformed):
        return _refused("spec_line_malformed", (), ports)
    if not cfg.owner_login:
        return _refused("spec_unapproved", ("owner_login",), ports, _OWNER_LOGIN_REASON)
    upstream = ports.workspace.upstream()
    if upstream.note is not None or upstream.sha is None:
        return _refused("spec_unreadable", ("unreachable",), ports)
    try:
        text = reader.read(upstream.sha, line.path)
    except SpecUnreadable as exc:
        return _refused("spec_unreadable", (exc.reason,), ports)
    verdict = validator.validate(text, line.path, upstream.sha, line.slice, cfg.owner_login)
    if isinstance(verdict, str):
        return _refused(verdict, (line.path,), ports)
    patch = spec_delivery.spec_state_patch(verdict)
    if isinstance(patch, SpecRefusal):
        return _refused("spec_invalid", patch.detail, ports)
    return {"spec": patch["spec"], "flags": patch["flags"]}


async def classify(state: TaskState, cfg: Configuration, ports: Ports) -> TaskState:
    intake = spec_intake(state, cfg, ports)
    if intake is not None and "escalated" in intake:
        return finish(state, intake, ports)  # a refused spec costs no PjM run
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
        resolved = guess
        if output.data["confidence"] == "low":
            # Low confidence is an assumption, not an escalation (ADR-0048): the task
            # continues on the most likely path, and the PjM section says what was
            # guessed, what it resolved to and why, so a reviewer can undo it.
            resolved = rules.low_confidence_type(guess)
            assumption = (
                f"Assumption (low confidence): PjM guessed `{guess}` and the task "
                f"continues as `{resolved}`. Reason: {output.data['reason']}"
            )
            section = patch.get("sections", {}).get(PJM.name, "")
            patch["sections"] = {PJM.name: f"{section}\n\n{assumption}".strip()}
        if intake is not None and resolved != "feature":
            # Before `type` and the path are written, so a refused classification leaves none
            # of them behind: the resume runs PjM again, and the PjM run is kept.
            refusal = _refused("spec_type", (intake["spec"]["path"],), ports)
            return finish(state, {**patch, **refusal}, ports)
        patch["type"] = resolved
        _record_path(state, output.data, patch, areas, ports)
        if intake is not None:
            patch.update(intake)
    return finish(state, patch, ports)


def owner_labels(state: TaskState, ports: Ports) -> tuple[str, ...] | None:
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
    labels = owner_labels(state, ports)
    known = None if areas is None else frozenset(identifier for identifier, _ in areas)
    size, risk = data.get("size"), data.get("risk")
    decision = task_path.decide(patch["type"], size, risk, labels, known)
    label = task_path.owner_label(labels)
    patch.update(task_path.classification_patch(decision, label))
    if size in get_args(task_path.Size):
        patch["size"] = size  # a closed value only, read by `metrics.task_size_value`
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
