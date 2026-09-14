import pytest

from agentic.core.knowledge.adapters.filesystem import FilesystemFlags


def test_default_vocabulary(tmp_path):
    assert set(FilesystemFlags(tmp_path).load()) == {"touches_ui", "touches_copy"}


def test_product_overlay_replaces_entirely(tmp_path):
    overlay = tmp_path / ".agentic/flags.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("needs_migration: Changes the schema.\n")
    assert FilesystemFlags(tmp_path).load() == {"needs_migration": "Changes the schema."}


def test_empty_overlay_is_an_error(tmp_path):
    overlay = tmp_path / ".agentic/flags.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("")
    with pytest.raises(ValueError, match="flags.yaml: expected a non-empty mapping"):
        FilesystemFlags(tmp_path).load()
