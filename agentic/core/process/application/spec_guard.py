"""The one reader of a delivery's spec after the intake (GH-594, part 4b-3): what a head makes of
it, and the cut of the task branch.

It is the only module that calls `ports.spec_reader`, so the rule "a spec is read at a full commit
id, and compared only by `spec_delivery.spec_intact`" has one place. Without `state["spec"]` every
function answers "intact" or `{}` and makes no call.

A `SpecUnreadable` never escapes: a deleted spec at a head is `Changed`, every other reason is
`Unreadable`, and the caller escalates with `refusal_patch`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ..domain import rules, spec_delivery, spec_route
from ..domain.spec_delivery import SpecReadRefusal
from ..domain.task import WAITING_ON_HUMAN, TaskState
from .checkouts import task_base
from .ports import Ports, SpecReader, SpecUnreadable


@dataclass(frozen=True)
class Intact:
    """The spec at the head is what the delivery recorded."""


@dataclass(frozen=True)
class Changed:
    """The spec at the head differs from the record (or is gone). `detail` is the closed detail
    of the `spec_changed` refusal: the path, plus `upstream` when the product's own upstream
    carries the same text."""

    detail: tuple[str, ...]


@dataclass(frozen=True)
class Unreadable:
    """The spec could not be read, for a closed `reason`."""

    reason: SpecReadRefusal


Verdict = Intact | Changed | Unreadable


def _read(reader: SpecReader, commit: str, path: str) -> tuple[str | None, SpecReadRefusal | None]:
    """(text, None), or (None, the reason it cannot be read)."""
    try:
        return reader.read(commit, path), None
    except SpecUnreadable as exc:
        return None, exc.reason


def read_at(ports: Ports, commit: str, path: str) -> str:
    """The text of `path` at `commit`, for a read that has no `state["spec"]` yet (the intake).
    Raises `SpecUnreadable`: `git` with no reader, `commit` when `commit` is not a full id."""
    reader = ports.spec_reader
    if reader is None:
        raise SpecUnreadable("git")
    if not rules.full_commit_id(commit):
        raise SpecUnreadable("commit")
    return reader.read(commit.strip(), path)


def _upstream_detail(
    reader: SpecReader, ports: Ports, path: str, head_text: str | None
) -> tuple[str, ...]:
    """`(path, "upstream")` when the head's text is the upstream's, else `(path,)`. A fetch that
    gives no usable sha, or a spec the upstream cannot show, means the change is not upstream's."""
    sha = ports.workspace.upstream().sha
    if head_text is None or sha is None or not rules.full_commit_id(sha):
        return (path,)
    text, _ = _read(reader, sha.strip(), path)
    return (path, "upstream") if text == head_text else (path,)


def check_at(state: TaskState, ports: Ports, head: str) -> Verdict:
    """What `head` makes of the delivery's spec. `Intact` without a spec, with no read."""
    spec = state.get("spec")
    if not spec:
        return Intact()
    reader = ports.spec_reader
    if reader is None:
        return Unreadable("git")
    if not rules.full_commit_id(head):
        return Unreadable("commit")
    path = spec["path"]
    recorded, why = _read(reader, spec["commit"], path)
    if why is not None:
        return Unreadable(why)
    head_text, why = _read(reader, head.strip(), path)
    if why is not None and why != "missing":
        return Unreadable(why)
    if spec_delivery.spec_intact(spec["sha256"], recorded, head_text):
        return Intact()
    return Changed(_upstream_detail(reader, ports, path, head_text))


def intact_at(state: TaskState, ports: Ports, head: str) -> bool:
    """Whether the spec is intact at `head`: the question a carry asks (`spec_carry_ok`)."""
    return isinstance(check_at(state, ports, head), Intact)


def refusal_patch(result: Changed | Unreadable, path: str, at: str) -> TaskState:
    """The escalation of a spec that is not intact: `spec_changed`, or `spec_unreadable` with the
    reason. Pauses the task for the human; adds no findings and moves no counter."""
    if isinstance(result, Changed):
        patch = spec_route.refusal_escalation(
            "spec_changed", result.detail, "the approved spec changed after it was recorded"
        )
    else:
        patch = spec_route.refusal_escalation(
            "spec_unreadable", (path, result.reason), "the approved spec could not be read"
        )
    return cast(TaskState, {**rules.logged(dict(patch), at=at), "status": WAITING_ON_HUMAN})


def cut(state: TaskState, ports: Ports) -> TaskState:
    """The check at the task branch's cut, before any Developer is dispatched. Without a spec
    `{}`. The spec intact at the base: re-record `commit` as the base (`{}` when it already is).
    Otherwise the refusal."""
    spec = state.get("spec")
    if not spec:
        return {}
    base = task_base(state, ports)
    result = check_at(state, ports, base)
    if isinstance(result, Intact):
        return {} if base == spec["commit"] else {"spec": {**spec, "commit": base}}
    return refusal_patch(result, spec["path"], ports.now())
