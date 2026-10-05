from pathlib import Path

import pytest

from agentic.core.process.domain.roles import (
    PAIR_PM_PA,
    PJM,
    PM,
    PRODUCT_ANALYST,
    PROMPTS_DIR,
    ROLES,
    TECH_LEAD_EPHEMERAL,
    TECH_LEAD_EPHEMERAL_PLATFORM,
    TECH_LEAD_EPHEMERAL_STACK,
    Pair,
    describe_flags,
    prompt_hash,
    render,
)
from agentic.shared.configuration import Configuration

EPHEMERAL_ROLES = (TECH_LEAD_EPHEMERAL, TECH_LEAD_EPHEMERAL_PLATFORM, TECH_LEAD_EPHEMERAL_STACK)


def test_every_role_but_the_ephemeral_tech_lead_runs_on_its_own_kind():
    # `runs_on` is what `model_for` is given (ADR-0014, addendum of 2026-10-05): for
    # every role but the three ephemeral slugs, it is exactly `kind`, unchanged.
    for role in ROLES:
        if role not in EPHEMERAL_ROLES:
            assert role.runs_on == role.kind, role.slug


def test_the_three_ephemeral_slugs_run_on_the_actor_model(tmp_path):
    cfg = Configuration(
        product_dir=tmp_path,
        actor_model="actor-sentinel",
        verifier_model="verifier-sentinel",
        model_overrides={},
    )
    for role in EPHEMERAL_ROLES:
        # `kind` itself stays the glossary term (Validador): only the model changes.
        assert role.kind == "verifier"
        assert cfg.model_for(role.slug, role.runs_on) == cfg.actor_model
        # Rejected case: none of the three resolves to the verifier model.
        assert cfg.model_for(role.slug, role.runs_on) != cfg.verifier_model


def test_an_explicit_override_still_wins_for_an_ephemeral_slug(tmp_path):
    for role in EPHEMERAL_ROLES:
        cfg = Configuration(
            product_dir=tmp_path,
            actor_model="actor-sentinel",
            verifier_model="verifier-sentinel",
            model_overrides={role.slug: "override-sentinel"},
        )
        assert cfg.model_for(role.slug, role.runs_on) == "override-sentinel"


def test_the_roles_that_execute_cannot_touch_git_beyond_their_branch():
    from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, GIT_DENIED

    # The prompt already forbids these; the rules make it hold even unattended.
    # `checkout` stays allowed: `git checkout -- file` is legitimate.
    assert GIT_DENIED == ("Bash(git config:*)", "Bash(git push:*)", "Bash(git worktree:*)")
    for role in (DEVELOPER, DEVELOPER_INTEGRATOR):
        assert role.executes and role.denied == GIT_DENIED
    assert all(not role.denied for role in ROLES if not role.executes)


def test_the_reviewer_runs_tests_but_writes_nothing():
    # A behaviour objection comes with the test that reproduces it (ADR-0016): the
    # reviewer may run pytest in its checkout, and still has no tool that writes.
    from agentic.core.process.domain.roles import TECH_LEAD, TECH_LEAD_REVIEWER, reviewer_for

    reviewer = reviewer_for({"tests": "uv run --no-project pytest", "lint": "ruff check ."})
    # The product's declared checks, and nothing else that runs: they replace any
    # hardcoded test command, which did not match the product's (issue #22).
    assert set(reviewer.allowed) - set(TECH_LEAD_REVIEWER.allowed) == {
        "Bash(uv run --no-project pytest:*)",
        "Bash(ruff check .:*)",
    }
    assert not any("pytest" in rule for rule in TECH_LEAD_REVIEWER.allowed)
    assert reviewer.slug == TECH_LEAD_REVIEWER.slug and reviewer.tools == TECH_LEAD_REVIEWER.tools
    assert not {"Edit", "Write"} & set(reviewer.tools)
    assert not reviewer.executes
    assert not any("pytest" in rule for rule in TECH_LEAD.allowed)


def test_the_light_reviewer_is_a_read_only_verifier_with_a_narrower_reach():
    # ADR-0013 addendum: a Validador too, `kind` unchanged, so the model it runs on
    # still goes through `Configuration.model_overrides`/`model_for`, never a `kind`
    # change. No `code-review`, no `Agent`; it reads neither the original request nor
    # PM's PRD (ADR-0016 addendum), so it never grounds a `spec-*` finding there.
    from agentic.core.process.domain.roles import (
        FINDINGS,
        READ_CODE,
        READ_CODE_RULES,
        TECH_LEAD_REVIEWER,
        TECH_LEAD_REVIEWER_LIGHT,
    )

    assert TECH_LEAD_REVIEWER_LIGHT.kind == "verifier"
    assert TECH_LEAD_REVIEWER_LIGHT.slug == "tech_lead_review_light"
    assert TECH_LEAD_REVIEWER_LIGHT.skills == ()
    assert TECH_LEAD_REVIEWER_LIGHT.tools == READ_CODE
    assert TECH_LEAD_REVIEWER_LIGHT.allowed == READ_CODE_RULES
    assert "Agent" not in TECH_LEAD_REVIEWER_LIGHT.tools
    assert TECH_LEAD_REVIEWER_LIGHT.reads == ("Tech Lead", FINDINGS)
    assert not TECH_LEAD_REVIEWER_LIGHT.executes
    # `TECH_LEAD_REVIEWER` itself is untouched: `replace(...)` only.
    assert TECH_LEAD_REVIEWER.skills == ("code-review",)
    assert TECH_LEAD_REVIEWER.tools == READ_CODE + ("Agent",)


def test_reviewer_for_at_default_returns_exactly_what_it_returns_today():
    from agentic.core.process.domain.roles import TECH_LEAD_REVIEWER, reviewer_for

    reviewer = reviewer_for({"tests": "pytest"})
    assert reviewer.slug == TECH_LEAD_REVIEWER.slug
    assert reviewer.skills == TECH_LEAD_REVIEWER.skills


def test_reviewer_for_under_light_gives_the_light_role_only_to_a_real_subtask():
    # The integration always gets the full reviewer, in every mode (the plan's own
    # invariant): without this, arm B has no expensive pass anywhere and tests a
    # hypothesis nobody proposed. A real subtask under `light` gets the light one.
    from agentic.core.process.domain.roles import (
        TECH_LEAD_REVIEWER,
        TECH_LEAD_REVIEWER_LIGHT,
        reviewer_for,
    )
    from agentic.core.process.domain.task import INTEGRATION

    checks = {"tests": "pytest"}
    integration = reviewer_for(checks, INTEGRATION, mode="light")
    assert integration.slug == TECH_LEAD_REVIEWER.slug
    assert "code-review" in integration.skills

    subtask = reviewer_for(checks, "api", mode="light")
    assert subtask.slug == TECH_LEAD_REVIEWER_LIGHT.slug
    assert subtask.skills == ()


def test_describe_flags_is_one_bullet_per_flag():
    assert describe_flags({"touches_ui": "UI", "b": "B"}) == "- `touches_ui`: UI\n- `b`: B"


def test_render_fills_placeholders_and_hashes_the_raw_text():
    raw = (PROMPTS_DIR / "pm.md").read_text()
    loaded = render(
        raw, PROMPTS_DIR / "pm.md", max_changes=2, remaining_changes=1, flags="- `touches_ui`: x"
    )
    assert "{{" not in loaded.text
    assert "- `touches_ui`: x" in loaded.text
    assert "You have 1 left" in loaded.text
    assert "allows 2 rounds of requested changes" in loaded.text
    assert loaded.source == PROMPTS_DIR / "pm.md"
    assert loaded.hash == prompt_hash(raw)
    assert loaded.hash != prompt_hash(raw + " ")


def test_date_is_filled_by_default():
    loaded = render("Tuned prompt for {{date}}.", Path("/x/pjm.md"))
    assert loaded.text.startswith("Tuned prompt for 20")


def test_missing_placeholder_is_an_error():
    # An overlay may copy a placeholder its role never receives; it must not ship as `{{...}}`.
    with pytest.raises(ValueError, match="remaining_returns"):
        render("Left: {{remaining_returns}}", Path("/x/pjm.md"))


def test_the_definition_stage_roles_come_first():
    assert ROLES[0] is PJM and {PRODUCT_ANALYST, PM} <= set(ROLES)
    for role in ROLES:
        assert (PROMPTS_DIR / f"{role.slug}.md").exists()


def test_product_analyst_bug_has_its_own_larger_turn_budget():
    from agentic.core.process.domain.roles import PRODUCT_ANALYST, PRODUCT_ANALYST_BUG

    # Reproducing a bug means reading several modules and task documents of a repo
    # this size before ruling (issues #69, #81); the feature Product Analyst answers
    # from the request alone, so its default is not enough. Declared on the bug role
    # itself, not inherited from `PRODUCT_ANALYST`'s.
    assert PRODUCT_ANALYST_BUG.max_turns == 30
    assert PRODUCT_ANALYST_BUG.max_turns != PRODUCT_ANALYST.max_turns


def test_the_bug_product_analysts_prompt_renders_its_own_turn_budget():
    from agentic.core.process.domain.roles import PRODUCT_ANALYST_BUG

    raw = (PROMPTS_DIR / f"{PRODUCT_ANALYST_BUG.slug}.md").read_text()
    loaded = render(
        raw,
        PROMPTS_DIR / f"{PRODUCT_ANALYST_BUG.slug}.md",
        checks="- `tests`: `pytest`",
        max_turns=PRODUCT_ANALYST_BUG.max_turns,
    )
    assert "{{" not in loaded.text
    # `load_prompt` already passes every role's own `max_turns` (`run_role.py`); the
    # closing rule renders it instead of claiming a single turn (issues #69, #81).
    assert str(PRODUCT_ANALYST_BUG.max_turns) in loaded.text
    assert "Single turn: read, run, and deliver once" not in loaded.text
    # The rule a denied write should give the role (its own, not the path rule a hook
    # would name): the prompt states it directly, since this role runs with no hook
    # installed (`hooks_for`, `role.executes`).
    assert "writing is outside your role" in loaded.text.lower()
    assert "not a syntax problem" in loaded.text.lower()


def test_reads_include_own_section_for_returns():
    # On a return the actor must see what it wrote and what the verifier said.
    assert "Product Analyst" in PRODUCT_ANALYST.reads
    assert "PM" in PRODUCT_ANALYST.reads
    assert "Product Analyst" in PM.reads


def test_pair_key_is_who_returns_to_whom():
    # The key is what the state and the document store; a subtask makes it per-subtask,
    # as Tech Lead→Developer needs (README.md, 3 and 14).
    assert Pair(by=PM, to=PRODUCT_ANALYST).key == "PM→Product Analyst"
    assert Pair(by=PM, to=PRODUCT_ANALYST, subtask="s1").key == "PM→Product Analyst:s1"
    # The type says `str | None`; an empty id is still an id, not the task-level pair.
    assert Pair(by=PM, to=PRODUCT_ANALYST, subtask="").key == "PM→Product Analyst:"
    assert PAIR_PM_PA == Pair(by=PM, to=PRODUCT_ANALYST)


def test_pair_is_a_value():
    assert Pair(by=PM, to=PRODUCT_ANALYST) == Pair(by=PM, to=PRODUCT_ANALYST)
    assert Pair(by=PM, to=PRODUCT_ANALYST) != Pair(by=PM, to=PRODUCT_ANALYST, subtask="s1")
    assert hash(Pair(by=PM, to=PRODUCT_ANALYST)) == hash(Pair(by=PM, to=PRODUCT_ANALYST))


def test_the_reviewer_prompt_explains_every_return_category():
    from agentic.core.process.domain.schemas import CATEGORIES

    prompt = (PROMPTS_DIR / "tech_lead_review.md").read_text()
    for category in CATEGORIES:
        assert f"- `{category}`:" in prompt, category


def test_pjm_receives_the_declared_product_name():
    # `classify.py` passes it as a placeholder, the same class of fact as `{{date}}`
    # (docs/adr/0019): the model does not derive the product from issue vocabulary.
    raw = (PROMPTS_DIR / "pjm.md").read_text()
    assert "{{product}}" in raw


def test_pjm_never_classifies_a_request_about_the_system_as_a_bug():
    # ADR-0017: a task on the system itself is always platform_enhancement. The old
    # clause that let a system run failing its own docs come back `bug` is gone
    # (docs/product/classification.md, "Cuando el producto es el propio sistema").
    raw = (PROMPTS_DIR / "pjm.md").read_text()
    assert "never a `bug`" in raw


def test_pjm_a_documented_discrepancy_offering_both_fixes_stays_high_confidence():
    # gym-saas #20: the request cited a definition and an observed behaviour that did
    # not match, and offering both resolutions (fix the code, or the doc) must not
    # lower the confidence PjM reports (docs/product/classification.md).
    raw = (PROMPTS_DIR / "pjm.md").read_text()
    assert "never lowers your confidence" in raw


def test_pjm_a_documented_discrepancy_offering_both_fixes_does_not_escalate():
    # The `<Hard Limits>` line that fired for gym-saas #20 ("fits two types ... say
    # `low`") now carves the case out explicitly, so it cannot compete with the rule
    # above (Tech Lead's solution: the carve-out lives where the failure fired).
    raw = (PROMPTS_DIR / "pjm.md").read_text()
    assert "offering both does not make it `low`" in raw


def test_in_the_integration_review_a_problem_between_subtasks_is_the_subtasks():
    # The integration branch holds every subtask: what crosses them is the integrator's
    # to fix there, so it can block. Told otherwise, the reviewer marks it `plan`.
    from agentic.core.process.domain.schemas import Finding

    prompt = (PROMPTS_DIR / "tech_lead_review.md").read_text()
    assert "In the integration review" in prompt
    # The schema reaches the model too: both must say the same.
    assert "On the integration branch" in Finding.model_fields["scope"].description
