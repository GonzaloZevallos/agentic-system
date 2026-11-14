from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from agentic.core.process.adapters.filesystem import FilesystemPrompts
from agentic.core.process.application.ports import Prompts
from agentic.core.process.domain import playbook
from agentic.core.process.domain.playbook import Playbook
from agentic.core.process.domain.roles import DEVELOPER, PJM, PM, PROMPTS_DIR
from tests.conformance import (
    prompts_keep_an_unreadable_file_for_its_role,
    prompts_serve_spec_blocks,
    prompts_serve_the_text_of_construction,
)
from tests.fakes import MemoryPrompts


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


def write_block(directory, name, text):
    path = directory / "spec" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_block_is_read_from_the_system_snapshot(tmp_path):
    system = tmp_path / "system"
    write_block(system, "build", "System block.")
    prompts = FilesystemPrompts(tmp_path / "product", system, blocks={"developer": "build"})
    assert prompts.spec_block("build") == "System block."
    assert prompts.spec_block_names() == ("build",)


def test_the_product_overlay_of_a_block_wins(tmp_path):
    system, product = tmp_path / "system", tmp_path / "product"
    write_block(system, "build", "System block.")
    write_block(product / ".agentic/prompts", "build", "Product block.")
    prompts = FilesystemPrompts(product, system, blocks={"developer": "build"})
    assert prompts.spec_block("build") == "Product block."


def test_a_block_edited_or_deleted_after_construction_still_serves_the_old_text(tmp_path):
    system = tmp_path / "system"
    path = write_block(system, "build", "Old.")
    prompts = FilesystemPrompts(tmp_path / "product", system, blocks={"developer": "build"})
    path.write_text("New.", encoding="utf-8")
    assert prompts.spec_block("build") == "Old."
    path.unlink()
    assert prompts.spec_block("build") == "Old."
    write_block(tmp_path / "product/.agentic/prompts", "build", "Late overlay.")
    assert prompts.spec_block("build") == "Old."


def test_a_missing_block_file_raises_on_the_call_and_not_on_construction(tmp_path):
    prompts = FilesystemPrompts(tmp_path, tmp_path / "system", blocks={"pm": "ghost", "dev": "ok"})
    with pytest.raises(OSError):
        prompts.spec_block("ghost")
    assert prompts.spec_block_names() == ("ghost", "ok")


def test_a_block_that_is_not_utf8_raises_on_the_call(tmp_path):
    path = write_block(tmp_path / "system", "bad", "x")
    path.write_bytes(b"\xff\xfe not utf-8")
    prompts = FilesystemPrompts(tmp_path, tmp_path / "system", blocks={"pm": "bad"})
    with pytest.raises(UnicodeDecodeError):
        prompts.spec_block("bad")


def test_a_bad_name_is_kept_as_a_value_error_and_no_path_is_built_for_it(tmp_path):
    system, product = tmp_path / "system", tmp_path / "product"
    for stray in (system / "secret.md", product / ".agentic/prompts/secret.md"):
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("never read", encoding="utf-8")
    table = {"pm": "../secret", "dev": "../../secret", "x": "A"}
    prompts = FilesystemPrompts(product, system, blocks=table)
    assert prompts.spec_block_names() == ("../../secret", "../secret", "A")
    for name in prompts.spec_block_names():
        with pytest.raises(ValueError, match="not a valid spec block name"):
            prompts.spec_block(name)


def test_a_name_outside_the_snapshot_is_a_value_error_not_a_key_error(tmp_path):
    prompts = FilesystemPrompts(tmp_path, tmp_path / "system", blocks={})
    with pytest.raises(ValueError, match="build"):
        prompts.spec_block("build")
    assert prompts.spec_block_names() == ()


def test_the_shipped_table_is_empty_so_construction_opens_nothing_under_spec(monkeypatch, tmp_path):
    opened = []
    real = Path.read_text

    def spy(self, *args, **kwargs):
        opened.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    prompts = FilesystemPrompts(tmp_path)
    assert prompts.spec_block_names() == ()
    assert not [p for p in opened if "spec" in p.parts]


def test_both_implementations_serve_spec_blocks_alike(tmp_path):
    def filesystem(tmp: Path, table: Mapping[str, str]) -> Prompts:
        system = tmp / "system"
        for name in set(table.values()):
            write_block(system, name, f"text of {name}")
        return FilesystemPrompts(tmp / "product", system, blocks=table)

    def memory(tmp: Path, table: Mapping[str, str]) -> Prompts:
        return MemoryPrompts(blocks={name: f"text of {name}" for name in set(table.values())})

    prompts_serve_spec_blocks(filesystem, tmp_path / "fs")
    prompts_serve_spec_blocks(memory, tmp_path / "memory")


class _UncheckedNames(MemoryPrompts):
    """A port that looks a name up without `block_name_ok`: what the contract refuses."""

    def spec_block(self, name: str) -> str:
        if name not in self.blocks:
            raise ValueError(f"no spec block {name!r}")
        held = self.blocks[name]
        assert isinstance(held, str)
        return held


def test_the_spec_block_contract_refuses_a_port_that_serves_an_invalid_name(tmp_path):
    def unchecked(tmp: Path, table: Mapping[str, str]) -> Prompts:
        return _UncheckedNames(blocks={name: f"text of {name}" for name in set(table.values())})

    with pytest.raises(AssertionError, match=r"\.\./x"):
        prompts_serve_spec_blocks(unchecked, tmp_path)
