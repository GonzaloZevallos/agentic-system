"""Fail before spending money if a product overlay is off, or if the run would edit
the code that is running it."""

from __future__ import annotations

from agentic.shared.configuration import Configuration
from agentic.shared.stage0 import CHECKOUT

from ..domain import docs, rules
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
    "cost_history": "- none",
    "checks": "- `tests`: `<the product's own command>`",
    "split_part": "",
    # Empty for a product that declares no `docs`, so the gate renders the prompts as they ship.
    "docs_plan": docs.plan_text(None),
    "docs_review": docs.review_text(None),
    # The wording of a task that is not a part, so the gate renders the prompt as it ships.
    "parts_remedy": rules.parts_remedy_text(None),
    "parts_step": rules.parts_step_text(None),
    "parts_field": rules.parts_field_text(None),
    "tracker_with_parts": rules.TRACKER_WITH_PARTS,
    # `pjm.md` names it (ADR-0019); the real value `classify.py` passes at runtime is
    # `product_name(ports)`, not this generic stand-in, which only has to render.
    "product": "product",
}


# What a span carries when the product's name cannot be read; `product_name` says when.
UNKNOWN_PRODUCT = "unknown"


class RunsOnItsOwnCheckout(ValueError):
    """The product is the repository the package runs from: the run would rewrite the
    code executing it (ADR-0017). A `ValueError`, so the CLI refuses with it."""


def preflight(cfg: Configuration, ports: Ports) -> None:
    """The run's own checkout first, then the product's stack and its name, then the
    vocabulary and every role's prompt rendered: a product that declares no stack, no
    name, a bad flags file or an overlay with a stray placeholder fails here, not in the
    middle of a task."""
    refuse_its_own_checkout(cfg, ports)
    declared_stack(ports)
    # Not inside `declared_stack`: that one is also read at a task's base, where a task
    # older than the key has no name to find and must still resume (issue #47).
    ports.stack.product()
    vocabulary = ports.flags.load()
    for role in ROLES:
        load_prompt(role, cfg, ports, vocabulary, **PLACEHOLDERS)


# Where each Tech Lead prompt that carries the rules for written docs holds them.
DOCS_PLACEHOLDERS = {
    "tech_lead": "docs_plan",
    "tech_lead_platform": "docs_plan",
    "tech_lead_review": "docs_review",
    "tech_lead_review_light": "docs_review",
}


def docs_placeholders_missing(ports: Ports, revision: str = "HEAD") -> list[str]:
    """One advisory per Tech Lead prompt that cannot carry the written-docs rules: the
    product declares `docs` at `revision` and the prompt it resolves to (a product override
    included) lacks its placeholder, so the plan and the review never hear them. Never a
    refusal: the gate on generated files still applies, and a product may mean it."""
    if ports.stack.docs(revision) is None:
        return []
    messages = []
    for role in ROLES:
        placeholder = DOCS_PLACEHOLDERS.get(role.slug)
        if placeholder is None:
            continue
        raw, source = ports.prompts.raw(role)
        if "{{" + placeholder + "}}" not in raw:
            messages.append(
                f"the product declares `docs`, but {source} has no `{{{{{placeholder}}}}}`: "
                "the Tech Lead will not hear the rules for the written docs "
                "(`docs/architecture/`, `docs/README.md`). Put the placeholder back."
            )
    return messages


def declared_stack(ports: Ports, revision: str = "HEAD") -> dict[str, str]:
    """The product's setup as `revision` declares it, having read the whole file.

    HEAD by default, the commit a task starting now would be measured against; there is
    no task yet at preflight. `checks`, `witness`, `parallel_checks`, `affected_checks`,
    `affected_fallback` (ADR-0046), `delivery_checks`, `rerun_checks` (ADR-0066),
    `check_budgets`, `merge_inert` (GH-353), `forbidden` and `design` are all loaded here
    for validation only, so a broken one fails here and not when a worktree is born or a Developer
    delivers halfway through the run (issues #36, #37, #38; ADR-0040) -- the
    return expression stays `setup`'s alone, so `no_setup`'s warning does not move."""
    ports.stack.checks(revision)
    ports.stack.witness(revision)
    ports.stack.parallel_checks(revision)
    ports.stack.affected_checks(revision)
    ports.stack.affected_fallback(revision)
    ports.stack.delivery_checks(revision)
    ports.stack.rerun_checks(revision)
    ports.stack.check_budgets(revision)
    ports.stack.merge_inert(revision)
    ports.stack.forbidden(revision)
    ports.stack.design(revision)
    ports.stack.ci(revision)
    return ports.stack.setup(revision)


def declared_stack_of(task_id: str, ports: Ports) -> str:
    """The stack at the base of a task already under way, for a resume, and that base
    itself.

    HEAD is not enough here: a task whose branch started before the product declared a
    stack passes the check at HEAD and then dies inside a node, where nothing turns an
    infrastructure error into an escalation. Cheap and worth it: the base is the commit
    that run will actually be measured against (ADR-0016, addendum). Returning the base
    lets a caller print `no_forbidden` against the exact revision this task is
    governed by, instead of asking `task_base` a second time (issue #38)."""
    base = task_base({"id": task_id}, ports)
    declared_stack(ports, base)
    return base


def product_name(ports: Ports) -> str:
    """The product's name for the run's spans and metrics, `unknown` when it cannot be
    read.

    `preflight` is where a product with no name refuses, before anything is spent. By the
    time a span opens the run is already under way, and a label is never worth killing a
    node mid-task: the same `unknown` `system_version` falls back to when there is no git
    (ADR-0008). It matters for the door `preflight` does not guard, `langgraph dev`, which
    imports the wired graph and never sees the CLI."""
    try:
        return ports.stack.product()
    except (ValueError, OSError):
        return UNKNOWN_PRODUCT


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


def criteria_texts(ports: Ports, revision: str) -> dict[str, str]:
    """The criteria documents the product holds at `revision`, file name -> text. A file
    that is absent or was deleted at `revision` reads None and is left out, so it is never
    measured. The names come from the knowledge port, which owns them (ADR-0010, invariant
    5); the system default of the standards is no file of the product's and is never read."""
    return {
        name: text
        for name in ports.knowledge.criteria_files()
        if (text := ports.workspace.show(revision, name)) is not None
    }


def over_cap(ports: Ports, revision: str = "HEAD") -> list[str]:
    """One message per criteria file the product holds at `revision` past the line cap
    (GH-295, ADR-0055); the count itself is `knowledge`'s alone. Never a refusal: what to
    do with the messages is the caller's."""
    return ports.knowledge.over_cap(criteria_texts(ports, revision))


def criteria_regressed(ports: Ports, base: str, head: str) -> list[str]:
    """One message per criteria file that is over the cap at `head` and longer than at
    `base` (GH-334): the verdict the delivery gate and the merge decision share, the
    count and the cap being `knowledge`'s alone. A `GitError` from either read goes to the
    caller, which knows whether an unread document may pass."""
    return ports.knowledge.regressions(criteria_texts(ports, base), criteria_texts(ports, head))


def no_forbidden(ports: Ports, revision: str) -> str | None:
    """Why this run's Edit/Write cage denies nothing, or None when it forbids at least
    one suppression (issue #38). Never a refusal, the same fact `no_setup` is: a Go
    product, or one that genuinely forbids nothing, has declared nothing wrong. Does
    not join the `cli.py` warning loop next to `unfixed_stage0`/`no_setup`: those ask
    against a single revision every invocation shares, while this is asked against
    `"HEAD"` on `run` and the base `declared_stack_of` returns on `resume` -- the
    revision the run will actually be measured against, never the same for both."""
    if ports.stack.forbidden(revision):
        return None
    return (
        # The file is named and not imported, `no_setup`'s own reason (ADR-0010).
        "the product declares no `forbidden` in .agentic/stack.yaml: the Edit/Write "
        "cage denies no suppression this run (issue #38). Declare the patterns this "
        "product's own tools spell, or accept that none is forbidden."
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
