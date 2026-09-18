"""What a non-executing role reads when the SDK denies a call outside its `Role.allowed`
(glossary: Negación; ADR-0011, fourth addendum; issue #69, #81).

The permission layer has already decided by the time this is asked for: the SDK's
`can_use_tool` callback is only consulted for a call an allow rule does not already
cover (never for a role that executes, which runs under `bypassPermissions`), so this
module never re-parses what a role may run — it reads `role.tools` alone, the same
tuple the SDK evaluates, and supplies the words instead of the SDK's generic reason. An
adapter passes this text through; it never holds one of its own (README.md, "denial
mechanism" invariant).

Issue #69: a Product Analyst in bug mode treated eight denials as a syntax problem,
retrying with heredocs, `python3 -` and base64 instead of changing what it did. The
sentence names the role's own rule instead."""

from __future__ import annotations

from .roles import Role

_READING = "the code, the task documents and the existing tests"


def denial_for(role: Role) -> str:
    """The sentence `role` reads when a call outside its `Role.allowed` reaches the
    SDK's permission layer: writing is outside its role, and how to get what it needs
    instead. Whether it may also run something beyond reading is read straight off
    `role.tools` (`Bash` declared or not), never a second list of what the role may do."""
    if "Bash" in role.tools:
        reproduce = f"reproduce by reading {_READING}, or by running the declared checks"
    else:
        reproduce = f"reproduce by reading {_READING}"
    return f"this role writes nothing; {reproduce}. A denial here is not a syntax problem."
