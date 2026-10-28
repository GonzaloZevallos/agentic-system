import pytest
import yaml

from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.domain import playbook
from agentic.core.process.domain.playbook import Playbook
from agentic.core.process.domain.roles import DEVELOPER, PJM, PM, PROMPTS_DIR
from tests.conformance import (
    prompts_keep_an_unreadable_file_for_its_role,
    prompts_serve_the_text_of_construction,
)


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


ITEM = {
    "id": "developer-1",
    "lesson": "Run the declared checks before delivering.",
    "category": "spec-behaviour",
    "since": "empty",
    "seen_in": ["GH-1"],
    "added_at": "2026-10-05",
}


def write_playbook(product, stem, text):
    path = product / ".agentic/playbooks" / f"{stem}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_a_missing_playbook_file_is_an_empty_playbook(tmp_path):
    assert FilesystemPrompts(tmp_path).playbook(DEVELOPER) == Playbook(slug="developer")


def test_the_playbook_is_read_from_the_product_overlay(tmp_path):
    write_playbook(tmp_path, "developer", yaml.safe_dump({"items": [ITEM]}))
    read = FilesystemPrompts(tmp_path).playbook(DEVELOPER)
    assert [i.id for i in read.items] == ["developer-1"]
    assert read.slug == "developer"


def test_a_malformed_playbook_raises(tmp_path):
    write_playbook(tmp_path, "developer", "items: not-a-list")
    with pytest.raises(ValueError, match="developer"):
        FilesystemPrompts(tmp_path).playbook(DEVELOPER)


def test_a_role_outside_the_set_reads_nothing(tmp_path):
    """Not even its file: a slug the table does not name has no playbook to find."""
    write_playbook(tmp_path, "developer", yaml.safe_dump({"items": [ITEM]}))
    assert FilesystemPrompts(tmp_path).playbook(PM) == Playbook(slug="pm")


def test_a_file_named_for_a_slug_outside_the_set_is_refused(tmp_path):
    write_playbook(tmp_path, "pm", "items: []")
    with pytest.raises(ValueError, match="pm"):
        FilesystemPrompts(tmp_path).playbook(DEVELOPER)


def test_a_misnamed_file_is_refused_even_for_a_role_outside_the_set(tmp_path):
    write_playbook(tmp_path, "pm", "items: []")
    with pytest.raises(ValueError, match="pm"):
        FilesystemPrompts(tmp_path).playbook(PM)


def test_the_record_files_and_summaries_are_not_playbooks(tmp_path):
    for stem in playbook.RECORD_FILES:
        write_playbook(tmp_path, stem, "anything: at all")
    (tmp_path / ".agentic/playbooks/summaries").mkdir()
    (tmp_path / ".agentic/playbooks/summaries/10.md").write_text("# summary")
    assert FilesystemPrompts(tmp_path).playbook(DEVELOPER) == Playbook(slug="developer")


def test_the_pjm_prompt_never_escalates_to_ask_for_gh_on_tracker_only_work(tmp_path):
    """GH-336: the rule is present in the prompt the PjM is rendered from."""
    raw, _ = FilesystemPrompts(tmp_path).raw(PJM)
    assert "only on the issue tracker" in raw
    assert "never escalate to ask the human for `gh`" in raw


def test_the_port_keeps_the_snapshot_contract(tmp_path):
    def build(system_dir):
        return FilesystemPrompts(tmp_path / "product", system_dir=system_dir)

    first = tmp_path / "first"
    first.mkdir()
    prompts_serve_the_text_of_construction(build, first, PM)
    second = tmp_path / "second"
    second.mkdir()
    prompts_keep_an_unreadable_file_for_its_role(build, second, PM, DEVELOPER)


def test_an_overlay_written_after_construction_is_not_read(tmp_path):
    prompts = FilesystemPrompts(tmp_path)
    overlay = tmp_path / ".agentic/prompts/pjm.md"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("Late overlay.")
    assert prompts.raw(PJM) == ((PROMPTS_DIR / "pjm.md").read_text(), PROMPTS_DIR / "pjm.md")


def test_a_file_of_the_overlay_outside_the_role_set_is_ignored(tmp_path):
    overlay = tmp_path / ".agentic/prompts/not_a_role.md"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"\xff\xfe")
    assert FilesystemPrompts(tmp_path).raw(PJM)[1] == PROMPTS_DIR / "pjm.md"


def test_an_unreadable_overlay_fails_its_role_and_not_the_construction(tmp_path):
    overlay = tmp_path / ".agentic/prompts/pjm.md"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"\xff\xfe")
    prompts = FilesystemPrompts(tmp_path)
    with pytest.raises(UnicodeDecodeError):
        prompts.raw(PJM)
    assert prompts.raw(PM)[1] == PROMPTS_DIR / "pm.md"
