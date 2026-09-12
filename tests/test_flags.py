import pytest

from agentic import flags


def test_default_vocabulary(tmp_path):
    vocab = flags.load(tmp_path)
    assert set(vocab) == {"touches_ui", "touches_copy"}
    assert flags.describe(vocab).startswith("- `touches_ui`: ")


def test_product_overlay_replaces_entirely(tmp_path):
    overlay = tmp_path / ".agentic/flags.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("needs_migration: Changes the schema.\n")
    assert flags.load(tmp_path) == {"needs_migration": "Changes the schema."}


def test_empty_overlay_is_an_error(tmp_path):
    overlay = tmp_path / ".agentic/flags.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("")
    with pytest.raises(ValueError, match="non-empty mapping"):
        flags.load(tmp_path)
