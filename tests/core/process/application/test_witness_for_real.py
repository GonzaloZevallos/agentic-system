"""The checkout witness through the real adapters: git makes the worktree, the shell
runs the declared witness, and a fake cannot show the negative case (ADR-0023 calls an
enforcement artefact blocking when its tests never exercise a rejected input). The
pattern follows `test_setup_for_real.py`: a fake cannot show that the commands land in
the checkout git just made, and neither can it show that a witness printing somewhere
else is the one thing that makes it a witness."""

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.knowledge.adapters.git import GitStack
from agentic.core.process.adapters.git.workspace import GitWorkspace
from agentic.core.process.application.checkouts import subtask_checkout
from agentic.core.process.application.presubmit import CheckoutPresubmit
from agentic.core.process.domain.check_schedule import CheckPriority
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports, real_commands

# A check that always passes, whatever the environment: the point of these two tests is
# the witness alone, not the product's own checks.
STACK = "checks:\n  noop: 'true'\n"
STATE = {"id": "GH-41", "subtasks": {"api": {"id": "api"}}}


def git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def product(tmp_path):
    (tmp_path / ".agentic").mkdir()
    (tmp_path / ".agentic/stack.yaml").write_text(STACK)
    git("init", "-q", "-b", "master", ".", cwd=tmp_path)
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "v0", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def real(product):
    cfg = Configuration(product_dir=product)
    ports = replace(
        memory_ports(),
        workspace=GitWorkspace(product, cfg.git_identity),
        stack=GitStack(product),
        commands=real_commands(),
    )
    return cfg, ports


def _gate(real, product, witness: str, path: Path | None = None) -> CheckoutPresubmit:
    cfg, ports = real
    checkout = subtask_checkout(STATE, "api", cfg, ports)
    before = ports.workspace.head(checkout.path)
    (checkout.path / "code.py").write_text("x = 1\n")
    git("add", "-A", cwd=checkout.path)
    git("commit", "-q", "-m", "deliver", cwd=checkout.path)
    return CheckoutPresubmit(
        ports,
        path or checkout.path,
        before,
        {"noop": "true"},
        cfg.check_timeout_s,
        witness=witness,
        priority=CheckPriority.SUBTASK,
    )


def test_a_witness_that_prints_outside_the_checkout_refuses(real, product):
    """The declared witness prints the *product's* own directory -- the parent of
    every worktree `GitWorkspace` makes -- never the Developer's checkout."""
    gate = _gate(real, product, witness=f"echo {product}")
    reason = gate()
    assert reason is not None
    assert "checkout witness" in reason.lower()
    assert str(product) in reason


def test_its_twin_that_prints_inside_the_checkout_does_not_refuse(real, product):
    """Its twin, printing the checkout `pwd` itself runs in: same mechanism, same
    checkout, the other verdict."""
    gate = _gate(real, product, witness="pwd")
    assert gate() is None


def test_a_checkout_reached_through_a_symlink_whose_witness_prints_the_resolved_form_agrees(
    real, product, tmp_path
):
    """`CheckoutPresubmit` is handed the checkout through a symlink, never the real
    directory `GitWorkspace` made -- exactly what `self.path.resolve()` exists for
    (ADR-0024). The witness prints the already-resolved, symlink-free form, as a
    well-behaved witness must (this repo's own does the same): the gate must still
    agree, not wrongly refuse a checkout it only reached indirectly."""
    cfg, ports = real
    checkout = subtask_checkout(STATE, "api", cfg, ports)
    before = ports.workspace.head(checkout.path)
    (checkout.path / "code.py").write_text("x = 1\n")
    git("add", "-A", cwd=checkout.path)
    git("commit", "-q", "-m", "deliver", cwd=checkout.path)

    link = tmp_path / "via-symlink"
    link.symlink_to(checkout.path)
    resolved = checkout.path.resolve()

    gate = CheckoutPresubmit(
        ports,
        link,
        before,
        {"noop": "true"},
        cfg.check_timeout_s,
        witness=f"echo {resolved}",
        priority=CheckPriority.SUBTASK,
    )
    assert gate() is None
