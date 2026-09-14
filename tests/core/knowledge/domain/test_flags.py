import pytest

from agentic.core.knowledge.domain import flags


def test_vocabulary_normalises_a_mapping():
    assert flags.vocabulary({"touches_ui": "UI"}, "x") == {"touches_ui": "UI"}


@pytest.mark.parametrize("data", [None, {}, ["touches_ui"], "touches_ui"])
def test_anything_but_a_non_empty_mapping_is_an_error(data):
    with pytest.raises(ValueError, match="non-empty mapping"):
        flags.vocabulary(data, "flags.yaml")


def test_default_file_exists_and_is_valid():
    assert flags.DEFAULT.name == "flags.yaml" and flags.DEFAULT.is_file()
