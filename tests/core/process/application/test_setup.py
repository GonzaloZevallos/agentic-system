"""The product's setup: a checkout is not ready when git finishes with it (issue #37).

A worktree has no environment of its own, so a tool that wants one walks up until it
finds the product's. The system prepares each checkout once, with what the product
declares, before any role works in it."""

import pytest

from agentic.core.process.application.checkouts import SetupFailed, subtask_checkout, task_checkout
from agentic.core.process.application.define import define
from agentic.core.process.application.deliver import deliver
from agentic.core.process.application.develop import develop
from agentic.core.process.application.integrate import integrate
from agentic.core.process.application.ports import CommandResult
from agentic.core.process.domain.task import WAITING_ON_HUMAN
from agentic.shared.configuration import Configuration
from tests.fakes import (
    FakeReaper,
    MemoryCommands,
    MemoryStack,
    MemoryWorkspace,
    ScriptedRunner,
    delivered,
    memory_ports,
)

pytestmark = pytest.mark.anyio

TASK = {"id": "GH-12", "title": "Dashboard", "description": "d", "type": "feature"}
TASK_PATH = MemoryWorkspace.ROOT / "GH-12"
SETUP = {"venv": "uv venv", "deps": "uv sync --locked"}


def sub(id_, status="pending"):
    return {
        "id": id_,
        "title": id_,
        "brief": "b",
        "seams": ["s"],
        "depends_on": [],
        "status": status,
    }


@pytest.fixture
def cfg(tmp_path):
    return Configuration(product_dir=tmp_path)


def ports_with(setup=SETUP, **overrides):
    return memory_ports(ScriptedRunner(), stack=MemoryStack(setup=setup), **overrides)


def test_a_checkout_is_prepared_when_it_is_born_in_the_order_the_product_declared(cfg):
    ports = ports_with()
    checkout = task_checkout(TASK, cfg, ports)
    assert ports.commands.ran == [(TASK_PATH, "uv venv"), (TASK_PATH, "uv sync --locked")]
    assert checkout.path == TASK_PATH


def test_asking_again_gets_the_checkout_that_exists_and_prepares_nothing(cfg):
    # A subtask that adds a dependency installs it in its own environment: its work.
    ports = ports_with()
    task_checkout(TASK, cfg, ports)
    ports.commands.ran.clear()
    task_checkout(TASK, cfg, ports)
    assert ports.commands.ran == []


def test_a_product_that_declares_no_setup_prepares_nothing(cfg):
    ports = ports_with(setup={})
    task_checkout(TASK, cfg, ports)
    assert ports.commands.ran == []


def test_the_setup_is_read_at_the_tasks_base_never_at_the_branchs_own(cfg):
    ports = ports_with()
    ports.workspace.existing_branches["agentic/GH-12"] = ("head", "base0")
    task_checkout(TASK, cfg, ports)
    assert ports.stack.revisions == ["base0"]


def test_every_worktree_of_a_wave_is_prepared_not_only_the_task_one(cfg):
    state = {**TASK, "subtasks": {"api": sub("api"), "domain": sub("domain")}}
    ports = ports_with()
    api = subtask_checkout(state, "api", cfg, ports)
    domain = subtask_checkout(state, "domain", cfg, ports)
    # The task's checkout too: the workspace makes it on the way to a subtask's, so it
    # would otherwise be the one worktree nobody ever prepares.
    assert ports.commands.ran == [
        (TASK_PATH, "uv venv"),
        (TASK_PATH, "uv sync --locked"),
        (api.path, "uv venv"),
        (api.path, "uv sync --locked"),
        (domain.path, "uv venv"),
        (domain.path, "uv sync --locked"),
    ]


def test_a_step_that_fails_stops_the_ones_after_it(cfg):
    commands = MemoryCommands(**{"uv venv": [CommandResult(1, "no python 3.13")]})
    ports = ports_with(commands=commands)
    with pytest.raises(SetupFailed):
        task_checkout(TASK, cfg, ports)
    assert ports.commands.ran == [(TASK_PATH, "uv venv")]


async def test_a_setup_that_fails_in_the_fan_in_stops_the_wave_before_a_developer_runs(cfg):
    """No role has run and none will: an environment that will not build is not
    something a model fixes, so the human gets it with the command and its last lines."""
    output = "\n".join(f"line {n}" for n in range(40))
    commands = MemoryCommands(**{"uv sync --locked": [CommandResult(1, output)]})
    ports = ports_with(commands=commands)
    state = {**TASK, "subtasks": {"api": sub("api"), "domain": sub("domain")}}

    patch = await integrate(state, cfg, ports)

    assert patch["status"] == WAITING_ON_HUMAN
    reason = patch["escalated"]
    assert "'deps' failed on 'agentic/GH-12'" in reason
    assert "$ uv sync --locked" in reason
    assert "line 39" in reason and "line 9" not in reason  # the last 30 lines, no more
    assert ".agentic/stack.yaml" in reason
    # Whose turn it was, not whoever wrote last: no role seeded `current_role` here.
    assert patch["current_role"] == "Developer"
    # The wave stops where it broke: no worktree of a sister subtask is even attempted.
    assert ports.workspace.checkouts_made == ["agentic/GH-12"]


async def test_a_setup_that_fails_on_the_task_checkout_escalates_from_the_definition(cfg):
    commands = MemoryCommands(**{"uv venv": [CommandResult(1, "no python 3.13")]})
    ports = ports_with(commands=commands)

    patch = await define(TASK, cfg, ports)

    assert patch["status"] == WAITING_ON_HUMAN
    assert "'venv' failed on 'agentic/GH-12'" in patch["escalated"]
    assert patch["current_role"] == "Product Analyst"
    # Nothing was paid: the interview never started.
    assert patch.get("cost_usd", 0.0) == 0.0


async def test_the_developer_works_in_a_checkout_the_fan_in_already_prepared(cfg):
    """The setup never runs inside a branch of the fan-out, where an escalation would
    have nowhere to go: the fan-in made every worktree of the wave before dispatching."""
    ports = memory_ports(ScriptedRunner(developer=[delivered()]), stack=MemoryStack(setup=SETUP))
    state = {**TASK, "subtasks": {"api": sub("api"), "domain": sub("domain")}}
    await integrate(state, cfg, ports)
    ports.commands.ran.clear()

    checkout = ports.workspace.subtask_checkout("GH-12", "api")
    ports.workspace.moved.add(checkout.path)
    await develop({**state, "subtask_id": "api"}, cfg, ports)

    assert [command for _, command in ports.commands.ran] == ["pytest"]  # the check, no setup


def test_a_checkout_whose_setup_failed_is_discarded_so_the_resume_makes_it_again(cfg):
    """Otherwise it sits there half built, every later ask reports it as existing, and
    the resume the escalation asks for runs roles in the environment the human fixed."""
    commands = MemoryCommands(**{"uv sync --locked": [CommandResult(1, "no lockfile")]})
    ports = ports_with(commands=commands)

    with pytest.raises(SetupFailed):
        task_checkout(TASK, cfg, ports)
    assert ports.workspace.discarded == [TASK_PATH]

    # The human fixes the product and resumes: the checkout is made and prepared anew.
    ports.commands.ran.clear()
    task_checkout(TASK, cfg, ports)
    assert ports.commands.ran == [(TASK_PATH, "uv venv"), (TASK_PATH, "uv sync --locked")]


def test_a_setup_that_times_out_says_so(cfg):
    """A killed process often drains nothing: without the note the human reads a blank
    block and no reason for it."""
    commands = MemoryCommands(**{"uv venv": [CommandResult(-9, "", timed_out=True)]})
    ports = ports_with(commands=commands)
    with pytest.raises(SetupFailed) as failure:
        task_checkout(TASK, cfg, ports)
    assert "timed out after 600 s" in failure.value.output


async def test_a_setup_that_fails_outside_the_fan_in_still_reaches_the_human(cfg):
    """`deliver` runs after `cleanup` removed the worktrees, so it can be the call that
    makes one: a crash there would lose the run instead of escalating."""
    commands = MemoryCommands(**{"uv venv": [CommandResult(1, "no python")]})
    ports = ports_with(commands=commands)

    patch = await deliver({**TASK, "subtasks": {"api": sub("api", "approved")}}, cfg, ports)

    assert patch["status"] == WAITING_ON_HUMAN
    assert "'venv' failed" in patch["escalated"]
    assert ports.workspace.pushed == []  # nothing was delivered


def test_a_checkout_whose_setup_failed_is_reaped_before_it_is_discarded(cfg):
    journal: list[str] = []
    commands = MemoryCommands(**{"uv venv": [CommandResult(1, "no python 3.13")]})
    ports = ports_with(commands=commands)
    assert isinstance(ports.workspace, MemoryWorkspace) and isinstance(ports.reaper, FakeReaper)
    ports.workspace.journal = journal
    ports.reaper.journal = journal

    with pytest.raises(SetupFailed):
        task_checkout(TASK, cfg, ports)

    assert journal == ["reap:GH-12", "discard:GH-12"]
