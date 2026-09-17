"""The refusal that costs nothing: a run whose product is the checkout running it."""

from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.application import preflight as module
from agentic.core.process.application.preflight import (
    RunsOnItsOwnCheckout,
    no_setup,
    preflight,
    unfixed_stage0,
)
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
