"""`eval run`'s driver (`eval_commands._eval_run`, GH-470) with an in-memory `EvalRoots`, the
machine registry double and a fake child: no git, no process, no `gh`."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from agentic.core.measurement.application import eval_commands
from agentic.core.process.application.ports import RunRegistration
from agentic.core.process.domain import document as task_document
from agentic.core.process.domain import rules
from agentic.shared.configuration import Configuration
from agentic.shared.errors import CannotStart
from tests.fakes import (
    FakeExperimentFile,
    FakeFreeSpace,
    MemoryRunRegistry,
    MemoryStack,
    eval_set_yaml,
    memory_ports,
)

BASE = "a" * 40
SHA8 = "a" * 8
NOW = datetime(2026, 11, 4, tzinfo=UTC)
EXPERIMENT = {
    "id": "exp",
    "assignment": "alternate",
    "arms": [
        {"name": "control", "share": 0.5, "switches": {}},
        {"name": "lean", "share": 0.5, "switches": {"review_mode": "light"}},
    ],
}


class MemoryRoots:
    """`EvalRoots` over a temporary directory: a root is a plain directory, `bases` says
    which commits resolve and which carry a stack."""

    def __init__(self, product: Path, bases: dict[str, bool] | None = None):
        self.product = product
        self.bases = {BASE: True} if bases is None else bases
        self.created: list[tuple[str, str]] = []
        self.removed: list[str] = []
        self.deleted: list[str] = []

    def resolves(self, base):
        return base in self.bases

    def has_stack(self, base):
        return self.bases.get(base, False)

    def path(self, eval_id):
        return self.product / ".agentic/eval" / eval_id

    def create(self, eval_id, base):
        self.created.append((eval_id, base))
        self.path(eval_id).mkdir(parents=True)
        return self.path(eval_id)

    def ids(self):
        parent = self.product / ".agentic/eval"
        return sorted(p.name for p in parent.glob("EVAL-*")) if parent.is_dir() else []

    def remove(self, eval_id):
        self.removed.append(eval_id)
        shutil.rmtree(self.path(eval_id), ignore_errors=True)

    def delete_branches(self, eval_id):
        self.deleted.append(eval_id)


class FakeChild:
    def __init__(self, on_wait):
        self.on_wait = on_wait

    def wait(self):
        self.on_wait()
        return 0


class Spawner:
    """Starts no process: the child "runs" when waited, writing its document in its root."""

    def __init__(self, registry, cost=3.0, writes_document=True):
        self.registry, self.cost, self.writes = registry, cost, writes_document
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv, overlay):
        self.calls.append((argv, dict(overlay)))
        root = Path(overlay["AGENTIC_PRODUCT_DIR"])
        eval_id = root.name
        self.registry.entries.append(RunRegistration(1, "h", "product", eval_id, "role"))

        def finish():
            self.registry.entries[:] = [e for e in self.registry.entries if e.task_id != eval_id]
            if self.writes:
                (root / ".agentic/tasks").mkdir(parents=True)
                (root / ".agentic/tasks" / f"{eval_id}.md").write_text(
                    f"---\nid: {eval_id}\nevaluation:\n  issue: 1\ncost_usd: {self.cost}\n---\nbody\n"
                )

        return FakeChild(finish)


@pytest.fixture
def product(tmp_path):
    root = tmp_path / "p"
    (root / ".agentic").mkdir(parents=True)
    (root / ".agentic/eval-set.yaml").write_text(eval_set_yaml(1))
    return root


def cfg_of(product, cap=2):
    return Configuration(product_dir=product, max_concurrent_runs=cap)


async def drive(
    product,
    *,
    registry=None,
    roots=None,
    spawner=None,
    experiment_id=None,
    budget=500.0,
    yes=True,
    dry_run=False,
    ports=None,
    cap=2,
    sleep=None,
    **kw,
):
    registry = registry or MemoryRunRegistry()
    roots = roots or MemoryRoots(product)
    spawner = spawner or Spawner(registry)
    ports = ports or memory_ports(
        free_space=FakeFreeSpace(), experiment=FakeExperimentFile(EXPERIMENT)
    )
    await eval_commands._eval_run(
        cfg_of(product, cap),
        ports,
        registry,
        roots,
        spawner,
        "agentic",
        None,
        experiment_id,
        budget,
        yes,
        dry_run,
        sleep=sleep or (lambda s: None),
        clock=lambda: NOW,
        **kw,
    )
    return registry, roots, spawner


@pytest.mark.anyio
async def test_two_arms_of_one_entry_give_two_roots_and_documents_and_a_rerun_gets_k2(product):
    _, roots, spawner = await drive(product, experiment_id="exp")
    ids = [c[0] for c in roots.created]
    assert ids == [f"EVAL-1-{SHA8}-control-1", f"EVAL-1-{SHA8}-lean-1"]
    docs = product / task_document.EVAL_DOCS_SUBDIR
    assert sorted(p.stem for p in docs.glob("*.md")) == sorted(ids)
    argv, overlay = spawner.calls[1]
    assert argv[:2] == ["agentic", "run"] and "--arm" in argv and "lean" in argv
    assert overlay == {"AGENTIC_PRODUCT_DIR": str(product / ".agentic/eval" / ids[1])}
    _, roots2, _ = await drive(product, experiment_id="exp")
    assert [c[0] for c in roots2.created] == [
        f"EVAL-1-{SHA8}-control-2",
        f"EVAL-1-{SHA8}-lean-2",
    ]


@pytest.mark.anyio
async def test_after_a_child_the_root_and_branches_are_gone_and_the_document_is_kept(product):
    _, roots, spawner = await drive(product)
    eval_id = f"EVAL-1-{SHA8}-default-1"
    assert roots.ids() == [] and roots.removed == [eval_id] and roots.deleted == [eval_id]
    kept = product / task_document.EVAL_DOCS_SUBDIR / f"{eval_id}.md"
    assert kept.is_file()
    argv = spawner.calls[0][0]
    assert "--arm" not in argv  # the default arm names no experiment


@pytest.mark.anyio
async def test_the_frozen_entry_and_the_experiment_are_written_into_the_root(product):
    seen = {}
    registry = MemoryRunRegistry()
    spawner = Spawner(registry)
    inner = spawner.__call__

    def reading(argv, overlay):
        if seen:
            return inner(argv, overlay)  # the first child's input is the one checked
        seen["entry"] = yaml.safe_load(Path(argv[argv.index("--issue-input") + 1]).read_text())
        seen["experiment"] = yaml.safe_load(
            Path(argv[argv.index("--experiment-file") + 1]).read_text()
        )
        return inner(argv, overlay)

    await drive(product, registry=registry, spawner=reading, experiment_id="exp")
    assert seen["entry"]["id"].startswith(f"EVAL-1-{SHA8}-control-")
    assert (seen["entry"]["issue"], seen["entry"]["base"], seen["entry"]["product"]) == (
        1,
        BASE,
        "product",
    )
    assert seen["entry"]["title"] == "Issue 1" and seen["experiment"]["id"] == "exp"


@pytest.mark.anyio
async def test_a_base_that_does_not_resolve_or_has_no_stack_is_refused_before_any_launch(product):
    for bases, text in (({}, "does not resolve"), ({BASE: False}, "no .agentic/stack.yaml")):
        roots = MemoryRoots(product, bases)
        spawner = Spawner(MemoryRunRegistry())
        with pytest.raises(CannotStart, match=text):
            await drive(product, roots=roots, spawner=spawner)
        assert spawner.calls == [] and roots.created == []


@pytest.mark.anyio
async def test_it_requires_yes_prints_the_plan_and_dry_run_spends_nothing(product, capsys):
    with pytest.raises(CannotStart, match=r"estimated spend \$45.00"):
        await drive(product, yes=False)
    assert "arm default: 1 run(s)" in capsys.readouterr().out
    _, roots, spawner = await drive(product, yes=False, dry_run=True)
    assert spawner.calls == [] and roots.created == []


@pytest.mark.anyio
async def test_an_unknown_experiment_or_a_missing_declaration_is_refused(product):
    with pytest.raises(CannotStart, match="'exp'"):
        await drive(product, experiment_id="other")
    ports = memory_ports(free_space=FakeFreeSpace(), experiment=FakeExperimentFile(None))
    with pytest.raises(CannotStart, match="declares no experiment"):
        await drive(product, experiment_id="exp", ports=ports)


@pytest.mark.anyio
async def test_the_budget_stops_the_loop_and_a_child_with_no_document_costs_the_estimate(
    product, capsys
):
    (product / ".agentic/eval-set.yaml").write_text(eval_set_yaml(1, 2))
    registry = MemoryRunRegistry()
    _, _, spawner = await drive(
        product, registry=registry, spawner=Spawner(registry, writes_document=False), budget=45.0
    )
    assert len(spawner.calls) == 1
    assert "$45.00 spent so far" in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_full_cap_makes_the_driver_wait_and_it_never_uses_the_land_headroom(product):
    busy = RunRegistration(9, "h", "other", "GH-9", "role")
    registry = MemoryRunRegistry([busy])  # cap 1: the one slot is taken
    sleeps: list[float] = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            registry.entries.remove(busy)  # a slot frees; headroom alone never sufficed

    _, _, spawner = await drive(product, registry=registry, cap=1, sleep=sleep)
    assert sleeps[:2] == [eval_commands.HOLD_POLL_S] * 2 and len(spawner.calls) == 1
    assert registry.exclusive_entries >= 3


@pytest.mark.anyio
async def test_another_products_land_wait_or_an_account_pause_holds_until_lifted(product):
    registry = MemoryRunRegistry()
    other = product.parent / "other" / ".agentic/tasks"
    registry.publish_land_wait(other, "other", [5], NOW, NOW)
    held: list[int] = []

    def sleep(seconds):
        held.append(1)
        registry.publish_land_wait(other, "other", [], None, NOW)

    _, _, spawner = await drive(product, registry=registry, sleep=sleep)
    assert held == [1] and len(spawner.calls) == 1
    # the product's own wait never holds itself
    registry2 = MemoryRunRegistry()
    registry2.publish_land_wait(product / ".agentic/tasks", "product", [5], NOW, NOW)
    _, _, again = await drive(product, registry=registry2, sleep=lambda s: pytest.fail("held"))
    assert len(again.calls) == 1


def test_the_hold_reasons_come_from_the_pollers_rules(product):
    registry = MemoryRunRegistry()
    paused = [rules.ResumeCandidate(1, "account_limit", NOW, 0, 3, 0, False, None, False)]
    reason = eval_commands._held_by_slot(cfg_of(product), registry, paused, NOW)
    assert reason is not None and "account" in reason
    assert eval_commands._held_by_slot(cfg_of(product), registry, [], NOW) is None


@pytest.mark.anyio
async def test_a_volume_under_its_floor_holds_the_launch(product):
    from agentic.core.process.domain.scratch import SpaceReading

    low = FakeFreeSpace(SpaceReading(free_bytes=1, free_inodes=1))
    ports = memory_ports(free_space=low)
    calls: list[int] = []

    def sleep(seconds):
        calls.append(1)
        low.reading = FakeFreeSpace.AMPLE

    _, _, spawner = await drive(product, ports=ports, sleep=sleep)
    assert calls == [1] and len(spawner.calls) == 1


@pytest.mark.anyio
async def test_an_orphan_root_is_swept_at_start_but_a_live_one_is_not(product):
    roots = MemoryRoots(product)
    orphan, live = f"EVAL-9-{SHA8}-default-1", f"EVAL-8-{SHA8}-default-1"
    for name in (orphan, live):
        roots.create(name, BASE)
    (roots.path(orphan) / ".agentic/tasks").mkdir(parents=True)
    (roots.path(orphan) / ".agentic/tasks" / f"{orphan}.md").write_text("---\nid: x\n---\n")
    registry = MemoryRunRegistry([RunRegistration(2, "h", "product", live, "role")])
    await drive(product, registry=registry, roots=roots)
    assert orphan in roots.removed and orphan in roots.deleted
    assert live not in roots.removed
    assert (product / task_document.EVAL_DOCS_SUBDIR / f"{orphan}.md").is_file()


@pytest.mark.anyio
async def test_the_product_without_a_name_is_refused_and_the_eval_lock_is_taken(product):
    ports = memory_ports(stack=MemoryStack(product=None), free_space=FakeFreeSpace())
    with pytest.raises(CannotStart, match="expected `product`"):
        await drive(product, ports=ports)
    entered: list[int] = []

    class Lock:
        def __enter__(self):
            entered.append(1)

        def __exit__(self, *exc):
            return None

    await drive(product, locked=Lock)
    assert entered == [1]
