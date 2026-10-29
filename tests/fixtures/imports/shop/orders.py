"""Fixture for tests/test_imports.py: one import of each kind the reader must count."""

import collections.abc
from os import path, sep
from typing import TYPE_CHECKING

from . import util
from .util import helper

if TYPE_CHECKING:
    from decimal import Decimal


def late() -> object:
    import json

    return json


USED = (collections.abc, path, sep, util, helper, Decimal, late)
