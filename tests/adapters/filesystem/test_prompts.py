from agentic.adapters.filesystem import FilesystemPrompts
from agentic.domain.roles import PJM, PROMPTS_DIR


def test_default_comes_from_the_domain(tmp_path):
    raw, source = FilesystemPrompts(tmp_path).raw(PJM)
    assert source == PROMPTS_DIR / "pjm.md"
    assert raw == source.read_text()


def test_product_overlay_wins(tmp_path):
    overlay = tmp_path / ".agentic/prompts/pjm.md"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("Tuned prompt for {{date}}.")
    raw, source = FilesystemPrompts(tmp_path).raw(PJM)
    assert (raw, source) == ("Tuned prompt for {{date}}.", overlay)
