"""The optional `docs` block of the product stack: the command that regenerates every
generated doc artifact and the paths that command owns.

Declared under `docs` in `.agentic/stack.yaml` (`core.knowledge.domain.stack`) and validated
there, at load -- a broken declaration never reaches this module, which is pure: no
filesystem, no YAML, nothing of a stack the domain does not already hand it as a `Docs`.
Read at the task's base, like `checks` and `design`, never at the branch's HEAD."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Docs:
    """The product's docs block, once `core.knowledge.domain.stack.docs` has validated it.
    `generate` is the one-line command that rewrites every generated doc artifact; `paths`
    are the globs it owns, each validated at load."""

    generate: str
    paths: tuple[str, ...]
