"""The approved drawing's rule (glossary: Dibujo aprobado), pure.

`rule_for` is the only producer of the rule wording: no other module builds or copies it.
`normalize_drawing` is the only owner of the drawing-path rule, `resolve` of how a cited drawing
becomes its full path (against `Design.files`, the prototype's listing) and `departure_question`
of the escalation text of a departure: callers use them, never repeat them. `verdicts` and `answers`
are the only owners of the comment grammar of the owner's answer, `flag_verdict` and
`flag_verdicts` of the `--drawing` flag's, `screens_due` of which screens a change owes a
check for and `row_is_open` of whether a check row still blocks. `question_numbers` is the only
reader of the interviewer's `**Qn**` markers and `question_ref` the only builder of a `Q<n>` ref;
`unverified_lines` the only builder of the lines for questions whose drawing could not be compared;
one private classifier reads every line of a comment or an interview. `interviewer_check` and
`interview_check` are the pure rules of the definition interview's drawing checks. The module
is a leaf: it imports neither `task`, `rules` nor `schemas`, not even inside a function.
"""

from __future__ import annotations

import difflib
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Literal, Protocol

from .design import Capture, Design

DRAWING_RULE = (
    "Anything visible that departs from an approved drawing is decided by the owner, "
    "never by a role: do not choose it yourself, raise it."
)


Seat = Literal["interviewer", "interviewee"]

DRAWING_SEATS: Mapping[str, Seat] = MappingProxyType(
    {"product_analyst_interviewer": "interviewer", "pm_interviewee": "interviewee"}
)
"""The roles of the definition interview that carry a drawing check, by slug: the table says
which check and which variant of the rule a seat gets, never the shape of its data."""

_SEAT_RULES: Mapping[Seat, str] = MappingProxyType(
    {
        "interviewer": "Mark in `visible` every question whose answer changes what a person sees.",
        "interviewee": (
            "Answer each visible question with a drawing check; a departure goes in "
            "`departures`, never as an assumption."
        ),
    }
)


PLAN_WRITERS: frozenset[str] = frozenset({"tech_lead"})
"""The roles whose plan is gated on its `drawing_departures` (GH-766 part 4b): with
`rule_for(plan=True)` they are told where a departure goes."""

_PLAN_RULE = (
    "A visible departure from an approved drawing is declared in `drawing_departures` of the "
    "plan, never taken as an assumption."
)

_RULE_PLACEHOLDER = "{{drawing_rule}}"


def rule_for(design: Design | None, slug: str, *, plan: bool = False) -> str:
    """The rule for the role `slug` when the product declares a `design`, "" when `None`:
    one line, the base rule plus what the seat of `DRAWING_SEATS` must do; any other role
    gets the base rule alone. With `plan=True` a role of `PLAN_WRITERS` also gets where its
    departures go; for any other role `plan` changes nothing.

    `None` means the stack was read and declares no `design`; an unreadable stack never
    reaches here, it raises where it is read."""
    if design is None:
        return ""
    seat = DRAWING_SEATS.get(slug)
    rule = DRAWING_RULE if seat is None else f"{DRAWING_RULE} {_SEAT_RULES[seat]}"
    return f"{rule} {_PLAN_RULE}" if plan and slug in PLAN_WRITERS else rule


def block_rule(body_raw: str, rule: str) -> str:
    """What fills `{{drawing_rule}}` in a role's Spec block: "" when the role's raw body already
    carries the placeholder (the rule comes through the body, once), `rule` otherwise. The
    one decision of which of the two carries it."""
    return "" if _RULE_PLACEHOLDER in body_raw else rule


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
_DRAWING_SHAPED = re.compile(r"^\s*Drawing\s*[0-9]", re.IGNORECASE)
_QUESTION_HEAD = re.compile(r"^\s*(?:\u2753\ufe0f?\s*)?\*\*Q([0-9]{1,6})\*\*")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Verdicts:
    """What the owner said in some comments: `given` the verdict of each number answered
    once, `contradicted` the numbers that got two different verdicts and so have none."""

    given: Mapping[int, Verdict]
    contradicted: frozenset[int]


@dataclass(frozen=True)
class _Line:
    """One classified line: `kind` says what it is, `number` the n of a verdict or a question
    head, `verdict` the verdict of a `verdict` line."""

    kind: Literal["verdict", "drawing_shaped", "question_head", "other"]
    number: int = 0
    verdict: Verdict | None = None


def _classify(text: str) -> list[_Line]:
    """The one classifier of a comment or an interview: every non-blank line outside a fenced
    block and a quote (first non-blank character `>`), in order. Whole lines only; number 0 is
    never a verdict nor a question head."""
    out: list[_Line] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker:
            fence = None if fence == marker.group(1) else (fence or marker.group(1))
            continue
        if fence is not None or line.lstrip().startswith(">") or not line.strip():
            continue
        verdict = _VERDICT_LINE.match(line)
        head = _QUESTION_HEAD.match(line)
        if verdict and int(verdict.group(1)) > 0:
            out.append(
                _Line(
                    "verdict",
                    int(verdict.group(1)),
                    "accept" if verdict.group(2).lower() == "accept" else "reject",
                )
            )
        elif verdict or _DRAWING_SHAPED.match(line):
            out.append(_Line("drawing_shaped"))
        elif head and int(head.group(1)) > 0:
            out.append(_Line("question_head", int(head.group(1))))
        else:
            out.append(_Line("other"))
    return out


def _lines_of(text: str) -> list[tuple[int, Verdict]]:
    """The verdict lines of one comment in order."""
    return [(c.number, c.verdict) for c in _classify(text) if c.verdict is not None]


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


def question_numbers(text: str) -> frozenset[int]:
    """The n of the `**Qn**` question heads of an interview text: a marker that opens a line
    (an optional `❓` before it), never one inside a body, a fence or a quote."""
    return frozenset(c.number for c in _classify(text) if c.kind == "question_head")


def question_ref(n: int) -> str:
    """`Q<n>`, the ref of a question's drawing check."""
    return f"Q{n}"


def unverified_question(ref: str, drawing: str | None) -> str:
    """The line for the human when the approved drawing of a question could not be compared:
    it names the ref and the drawing path, never a key."""
    cited = "no approved drawing" if drawing is None else f"the approved drawing {drawing}"
    return (
        f"{ref}: {cited} could not be compared with what this question changes; "
        "check it by eye before you answer."
    )


class CheckLike(Protocol):
    """What `interview_check` reads of a drawing check (`schemas.DrawingCheck` fits it)."""

    @property
    def subject(self) -> str: ...
    @property
    def ref(self) -> str: ...
    @property
    def verdict(self) -> str: ...
    @property
    def drawing(self) -> str | None: ...
    @property
    def element(self) -> str | None: ...


@dataclass(frozen=True)
class UnverifiedAsk:
    """A question whose approved drawing could not be compared, asked of the owner: `ref` the
    check's ref, `path` the normalized drawing path (`""` when the check names none) and `line`
    the text for the human."""

    ref: str
    path: str
    line: str


def unverified_lines(checks: Sequence[CheckLike]) -> list[UnverifiedAsk]:
    """One ask per `question` check whose verdict is `unverified`, in input order. The only
    builder of those lines and the only place their path is normalized."""
    return [
        UnverifiedAsk(
            ref=c.ref,
            path="" if c.drawing is None else normalize_drawing(c.drawing),
            line=unverified_question(c.ref, c.drawing),
        )
        for c in checks
        if c.subject == "question" and c.verdict == "unverified"
    ]


class DepartureLike(Protocol):
    """What `interview_check` reads of a departure (`schemas.DrawingDeparture` fits it)."""

    @property
    def drawing(self) -> str: ...
    @property
    def element(self) -> str: ...


def interviewer_check(visible: Sequence[int], done: bool, text: str) -> str | None:
    """Why the interviewer's round is refused, `None` when it holds: `visible` names the
    questions that change what a person sees, so it needs a round with questions, and each n
    must be a `**Qn**` question of that round."""
    if not visible:
        return None
    if done:
        return "the round is done but marks questions visible: a closing summary asks nothing"
    asked = question_numbers(text)
    if not asked:
        return "the round marks questions visible but has no readable **Qn** question"
    for n in sorted(set(visible)):
        if n not in asked:
            return f"{question_ref(n)} is marked visible but is not a question of this round"
    return None


@dataclass(frozen=True)
class Resolved:
    """A cited drawing that resolves: `path` is its full path from the repo root."""

    path: str


@dataclass(frozen=True)
class Refused:
    """A cited drawing that does not resolve: `reason` is the text given back to the model."""

    reason: str


_CLOSE_MATCH_CUTOFF = 0.6
_CANDIDATES_MAX = 3
_ECHO_MAX = 120


def _echo(text: str) -> str:
    """The model's text as it may be quoted back to it: whitespace runs collapsed to one
    space, backticks and template braces removed, capped at `_ECHO_MAX` characters."""
    cleaned = " ".join(text.replace("`", "").split())
    while "{{" in cleaned or "}}" in cleaned:
        cleaned = cleaned.replace("{{", "").replace("}}", "")
    return cleaned if len(cleaned) <= _ECHO_MAX else cleaned[: _ECHO_MAX - 1] + "\u2026"


def _expected_form(prototype: str) -> str:
    return "cite the full path" if prototype == "." else f"cite the full path, {prototype}/<name>"


def resolve(design: Design, cited: str, who: str) -> Resolved | Refused:
    """The only place that turns a cited drawing into its full path from the repo root, or says
    why it cannot. `who` is the subject of a refusal. In order: a capture image by its exact
    path; with no listing (`design.files` empty) the prefix rule (the prototype's own
    directory, or any path when the prototype is `.`); with a listing, the path itself, the
    path under the prototype, then the one listed file whose trailing path segments equal the
    cited ones (a bare name, or a path relative to the prototype's parent). Two or more such
    files are a refusal listing up to three, never a pick. A refusal always names the expected
    form; the closest listed file comes only from `design.files`, never from model text."""
    prototype = posixpath.normpath(design.prototype)
    try:
        path = normalize_drawing(cited)
    except ValueError:
        where = (
            "cite a relative path inside the repo"
            if prototype == "."
            else _expected_form(prototype)
        )
        return Refused(f"{who} cites a drawing path that is not valid; {where}")
    if any(path == capture.image for capture in design.captures):
        return Resolved(path)
    if not design.files:
        if prototype == "." or path.startswith(f"{prototype}/"):
            return Resolved(path)
        return Refused(
            f"{who} cites {_echo(cited)}, which the design does not declare; "
            f"{_expected_form(prototype)}"
        )
    images = {capture.image for capture in design.captures}
    listed = tuple(f for f in design.files if f not in images)
    if path in listed:
        return Resolved(path)
    under = path if prototype == "." else f"{prototype}/{path}"
    if under in listed:
        return Resolved(under)
    segments = path.split("/")
    matches = [f for f in listed if f.split("/")[-len(segments) :] == segments]
    if len(matches) == 1:
        return Resolved(matches[0])
    if matches:
        shown = ", ".join(matches[:_CANDIDATES_MAX])
        more = len(matches) - _CANDIDATES_MAX
        extra = f" (+{more} more)" if more > 0 else ""
        return Refused(
            f"{who} cites {_echo(cited)}, which matches more than one drawing: "
            f"{shown}{extra}; {_expected_form(prototype)}"
        )
    refusal = (
        f"{who} cites {_echo(cited)}, which the design does not declare; "
        f"{_expected_form(prototype)}"
    )
    close = difflib.get_close_matches(
        posixpath.basename(path),
        [posixpath.basename(f) for f in listed],
        n=1,
        cutoff=_CLOSE_MATCH_CUTOFF,
    )
    if close:
        closest = next(f for f in listed if posixpath.basename(f) == close[0])
        refusal += f", such as {closest}"
    return Refused(refusal)


def interview_check(
    visible: Sequence[int],
    checks: Sequence[CheckLike],
    departures: Sequence[DepartureLike],
    declared: Design,
    rejected: Mapping[str, int],
    must_match: Mapping[str, int],
) -> str | None:
    """Why the interviewer's drawing checks are refused, `None` when they hold. `rejected` and
    `must_match` map a departure key to its number. A reason names `Q<n>`, a ref or `Drawing
    #n`, never a key."""
    for n in sorted(set(visible)):
        ref = question_ref(n)
        if not any(c.subject == "question" and c.ref == ref for c in checks):
            return f"{ref} is marked visible but has no drawing check"
    departing: set[str] = set()
    matched: set[str] = set()
    keyed: list[tuple[CheckLike, str]] = []
    for check in checks:
        resolved: str | None = None
        if check.drawing is not None:
            found = resolve(declared, check.drawing, check.ref)
            if isinstance(found, Refused):
                return found.reason
            resolved = found.path
        if check.verdict not in ("matches", "departs"):
            continue
        if resolved is None or check.element is None:
            return f"{check.ref} says {check.verdict} without naming the drawing and the element"
        try:
            key = departure_key(resolved, check.element)
        except ValueError:
            return f"{check.ref} cites an element that is empty"
        (departing if check.verdict == "departs" else matched).add(key)
        keyed.append((check, key))
    raised: set[str] = set()
    for departure in departures:
        who = f"a departure of {_echo(departure.drawing)}"
        found = resolve(declared, departure.drawing, who)
        if isinstance(found, Refused):
            return found.reason
        try:
            key = departure_key(found.path, departure.element)
        except ValueError:
            return f"{who} cites an element that is empty"
        raised.add(key)
        if key not in departing:
            return f"{who} has no departs check"
    for check, key in keyed:
        if check.verdict == "departs" and key not in raised:
            return f"{check.ref} says departs but carries no departure for it"
    for key, number in rejected.items():
        if key in departing:
            return (
                f"Drawing #{number} was rejected by the owner and cannot come back as a "
                "departure: make it match the drawing or leave it out"
            )
    for key, number in must_match.items():
        if key not in matched:
            return f"Drawing #{number} was rejected and must now match: add a matches check for it"
    return None


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
        "pm_consultant pm_bug spec/pm_consultant spec/pm_draft",
    ),
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
        "pjm plan_sizer definition_sizer landing_diagnoser reflector qa_performance "
        "tech_lead_carve_out",
    ),
):
    _NO_DRAWING_RULE.update(dict.fromkeys(_names.split(), _reason))

# The prompts that do not carry `{{drawing_rule}}`, each with a reason from a closed grammar.
# Only the prompt-registry test reads it: no production module consults it.
NO_DRAWING_RULE: Mapping[str, str] = MappingProxyType(_NO_DRAWING_RULE)
