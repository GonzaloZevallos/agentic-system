"""The shared import reader (`scripts/docs/imports.py`, GH-430), pinned on a fixture package."""

from pathlib import Path

import pytest

from tests.docs_scripts import imports

ROOT = Path(__file__).parent / "fixtures" / "imports"


def _names(relative: str) -> set[str]:
    return imports.imports_of(ROOT / relative, ROOT)


def test_a_lazy_import_inside_a_function_counts():
    assert "json" in _names("shop/orders.py")


def test_an_import_under_type_checking_counts():
    assert {"decimal", "decimal.Decimal"} <= _names("shop/orders.py")


def test_import_a_dot_b_yields_the_dotted_name():
    assert "collections.abc" in _names("shop/orders.py")


def test_from_x_import_a_yields_x_and_x_dot_a_for_a_submodule_and_for_a_plain_name():
    names = _names("shop/orders.py")
    assert {"os", "os.path"} <= names  # `path` is a submodule of `os`
    assert {"os", "os.sep"} <= names  # `sep` is a plain name


def test_a_level_one_relative_import_resolves_against_the_files_package():
    names = _names("shop/orders.py")
    assert {"shop", "shop.util"} <= names  # from . import util
    assert {"shop.util", "shop.util.helper"} <= names  # from .util import helper


def test_a_level_two_relative_import_resolves_against_the_parent_package():
    names = _names("shop/billing/invoice.py")
    assert {"shop", "shop.util"} <= names  # from .. import util
    assert {"shop.util", "shop.util.helper"} <= names  # from ..util import helper
    assert {"shop.billing", "shop.billing.ledger"} <= names  # from . import ledger


def test_the_exact_set_of_a_file_is_what_the_rules_say():
    assert _names("shop/orders.py") == {
        "collections.abc",
        "decimal",
        "decimal.Decimal",
        "json",
        "os",
        "os.path",
        "os.sep",
        "shop",
        "shop.util",
        "shop.util.helper",
        "typing",
        "typing.TYPE_CHECKING",
    }


def test_the_root_decides_what_a_relative_import_resolves_to():
    """A file outside the root cannot be resolved: the reader refuses it, never guesses."""
    with pytest.raises(ValueError):
        imports.imports_of(ROOT / "shop" / "billing" / "invoice.py", ROOT / "shop" / "other")
