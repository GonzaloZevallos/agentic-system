"""The approved drawing's rule (glossary: Dibujo aprobado), pure.

`rule_for` is the only producer of the rule wording: no other module builds or copies it.
`normalize_drawing` is the only owner of the drawing-path rule and `departure_question` of the
escalation text of a departure: callers use them, never repeat them. `verdicts` and `answers`
are the only owners of the comment grammar of the owner's answer, `flag_verdict` and
`flag_verdicts` of the `--drawing` flag's, `screens_due` of which screens a change owes a
check for and `row_is_open` of whether a check row still blocks. The module is a leaf: it
imports neither `task` nor `rules`, not even inside a function.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Literal

from .design import Capture, Design

DRAWING_RULE = (
    "Anything visible that departs from an approved drawing is decided by the owner, "
    "never by a role: do not choose it yourself, raise it."
)


def rule_for(design: Design | None) -> str:
    """The rule when the product declares a `design`, "" when `None`.

    `None` means the stack was read and declares no `design`; an unreadable stack never
    reaches here, it raises where it is read."""
    return "" if design is None else DRAWING_RULE


@dataclass(frozen=True)
class Departure:
    """Something visible that would differ from an approved drawing, raised for the owner."""

    drawing: str
    element: str
    shows: str
    would_ship: str
    recommendation: str
    reason: str
    raised_by: str


_NUMBER_SUFFIX = re.compile(r":\d+$")


def normalize_drawing(path: str) -> str:
    """The drawing path as the key sees it: normalized, never casefolded.

    Raises `ValueError` for an empty or blank path, a `:`, an absolute path or one that
    leaves the design folder."""
    if not path.strip():
        raise ValueError("the drawing path is empty")
    suffix = _NUMBER_SUFFIX.search(path)
    if suffix:
        raise ValueError(
            f"the drawing path {path!r} ends in {suffix.group()!r}: cite the element apart"
        )
    if ":" in path:
        raise ValueError(f"the drawing path {path!r} may not contain ':'")
    if path.startswith("/"):
        raise ValueError(f"the drawing path {path!r} must be relative")
    normal = posixpath.normpath(path)
    if normal == "." or normal == ".." or normal.startswith("../"):
        raise ValueError(f"the drawing path {path!r} must stay inside the design")
    return normal


def departure_key(drawing: str, element: str) -> str:
    """`path:element`, the identity of a departure: only the path and the cited element."""
    folded = " ".join(element.casefold().split())
    if not folded:
        raise ValueError("the departure element is empty")
    return f"{normalize_drawing(drawing)}:{folded}"


def departure_question(d: Departure, number: int) -> str:
    """The owner's question for a departure: it quotes the path and the number, never the key."""
    return (
        f"Departure #{number}: the approved drawing {d.drawing} shows {d.shows}, "
        f"but what would ship is {d.would_ship}. "
        f"Recommendation: {d.recommendation}, because {d.reason}. "
        f'Answer with a line "Drawing {number}: accept" or "Drawing {number}: reject".'
    )


def screens_due(design: Design, diff_paths: Sequence[str]) -> list[Capture]:
    """The captures of `design`, in declared order, that this change owes a check for: the ones
    whose `paths` globs match some path of `diff_paths` (`fnmatch.fnmatchcase`, so case-sensitive
    and `*` crosses `/`: `src/*.py` matches `src/a/b.py`). A capture with no `paths` is due
    always, deliberately: the product did not say which code draws it. The identity of a
    capture is its `image`, so two captures of one `screen` are two screens due. The only
    decision of which screens are due; a deleted path still matches, textually."""
    return [
        capture
        for capture in design.captures
        if not capture.paths
        or any(fnmatchcase(path, glob) for glob in capture.paths for path in diff_paths)
    ]


def row_is_open(verdict: str, settled: bool) -> bool:
    """Whether a drawing row still blocks: `matches` is closed, `departs` is closed only when
    `settled`, anything else (`unverified`, an unsettled `departs`, an unknown verdict) is open.
    The only decision of that."""
    return not (verdict == "matches" or (verdict == "departs" and settled))


Verdict = Literal["accept", "reject"]

DEPARTURE_ESCALATIONS_MAX = 3
"""The hard cap of rounds in which a task may escalate drawing departures (counts rounds, not
progress): the next one is `drawing_cap`. Read by the escalation (GH-814)."""

_VERDICT_LINE = re.compile(r"^\s*Drawing ([0-9]{1,6}): (accept|reject)\s*$", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Verdicts:
    """What the owner said in some comments: `given` the verdict of each number answered
    once, `contradicted` the numbers that got two different verdicts and so have none."""

    given: Mapping[int, Verdict]
    contradicted: frozenset[int]


def _lines_of(text: str) -> list[tuple[int, Verdict]]:
    """The verdict lines of one comment in order: whole lines only, never a quoted line
    (first non-blank character `>`) nor one inside a fenced block. Number 0 is discarded."""
    found: list[tuple[int, Verdict]] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker:
            fence = None if fence == marker.group(1) else (fence or marker.group(1))
            continue
        if fence is not None or line.lstrip().startswith(">"):
            continue
        hit = _VERDICT_LINE.match(line)
        if hit and int(hit.group(1)) > 0:
            found.append(
                (int(hit.group(1)), "accept" if hit.group(2).lower() == "accept" else "reject")
            )
    return found


def verdicts(text: str) -> Verdicts:
    """The verdicts one comment gives: a number with two different verdicts in it is
    `contradicted` and not `given`; the same verdict twice counts once."""
    seen: dict[int, set[Verdict]] = {}
    for number, verdict in _lines_of(text):
        seen.setdefault(number, set()).add(verdict)
    return Verdicts(
        given={n: next(iter(v)) for n, v in seen.items() if len(v) == 1},
        contradicted=frozenset(n for n, v in seen.items() if len(v) > 1),
    )


def answers(comments: Sequence[str]) -> Verdicts:
    """The verdicts of `comments` read in order: the last line wins across comments; a number
    contradicted in a later comment loses its earlier verdict, a later clean one clears the
    contradiction."""
    given: dict[int, Verdict] = {}
    contradicted: set[int] = set()
    for text in comments:
        one = verdicts(text)
        for number, verdict in one.given.items():
            given[number] = verdict
            contradicted.discard(number)
        for number in one.contradicted:
            given.pop(number, None)
            contradicted.add(number)
    return Verdicts(given=given, contradicted=frozenset(contradicted))


_FLAG = re.compile(r"^([0-9]+)=(accept|reject)$")


def flag_verdict(raw: str) -> tuple[int, Verdict]:
    """`<n>=accept|reject` of `--drawing` as a pair; `ValueError` for anything else or `n` < 1."""
    hit = _FLAG.match(raw.strip())
    if hit is None or int(hit.group(1)) < 1:
        raise ValueError(f"--drawing {raw!r}: expected <n>=accept or <n>=reject with n >= 1")
    return int(hit.group(1)), "accept" if hit.group(2) == "accept" else "reject"


def flag_verdicts(raws: Sequence[str]) -> Mapping[int, Verdict]:
    """The pairs of every `--drawing` flag; `ValueError` for one number with two verdicts."""
    out: dict[int, Verdict] = {}
    for raw in raws:
        number, verdict = flag_verdict(raw)
        if out.setdefault(number, verdict) != verdict:
            raise ValueError(f"--drawing gives drawing {number} two different verdicts")
    return out


_NO_DRAWING_RULE: dict[str, str] = {}
for _reason, _names in (
    (
        "pending GH-766 part 3",
        "pm_interviewee pm_consultant pm_bug product_analyst_interviewer spec/pm_consultant",
    ),
    ("pending GH-766 part 4", "tech_lead spec/plan"),
    ("pending GH-766 part 5", "ux_lead_final ui_lead_final"),
    (
        "covered by tech_lead",
        "developer developer_integrator tech_lead_review tech_lead_review_light",
    ),
    ("covered by ux_lead_final", "ux_designer ux_lead tech_writer tech_writer_review"),
    (
        "covered by ui_lead_final",
        "ui_designer ui_lead pm_final spec/pm_final pm_consolidator",
    ),
    ("covered by pm_interviewee", "pm"),
    ("covered by product_analyst_interviewer", "product_analyst"),
    ("covered by pm_bug", "product_analyst_bug"),
    (
        "plan carries the rule",
        "tech_lead_critic tech_lead_ephemeral tech_lead_interviewee spec/review spec/copy_review",
    ),
    (
        "no visible change",
        "tech_lead_platform tech_lead_stack tech_lead_critic_platform "
        "tech_lead_ephemeral_platform tech_lead_ephemeral_stack "
        "tech_lead_interviewee_platform tech_lead_interviewee_stack",
    ),
    (
        "never decides what a person sees",
        "pjm plan_sizer landing_diagnoser reflector qa_performance tech_lead_carve_out",
    ),
):
    _NO_DRAWING_RULE.update(dict.fromkeys(_names.split(), _reason))

# The prompts that do not carry `{{drawing_rule}}`, each with a reason from a closed grammar.
# Only the prompt-registry test reads it: no production module consults it.
NO_DRAWING_RULE: Mapping[str, str] = MappingProxyType(_NO_DRAWING_RULE)
