"""The rules of a depth-2 carve-out (GH-719, ADR-0093 addendum): a part whose plan is over the
size budget moves whole subtasks into a follow-up issue instead of escalating. Pure: texts and
mappings in, texts, lists and verdicts out. Imports nothing from `rules`, `schemas`, `task`,
`application` or `adapters`, and has no caller yet (part 3 of GH-719).

The marker's `<part_issue>` is the GitHub issue number of the part that extracted the steps,
never its index, and `rules.split_part_of` never reads the marker: a follow-up is not a part
and has no depth. Text is refused, never sanitized."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Protocol, TypedDict

KINDS = ("change", "migration")
# Literal copies of the labels a follow-up must not inherit: `needs-human`, the hold label, the
# stop label and the split part label (`split.PART_LABEL`); `copied_labels` adds `agentic` itself.
# `agentic-stop` is a reserved literal: no code defines a stop label today, so it matches nothing
# real yet; it is kept so a stop label added later is never inherited by a follow-up.
LABEL_EXCLUDED = frozenset({"needs-human", "agentic-hold", "agentic-stop", "agentic"})
LABEL_EXCLUDED_PREFIX = "path:"
_SYSTEM_LABEL = "agentic"
_OPEN, _CLOSE, _BLOCKED = "<!--", "-->", "Blocked by:"
_MARKER = re.compile(r"<!-- agentic-carve-out: (\S+) of #(\d+) -->")
_ISSUE_REF = r"(?:[\w.-]+/[\w.-]+)?#\d+|https?://github\.com/\S+/(?:issues|pull)/\d+"
_CLOSING = re.compile(
    rf"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\b[:\s]*(?:{_ISSUE_REF})", re.IGNORECASE
)
_CLOSING_WORD = re.compile(r"\b(?:closes|fixes|resolves)\b", re.IGNORECASE)
_BLOCKED_LINE = re.compile(r"^(\s*Blocked by:\s*)(#\d+(?:\s*,\s*#\d+)*)(\s*)$", re.IGNORECASE)
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_REDECLARED = (
    "estimated_cost_usd",
    "estimated_files",
    "hand_files",
    "hand_lines",
    "test_lines",
    "required_paths",
    "ci_required",
    "integration",
)


class CarveOut(TypedDict):
    """What the model declares when it carves steps out of a plan that is over the budget."""

    steps: list[str]
    title: str
    body: str
    kind: str
    moved_criteria: list[str]
    estimated_cost_usd: float
    estimated_files: int
    hand_files: int
    hand_lines: int
    test_lines: int
    required_paths: list[str]
    ci_required: list[str]
    integration: str
    remainder_green: str
    guard: str


class _CarveOutRecord(TypedDict, total=False):
    """The task document's `carve_out` record (persisted in part 3, not in `TaskState`):
    `data` is the declared `CarveOut`, `outcome` is written only at close."""

    data: Mapping[str, object]
    outcome: str


class _Ref(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def body(self) -> str: ...


def _subtasks(plan_output: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = plan_output.get("subtasks")
    return [s for s in raw if isinstance(s, Mapping)] if isinstance(raw, list) else []


def _invariants(subtask: Mapping[str, object]) -> list[str]:
    raw = subtask.get("invariants")
    if isinstance(raw, str):
        return [raw]
    return [str(i) for i in raw] if isinstance(raw, list) else []


def _copied_refusal(text: str) -> str | None:
    low = text.lower()
    for token in (_OPEN, _CLOSE, _BLOCKED.lower()):
        if token in low:
            return f"a copied brief or invariant holds {token!r}"
    if _CLOSING.search(text):
        return "a copied brief or invariant holds a closing keyword before an issue reference"
    return None


def validate(carve: CarveOut, plan_output: Mapping[str, object]) -> list[str]:
    """The reasons `carve` is refused for `plan_output`; empty when it is valid."""
    refusals: list[str] = []
    subtasks = _subtasks(plan_output)
    ids = [str(s.get("id")) for s in subtasks]
    steps = list(carve["steps"])
    extracted = set(steps)
    if not steps:
        refusals.append("steps is empty: a carve-out extracts at least one subtask")
    unknown = [s for s in steps if s not in ids]
    if unknown:
        refusals.append(f"steps names ids that are not subtasks of the plan: {unknown}")
    if steps and not unknown and extracted >= set(ids):
        refusals.append("steps is every subtask of the plan: a carve-out keeps at least one")
    for sub in subtasks:
        if str(sub.get("id")) in extracted:
            continue
        deps = sub.get("depends_on")
        named = [d for d in deps if d in extracted] if isinstance(deps, list) else []
        if named:
            refusals.append(f"remaining subtask {sub.get('id')!r} depends on extracted {named}")
    # The original plan's integration usually wires every subtask; what becomes the
    # remainder's integration is the redeclared text, so that is what must not name them.
    integration = str(carve.get("integration") or "")
    for step in steps:
        if re.search(rf"(?<![\w-]){re.escape(step)}(?![\w-])", integration):
            refusals.append(f"the redeclared integration names the extracted id {step!r}")
    if carve["kind"] not in KINDS:
        refusals.append(f"kind {carve['kind']!r} is not one of {list(KINDS)}")
    if "\n" in carve["remainder_green"]:
        refusals.append("remainder_green is one line")
    for sub in subtasks:
        if str(sub.get("id")) not in extracted:
            continue
        for text in [str(sub.get("brief") or ""), *_invariants(sub)]:
            reason = _copied_refusal(text)
            if reason:
                refusals.append(f"{sub.get('id')!r}: {reason}")
    return refusals


def text_refused(title: str, body: str) -> str | None:
    """The first reason the model's own title/body is refused, or `None`. Never sanitizes."""
    for text in (title, body):
        for token in (_OPEN, _CLOSE, _BLOCKED):
            if token.lower() in text.lower():
                return f"the text holds {token!r}"
        if _CLOSING_WORD.search(text):
            return "the text holds a closing keyword (Closes, Fixes or Resolves)"
    if "\n" in title:
        return "the title has a newline"
    return None


def remainder(plan_output: Mapping[str, object], carve: CarveOut) -> dict[str, object]:
    """A copy of `plan_output` without the extracted subtasks, with the size fields,
    `required_paths`, `ci_required` and `integration` redeclared from `carve`."""
    declared: Mapping[str, object] = carve
    out = dict(plan_output)
    out["subtasks"] = [s for s in _subtasks(plan_output) if s.get("id") not in carve["steps"]]
    out.update({key: declared[key] for key in _REDECLARED})
    return out


def remainder_refusal(
    plan_output: Mapping[str, object],
    carve: CarveOut,
    fits: Callable[[Mapping[str, object]], bool],
) -> str | None:
    """A reason when the remainder does not fit, as `fits` (the caller's `rules.estimate_fits`
    or `schemas.plan_fits`) judges; no size number lives here."""
    if fits(remainder(plan_output, carve)):
        return None
    return "the remainder still does not fit the size budget"


def marker(task_id: str, part_issue: int) -> str:
    """The follow-up's marker; `part_issue` is the part's issue number, never its index."""
    return f"<!-- agentic-carve-out: {task_id} of #{part_issue} -->"


def marked_part_issue(body: str) -> int | None:
    """The part issue number the body's carve-out marker names, or `None`."""
    found = _MARKER.search(body)
    return int(found.group(2)) if found else None


def follow_up_body(
    model_body: str,
    extracted_subtasks: Sequence[Mapping[str, object]],
    part_issue: int,
    parent_context: str,
    kind: str,
    *,
    task_id: str,
) -> str:
    """The follow-up issue's body: the model text, `## Carved-out steps` with each brief and
    invariants copied verbatim, then the system lines. Total: `validate` and `text_refused`
    must already be green."""
    steps: list[str] = []
    for sub in extracted_subtasks:
        steps.append(f"### {sub.get('id')}: {sub.get('title')}\n\n{sub.get('brief') or ''}")
        invariants = _invariants(sub)
        if invariants:
            steps.append("Invariants:\n" + "\n".join(f"- {i}" for i in invariants))
    return "\n\n".join(
        [
            model_body,
            "## Carved-out steps",
            *steps,
            f"Kind: {kind}",
            parent_context,
            f"{_BLOCKED} #{part_issue}",
            marker(task_id, part_issue),
        ]
    )


def copied_labels(labels: Sequence[str] | None) -> tuple[str, ...]:
    """`agentic` first, then the part's labels that are neither excluded nor `path:`-prefixed."""
    kept = [
        label
        for label in labels or ()
        if label not in LABEL_EXCLUDED and not label.startswith(LABEL_EXCLUDED_PREFIX)
    ]
    return tuple(dict.fromkeys([_SYSTEM_LABEL, *kept]))


def append_blocker(body: str, parent: int, targets: Sequence[int]) -> str | None:
    """`body` with `targets[1:]` inserted after `#parent` on the exact `Blocked by:` line (a
    `split_sweep.Rewrite`: `targets` starts with `parent`), or `None` when none is missing."""
    if not targets or targets[0] != parent:
        return None
    rebuilt: list[str] = []
    changed = False
    fence = ""
    for line in body.splitlines(keepends=True):
        content = line.splitlines()[0]
        ending = line[len(content) :]
        stripped = content.strip()
        opener = _FENCE.match(stripped)
        if fence:
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = ""
        elif opener:
            fence = opener.group(1)
        else:
            found = _BLOCKED_LINE.match(content)
            numbers = [int(n) for n in re.findall(r"\d+", found.group(2))] if found else []
            if found and parent in numbers:
                merged: list[int] = []
                for number in numbers:
                    merged.extend(targets if number == parent else (number,))
                unique = list(dict.fromkeys(merged))
                if unique != numbers:
                    listed = ", ".join(f"#{n}" for n in unique)
                    content = found.group(1) + listed + found.group(3)
                    changed = True
        rebuilt.append(content + ending)
    return "".join(rebuilt) if changed else None


def followups_of(refs: Sequence[_Ref], part_issues: Collection[int]) -> list[int]:
    """Issue numbers of the `refs` whose carve-out marker names one of `part_issues`."""
    found: list[int] = []
    for ref in refs:
        tail = ref.url.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit() and marked_part_issue(ref.body) in part_issues:
            found.append(int(tail))
    return found


def carve_out_due(
    state: Mapping[str, object], judged: Mapping[str, object], *, writes_tracker: bool
) -> bool:
    """A carve-out is due when `judged` carries a `fit_question`, the document has no
    `carve_out` record and the run writes to the tracker."""
    return bool(judged.get("fit_question")) and "carve_out" not in state and writes_tracker


def carve_out_pending(state: Mapping[str, object]) -> bool:
    """A record with `data` and no `outcome` is an unfinished carve-out."""
    record = state.get("carve_out")
    return isinstance(record, Mapping) and "data" in record and "outcome" not in record
