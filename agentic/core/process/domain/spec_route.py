"""The escalation of a refused spec (GH-594): `refusal_escalation` and the patch it returns.

It lives beside `spec_delivery`, not in it: it calls `rules.escalation`, and `rules` and `task`
already import `spec_delivery`, so the other way round would close an import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import get_args

from . import rules
from .spec_delivery import (
    SLICE_SLUG,
    SpecContract,
    SpecReadRefusal,
    SpecRefusalRecord,
    SpecRouteCause,
    spec_path_ok,
)
from .task import EscalationPatch

SPEC_REFUSAL_DETAIL_MAX = 16
_SLUG_MAX_CHARS = 64
_SLUG = re.compile(SLICE_SLUG)


class SpecRefusalPatch(EscalationPatch):
    """The escalation keys plus the refusal the spec intake and the graph read."""

    spec_refusal: SpecRefusalRecord


def _detail_ok(item: str) -> bool:
    return (
        item in SpecContract.__annotations__
        or item in get_args(SpecReadRefusal)
        or item == "unreachable"
        or spec_path_ok(item)
        or (len(item) <= _SLUG_MAX_CHARS and _SLUG.fullmatch(item) is not None)
    )


def refusal_escalation(
    cause: SpecRouteCause, detail: Sequence[str], reason: str
) -> SpecRefusalPatch:
    """The patch that pauses a task on a refused spec: `rules.escalation('classification',
    reason)` plus `spec_refusal: {cause, detail}`. `ValueError` for a `cause` outside
    `SpecRouteCause`, for more than 16 items, or for any item that is not one of the closed
    classes below (an issue body is editable, so no free text reaches the document):
    - a field name of `SpecContract`;
    - a member of `SpecReadRefusal`, or `unreachable`;
    - a spec path for which `spec_path_ok` holds;
    - a lowercase slug (`SLICE_SLUG`, at most 64 characters): a slice id or a risk id.
    An empty `detail` is valid."""
    if cause not in get_args(SpecRouteCause):
        raise ValueError(f"not a spec refusal cause: {cause!r}")
    if len(detail) > SPEC_REFUSAL_DETAIL_MAX:
        raise ValueError(f"a spec refusal names at most {SPEC_REFUSAL_DETAIL_MAX} items")
    for item in detail:
        if not _detail_ok(item):
            raise ValueError(f"not a closed spec refusal detail: {item!r}")
    return {
        **rules.escalation("classification", reason),
        "spec_refusal": {"cause": cause, "detail": list(detail)},
    }
