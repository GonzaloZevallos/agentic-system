from agentic.roles import PJM, PM, PRODUCT_ANALYST, PROMPTS_DIR, prompt_hash


def test_prompt_fills_placeholders_and_hashes_raw_file(tmp_path):
    loaded = PM.prompt(tmp_path, max_returns=2, remaining_returns=1, flags="- `touches_ui`: x")
    assert "{{" not in loaded.text
    assert "- `touches_ui`: x" in loaded.text
    assert "You have 1 left" in loaded.text
    assert "allows 2 returns" in loaded.text
    assert loaded.source == PROMPTS_DIR / "pm.md"
    assert loaded.hash == prompt_hash((PROMPTS_DIR / "pm.md").read_text())


def test_product_overlay_wins(tmp_path):
    overlay = tmp_path / ".agentic/prompts/pjm.md"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("Tuned prompt for {{date}}.")
    loaded = PJM.prompt(tmp_path)
    assert loaded.source == overlay
    assert loaded.text.startswith("Tuned prompt for 20")
    assert loaded.hash != PJM.prompt(tmp_path / "elsewhere").hash


def test_every_role_has_a_prompt_file():
    for role in (PJM, PRODUCT_ANALYST, PM):
        assert (PROMPTS_DIR / f"{role.slug}.md").exists()


def test_reads_include_own_section_for_returns():
    # On a return the thinker must see what it wrote and what the validator said.
    assert "Product Analyst" in PRODUCT_ANALYST.reads
    assert "PM" in PRODUCT_ANALYST.reads
    assert "Product Analyst" in PM.reads
