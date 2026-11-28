"""The retry message of a refused structured output (GH-1017, README.md, 14).

A delivery refused for its format (`schema`, `check`) that names its rule is retried with
exactly what was refused: the rule id, the field, the value that was written there, the
motive, and one line saying how to fix it. The fix lines are a closed table over
`RefusalRule`, so a rule added to the type without a line fails a test instead of a retry.

The refused value is model output: it comes back to the session that wrote it, on one line,
cut, and unable to close the block it sits in. `clean_value` is the only place that does that;
the value is never a metric label or a span attribute and travels no further than the
`Failure` and this message. Pure: no I/O, imports only the task's own types."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

from .task import RefusalRule

VALUE_MAX_CHARS: Final = 240
DETAIL_MAX_CHARS: Final = 500
_TAG: Final = "PreviousAttemptFailed"
_NEUTRAL_TAG: Final = "Previous-Attempt-Failed"

FIX: Final[Mapping[RefusalRule, str]] = {
    "preview_slice_missing": (
        "Fold the work into exactly one slice, or return it again with several to have a "
        "split proposed."
    ),
    "visible_without_drawing_check": (
        "Declare the drawing check the visible work needs, or mark the work as not visible."
    ),
    "drawing_path_undeclared": "Use only drawing paths that the product's design declares.",
    "parts_when_fits": "The plan fits in one task: return it without parts.",
    "estimate_does_not_fit": (
        "Return the plan as parts, each one small enough to fit its own estimate."
    ),
    "part_over_budget": "Make the named part smaller, or split it into more parts.",
    "depends_on_reason_too_long": (
        "Rewrite each depends_on_reasons entry as one line of at most one sentence."
    ),
    "estimated_files_empty": "List the files the work is expected to touch.",
    "estimate_below_spent_floor": (
        "Raise the estimate to cover what the task already spent, then deliver again."
    ),
    "system_category": (
        "Pick one of the categories the schema offers: the one you named is written by the "
        "system alone."
    ),
    "output_missing": (
        "Your work is committed; call the delivery tool now with your result. Do not redo the work."
    ),
    "schema_invalid": "Return the field with a value the schema accepts, and nothing else changed.",
    "unmapped": "Fix exactly what the detail says, then deliver again.",
}


class _Missing:
    """The type of `MISSING`: what `value_at` answers when the path does not resolve, apart
    from a leaf that is `None`."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()


def _one_line(text: str, limit: int) -> str:
    """`text` on one line, unable to write the block's tag, cut to `limit` last."""
    flat = text.replace("\r", " ").replace("\n", " ")
    return flat.replace(_TAG, _NEUTRAL_TAG)[:limit]


def clean_value(value: object) -> str:
    """A refused value as the retry message shows it: a `str` as it is, anything else as
    JSON (`repr` where JSON cannot write it), on one line, with the block's tag neutralised
    and cut to 240 characters. The one place that cleans a refusal's value."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=repr, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(value)
    return _one_line(text, VALUE_MAX_CHARS)


def clean_detail(detail: str) -> str:
    """The motive of a refusal as the retry message shows it: one line, tag neutralised, cut
    to 500 characters. Longer than a value, because the motive is what names what is wrong."""
    return _one_line(detail, DETAIL_MAX_CHARS)


def value_at(output: object, loc: Sequence[str | int]) -> object:
    """What `output` holds at `loc` (dict keys and list indices), or `MISSING` when the path
    does not resolve. A string is never walked into, and a negative index is no index."""
    node = output
    for step in loc:
        if isinstance(node, Mapping) and step in node:
            node = node[step]
        elif (
            isinstance(node, list)
            and isinstance(step, int)
            and not isinstance(step, bool)
            and 0 <= step < len(node)
        ):
            node = node[step]
        else:
            return MISSING
    return node


def retry_message(rule: RefusalRule, field: str, value: str | None, detail: str | None) -> str:
    """The `<PreviousAttemptFailed>` block of a refusal that names its rule: `rule`, `field`,
    `value` (only with one), `detail` (only with one, and never for `output_missing`, whose
    only motive is that nothing was delivered) and `fix`, then the line every retry ends in.
    `value` and `detail` arrive already cleaned."""
    lines = [f"<{_TAG}>", f"rule: {rule}", f"field: {field}"]
    if value is not None:
        lines.append(f"value: {value}")
    if detail and rule != "output_missing":
        lines.append(f"detail: {detail}")
    lines += [f"fix: {FIX[rule]}", f"</{_TAG}>", "", "Retry. Fix exactly what failed."]
    return "\n".join(lines)
