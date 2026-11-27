"""What kind of `gh` call an argument list is (GH-990).

A kind is the command, not a rate bucket: `api` includes GraphQL, `gh api rate_limit` and
the REST writes sent as `gh api -i`, which GitHub meters differently from one another. The
count by kind says what the system asked `gh` to do, not how many requests GitHub saw.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args

GhCallKind = Literal["issue_list", "issue_view", "pr_list", "pr_view", "api", "other"]

GH_CALL_KINDS: tuple[GhCallKind, ...] = get_args(GhCallKind)

_BY_COMMAND: dict[tuple[str, str], GhCallKind] = {
    ("issue", "list"): "issue_list",
    ("issue", "view"): "issue_view",
    ("pr", "list"): "pr_list",
    ("pr", "view"): "pr_view",
}


def kind_of(args: Sequence[str]) -> GhCallKind:
    """The kind of `gh <args>`, `args` given without the leading `gh`. Reads only the first
    two arguments: never the name a caller gives the call, never what `gh` printed. Anything
    that is not one of the listed commands, empty args included, is `other`."""
    head = tuple(args[:2])
    if head[:1] == ("api",):
        return "api"
    return _BY_COMMAND.get(head, "other") if len(head) == 2 else "other"


__all__ = ["GH_CALL_KINDS", "GhCallKind", "kind_of"]
