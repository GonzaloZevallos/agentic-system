"""The suppressions a product forbids in the code a Developer writes (issue #38):
pattern, the extensions it applies to, and the reason a denial carries. Declared under
`forbidden` in `.agentic/stack.yaml` (`core.knowledge.domain.stack`, glossary: Stack
del producto) and validated there, at load -- a broken declaration never reaches this
module, which is pure: no filesystem, no YAML, nothing of a stack the domain does not
already hand it as a `Forbidden`.

The system carries no suppression of its own: what a silencing comment even looks
like, in whatever language the product is in, is the product's to say."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Forbidden:
    """One product-declared suppression: `pattern` already compiled (`stack.forbidden`
    raises `ValueError` at load when `re.compile` rejects it), the extensions it
    applies to (with the leading dot, e.g. `.py`), and the `reason` a denial carries
    verbatim -- the only text a Developer reads for it, nothing the system authors."""

    pattern: re.Pattern[str]
    extensions: frozenset[str]
    reason: str


def denial(file_path: str, before: str, after: str, forbidden: Sequence[Forbidden]) -> str | None:
    """Why replacing `before` with `after` in `file_path` may not happen, or the first
    declared rule's own `reason`, in file order (deterministic: the reason a Developer
    reads never depends on which rule happened to run last). `None` when `forbidden` is
    empty, when no rule applies to this file's extension, or when a rule's occurrences
    in `after` do not outnumber `before`'s -- one that was already there is not the
    task's."""
    suffix = Path(file_path).suffix
    for rule in forbidden:
        if suffix not in rule.extensions:
            continue
        if len(rule.pattern.findall(after)) > len(rule.pattern.findall(before)):
            return rule.reason
    return None
