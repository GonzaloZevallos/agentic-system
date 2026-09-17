"""Fail before spending money if a product overlay is off, or if the run would edit
the code that is running it."""

from __future__ import annotations

from agentic.shared.configuration import Configuration
from agentic.shared.stage0 import CHECKOUT

from ..domain.roles import ROLES
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
    """The run's own checkout first, then the vocabulary and every role's prompt
    rendered: a bad flags file or an overlay with a stray placeholder fails here, not in
    the middle of a task."""
    refuse_its_own_checkout(cfg, ports)
    vocabulary = ports.flags.load()
    for role in ROLES:
        load_prompt(role, cfg, ports, vocabulary, **PLACEHOLDERS)


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
