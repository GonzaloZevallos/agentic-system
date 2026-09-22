"""The pure predicate over a product-declared suppression (issue #38). No filesystem,
no YAML, and no language of its own: `Forbidden` records arrive already built, with
whatever pattern and extensions a product happens to declare."""

import re

from agentic.core.process.domain.suppressions import Forbidden, denial

SUPPRESS = Forbidden(
    pattern=re.compile(r"SUPPRESS\b"),
    extensions=frozenset({".src"}),
    reason="the product's own reason for forbidding this, verbatim",
)


def test_denial_refuses_a_new_occurrence():
    reason = denial("app/x.src", "keep\n", "keep  SUPPRESS\n", [SUPPRESS])
    assert reason == SUPPRESS.reason


def test_denial_allows_an_occurrence_that_was_already_there():
    # One already in `before` is not the task's: counted against `before`, not absent.
    before = "a()  SUPPRESS\n"
    after = "a()  SUPPRESS\nb()\n"
    assert denial("app/x.src", before, after, [SUPPRESS]) is None


def test_denial_ignores_a_file_extension_the_rule_does_not_cover():
    assert denial("docs/notes.md", "", "SUPPRESS\n", [SUPPRESS]) is None


def test_denial_is_none_with_no_forbidden_rules_declared():
    assert denial("app/x.src", "", "SUPPRESS\n", []) is None


def test_denial_is_the_first_declared_rule_in_file_order():
    first = Forbidden(re.compile("TODO"), frozenset({".src"}), "first reason")
    second = Forbidden(re.compile("TODO"), frozenset({".src"}), "second reason")
    reason = denial("app/x.src", "", "TODO\n", [first, second])
    assert reason == "first reason"
