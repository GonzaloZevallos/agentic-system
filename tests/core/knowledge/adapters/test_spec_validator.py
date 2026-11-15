"""`DomainSpecValidator` (GH-594) passes straight through to `knowledge.domain.spec.validate`."""

from __future__ import annotations

from agentic.core.knowledge.adapters.spec_validator import DomainSpecValidator
from agentic.core.knowledge.domain import spec

COMMIT = "a" * 40
PATH = "docs/specs/a.md"


def test_it_answers_what_validate_answers_for_a_text_it_must_refuse():
    text = "no front matter at all"
    assert DomainSpecValidator().validate(text, PATH, COMMIT, None, "owner") == "spec_invalid"
    assert spec.validate(text, PATH, COMMIT, None, "owner") == "spec_invalid"


def test_a_draft_spec_is_unapproved_through_the_adapter():
    text = "---\nstatus: draft\n---\n# S\n"
    got = DomainSpecValidator().validate(text, PATH, COMMIT, None, "owner")
    assert got == spec.validate(text, PATH, COMMIT, None, "owner")
