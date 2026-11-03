"""`agentic run --arm`, the composition root's binding of an assignment, and `resume` over a
document that holds one (GH-470, subtask `run-arm`). The seams: the `agentic run` argparse
(`cli.main`), `graph.configuration_from` (where every node binds its `Configuration`) and
`cli._resume` from a document. No test here spends: the graph is a double."""

import pytest

from agentic import cli
from agentic.core.process.adapters.langgraph.graph import configuration_from
from agentic.core.process.application import poll
from agentic.core.process.domain.experiment import ARM_FLAG, EXPERIMENT_FILE_FLAG
from agentic.shared.configuration import Configuration
from tests.fakes import memory_ports
from tests.test_cli import Delivers, in_memory, product

DECLARATION = """\
id: light-vs-full
assignment: alternate
arms:
  - name: control
    share: 0.5
    switches: {}
  - name: light
    share: 0.5
    switches:
      review_mode: light
      skip_ephemeral: true
"""
LIGHT = {
    "experiment": "light-vs-full",
    "arm": "light",
    "switches": {"review_mode": "light", "skip_ephemeral": "True"},
}


def declared(tmp_path, text=DECLARATION, name="p"):
    root = product(tmp_path, name)
    (root / ".agentic/experiment.yaml").write_text(text)
    return root


def capture(monkeypatch):
    """The `work` partial `main` would have run, whole: function, positional and keywords."""
    seen = {}

    def fake_run(func, *args):
        work, *_ = args
        seen.update(fn=work.func, args=work.args, keywords=work.keywords)

    monkeypatch.setattr(cli.anyio, "run", fake_run)
    monkeypatch.setattr(cli, "unfixed_stage0", lambda ports: None)
    return seen


def run_main(monkeypatch, root, *extra):
    monkeypatch.setattr(
        "sys.argv", ["agentic", "run", "t", "d", "--product-dir", str(root), *extra]
    )
    cli.main()


def test_run_arm_writes_the_assignment_and_the_nodes_bind_its_switches(monkeypatch, tmp_path):
    root = declared(tmp_path)
    seen = capture(monkeypatch)
    run_main(monkeypatch, root, ARM_FLAG, "light")
    assert seen["keywords"]["assignment"] == LIGHT
    assert seen["keywords"]["experiment_id"] == "light-vs-full"
    # The effective Configuration of a node: the arm's switches, typed as the fields are,
    # and winning over a value the environment holds.
    monkeypatch.setenv("AGENTIC_REVIEW_MODE", "full")
    configurable = {"configurable": {"product_dir": str(root)}}
    cfg = configuration_from(configurable, {"assignment": LIGHT})
    assert cfg.review_mode == "light" and cfg.skip_ephemeral is True
    # No assignment: exactly what the sources say.
    plain = configuration_from(configurable, {})
    assert plain.review_mode == "full" and plain.skip_ephemeral is False


def test_run_without_arm_passes_no_assignment_even_with_a_declaration(monkeypatch, tmp_path):
    root = declared(tmp_path)
    seen = capture(monkeypatch)
    run_main(monkeypatch, root)
    assert seen["keywords"]["assignment"] is None
    assert seen["keywords"]["experiment_id"] is None


def test_run_arm_under_a_conflicting_environment_is_refused_before_anything_is_written(
    monkeypatch, tmp_path, capsys
):
    root = declared(tmp_path)
    capture(monkeypatch)
    monkeypatch.setenv("AGENTIC_REVIEW_MODE", "light")
    with pytest.raises(SystemExit) as info:
        run_main(monkeypatch, root, ARM_FLAG, "control")
    assert info.value.code == 2
    error = capsys.readouterr().err.splitlines()[-1]  # the refusal is the one last line
    assert error.startswith("agentic: --arm control") and "review_mode" in error
    assert not (root / ".agentic/tasks").exists()


@pytest.mark.parametrize(
    ("text", "arm", "reason"),
    [
        (DECLARATION, "missing", "no arm named 'missing'"),
        ("id: x\nassignment: alternate\narms: []\n", "control", "arms must be a non-empty list"),
        (DECLARATION.replace("share: 0.5", "share: 0.7", 1), "control", "sum to"),
        (DECLARATION.replace("review_mode: light", "review_mode: heavy"), "light", "review_mode"),
        ("[unclosed", "control", "not valid YAML"),
    ],
)
def test_run_arm_with_an_unknown_arm_or_an_invalid_file_is_refused(
    monkeypatch, tmp_path, capsys, text, arm, reason
):
    root = declared(tmp_path, text)
    capture(monkeypatch)
    with pytest.raises(SystemExit) as info:
        run_main(monkeypatch, root, ARM_FLAG, arm)
    assert info.value.code == 2
    error = capsys.readouterr().err.splitlines()[-1]
    assert reason in error


def test_run_arm_without_a_declaration_is_refused(monkeypatch, tmp_path, capsys):
    root = product(tmp_path, "p")
    capture(monkeypatch)
    with pytest.raises(SystemExit) as info:
        run_main(monkeypatch, root, ARM_FLAG, "control")
    assert info.value.code == 2
    assert "experiment.yaml" in capsys.readouterr().err.splitlines()[-1]


def test_experiment_file_names_another_declaration(monkeypatch, tmp_path):
    root = product(tmp_path, "p")
    other = tmp_path / "elsewhere.yaml"
    other.write_text(DECLARATION)
    seen = capture(monkeypatch)
    run_main(monkeypatch, root, ARM_FLAG, "control", EXPERIMENT_FILE_FLAG, str(other))
    assert seen["keywords"]["assignment"] == {
        "experiment": "light-vs-full",
        "arm": "control",
        "switches": {},
    }


def test_the_pollers_argv_parses_with_the_real_run_parser():
    argv = poll._argv("agentic", "run", 7, "/p", ARM_FLAG, "light")
    args = cli.build_parser().root.parse_args(argv[1:])
    assert args.arm == "light" and args.issue == 7 and args.experiment_file is None


class Recorder(Delivers):
    def __init__(self):
        super().__init__()
        self.inputs = []

    async def ainvoke(self, task, config):
        self.inputs.append(task)
        return self.final


TASK = {"id": "GH-5", "title": "t", "description": "d"}


async def run_fresh(monkeypatch, tmp_path, previous, experiment_id):
    graph = Recorder()
    monkeypatch.setattr(cli, "graph", graph)
    ports = memory_ports()
    ports.documents.save({**TASK, **previous})
    await cli._run(
        TASK,
        Configuration(product_dir=tmp_path),
        ports,
        True,
        checkpointer=in_memory,
        experiment_id=experiment_id,
    )
    return graph.inputs[0]


@pytest.mark.anyio
async def test_fresh_under_the_same_experiment_keeps_the_arm(monkeypatch, tmp_path):
    entered = await run_fresh(monkeypatch, tmp_path, {"assignment": LIGHT}, "light-vs-full")
    assert entered["assignment"] == LIGHT


@pytest.mark.anyio
async def test_fresh_under_another_experiment_or_none_starts_unassigned(monkeypatch, tmp_path):
    entered = await run_fresh(monkeypatch, tmp_path, {"assignment": LIGHT}, "next-one")
    assert "assignment" not in entered
    entered = await run_fresh(monkeypatch, tmp_path, {"assignment": LIGHT}, None)
    assert "assignment" not in entered


@pytest.mark.anyio
async def test_a_run_without_an_arm_enters_exactly_as_before(monkeypatch, tmp_path):
    graph = Recorder()
    monkeypatch.setattr(cli, "graph", graph)
    await cli._run(
        TASK, Configuration(product_dir=tmp_path), memory_ports(), checkpointer=in_memory
    )
    assert graph.inputs == [TASK]


@pytest.mark.anyio
async def test_a_run_with_an_assignment_enters_with_it(monkeypatch, tmp_path):
    graph = Recorder()
    monkeypatch.setattr(cli, "graph", graph)
    await cli._run(
        TASK,
        Configuration(product_dir=tmp_path),
        memory_ports(),
        checkpointer=in_memory,
        assignment=LIGHT,
    )
    assert graph.inputs == [{**TASK, "assignment": LIGHT}]


PAUSED = {
    **TASK,
    "type": "bug",
    "status": "waiting_on_human",
    "escalated": "Developer stopped by the task cap",
    "cost_usd": 1.0,
}


async def resume_dry_run(monkeypatch, cfg):
    monkeypatch.setattr(cli, "graph", Delivers())
    ports = memory_ports()
    ports.documents.save({**PAUSED, "assignment": LIGHT})
    ports.tracker.earlier = ["Escalated: Developer stopped by the task cap"]
    await cli._resume("GH-5", cfg, ports, from_document=True, dry_run=True, checkpointer=in_memory)


@pytest.mark.anyio
async def test_resume_keeps_the_documents_arm_and_says_so_when_the_environment_differs(
    monkeypatch, tmp_path, capsys
):
    cfg = Configuration(product_dir=tmp_path, review_mode="full", skip_ephemeral=True)
    await resume_dry_run(monkeypatch, cfg)
    error = capsys.readouterr().err
    assert error.count("\n") == 1
    assert "review_mode" in error and "skip_ephemeral" not in error and "'light'" in error


@pytest.mark.anyio
async def test_resume_with_a_matching_environment_says_nothing_and_needs_no_file(
    monkeypatch, tmp_path, capsys
):
    # No `.agentic/experiment.yaml` anywhere under `tmp_path`, and the declaration that
    # produced the arm may have changed since: the document alone decides.
    cfg = Configuration(product_dir=tmp_path, review_mode="light", skip_ephemeral=True)
    await resume_dry_run(monkeypatch, cfg)
    assert capsys.readouterr().err == ""


def test_the_resumed_state_binds_the_documents_switches_whatever_the_file_says(tmp_path):
    root = declared(tmp_path, DECLARATION.replace("review_mode: light", "review_mode: full"))
    cfg = configuration_from({"configurable": {"product_dir": str(root)}}, {"assignment": LIGHT})
    assert cfg.review_mode == "light"
