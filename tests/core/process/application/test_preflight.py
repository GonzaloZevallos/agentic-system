"""The refusal that costs nothing: a run whose product is the checkout running it."""

from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.process.application import preflight as module
from agentic.core.process.application.preflight import RunsOnItsOwnCheckout, preflight
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports

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


def test_the_checkout_is_judged_before_anything_is_read(stage0):
    """The cheapest check there is, so it goes first: nothing of a product that must not
    run gets opened at all."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/r/.git"})
    with pytest.raises(RunsOnItsOwnCheckout):
        preflight(Configuration(product_dir=PRODUCT), replace(ports, flags=ExplodingFlags()))
