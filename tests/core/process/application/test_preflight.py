"""The refusal that costs nothing: a run whose product is the checkout running it."""

import re
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.application import preflight as module
from agentic.core.process.application.preflight import (
    PLACEHOLDERS,
    UNKNOWN_PRODUCT,
    RunsOnItsOwnCheckout,
    declared_stack_of,
    no_forbidden,
    no_setup,
    preflight,
    product_name,
    unfixed_stage0,
)
from agentic.core.process.domain.suppressions import Forbidden
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryStack, memory_ports

PRODUCT = Path("/repos/product")


def ports_where(**repositories: str):
    """Ports whose workspace answers these repositories, keyed by path."""
    ports = memory_ports()
    ports.workspace.repositories = {Path(k): Path(v) for k, v in repositories.items()}
    return ports


@pytest.fixture
def stage0(monkeypatch, tmp_path):
    """The checkout the package runs from, wherever the test wants it."""
    checkout = tmp_path / "stage0"
    monkeypatch.setattr(module, "CHECKOUT", checkout)
    return checkout


def test_a_product_in_another_repository_passes(stage0):
    ports = ports_where(**{str(stage0): "/repos/system/.git", str(PRODUCT): "/repos/product/.git"})
    preflight(Configuration(product_dir=PRODUCT), ports)


def test_a_product_in_no_repository_passes(stage0):
    preflight(Configuration(product_dir=PRODUCT), ports_where(**{str(stage0): "/sys/.git"}))


def test_a_package_outside_any_repository_passes(stage0):
    """An installed wheel answers site-packages, which is in no repository: not stage0,
    and not a reason to refuse."""
    preflight(Configuration(product_dir=PRODUCT), ports_where(**{str(PRODUCT): "/p/.git"}))


def test_the_product_being_the_running_checkout_is_refused(stage0):
    ports = ports_where(**{str(stage0): "/repos/system/.git", str(PRODUCT): "/repos/system/.git"})
    with pytest.raises(RunsOnItsOwnCheckout) as refusal:
        preflight(Configuration(product_dir=PRODUCT), ports)
    # The message says what to use and how to make it, with paths that can be pasted.
    assert str(PRODUCT) in str(refusal.value)
    assert f"{stage0}/scripts/stage0.sh" in str(refusal.value)


def test_a_worktree_of_the_running_repository_is_refused_too(stage0):
    """Another directory, the same repository: comparing paths would have let it in."""
    worktree = Path("/repos/system/.agentic/worktrees/GH-44")
    ports = ports_where(**{str(stage0): "/repos/system/.git", str(worktree): "/repos/system/.git"})
    with pytest.raises(RunsOnItsOwnCheckout):
        preflight(Configuration(product_dir=worktree), ports)


class ExplodingFlags:
    def load(self) -> dict[str, str]:
        raise AssertionError("the product was read before the refusal")


class ExplodingStack:
    def checks(self, revision: str) -> dict[str, str]:
        raise AssertionError("the stack was read before the refusal")

    def setup(self, revision: str) -> dict[str, str]:
        raise AssertionError("the stack was read before the refusal")


def test_the_checkout_is_judged_before_anything_is_read(stage0):
    """The cheapest check there is, so it goes first: nothing of a product that must not
    run gets opened at all."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/r/.git"})
    with pytest.raises(RunsOnItsOwnCheckout):
        preflight(
            Configuration(product_dir=PRODUCT),
            replace(ports, flags=ExplodingFlags(), stack=ExplodingStack()),
        )


class UndeclaredStack:
    def checks(self, revision: str) -> dict[str, str]:
        raise stack.not_declared(f"{revision}:{stack.OVERLAY}")

    def setup(self, revision: str) -> dict[str, str]:
        raise stack.not_declared(f"{revision}:{stack.OVERLAY}")


def test_a_product_that_declares_no_stack_fails_before_a_prompt_is_rendered(stage0):
    """Mandatory since #36, and read at HEAD, which is the base the next task starts
    from. Before the prompts, because it is the cheaper of the two."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(stack.NotDeclared, match="declares no stack"):
        preflight(
            Configuration(product_dir=PRODUCT),
            replace(ports, stack=UndeclaredStack(), flags=ExplodingFlags()),
        )


def test_the_stack_is_read_at_head_and_both_keys_are_loaded(stage0):
    """A `setup` that does not parse must fail here, not when a worktree is born
    halfway through the run."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    asked = MemoryStack(setup={"deps": "uv sync"})
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=asked))
    assert asked.revisions == ["HEAD", "HEAD"]


def test_a_product_without_setup_cannot_be_promised_an_environment_of_its_own(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    assert no_setup(replace(ports, stack=MemoryStack(setup={"deps": "uv sync"}))) is None
    warning = no_setup(replace(ports, stack=MemoryStack(setup={})))
    assert warning is not None and "declares no `setup`" in warning
    assert ".agentic/stack.yaml" in warning


# -- the product's declared suppression cage -----------------------------------------


class BrokenForbiddenStack(MemoryStack):
    """A stack whose `forbidden` is half-written, the same shape `UndeclaredStack`
    gives `checks`: `declared_stack` must surface it at load, never at a hook."""

    def forbidden(self, revision: str) -> list[Forbidden]:
        raise ValueError("stack.yaml: expected a non-empty `reason`")


class BrokenDesignStack(MemoryStack):
    """A stack whose `design` block is half-written: `declared_stack` must surface it
    at load, before a single role is paid, not inside a design role mid-task."""

    def design(self, revision: str):
        raise ValueError("stack.yaml: expected a non-empty `prototype` under `design`")


def test_a_broken_design_block_refuses_before_spending(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match="expected a non-empty `prototype`"):
        preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenDesignStack()))


def test_a_stack_without_design_passes_byte_for_byte(stage0):
    """No `design` block is `None`, never an error: a product that has not written
    one yet runs exactly as it does today."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=MemoryStack()))


def test_declared_stack_validates_forbidden_at_load_too(stage0):
    """A broken `forbidden` must fail here, not inside an Edit/Write hook mid-task."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match="expected a non-empty `reason`"):
        preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenForbiddenStack()))


def test_a_product_that_declares_no_forbidden_rule_is_told_so():
    warning = no_forbidden(replace(memory_ports(), stack=MemoryStack(forbidden=[])), "HEAD")
    assert warning is not None and "forbidden" in warning


def test_a_product_that_declares_a_forbidden_rule_says_nothing():
    rule = Forbidden(re.compile("x"), frozenset({".src"}), "no")
    ports = replace(memory_ports(), stack=MemoryStack(forbidden=[rule]))
    assert no_forbidden(ports, "HEAD") is None


def test_declared_stack_of_returns_the_base_it_already_validated():
    ports = replace(memory_ports(), stack=MemoryStack())
    assert declared_stack_of("T-1", ports) == "HEAD"  # no branch on record: falls back


# -- stage0 that is not fixed to anything --------------------------------------------


def test_a_clean_stage0_says_nothing(stage0):
    assert unfixed_stage0(ports_where(**{str(stage0): "/sys/.git"})) is None


def test_a_dirty_stage0_is_told_before_the_run_starts(stage0):
    """Not a refusal: editing stage0 is a deliberate act, and debugging the system is a
    good reason for it. But finding out afterwards costs a run's worth of money."""
    ports = ports_where(**{str(stage0): "/sys/.git"})
    ports.workspace.uncommitted.add(stage0)
    warning = unfixed_stage0(ports)
    assert str(stage0) in warning and "+dirty" in warning
    assert f"{stage0}/scripts/stage0.sh" in warning


def test_a_package_outside_any_repository_says_nothing(stage0):
    """An installed wheel: there is no tree to be dirty, and asking git would fail."""
    assert unfixed_stage0(ports_where()) is None


def test_an_untracked_file_in_stage0_is_not_what_the_warning_claims(stage0):
    """`system_version` stamps `+dirty` from tracked changes only, so a scratch file git
    never heard of leaves the run comparable. Warning about it would state a consequence
    that does not happen."""
    ports = ports_where(**{str(stage0): "/sys/.git"})
    ports.workspace.dirty.add(stage0)  # untracked counts for a delivery, not for a version
    assert unfixed_stage0(ports) is None


def test_the_remedy_fixes_the_stage0_it_names(stage0):
    """A second stage0, or one installed elsewhere: the script's default directory is
    `~/.agentic-stage0`, so the command has to name the dirty one or it fixes another."""
    ports = ports_where(**{str(stage0): "/sys/.git"})
    ports.workspace.uncommitted.add(stage0)
    assert f"scripts/stage0.sh <sha> {stage0}" in unfixed_stage0(ports)


def test_a_product_that_declares_no_name_does_not_run(stage0):
    """Every span and every metric of the run would land unlabelled, and unlabelled is
    what makes two products add up in a panel (issue #47)."""
    ports = replace(memory_ports(), stack=MemoryStack(product=None))
    with pytest.raises(ValueError, match="expected `product`"):
        preflight(Configuration(product_dir=PRODUCT), ports)


def test_the_name_is_asked_at_head_so_a_task_older_than_the_key_still_resumes(stage0):
    stack_port = MemoryStack()
    preflight(Configuration(product_dir=PRODUCT), replace(memory_ports(), stack=stack_port))
    assert "HEAD" in stack_port.revisions


def test_placeholders_cover_the_name_pjm_md_now_declares():
    # `pjm.md` names `{{product}}` (docs/adr/0019); this gate renders every role's
    # prompt before a peso is spent, and a placeholder outside this set fails it for
    # every product, not just this one.
    assert "product" in PLACEHOLDERS


def test_preflight_renders_the_light_reviewers_prompt_too(stage0):
    """The review-arm subtask (issue #28) adds `tech_lead_review_light` to `ROLES`;
    `preflight` renders every role's prompt with `PLACEHOLDERS` alone, so a placeholder
    the new prompt would need outside that set fails here, not mid-task."""
    from agentic.core.process.domain.roles import ROLES

    ports = ports_where(**{str(stage0): "/sys/.git", str(PRODUCT): "/repos/product/.git"})
    preflight(Configuration(product_dir=PRODUCT), ports)  # no ValueError: every prompt renders
    assert "tech_lead_review_light" in {role.slug for role in ROLES}


def test_the_label_falls_back_instead_of_killing_a_node_already_running():
    """`preflight` is where a nameless product refuses. Past that point the run is under
    way and a label is not worth the task: `langgraph dev` never sees the CLI."""
    assert product_name(replace(memory_ports(), stack=MemoryStack(product=None))) == UNKNOWN_PRODUCT
    assert (
        product_name(replace(memory_ports(), stack=MemoryStack(product="gym-saas"))) == "gym-saas"
    )
