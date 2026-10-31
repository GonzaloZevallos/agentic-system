"""The refusal that costs nothing: a run whose product is the checkout running it."""

import difflib
import re
from dataclasses import replace
from pathlib import Path

import pytest

from agentic.core.knowledge.domain import stack
from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.application import preflight as module
from agentic.core.process.application.preflight import (
    PLACEHOLDERS,
    UNKNOWN_PRODUCT,
    RunsOnItsOwnCheckout,
    ScratchInARepository,
    declared_stack,
    declared_stack_of,
    docs_placeholders_missing,
    nearest_existing,
    no_forbidden,
    no_setup,
    preflight,
    product_name,
    refuse_scratch_in_a_repository,
    unfixed_stage0,
)
from agentic.core.process.application.run_role import load_prompt
from agentic.core.process.domain import rules
from agentic.core.process.domain.docs import Docs, plan_text, review_text
from agentic.core.process.domain.presubmit import RerunForm
from agentic.core.process.domain.run_check import Verification, WallOverCeiling
from agentic.core.process.domain.roles import ROLES
from agentic.core.process.domain.suppressions import Forbidden
from agentic.shared.configuration import Configuration
from tests.fakes import MemoryPrompts, MemoryStack, memory_ports

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


def test_the_nearest_existing_ancestor_of_a_root_that_is_not_there_yet(tmp_path):
    (tmp_path / "cache").mkdir()
    assert nearest_existing(tmp_path / "cache" / "a" / "b") == (tmp_path / "cache").resolve()
    assert nearest_existing(tmp_path / "cache") == (tmp_path / "cache").resolve()


def test_a_scratch_root_in_the_products_repository_is_refused(stage0, tmp_path):
    root = tmp_path / "cache" / "scratch"
    ports = ports_where(**{str(tmp_path.resolve()): "/r/.git", str(PRODUCT): "/r/.git"})
    with pytest.raises(ScratchInARepository) as refusal:
        refuse_scratch_in_a_repository(Configuration(product_dir=PRODUCT, scratch_dir=root), ports)
    assert str(root.resolve()) in str(refusal.value)
    assert "/r/.git" in str(refusal.value)
    assert "AGENTIC_SCRATCH_DIR" in str(refusal.value)


def test_a_scratch_root_in_stage0s_repository_is_refused(stage0, tmp_path):
    ports = ports_where(
        **{str(tmp_path.resolve()): "/sys/.git", str(stage0): "/sys/.git", str(PRODUCT): "/p/.git"}
    )
    with pytest.raises(ScratchInARepository):
        refuse_scratch_in_a_repository(
            Configuration(product_dir=PRODUCT, scratch_dir=tmp_path / "s"), ports
        )


def test_a_scratch_root_in_no_repository_or_in_a_third_one_passes(stage0, tmp_path):
    cfg = Configuration(product_dir=PRODUCT, scratch_dir=tmp_path / "s")
    refuse_scratch_in_a_repository(cfg, ports_where(**{str(PRODUCT): "/p/.git"}))
    third = ports_where(**{str(tmp_path.resolve()): "/o/.git", str(PRODUCT): "/p/.git"})
    refuse_scratch_in_a_repository(cfg, third)


def test_preflight_refuses_a_scratch_root_in_a_repository_before_reading_the_product(
    stage0, tmp_path
):
    ports = ports_where(**{str(tmp_path.resolve()): "/p/.git", str(PRODUCT): "/p/.git"})
    with pytest.raises(ScratchInARepository):
        preflight(
            Configuration(product_dir=PRODUCT, scratch_dir=tmp_path / "s"),
            replace(ports, flags=ExplodingFlags(), stack=ExplodingStack()),
        )


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


class BrokenParallelChecksStack(MemoryStack):
    """A stack whose `parallel_checks` is not a bool: `declared_stack` must surface it at
    load, before the gate would silently read it as a yes or a no."""

    def parallel_checks(self, revision: str) -> bool:
        raise ValueError("stack.yaml: expected `parallel_checks` to be true or false")


def test_a_broken_parallel_checks_refuses_before_spending(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match="expected `parallel_checks`"):
        preflight(
            Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenParallelChecksStack())
        )


def test_a_stack_without_parallel_checks_passes_and_keeps_its_setup_as_the_answer(stage0):
    """Absent is serial, never an error; and the validation call does not move what
    `declared_stack` returns."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    stack = MemoryStack(setup={"deps": "uv sync"})
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=stack))
    assert declared_stack(replace(ports, stack=stack)) == {"deps": "uv sync"}


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

    ports = ports_where(**{str(stage0): "/sys/.git", str(PRODUCT): "/repos/product/.git"})
    preflight(Configuration(product_dir=PRODUCT), ports)  # no ValueError: every prompt renders
    assert "tech_lead_review_light" in {role.slug for role in ROLES}


def test_placeholders_carry_an_empty_split_part_slot():
    assert PLACEHOLDERS["split_part"] == ""


# The three passages as the prompts carried them before they became placeholders (git
# 63d29be), typed here on purpose and not read from `rules`: this is what a typo in the
# `rules` constants is compared against.
ORIGINAL_PARTS_WORDING = {
    "{{parts_remedy}}": (
        "as one task: write it as `parts` (see `### Parts`) instead of subtasks. Do not shrink the"
        "\n"
        "   estimate to make it fit; a plan that fits and carries `parts` is refused too."
    ),
    "{{parts_step}}": (
        "8. `### Parts`, only when the estimate does not fit: at least two chained parts, each a\n"
        "   small task of its own that lands and shows in the product before the next one starts.\n"
        "   Each part has a `title`, a `body` (its whole description: the person who builds it has"
        "\n"
        "   not seen this plan) and `depends_on`, the numbers of EARLIER parts (1 is the first).\n"
        "   Write no subtasks then, and `### Subtasks` is not needed. A part is never split again."
        "\n"
        "   If the section `## Human` says the human decided to build this task whole, write\n"
        "   subtasks and no parts. Work that lands after the merge (a post-merge workflow, a bot\n"
        "   commit) is still a subtask of this task, never a part."
    ),
    "{{parts_field}}": (
        "- `parts`: one entry per part with `title`, `body` and `depends_on`, ONLY when the\n"
        "  estimate does not fit; then `subtasks` is empty. Empty when the plan fits."
    ),
}


def before_this_change(raw: str) -> str:
    """The prompt file as it was: no slot line, the original wording where the
    placeholders now stand."""
    before = raw.replace("{{split_part}}{{docs_plan}}\n", "")
    for placeholder, wording in ORIGINAL_PARTS_WORDING.items():
        assert before.count(placeholder) == 1
        before = before.replace(placeholder, wording)
    return before


@pytest.mark.parametrize("slug", ["tech_lead", "tech_lead_platform"])
def test_the_planning_prompts_differ_from_before_only_by_the_empty_slot(slug, tmp_path):
    """`{{split_part}}` sits on a line of its own in each planning Tech Lead prompt, so
    for a task that is not a part the render is what it was, plus one empty line."""
    role = next(r for r in ROLES if r.slug == slug)
    cfg = Configuration(product_dir=tmp_path)
    ports = memory_ports()
    raw, _ = ports.prompts.raw(role)
    assert raw.count("{{split_part}}{{docs_plan}}\n") == 1
    before_ports = memory_ports(prompts=MemoryPrompts(**{slug: before_this_change(raw)}))

    after = load_prompt(role, cfg, ports, {}, **PLACEHOLDERS).text.split("\n")
    before = load_prompt(role, cfg, before_ports, {}, **PLACEHOLDERS).text.split("\n")

    changes = [
        op for op in difflib.SequenceMatcher(None, before, after).get_opcodes() if op[0] != "equal"
    ]
    assert len(changes) == 1
    tag, i1, i2, j1, j2 = changes[0]
    assert (tag, before[i1:i2], after[j1:j2]) == ("insert", [], [""])


def test_a_filled_slot_reaches_the_rendered_prompt(tmp_path):

    role = next(r for r in ROLES if r.slug == "tech_lead_platform")
    values = {**PLACEHOLDERS, "split_part": rules.split_part_text(("GH-316", 2))}
    text = load_prompt(role, Configuration(product_dir=tmp_path), memory_ports(), {}, **values).text
    assert "GH-316" in text
    assert "{{" not in text


POST_MERGE_SENTENCE = "is still a subtask of this task, never a part"


@pytest.mark.parametrize("slug", ["tech_lead", "tech_lead_platform"])
def test_the_planning_prompts_say_work_after_the_merge_is_a_subtask_not_a_part(slug, tmp_path):
    role = next(r for r in ROLES if r.slug == slug)
    text = load_prompt(
        role, Configuration(product_dir=tmp_path), memory_ports(), {}, **PLACEHOLDERS
    ).text
    flat = " ".join(text.split())
    assert "a post-merge workflow, a bot commit) " + POST_MERGE_SENTENCE in flat
    # A part never reads it: it has no `### Parts` step at all.
    part = {**PLACEHOLDERS, "parts_step": rules.parts_step_text(("GH-316", 2))}
    part_text = load_prompt(role, Configuration(product_dir=tmp_path), memory_ports(), {}, **part)
    assert POST_MERGE_SENTENCE not in " ".join(part_text.text.split())


REMEDY_SENTENCES = (
    "write it as `parts` (see `### Parts`) instead of subtasks",
    "a plan that fits and carries `parts` is refused too",
    "8. `### Parts`, only when the estimate does not fit",
    "- `parts`: one entry per part with `title`, `body` and `depends_on`, ONLY when the",
)


def part_values(part_of):
    return {
        **PLACEHOLDERS,
        "split_part": rules.split_part_text(part_of),
        "parts_remedy": rules.parts_remedy_text(part_of),
        "parts_step": rules.parts_step_text(part_of),
        "parts_field": rules.parts_field_text(part_of),
        "tracker_with_parts": rules.TRACKER_WITH_PARTS,
    }


@pytest.mark.parametrize("slug", ["tech_lead", "tech_lead_platform"])
def test_a_part_reads_no_sentence_that_offers_parts_as_the_remedy(slug, tmp_path):
    role = next(r for r in ROLES if r.slug == slug)
    cfg = Configuration(product_dir=tmp_path)
    text = load_prompt(role, cfg, memory_ports(), {}, **part_values(("GH-316", 2))).text
    assert "{{" not in text
    assert "GH-316" in text
    for sentence in REMEDY_SENTENCES:
        assert sentence not in text
    assert "write subtasks only" in text


@pytest.mark.parametrize("slug", ["tech_lead", "tech_lead_platform"])
def test_a_task_that_is_not_a_part_still_reads_the_parts_instructions(slug, tmp_path):
    role = next(r for r in ROLES if r.slug == slug)
    cfg = Configuration(product_dir=tmp_path)
    text = load_prompt(role, cfg, memory_ports(), {}, **part_values(None)).text
    for sentence in REMEDY_SENTENCES:
        assert sentence in text
    assert text == load_prompt(role, cfg, memory_ports(), {}, **PLACEHOLDERS).text


def test_the_stack_prompt_has_no_split_part_slot():
    role = next(r for r in ROLES if r.slug == "tech_lead_stack")
    raw, _ = memory_ports().prompts.raw(role)
    assert "{{split_part}}" not in raw


def test_the_label_falls_back_instead_of_killing_a_node_already_running():
    """`preflight` is where a nameless product refuses. Past that point the run is under
    way and a label is not worth the task: `langgraph dev` never sees the CLI."""
    assert product_name(replace(memory_ports(), stack=MemoryStack(product=None))) == UNKNOWN_PRODUCT
    assert (
        product_name(replace(memory_ports(), stack=MemoryStack(product="gym-saas"))) == "gym-saas"
    )


def overlay_ports(product: Path, **files: str):
    """Ports whose prompts are the real overlay reader over `product`."""
    for stem, text in files.items():
        path = product / ".agentic/playbooks" / f"{stem}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return memory_ports(prompts=FilesystemPrompts(product))


def test_a_malformed_playbook_refuses_before_spending(stage0, tmp_path):
    ports = overlay_ports(tmp_path, developer="items: not-a-list")
    with pytest.raises(ValueError, match="developer"):
        preflight(Configuration(product_dir=tmp_path), ports)


def test_a_playbook_for_a_role_the_table_does_not_name_refuses(stage0, tmp_path):
    ports = overlay_ports(tmp_path, pm="items: []")
    with pytest.raises(ValueError, match="pm"):
        preflight(Configuration(product_dir=tmp_path), ports)


def test_the_delivery_records_are_not_mistaken_for_playbooks(stage0, tmp_path):
    ports = overlay_ports(tmp_path, reflected="tasks: []", rejected="adds: []")
    preflight(Configuration(product_dir=tmp_path), ports)


# -- the optional affected-only form (ADR-0046) ---------------------------------------


class BrokenAffectedChecksStack(MemoryStack):
    """A stack whose `affected_checks` has a command with no `{base}`: `declared_stack`
    must surface it at load, before a gate would run it."""

    def affected_checks(self, revision: str) -> dict[str, str]:
        raise ValueError(
            "stack.yaml: the command for 'tests' under `affected_checks` has no {base}"
        )


class BrokenAffectedFallbackStack(MemoryStack):
    def affected_fallback(self, revision: str) -> tuple[str, ...]:
        raise ValueError("stack.yaml: expected `affected_fallback` to be a list")


class EmptyAffectedFallbackStack(MemoryStack):
    """A stack whose base declares `affected_fallback: []`, read by the real domain
    function, so the message is the one a product would see."""

    def affected_fallback(self, revision: str) -> tuple[str, ...]:
        return stack.affected_fallback(
            {"checks": {"tests": "pytest"}, "affected_fallback": []}, "stack.yaml"
        )


def test_an_explicitly_empty_affected_fallback_is_refused_by_declared_stack(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match=r"stack.yaml: `affected_fallback` is declared empty"):
        declared_stack(replace(ports, stack=EmptyAffectedFallbackStack()))


def test_a_broken_affected_checks_refuses_before_spending(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match="under `affected_checks`"):
        preflight(
            Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenAffectedChecksStack())
        )


def test_a_broken_affected_fallback_refuses_before_spending(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match="expected `affected_fallback`"):
        preflight(
            Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenAffectedFallbackStack())
        )


def test_a_broken_affected_checks_also_refuses_at_a_tasks_base(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match="under `affected_checks`"):
        declared_stack(replace(ports, stack=BrokenAffectedChecksStack()), "abc123")


class BrokenDeliveryChecksStack(MemoryStack):
    """A stack whose base declares a `delivery_checks` name that is no check, read by the
    real domain function, so the message is the one a product would see."""

    def delivery_checks(self, revision: str) -> tuple[str, ...]:
        return stack.delivery_checks(
            {"checks": {"tests": "pytest"}, "delivery_checks": ["e2e"]}, "stack.yaml"
        )


def test_a_broken_delivery_checks_refuses_before_spending(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match=r"'e2e' under `delivery_checks`"):
        preflight(
            Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenDeliveryChecksStack())
        )


def test_a_broken_delivery_checks_also_refuses_at_a_tasks_base(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match=r"'e2e' under `delivery_checks`"):
        declared_stack(replace(ports, stack=BrokenDeliveryChecksStack()), "abc123")


def test_a_stack_with_delivery_checks_passes_preflight(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    stack = MemoryStack(delivery_checks=("tests",))
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=stack))


def test_a_stack_with_the_affected_keys_passes_and_keeps_its_setup_as_the_answer(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    stack = MemoryStack(
        setup={"deps": "uv sync"},
        affected_checks={"tests": "pytest --since {base}"},
        affected_fallback=("uv.lock",),
    )
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=stack))
    assert declared_stack(replace(ports, stack=stack)) == {"deps": "uv sync"}
    assert stack.revisions == ["HEAD", "HEAD", "HEAD", "HEAD"]


# -- criteria documents past the line cap (GH-295, ADR-0055) -------------------------


def _lines(count: int) -> str:
    return "".join(f"rule {n}\n" for n in range(count))


def test_the_files_measured_are_the_ones_the_knowledge_port_names():
    ports = memory_ports()
    ports.knowledge.criteria_files = lambda: ("OTHER.md",)
    ports.workspace.files[("HEAD", "OTHER.md")] = _lines(301)
    ports.workspace.files[("HEAD", "DESIGN_STANDARDS.md")] = _lines(301)
    (message,) = module.over_cap(ports)
    assert "OTHER.md" in message


def test_a_product_with_no_criteria_files_has_nothing_over_the_cap():
    """The system default `CODING_STANDARDS.md` (90 lines) is never read, let alone counted."""
    ports = memory_ports()
    assert module.over_cap(ports) == []


def test_a_file_over_the_cap_is_named_at_the_revision_asked():
    ports = memory_ports()
    ports.workspace.files[("abc", "DESIGN_STANDARDS.md")] = _lines(301)
    ports.workspace.files[("HEAD", "DESIGN_STANDARDS.md")] = _lines(10)
    (message,) = module.over_cap(ports, "abc")
    assert "DESIGN_STANDARDS.md" in message and "301" in message
    assert module.over_cap(ports) == []


def test_exactly_the_cap_is_not_over_it():
    ports = memory_ports()
    ports.workspace.files[("HEAD", "CODING_STANDARDS.md")] = _lines(300)
    assert module.over_cap(ports) == []


# -- criteria documents that a delivery makes longer than the cap (GH-334) -----------


def test_a_document_grown_past_the_cap_between_base_and_head_is_a_regression():
    ports = memory_ports()
    ports.workspace.files[("base", "DESIGN_STANDARDS.md")] = _lines(290)
    ports.workspace.files[("head", "DESIGN_STANDARDS.md")] = _lines(301)
    (message,) = module.criteria_regressed(ports, "base", "head")
    assert "DESIGN_STANDARDS.md" in message and "301" in message and "290" in message


def test_a_document_absent_at_the_base_counts_zero_and_one_over_the_cap_is_refused():
    ports = memory_ports()
    ports.workspace.files[("head", "DESIGN_STANDARDS.md")] = _lines(305)
    (message,) = module.criteria_regressed(ports, "base", "head")
    assert "DESIGN_STANDARDS.md" in message


def test_a_document_already_over_the_cap_at_the_base_and_not_longer_is_no_regression():
    ports = memory_ports()
    ports.workspace.files[("base", "DESIGN_STANDARDS.md")] = _lines(611)
    ports.workspace.files[("head", "DESIGN_STANDARDS.md")] = _lines(611)
    assert module.criteria_regressed(ports, "base", "head") == []


def test_criteria_texts_skips_the_files_absent_at_the_revision():
    ports = memory_ports()
    ports.workspace.files[("abc", "DESIGN_STANDARDS.md")] = _lines(3)
    assert module.criteria_texts(ports, "abc") == {"DESIGN_STANDARDS.md": _lines(3)}


@pytest.mark.parametrize("slug", ["tech_lead", "tech_lead_platform"])
@pytest.mark.parametrize("part_of", [None, ("GH-316", 2)])
def test_both_tech_lead_prompts_render_the_one_text_on_tracker_work_with_parts(
    slug, part_of, tmp_path
):
    """With the parts step empty (a part) and with the normal one, the rule on where tracker
    work goes is there, whole, from the one constant; and nothing asks the Tech Lead to
    write the re-point item itself: the system derives it."""
    role = next(r for r in ROLES if r.slug == slug)
    text = load_prompt(
        role, Configuration(product_dir=tmp_path), memory_ports(), {}, **part_values(part_of)
    ).text
    assert "{{" not in text
    assert rules.TRACKER_WITH_PARTS in text
    assert "so do not write those\n   items" in text
    assert "re-point" not in text.replace(rules.TRACKER_WITH_PARTS, "")


class BrokenRerunChecksStack(MemoryStack):
    """A stack whose base declares a `rerun_checks` form with no `delivery_checks`, read by
    the real domain function, so the message is the one a product would see."""

    def rerun_checks(self, revision: str) -> dict[str, RerunForm]:
        return stack.rerun_checks(
            {
                "checks": {"tests": "pytest"},
                "rerun_checks": {"tests": {"command": "pytest --lf", "test_id": r"(\S+)"}},
            },
            "stack.yaml",
        )


def test_a_broken_rerun_checks_refuses_before_spending(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match=r"`rerun_checks` requires `delivery_checks`"):
        preflight(
            Configuration(product_dir=PRODUCT), replace(ports, stack=BrokenRerunChecksStack())
        )


def test_a_broken_rerun_checks_also_refuses_at_a_tasks_base(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    with pytest.raises(ValueError, match=r"`rerun_checks` requires `delivery_checks`"):
        declared_stack(replace(ports, stack=BrokenRerunChecksStack()), "abc123")


def test_a_stack_with_a_rerun_form_passes_preflight(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    stack_ = MemoryStack(
        delivery_checks=("tests",),
        rerun_checks={"tests": RerunForm(command="pytest --lf", test_id=r"(\S+)")},
    )
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=stack_))


class BrokenCheckBudgetsStack(MemoryStack):
    """A stack whose base declares `check_budgets` with no `delivery_checks`, read by the
    real domain function, so the message is the one a product would see."""

    def check_budgets(self, revision: str) -> dict[str, float]:
        return stack.check_budgets(
            {"checks": {"tests": "pytest"}, "check_budgets": {"tests": 30}}, "stack.yaml"
        )


def test_a_broken_check_budgets_refuses_before_spending_and_at_a_tasks_base(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    broken = replace(ports, stack=BrokenCheckBudgetsStack())
    with pytest.raises(ValueError, match=r"`check_budgets` requires `delivery_checks`"):
        preflight(Configuration(product_dir=PRODUCT), broken)
    with pytest.raises(ValueError, match=r"`check_budgets` requires `delivery_checks`"):
        declared_stack(broken, "abc123")


def test_a_stack_with_check_budgets_passes_preflight(stage0):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    stack_ = MemoryStack(delivery_checks=("tests",), check_budgets={"tests": 30.0})
    preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=stack_))


def test_a_refused_merge_inert_glob_refuses_before_spending(stage0):
    """`*` would whitelist every path; the loader refuses it, and preflight reports it at
    load instead of leaving the merge gate to read it as "not covered" (GH-353)."""
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    stack_ = MemoryStack()
    stack_.merge_inert_error = "stack.yaml: '*' under `merge_inert` is refused: wildcard_root"
    with pytest.raises(ValueError, match=r"under `merge_inert` is refused"):
        preflight(Configuration(product_dir=PRODUCT), replace(ports, stack=stack_))
    with pytest.raises(ValueError, match=r"under `merge_inert` is refused"):
        declared_stack(replace(ports, stack=stack_), "abc123")


DOCS = Docs(generate="make docs", paths=("docs/architecture/generated/**",))
DOCS_SLUGS = ("tech_lead", "tech_lead_platform", "tech_lead_review", "tech_lead_review_light")


def _docs_text(slug: str) -> str:
    return plan_text(DOCS) if slug in ("tech_lead", "tech_lead_platform") else review_text(DOCS)


def _lines_of(slug: str, tmp_path, **docs_values) -> list[str]:
    role = next(r for r in ROLES if r.slug == slug)
    values = {**PLACEHOLDERS, **docs_values}
    return load_prompt(
        role, Configuration(product_dir=tmp_path), memory_ports(), {}, **values
    ).text.split("\n")


@pytest.mark.parametrize("slug", DOCS_SLUGS)
def test_the_docs_placeholder_ends_a_line_so_an_empty_one_leaves_the_prompt_as_it_was(
    slug, tmp_path
):
    """No blank line is added: the placeholder sits at the end of a line that existed before
    (`{{split_part}}` or `{{checks}}`), never on a line of its own."""
    raw, _ = memory_ports().prompts.raw(next(r for r in ROLES if r.slug == slug))
    name = "docs_plan" if slug in ("tech_lead", "tech_lead_platform") else "docs_review"
    assert raw.count("{{" + name + "}}") == 1
    assert "\n{{" + name + "}}\n" not in raw
    before = raw.replace("{{" + name + "}}", "")
    ports = memory_ports(prompts=MemoryPrompts(**{slug: before}))
    role = next(r for r in ROLES if r.slug == slug)
    cfg = Configuration(product_dir=tmp_path)
    assert (
        load_prompt(role, cfg, memory_ports(), {}, **PLACEHOLDERS).text
        == load_prompt(role, cfg, ports, {}, **PLACEHOLDERS).text
    )


@pytest.mark.parametrize("slug", DOCS_SLUGS)
def test_a_declared_docs_block_reaches_each_tech_lead_prompt(slug, tmp_path):
    name = "docs_plan" if slug in ("tech_lead", "tech_lead_platform") else "docs_review"
    text = _docs_text(slug)
    lines = _lines_of(slug, tmp_path, **{name: text})
    assert text.strip().split("\n")[0] in lines
    assert "{{" not in "\n".join(lines)


def test_no_advisory_when_the_product_declares_no_docs():
    assert docs_placeholders_missing(memory_ports()) == []


def test_no_advisory_when_every_tech_lead_prompt_carries_its_placeholder():
    ports = replace(memory_ports(), stack=MemoryStack(docs=DOCS))
    assert docs_placeholders_missing(ports) == []


def test_an_override_that_drops_a_placeholder_is_named_with_its_file_and_never_refused():
    kept = memory_ports().prompts.raw(next(r for r in ROLES if r.slug == "tech_lead_review"))[0]
    dropped = kept.replace("{{docs_review}}", "")
    assert dropped != kept
    ports = replace(
        memory_ports(),
        stack=MemoryStack(docs=DOCS),
        prompts=MemoryPrompts(tech_lead_review=dropped),
    )
    messages = docs_placeholders_missing(ports)
    assert len(messages) == 1
    assert "/memory/tech_lead_review.md" in messages[0]
    assert "{{docs_review}}" in messages[0]
    preflight(Configuration(product_dir=PRODUCT), ports)


def _rules(count: int) -> str:
    return "".join(f"- D-{n:08x}: regla {n}\n" for n in range(count))


def test_owner_decisions_past_the_live_cap_are_named_at_the_revision_asked():
    ports = memory_ports()
    ports.workspace.files[("abc", "docs/decisiones-del-dueño.md")] = _rules(121)
    ports.workspace.files[("HEAD", "docs/decisiones-del-dueño.md")] = _rules(10)
    (message,) = module.owner_over_cap(ports, "abc")
    assert "docs/decisiones-del-dueño.md" in message and "121" in message
    assert module.owner_over_cap(ports) == []


def test_owner_decisions_at_the_cap_or_absent_say_nothing():
    ports = memory_ports()
    assert module.owner_over_cap(ports) == []
    ports.workspace.files[("HEAD", "docs/product/decisiones-del-dueño.md")] = _rules(120)
    assert module.owner_over_cap(ports) == []


def test_the_owner_cap_is_an_advisory_and_preflight_does_not_refuse_over_it():
    ports = memory_ports()
    ports.workspace.files[("HEAD", "docs/decisiones-del-dueño.md")] = _rules(500)
    assert len(module.owner_over_cap(ports)) == 1
    assert module.over_cap(ports) == []  # the criteria cap, which can refuse a merge, is apart


# --- the ceiling of a declared wall (ADR-0065 addendum, GH-451) -----------------------------


def _walls_ports(stage0, **declared):
    ports = ports_where(**{str(stage0): "/r/.git", str(PRODUCT): "/other/.git"})
    return replace(ports, stack=MemoryStack(**declared))


def test_a_verification_wall_over_the_ceiling_refuses_before_spending(stage0):
    ports = _walls_ports(
        stage0, verifications={"compare": Verification(command="./compare.sh", wall_s=3601.0)}
    )
    with pytest.raises(WallOverCeiling, match=r"'compare'.*3601.*3600"):
        preflight(Configuration(product_dir=PRODUCT), ports)


def test_a_check_timeout_over_the_ceiling_refuses_before_spending(stage0):
    ports = _walls_ports(stage0, check_timeouts={"tests": 7200.0})
    with pytest.raises(WallOverCeiling, match=r"'tests'"):
        preflight(Configuration(product_dir=PRODUCT), ports)


def test_the_owner_can_raise_the_ceiling_and_the_same_stack_passes(stage0):
    ports = _walls_ports(stage0, check_timeouts={"tests": 7200.0})
    cfg = Configuration(product_dir=PRODUCT, verification_wall_max_s=7200.0)
    preflight(cfg, ports)


def test_walls_at_or_under_the_ceiling_and_no_walls_pass_preflight(stage0):
    preflight(Configuration(product_dir=PRODUCT), _walls_ports(stage0))
    ports = _walls_ports(
        stage0,
        verifications={"compare": Verification(command="./compare.sh", wall_s=3600.0)},
        check_timeouts={"tests": 30.0},
    )
    preflight(Configuration(product_dir=PRODUCT), ports)
