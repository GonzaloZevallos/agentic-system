"""Fail before spending money if a product overlay is off, or if the run would edit
the code that is running it."""

from __future__ import annotations

from agentic.shared.configuration import Configuration
from agentic.shared.stage0 import CHECKOUT

from ..domain.roles import ROLES
from .checkouts import task_base
from .ports import Ports
from .run_role import load_prompt

# Every placeholder any prompt may use, with a value good enough to render. A prompt
# that names one outside this set fails here, whatever role it belongs to.
PLACEHOLDERS = {
    "remaining_changes": 2,
    "max_rounds": 4,
    "round": 1,
    "subtask": "subtask",
    "base": "HEAD",
    "review": "a first review",
    "branches": "- none",
    "invariants": "- none",
    "proposed_invariants": "- none",
    "checks": "- `tests`: `pytest`",
}


class RunsOnItsOwnCheckout(ValueError):
    """The product is the repository the package runs from: the run would rewrite the
    code executing it (ADR-0017). A `ValueError`, so the CLI refuses with it."""


def preflight(cfg: Configuration, ports: Ports) -> None:
    """The run's own checkout first, then the product's stack, then the vocabulary and
    every role's prompt rendered: a product that declares no stack, a bad flags file or
    an overlay with a stray placeholder fails here, not in the middle of a task."""
    refuse_its_own_checkout(cfg, ports)
    declared_stack(ports)
    vocabulary = ports.flags.load()
    for role in ROLES:
        load_prompt(role, cfg, ports, vocabulary, **PLACEHOLDERS)


def declared_stack(ports: Ports, revision: str = "HEAD") -> dict[str, str]:
    """The product's setup as `revision` declares it, having read the whole file.

    HEAD by default, the commit a task starting now would be measured against; there is
    no task yet at preflight. Both keys are loaded, so a broken `setup` fails here and
    not when a worktree is born halfway through the run (issues #36, #37)."""
    ports.stack.checks(revision)
    return ports.stack.setup(revision)


def declared_stack_of(task_id: str, ports: Ports) -> None:
    """The stack at the base of a task already under way, for a resume.

    HEAD is not enough here: a task whose branch started before the product declared a
    stack passes the check at HEAD and then dies inside a node, where nothing turns an
    infrastructure error into an escalation. Cheap and worth it: the base is the commit
    that run will actually be measured against (ADR-0016, addendum)."""
    declared_stack(ports, task_base({"id": task_id}, ports))


def no_setup(ports: Ports) -> str | None:
    """Why this run cannot promise a Developer its own environment, or None when it can.

    A product that declares no `setup` gets checkouts that git made and nothing else. A
    worktree has no environment of its own, so a tool that wants one walks up to the
    product's, and two subtasks then share it: a `pytest` that imports a sister branch's
    code, or a `uv pip install -e .` that repoints the human's checkout (issue #37). Not
    a refusal: a product with no dependencies has nothing to prepare, and one that does
    may still prefer to live with it. A fact about the product, so what to do with it is
    the caller's; the CLI says it out loud, before the money is spent."""
    if declared_stack(ports):
        return None
    return (
        # The file is named and not imported: `core.knowledge` owns it, and the
        # dependency rule keeps the two cores apart (ADR-0010).
        "the product declares no `setup` in .agentic/stack.yaml: every checkout shares "
        "the product's environment, so what a Developer tests is not necessarily its "
        "own branch (issue #37). Declare the commands that prepare a fresh checkout."
    )


def unfixed_stage0(ports: Ports) -> str | None:
    """Why this run will compare against nothing, or None when it will.

    stage0 with uncommitted changes runs code that no sha names, so `system_version`
    stamps every trace `+dirty` and the run drops out of every comparison, which is
    exactly what that `+dirty` is for (ADR-0008). Tracked changes only, the same question
    `system_version` asks: warning about an untracked scratch file would promise a
    `+dirty` that never comes. Not a refusal: editing stage0 is a deliberate act and
    debugging the system is a good reason for one. A fact about the checkout, so what to
    do with it is the caller's; the CLI says it out loud, because finding out afterwards
    costs a run's worth of money."""
    if ports.workspace.repository_at(CHECKOUT) is None or ports.workspace.is_committed(CHECKOUT):
        return None
    return (
        f"stage0 has uncommitted changes ({CHECKOUT}): this run goes out as `+dirty` and "
        "compares against nothing (ADR-0008). Fix it to a sha with "
        # The directory spelled out: the script's default is `~/.agentic-stage0`, which
        # is not necessarily the stage0 that is dirty.
        f"{CHECKOUT}/scripts/stage0.sh <sha> {CHECKOUT}"
    )


def refuse_its_own_checkout(cfg: Configuration, ports: Ports) -> None:
    """The system may develop itself, and never from the checkout it runs from: a
    Developer editing `rules.py` while `rules.py` decides its routing, and every trace
    stamped with the sha being edited, `+dirty` forever, which is the one thing that
    `+dirty` exists to prevent (ADR-0008, ADR-0017).

    Repositories and not paths: a worktree of the system's repo is a different directory
    and the same repository, and running from one is the same mistake."""
    stage0 = ports.workspace.repository_at(CHECKOUT)
    if stage0 is None or stage0 != ports.workspace.repository_at(cfg.product_dir):
        return
    # Absolute in the message: from stage0 the command runs somewhere else, so a
    # `--product-dir .` copied out of here would point at stage0 itself.
    product = cfg.product_dir.resolve()
    raise RunsOnItsOwnCheckout(
        f"{product} is in the repository this package runs from ({CHECKOUT}): the run "
        "would rewrite the code executing it, and every trace would carry the sha being "
        "edited, `+dirty` forever (ADR-0017).\n"
        "Run the system from stage0, a checkout of its own fixed to a sha:\n"
        f"    {CHECKOUT}/scripts/stage0.sh <sha>\n"
        f"    cd ~/.agentic-stage0 && uv run agentic <command> --product-dir {product}"
    )
