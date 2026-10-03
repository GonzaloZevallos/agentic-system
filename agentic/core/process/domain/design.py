"""The optional `design` block of the product stack (ADR-0040, glossary: Diseño del
producto): where the static prototype lives, and the two commands -- `sample_app` and
`compare` -- a design role may run instead of a browser or an arbitrary URL.

Declared under `design` in `.agentic/stack.yaml` (`core.knowledge.domain.stack`) and
validated there, at load -- a broken declaration never reaches this module, which is
pure: no filesystem, no YAML, nothing of a stack the domain does not already hand it
as a `Design`. Read at the task's base, like `checks` and `witness`, never at the
branch's HEAD: a task that edits the block does not change the bar it is measured
against (`core.knowledge.domain.stack`'s own docstring)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Design:
    """The product's design block, once `core.knowledge.domain.stack.design` has
    validated it. `prototype` is the directory of static screens, `entry` the screen
    inside it a walk starts from. `sample_app` and `compare` are optional commands:
    `None` is the product declaring neither, not a half-written file -- that failure
    already happened at load, in `core.knowledge`."""

    prototype: str
    entry: str
    sample_app: str | None
    compare: str | None


# The one line the system writes in a lead's section when it cannot back a claim with
# a rendered screen or a comparison -- a fact about the product's own declaration, not
# a role failing (ADR-0040's own "Known risks"). Variants exist for the two narrower
# cases: a block that only lacks one command, so the note names the piece that is
# missing instead of claiming the whole block is gone.
NO_DESIGN_NOTE = "no design block: walk on text, no rendered screens, no comparisons"
NO_SAMPLE_APP_NOTE = "no `sample_app` command: no sample app to walk, text only"
NO_COMPARE_NOTE = "no `compare` command: no comparisons"


def design_rules(design: Design | None) -> tuple[str, ...]:
    """A permission rule for the lead that may run `compare`, mirroring
    `roles.check_rules`: one `Bash` prefix per declared command, so the lead's own
    reach is exactly the product's own command, nothing wider (ADR-0040, §8).

    `()` for no block, and for a block that declares no `compare` -- the lead walks on
    text either way, and gets no rule to run anything at all."""
    if design is None or design.compare is None:
        return ()
    return (f"Bash({design.compare}:*)",)


def design_facts(design: Design | None) -> str:
    """The prototype's own facts, as a design node's turn message hands them to any
    design role (ADR-0040, §6): the directory and the entry screen, as data, never a
    tool, a framework or a path named in a prompt. `""` for no block at all: the
    caller decides what that absence means for its own section (a thinker's prompt
    already says so in its own words; a lead's gets `NO_DESIGN_NOTE` instead)."""
    if design is None:
        return ""
    return (
        "Design data for this task:\n\n"
        f"- Prototype: `{design.prototype}`\n"
        f"- Entry screen: `{design.entry}`"
    )


def compare_facts(design: Design | None) -> str:
    """The `compare` command, as data, for the lead that may run it -- appended after
    `design_facts`, never in its place. `NO_COMPARE_NOTE` when the block exists but
    declares no `compare`; `""` when there is no block at all, since `design_facts`
    already says so alone and this would only repeat it."""
    if design is None:
        return ""
    if design.compare is None:
        return f"- {NO_COMPARE_NOTE}"
    return f"- Comparisons: run `{design.compare}` and read what it writes, one per screen."
