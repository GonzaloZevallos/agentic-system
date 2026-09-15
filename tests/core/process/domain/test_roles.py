from pathlib import Path

import pytest

from agentic.core.process.domain.roles import (
    PAIR_PM_PA,
    PJM,
    PM,
    PRODUCT_ANALYST,
    PROMPTS_DIR,
    ROLES,
    Pair,
    describe_flags,
    prompt_hash,
    render,
)


def test_the_roles_that_execute_cannot_touch_git_beyond_their_branch():
    from agentic.core.process.domain.roles import DEVELOPER, DEVELOPER_INTEGRATOR, GIT_DENIED

    # The prompt already forbids these; the rules make it hold even unattended.
    # `checkout` stays allowed: `git checkout -- file` is legitimate.
    assert GIT_DENIED == ("Bash(git config:*)", "Bash(git push:*)", "Bash(git worktree:*)")
    for role in (DEVELOPER, DEVELOPER_INTEGRATOR):
        assert role.executes and role.denied == GIT_DENIED
    assert all(not role.denied for role in ROLES if not role.executes)


def test_describe_flags_is_one_bullet_per_flag():
    assert describe_flags({"touches_ui": "UI", "b": "B"}) == "- `touches_ui`: UI\n- `b`: B"


def test_render_fills_placeholders_and_hashes_the_raw_text():
    raw = (PROMPTS_DIR / "pm.md").read_text()
    loaded = render(
        raw, PROMPTS_DIR / "pm.md", max_returns=2, remaining_returns=1, flags="- `touches_ui`: x"
    )
    assert "{{" not in loaded.text
    assert "- `touches_ui`: x" in loaded.text
    assert "You have 1 left" in loaded.text
    assert "allows 2 returns" in loaded.text
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


def test_reads_include_own_section_for_returns():
    # On a return the thinker must see what it wrote and what the validator said.
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
