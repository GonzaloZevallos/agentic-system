"""The task's size, its risks and the path it takes (GH-591, part 1; glossary: Tamaño, Riesgo,
Promoción). Pure: no I/O.

`decide` is the one function that derives `light` or `full`, from what PjM declared, the owner
label and the product's risk areas; `promote` is the one reducer that changes `path` afterwards.
Part 2 adds the nets that ask for a promotion (`risk_hits`, `relabel`) and the request a branch
leaves for the serialized code to apply (`PromoteRequest`, `record_request`, `select_request`).

Every value stored is from a closed list: no free text and no product risk id is ever a span
attribute or a metric label.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, TypedDict, get_args

Size = Literal["small", "normal", "large"]
Path = Literal["light", "full"]
# What the issue's owner label said: `none` is the common case, `unreadable` a label set the
# tracker could not read -- never taken for `none`.
PathLabel = Literal["light", "full", "both", "unreadable", "none"]
# Why the initial path is what it is: the first condition of `decide` that applied.
PathCause = Literal[
    "label_both",
    "label_unreadable",
    "missing_size",
    "missing_risk",
    "unknown_risk",
    "stack_unreadable",
    "owner_full",
    "owner_light",
    "risk_flag",
    "not_small",
    "small_no_risk",
]
PromotionKind = Literal["plan_over_cap", "risk_area", "owner", "role"]

# What a document written before this part reads as.
DEFAULT_PATH: Final[Path] = "full"
DEFAULT_LABEL: Final[PathLabel] = "none"
DEFAULT_CAUSE: Final = "unknown"

LABEL_LIGHT: Final = "path:light"
LABEL_FULL: Final = "path:full"
TEXT_MAX: Final = 240

# The six risk ids every product has, in the order the prompt shows them. Closed and compared
# exactly; the product's own `risk_areas` come on top (`knowledge/domain/stack.py` keeps a twin
# tuple to refuse a repeat, and a test pins the two equal).
BUILTIN_RISKS: Final[Mapping[str, str]] = {
    "data-migration": "changes, moves or converts existing data",
    "money-rounding": "money amounts or rounding",
    "authn-authz-sessions": "authentication, authorization or sessions",
    "tenant-isolation": "one tenant, business or account seeing or touching another's data",
    "user-data-deletion": "deleting user data",
    "public-api-break": "breaking a public API",
}


class Promotion(TypedDict):
    """`TaskState.promotion`: the one move from `light` to `full`, as the document keeps it."""

    from_role: str
    kind: PromotionKind
    text: str | None
    at: str


@dataclass(frozen=True)
class PathDecision:
    path: Path
    cause: PathCause


def owner_label(labels: tuple[str, ...] | None) -> PathLabel:
    """What the owner's labels say. `None` is a label set that could not be read."""
    if labels is None:
        return "unreadable"
    light, full = LABEL_LIGHT in labels, LABEL_FULL in labels
    if light and full:
        return "both"
    if light:
        return "light"
    if full:
        return "full"
    return "none"


def unknown_risks(ids: Sequence[str], known_risks: frozenset[str]) -> tuple[str, ...]:
    """The ids that are neither built-in nor declared by the product, exact match."""
    return tuple(i for i in ids if i not in BUILTIN_RISKS and i not in known_risks)


def decide(
    type_: str,
    size: Size | None,
    risk: Sequence[str] | None,
    labels: tuple[str, ...] | None,
    known_risks: frozenset[str] | None,
) -> PathDecision:
    """The path a task starts on, with the first cause that applied.

    A missing, unknown or unreadable input is always `full`, whatever the owner label says;
    the label `path:light` beats only a valid risk flag and a size above `small`. `type_` is
    part of the contract the later parts need and changes no result here."""
    label = owner_label(labels)
    if label == "both":
        return PathDecision("full", "label_both")
    if label == "unreadable":
        return PathDecision("full", "label_unreadable")
    if size is None:
        return PathDecision("full", "missing_size")
    if risk is None:
        return PathDecision("full", "missing_risk")
    if known_risks is None:
        return PathDecision("full", "stack_unreadable")
    if unknown_risks(risk, known_risks):
        return PathDecision("full", "unknown_risk")
    if label == "full":
        return PathDecision("full", "owner_full")
    if label == "light":
        return PathDecision("light", "owner_light")
    if risk:
        return PathDecision("full", "risk_flag")
    if size != "small":
        return PathDecision("full", "not_small")
    return PathDecision("light", "small_no_risk")


def path_for(
    type_: str,
    size: Size | None,
    risk: Sequence[str] | None,
    labels: tuple[str, ...] | None,
    known_risks: frozenset[str] | None,
) -> Path:
    return decide(type_, size, risk, labels, known_risks).path


def risk_menu(product_areas: Sequence[tuple[str, str]]) -> str:
    """The Markdown list PjM chooses its risks from: the built-ins, then the product's."""
    rows = [*BUILTIN_RISKS.items(), *product_areas]
    return "\n".join(f"- `{identifier}`: {description}" for identifier, description in rows)


def path_of(state: Mapping[str, Any]) -> Path:
    return "light" if state.get("path") == "light" else DEFAULT_PATH


def path_label_of(state: Mapping[str, Any]) -> PathLabel:
    value = state.get("path_label")
    return value if value in ("light", "full", "both", "unreadable") else DEFAULT_LABEL


def path_cause_of(state: Mapping[str, Any]) -> str:
    return str(state.get("path_cause") or DEFAULT_CAUSE)


def classification_patch(decision: PathDecision, label: PathLabel) -> dict[str, Any]:
    """What classify stores. The only writer of these four keys."""
    return {
        "initial_path": decision.path,
        "path": decision.path,
        "path_cause": decision.cause,
        "path_label": label,
    }


def promote(
    state: Mapping[str, Any],
    from_role: str,
    kind: PromotionKind,
    at: str,
    text: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """The only reducer that changes `path`: `light` to `full`, once, and never back.

    Under the owner label `path:light` it applies nothing and says so. It is not a return and
    not an escalation, and it writes `path` and `promotion` and nothing else: no counter."""
    if text is not None and (len(text) > TEXT_MAX or "\n" in text or "\r" in text):
        raise ValueError(f"a promotion's text is one line of at most {TEXT_MAX} characters")
    if path_label_of(state) == "light":
        return (
            {},
            f"The owner label `{LABEL_LIGHT}` keeps the light path: promotion ({kind}) not applied",
        )
    if path_of(state) == "full":
        return {}, None
    promotion: Promotion = {"from_role": from_role, "kind": kind, "text": text, "at": at}
    return {"path": "full", "promotion": promotion}, None


class PromoteRequest(TypedDict):
    """A promotion asked for, not yet applied: stored on the asking subtask
    (`subtasks[sid]["promote_request"]`) or at the top level (`plan_promote_request`). Only
    serialized system code turns it into a `promote` call."""

    kind: PromotionKind
    from_role: str
    text: str | None


def validate_request(request: Mapping[str, Any]) -> None:
    """Raise `ValueError` for a request `promote` would refuse later, or a kind outside the list."""
    if request.get("kind") not in get_args(PromotionKind):
        raise ValueError(f"a promotion request's kind is one of {get_args(PromotionKind)}")
    text = request.get("text")
    if text is not None and (len(text) > TEXT_MAX or "\n" in text or "\r" in text):
        raise ValueError(f"a promotion's text is one line of at most {TEXT_MAX} characters")


def record_request(current: PromoteRequest | None, new: PromoteRequest) -> PromoteRequest:
    """The value to store on a subtask: a `risk_area` request already there is never replaced,
    whatever the order two writers came in; otherwise the new one wins."""
    if current is not None and current["kind"] == "risk_area":
        return current
    return new


def select_request(
    plan_request: PromoteRequest | None, subtask_requests: Mapping[str, PromoteRequest]
) -> PromoteRequest | None:
    """The one request the serialized code applies. Fixed order: `risk_area` by lowest subtask
    id, then `role` by lowest subtask id, then the plan's (`role` before `plan_over_cap`)."""
    for kind in ("risk_area", "role"):
        for sid in sorted(subtask_requests):
            if subtask_requests[sid]["kind"] == kind:
                return subtask_requests[sid]
    return plan_request


@dataclass(frozen=True)
class RiskHit:
    id: str


@dataclass(frozen=True)
class RiskNone:
    pass


@dataclass(frozen=True)
class RiskUnreadable:
    pass


RiskVerdict = RiskHit | RiskNone | RiskUnreadable


def _matcher(glob: object) -> re.Pattern[str] | None:
    """The compiled glob, or `None` when it is not a usable one (not a string, empty, a `[`
    with no `]`, or a pattern the regex engine refuses)."""
    if not isinstance(glob, str) or not glob:
        return None
    for at, char in enumerate(glob):
        if char == "[" and "]" not in glob[at + 1 :]:
            return None
    try:
        return re.compile(fnmatch.translate(glob))
    except re.error:
        return None


def risk_hits(
    areas: Sequence[tuple[str, Sequence[str]]], touched_paths: Sequence[str] | None
) -> RiskVerdict:
    """Which product risk area a diff touches: the first, in declaration order, with a glob that
    matches any touched path. `RiskUnreadable` when the diff could not be read or a glob is
    invalid, and that is never `RiskNone`. An area with no `paths` never hits. The caller passes
    both paths of a rename and every deleted path."""
    if touched_paths is None:
        return RiskUnreadable()
    unreadable = False
    for identifier, globs in areas:
        compiled = [_matcher(glob) for glob in globs]
        if any(pattern is None for pattern in compiled):
            unreadable = True
            continue
        if any(p.match(path) for p in compiled if p is not None for path in touched_paths):
            return RiskHit(identifier)
    return RiskUnreadable() if unreadable else RiskNone()


def relabel(
    state: Mapping[str, Any], labels: tuple[str, ...] | None, at: str
) -> tuple[dict[str, Any], str | None]:
    """What a resume does with the owner labels as they are now. `None` (unreadable), `none` and
    `light` change nothing: a label never demotes. `full` and `both` promote with `owner`,
    judged against the NEW label so a stored `path_label: light` does not block it, and write it."""
    label = owner_label(labels)
    if label not in ("full", "both"):
        return {}, None
    patch, note = promote({**state, "path_label": label}, "owner", "owner", at)
    if not patch:
        return {}, note
    return {**patch, "path_label": label}, note
