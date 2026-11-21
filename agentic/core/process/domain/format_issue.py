"""The pure rules of the format-refusal issue (GH-1011): the marker a refusal issue carries,
the choice of the issue to comment on, when a refusal is due, how long the refused value was
and the public evidence the issue says. No I/O: nothing here files, reads or retries
anything; the filing is built in #1030 and after.

The marker is a convention, not authentication: any author can write the line. `matches` reads
it only as the last non-empty line of a body and compares the rule exactly, so a quote of it
in the middle of a body, or one rule that prefixes another, never matches.

`evidence` is written for a public repository: it uses closed fields only and never reads the
refused value, a failure's `error` or a refusal's `detail` (those hold the role's own text).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Protocol, get_args

from .task import Failure, RefusalRule

_RULES: tuple[str, ...] = get_args(RefusalRule)
_MARKER_FORMAT = "<!-- agentic-refusal: {rule} -->"
_MARKER_LINE = re.compile(r"<!-- agentic-refusal: ([a-z_]+) -->")
# The reasons a refusal can be recorded with (`Failure.reason`): only these two carry a rule.
_DUE_REASONS = ("schema", "check")


class _Ref(Protocol):
    @property
    def url(self) -> str: ...

    @property
    def body(self) -> str: ...


def marker(rule: str) -> str:
    """The marker line of the issue for `rule`. Raises `ValueError` for a rule that is not a
    `RefusalRule`, so a typo never files a marker nothing matches."""
    if rule not in _RULES:
        raise ValueError(f"unknown refusal rule: {rule!r}")
    return _MARKER_FORMAT.format(rule=rule)


def matches(body: str, rule: str) -> bool:
    """Whether the last non-empty line of `body` (trailing whitespace and `\\r` absorbed) is
    exactly the marker of `rule`. The only reader of the marker."""
    lines = body.rstrip().splitlines()
    if not lines:
        return False
    found = _MARKER_LINE.fullmatch(lines[-1].rstrip())
    return found is not None and found.group(1) == rule


def _number(url: str) -> int | None:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isascii() and tail.isdigit() else None


def find(refs: Sequence[_Ref], rule: str) -> _Ref | None:
    """Among `refs` whose body matches `rule`, the one with the lowest issue number (the last
    segment of its URL); one whose URL has no numeric tail is skipped. `None` when none is."""
    best: tuple[int, _Ref] | None = None
    for ref in refs:
        number = _number(ref.url)
        if number is None or not matches(ref.body, rule):
            continue
        if best is None or number < best[0]:
            best = (number, ref)
    return best[1] if best else None


def due(failures: Sequence[Failure]) -> bool:
    """Whether the refusal of the last failure is one to file: its `reason` is `schema` or
    `check` and the row carries a `rule`. Any other reason (disk_full, max_turns, stall,
    render, limit...) is not a refusal of the role's output."""
    if not failures:
        return False
    last = failures[-1]
    return last.get("reason") in _DUE_REASONS and "rule" in last


def value_len(output: object, field: str) -> int | None:
    """The size of the refused value of one top-level key of the role's structured output.

    `field` is exactly what `run_role.refusal_field` returns: a declared field, `markdown` or
    `""`, so only the top level is read. A `str` gives its length and a `list` or `dict` its
    item count; a missing key, `field == ""`, a non-dict `output`, and any other value (a
    `bool` first, an `int`, a `float`, `None`) give `None`. The value is never dumped or
    reprinted: it is measured in place.

    Nothing calls it yet. The refusal raise sites built in #1030 call it on the raw
    structured output and write `Failure.value_len` only under the `refused` gate that
    writes `rule`, leaving the key absent when the result is `None`."""
    if not isinstance(output, Mapping) or not field or field not in output:
        return None
    value = output[field]
    if isinstance(value, str | list | dict):
        return len(value)
    return None


def earlier_rules(epoch: Sequence[Failure]) -> list[str]:
    """The `rule` of each failure of `epoch` before the last, in order; a row without a rule
    is skipped and a repeated rule is kept."""
    return [row["rule"] for row in epoch[:-1] if "rule" in row]


def evidence(
    rule: str,
    field: str,
    role: str,
    product: str,
    issue_number: int,
    value_len: int | None,
    attempts: int,
    earlier_rules: Sequence[str],
) -> tuple[str, str]:
    """The title and body of the refusal issue for `rule`, from closed fields only: the
    rule, the schema field it names, the role, the product, the task's issue number, the
    length of the refused value (`unknown` when `None`), the attempts and the rules refused
    earlier in the epoch. It never reads the value, a failure's `error` or a refusal's
    `detail`. The body ends with `marker(rule)`."""
    title = f"Format refusal {rule}: {role} in {product}"
    length = "unknown" if value_len is None else str(value_len)
    earlier = ", ".join(earlier_rules) if earlier_rules else "none"
    lines = [
        f"The structured output of the {role} role was refused by the rule `{rule}`.",
        "",
        f"- product: {product}",
        f"- task: #{issue_number}",
        f"- role: {role}",
        f"- rule: {rule}",
        f"- field: {field or 'none'}",
        f"- value length: {length}",
        f"- attempts: {attempts}",
        f"- earlier rules in this epoch: {earlier}",
        "",
        "Closing this issue releases the held tasks once the fix runs.",
        "",
        marker(rule),
    ]
    return title, "\n".join(lines)
